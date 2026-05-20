"""技术指标特征工程"""
import numpy as np
import pandas as pd

from config.settings import (
    MA_WINDOWS, EMA_WINDOWS, RSI_PERIOD,
    MACD_FAST, MACD_SLOW, MACD_SIGNAL,
    BOLL_WINDOW, BOLL_STD, ATR_PERIOD, KDJ_PERIOD,
    FORWARD_DAYS,
)


def add_all_features(df: pd.DataFrame) -> pd.DataFrame:
    """对单只股票 K 线添加全部特征，返回新 DataFrame"""
    df = df.copy()
    df = _add_ma(df)
    df = _add_ema(df)
    df = _add_macd(df)
    df = _add_rsi(df)
    df = _add_bollinger(df)
    df = _add_kdj(df)
    df = _add_atr(df)
    df = _add_volume_features(df)
    df = _add_price_features(df)
    df = _add_derived_momentum(df)
    df = _add_sentiment(df)
    df = _add_ma_alignment(df)
    df = _add_fundamental_features(df)
    df = _add_fund_flow_features(df)
    df = _add_label(df)
    return df


# ── 趋势类 ────────────────────────────────────────────────────────────────────

def _add_ma(df: pd.DataFrame) -> pd.DataFrame:
    for w in MA_WINDOWS:
        df[f"ma{w}"] = df["close"].rolling(w).mean()
        df[f"ma{w}_ratio"] = df["close"] / df[f"ma{w}"] - 1
    return df


def _add_ema(df: pd.DataFrame) -> pd.DataFrame:
    for w in EMA_WINDOWS:
        df[f"ema{w}"] = df["close"].ewm(span=w, adjust=False).mean()
    return df


def _add_macd(df: pd.DataFrame) -> pd.DataFrame:
    ema_fast = df["close"].ewm(span=MACD_FAST, adjust=False).mean()
    ema_slow = df["close"].ewm(span=MACD_SLOW, adjust=False).mean()
    df["macd_dif"] = ema_fast - ema_slow
    df["macd_dea"] = df["macd_dif"].ewm(span=MACD_SIGNAL, adjust=False).mean()
    df["macd_hist"] = (df["macd_dif"] - df["macd_dea"]) * 2
    df["macd_cross"] = np.sign(df["macd_dif"] - df["macd_dea"]).diff().fillna(0)
    df["macd_positive"] = (df["macd_hist"] > 0).astype(int)
    df["macd_hist_slope"] = df["macd_hist"] - df["macd_hist"].shift(3)
    return df


# ── 震荡类 ────────────────────────────────────────────────────────────────────

