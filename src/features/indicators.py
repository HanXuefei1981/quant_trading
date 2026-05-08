"""技术指标特征工程"""
import numpy as np
import pandas as pd

from config.settings import (
    MA_WINDOWS, EMA_WINDOWS, RSI_PERIOD,
    MACD_FAST, MACD_SLOW, MACD_SIGNAL,
    BOLL_WINDOW, BOLL_STD, ATR_PERIOD, KDJ_PERIOD,
    FORWARD_DAYS, UP_THRESHOLD, DOWN_THRESHOLD,
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
    return df


def _add_price_features(df: pd.DataFrame) -> pd.DataFrame:
    df["ret1"] = df["close"].pct_change(1)
    df["ret5"] = df["close"].pct_change(5)
    df["ret10"] = df["close"].pct_change(10)
    df["ret20"] = df["close"].pct_change(20)
    df["volatility5"] = df["ret1"].rolling(5).std()
    df["volatility20"] = df["ret1"].rolling(20).std()
    df["high_low_ratio"] = (df["high"] - df["low"]) / (df["close"] + 1e-9)
    df["open_close_ratio"] = (df["close"] - df["open"]) / (df["open"] + 1e-9)
    df["upper_shadow"] = (df["high"] - df[["open", "close"]].max(axis=1)) / (df["close"] + 1e-9)
    df["lower_shadow"] = (df[["open", "close"]].min(axis=1) - df["low"]) / (df["close"] + 1e-9)
    return df


# ── 标签 ─────────────────────────────────────────────────────────────────────

def _add_label(df: pd.DataFrame) -> pd.DataFrame:
    """未来 N 日收益率 → 三分类标签：1涨 0震荡 -1跌"""
    future_ret = df["close"].shift(-FORWARD_DAYS) / df["close"] - 1
    df["future_ret"] = future_ret
    conditions = [future_ret >= UP_THRESHOLD, future_ret <= DOWN_THRESHOLD]
    choices = [1, -1]
    df["label"] = np.select(conditions, choices, default=0)
    return df


def get_feature_columns(df: pd.DataFrame) -> list[str]:
    """返回所有特征列名（排除原始 OHLCV 和标签）"""
    exclude = {"date", "open", "high", "low", "close", "volume", "amount",
               "turnover", "future_ret", "label",
               "boll_upper", "boll_mid", "boll_lower",
               "ma5", "ma10", "ma20", "ma60", "ema12", "ema26",
               "vol_ma5", "vol_ma20"}
    return [c for c in df.columns if c not in exclude]