def _add_rsi(df: pd.DataFrame) -> pd.DataFrame:
    delta = df["close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=RSI_PERIOD - 1, adjust=False).mean()
    avg_loss = loss.ewm(com=RSI_PERIOD - 1, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    df["rsi"] = 100 - 100 / (1 + rs)
    df["rsi_oversold"] = (df["rsi"] < 30).astype(int)
    df["rsi_overbought"] = (df["rsi"] > 70).astype(int)
    return df


def _add_kdj(df: pd.DataFrame) -> pd.DataFrame:
    low_min = df["low"].rolling(KDJ_PERIOD).min()
    high_max = df["high"].rolling(KDJ_PERIOD).max()
    rsv = (df["close"] - low_min) / (high_max - low_min + 1e-9) * 100
    df["kdj_k"] = rsv.ewm(com=2, adjust=False).mean()
    df["kdj_d"] = df["kdj_k"].ewm(com=2, adjust=False).mean()
    df["kdj_j"] = 3 * df["kdj_k"] - 2 * df["kdj_d"]
    # +2 金叉，-2 死叉，0 无交叉
    df["kdj_cross"] = np.sign(df["kdj_k"] - df["kdj_d"]).diff().fillna(0)
    return df


# ── 波动类 ────────────────────────────────────────────────────────────────────

def _add_bollinger(df: pd.DataFrame) -> pd.DataFrame:
    mid = df["close"].rolling(BOLL_WINDOW).mean()
    std = df["close"].rolling(BOLL_WINDOW).std()
    upper = mid + BOLL_STD * std
    lower = mid - BOLL_STD * std
    df["boll_upper"] = upper
    df["boll_mid"] = mid
    df["boll_lower"] = lower
    df["boll_width"] = (upper - lower) / (mid + 1e-9)
    df["boll_pct"] = (df["close"] - lower) / (upper - lower + 1e-9)
    df["boll_breakout"] = (df["close"] > upper).astype(int)
    return df


def _add_atr(df: pd.DataFrame) -> pd.DataFrame:
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    df["atr"] = tr.ewm(span=ATR_PERIOD, adjust=False).mean()
    df["atr_ratio"] = df["atr"] / (df["close"] + 1e-9)
    return df


# ── 量价类 ────────────────────────────────────────────────────────────────────

def _add_volume_features(df: pd.DataFrame) -> pd.DataFrame:
    df["vol_ma5"] = df["volume"].rolling(5).mean()
    df["vol_ma20"] = df["volume"].rolling(20).mean()
    df["vol_ratio"] = df["volume"] / (df["vol_ma5"] + 1e-9)
    df["vol_trend"] = df["vol_ma5"] / (df["vol_ma20"] + 1e-9)
    if "turnover" in df.columns:
        df["turnover_ma5"] = df["turnover"].rolling(5).mean()
        df["turnover_ma20"] = df["turnover"].rolling(20).mean()
        df["turnover_ratio"] = df["turnover"] / (df["turnover_ma20"] + 1e-9)
        df["turnover_trend"] = df["turnover_ma5"] / (df["turnover_ma20"] + 1e-9)
    return df


# ── 价格与多周期收益 ──────────────────────────────────────────────────────────

def _add_price_features(df: pd.DataFrame) -> pd.DataFrame:
    df["ret1"] = df["close"].pct_change(1)
    df["ret3"] = df["close"].pct_change(3)
    df["ret5"] = df["close"].pct_change(5)
    df["ret10"] = df["close"].pct_change(10)
    df["ret20"] = df["close"].pct_change(20)
    df["ret60"] = df["close"].pct_change(60)
    df["ret120"] = df["close"].pct_change(120)
    df["volatility5"] = df["ret1"].rolling(5).std()
    df["volatility20"] = df["ret1"].rolling(20).std()
    df["high_low_ratio"] = (df["high"] - df["low"]) / (df["close"] + 1e-9)
    df["open_close_ratio"] = (df["close"] - df["open"]) / (df["open"] + 1e-9)
    df["upper_shadow"] = (df["high"] - df[["open", "close"]].max(axis=1)) / (df["close"] + 1e-9)
    df["lower_shadow"] = (df[["open", "close"]].min(axis=1) - df["low"]) / (df["close"] + 1e-9)
    df["amplitude_ma5"] = df["high_low_ratio"].rolling(5).mean()
    df["log_price"] = np.log(df["close"] + 1e-9)
    return df


# ── 衍生动量（依赖 _add_price_features）────────────────────────────────────────

def _add_derived_momentum(df: pd.DataFrame) -> pd.DataFrame:
    df["momentum_quality"] = df["ret20"] / (df["volatility20"] + 1e-8)
    df["reversal"] = -df["ret3"]
    return df


# ── 量能情绪（A股特色，依赖 price/volume）────────────────────────────────────

def _add_sentiment(df: pd.DataFrame) -> pd.DataFrame:
    # 价量配合：价格方向与成交量异动方向一致为 +1，背离为 -1
    df["price_vol_agree"] = np.sign(df["ret1"]) * np.sign(df["vol_ratio"] - 1)

    # 连续上涨天数（最多统计 10 天）
    up = (df["ret1"] > 0).astype(float).values
    streak = np.zeros(len(up))
    count = 0.0
    for i, v in enumerate(up):
        count = (count + 1.0) * v
        streak[i] = min(count, 10.0)
    df["up_streak"] = streak
    return df


# ── 均线形态（依赖 _add_ma）──────────────────────────────────────────────────

def _add_ma_alignment(df: pd.DataFrame) -> pd.DataFrame:
    # 多头排列得分：0～3，3 为完全多头
    df["bull_score"] = (
        (df["ma5"] > df["ma10"]).astype(int) +
        (df["ma10"] > df["ma20"]).astype(int) +
        (df["ma20"] > df["ma60"]).astype(int)
    )
    return df


# ── 基本面与估值（A股情绪市场的价值锚）────────────────────────────────────

_FUND_HIST_WINDOW = 252   # 252 个交易日 ≈ 1 年自身分位


def _add_fundamental_features(df: pd.DataFrame) -> pd.DataFrame:
    """估值与市值衍生特征。无基本面数据时全部填 NaN，交由预处理填均值。"""
    # 估值水平：log 变换降低极端长尾的影响
    for src, dst in [("pe_ttm", "pe_ttm_log"), ("pb", "pb_log"), ("ps", "ps_log"), ("pcf", "pcf_log")]:
        if src in df.columns:
            df[dst] = np.log(df[src].where(df[src] > 0))
        else:
            df[dst] = np.nan

    # 市值规模因子（小盘股在 A 股有显著的情绪弹性）
    for src, dst in [("market_cap", "market_cap_log"), ("float_market_cap", "float_mc_log")]:
        if src in df.columns:
            df[dst] = np.log(df[src].where(df[src] > 0))
        else:
            df[dst] = np.nan

    # 自身 252 日分位：贵不贵相对于自己的历史
    for src, dst in [("pe_ttm", "pe_ttm_self_pct"), ("pb", "pb_self_pct")]:
        if src in df.columns:
            df[dst] = (
                df[src]
                .rolling(_FUND_HIST_WINDOW, min_periods=60)
                .rank(pct=True)
            )
        else:
            df[dst] = np.nan

    # 流通股本变化（增发、回购、减持的痕迹）
    if "float_shares" in df.columns:
        df["float_shares_chg20"] = df["float_shares"].pct_change(20)
    else:
        df["float_shares_chg20"] = np.nan

    # PEG 直接保留（已经是衍生指标）
    if "peg" in df.columns:
        df["peg_value"] = df["peg"]
    else:
        df["peg_value"] = np.nan

    return df


# ── 资金流向（个股主力 + 北向宏观）──────────────────────────────────────────

_FLOW_HIST_WINDOW = 252  # 自身历史分位窗口（约 1 年）


def _add_fund_flow_features(df: pd.DataFrame) -> pd.DataFrame:
    """资金流向因子。无数据时全部填 NaN，交由预处理填均值。"""
    # --- 个股主力资金 ---
    if "major_net_inflow" in df.columns:
        # log(|净流入|)×sign：压缩量级差异，保留方向性
        df["major_net_log"] = (
            np.sign(df["major_net_inflow"])
            * np.log1p(df["major_net_inflow"].abs())
        )
        # 近5日累计（动量方向）
        df["major_net_5d"] = df["major_net_log"].rolling(5, min_periods=1).sum()
    else:
        df["major_net_log"] = np.nan
        df["major_net_5d"] = np.nan

    if "major_net_pct" in df.columns:
        # 主力净占比（-100%~100%），反映控盘程度
        df["major_net_ratio"] = df["major_net_pct"] / 100.0
        # 自身252日分位：控盘程度相对于自身历史的高低
        df["major_net_self_pct"] = (
            df["major_net_pct"]
            .rolling(_FLOW_HIST_WINDOW, min_periods=60)
            .rank(pct=True)
        )
    else:
        df["major_net_ratio"] = np.nan
        df["major_net_self_pct"] = np.nan

    return df


# ── 标签（占位，由流水线截面步骤统一填充）───────────────────────────────────

def _add_label(df: pd.DataFrame) -> pd.DataFrame:
    """未来 N 日收益率；label 占位为 NaN，由流水线截面步骤统一填充"""
    future_ret = df["close"].shift(-FORWARD_DAYS) / df["close"] - 1
    df["future_ret"] = future_ret
    df["label"] = np.nan
    return df


def get_feature_columns(df: pd.DataFrame) -> list[str]:
    """返回所有特征列名（排除原始 OHLCV、中间量和标签）"""
    exclude = {
        "date", "open", "high", "low", "close", "volume", "amount",
        "turnover", "future_ret", "label", "code",
        # 预处理产生的辅助列
        "segment",
        # 均线原始值（ratio 才是特征）
        "ma5", "ma10", "ma20", "ma60", "ema12", "ema26",
        # 布林带原始值
        "boll_upper", "boll_mid", "boll_lower",
        # 量能中间量
        "vol_ma5", "vol_ma20",
        "turnover_ma5", "turnover_ma20",
        # 基本面原始值（log/self_pct 衍生量才是特征）
        "pe_ttm", "pe_static", "pb", "ps", "pcf", "peg",
        "market_cap", "float_market_cap", "total_shares", "float_shares",
        # 资金流向原始值（衍生特征才入模型）
        "major_net_inflow", "major_net_pct", "north_net_inflow",
    }
    return [c for c in df.columns if c not in exclude]
