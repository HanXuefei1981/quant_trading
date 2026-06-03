"""Phase 3: 回测引擎 —— 基于模型信号的多股票组合策略"""
import logging
from typing import Optional

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge

from config.settings import (
    COMMISSION_RATE, STAMP_DUTY, SLIPPAGE, INITIAL_CAPITAL, EXCLUDE_KCYB
)
from src.backtest.portfolio import build_target_weights, apply_turnover_limit
from src.features.preprocessing import get_segment
from src.features.scan_history import update_streak

logger = logging.getLogger(__name__)


def _is_stock(code: str) -> bool:
    """排除指数、ETF，仅保留 A 股个股代码（科创板视配置决定是否纳入）"""
    if code.startswith("6"):
        if EXCLUDE_KCYB and code.startswith("688"):
            return False            # 科创板需开通权限，默认排除
        return True                 # 沪市主板
    if code.startswith(("000", "001", "002", "003")):
        return True                 # 深市主板
    if code.startswith(("300", "301")):
        return True                 # 创业板
    if code.startswith("8"):
        return True                 # 北交所
    return False


def generate_signals(
    df: pd.DataFrame,
    lgbm_model: lgb.Booster,
    feature_cols: list[str],
    ridge_model: Optional[Ridge] = None,
    w_lgbm: float = 1.0,
    w_ridge: float = 0.0,
    exclude_codes: Optional[set] = None,
) -> pd.DataFrame:
    """对全量数据生成每日每股模型得分。

    当 ridge_model 不为 None 且 w_ridge > 0 时，将两个模型的截面
    Z-score 信号按 IC 权重合并；否则退化为纯 LightGBM 信号。
    exclude_codes: ST 等不可交易股票代码集合，由调用方传入。
    """
    logger.info("生成模型信号...")
    stock_mask = df["code"].apply(_is_stock)
    if exclude_codes:
        stock_mask &= ~df["code"].isin(exclude_codes)
    df_valid = df[stock_mask].copy().reset_index(drop=True)
    if exclude_codes:
        logger.info(f"已过滤 ST 等不可交易股票，排除 {exclude_codes.__len__()} 只")

    X = df_valid[feature_cols].values.astype(np.float32)
    proba = lgbm_model.predict(X)          # shape (n, 3): [P_跌, P_震荡, P_涨]
    lgbm_raw = (proba[:, 2] - proba[:, 0]).astype(np.float64)

    if ridge_model is not None and w_ridge > 0:
        X_ridge = np.where(np.isfinite(X), X, 0.0)
        ridge_raw = ridge_model.predict(X_ridge).astype(np.float64)
        dates = df_valid["date"].values
        lgbm_z = np.zeros(len(df_valid), dtype=np.float64)
        ridge_z = np.zeros(len(df_valid), dtype=np.float64)
        for d in np.unique(dates):
            mask = dates == d
            for raw, out in [(lgbm_raw, lgbm_z), (ridge_raw, ridge_z)]:
                seg = raw[mask]
                out[mask] = (seg - np.mean(seg)) / (np.std(seg) + 1e-8)
        signal = w_lgbm * lgbm_z + w_ridge * ridge_z
    else:
        signal = lgbm_raw

    df_valid["signal"] = signal
    logger.info(f"信号生成完成，共 {len(df_valid):,} 条，"
                f"{df_valid['code'].nunique():,} 只股票")
    return df_valid[["date", "code", "close", "signal", "ret1"]]


def _buy_cost(amount: float) -> float:
    return amount * (COMMISSION_RATE + SLIPPAGE)


def _sell_cost(amount: float) -> float:
    return amount * (COMMISSION_RATE + STAMP_DUTY + SLIPPAGE)


def run_backtest(
    signal_df: pd.DataFrame,
    start_date: str,
    end_date: str,
    top_k: int = 50,
    rebalance_every: int = 5,
    min_signal: float = 0.0,
    signal_weighted: bool = True,
    max_stock_weight: float = 0.05,
    max_sector_weight: float = 0.40,
    max_turnover: float = 0.5,
    replace_only: bool = False,
    confirm_streak: int = 0,
) -> dict:
    """
    回测主函数（每 rebalance_every 个交易日调仓一次）。

    replace_only=False（默认，机构式）：
      按目标权重全量再平衡，减持超配、补仓低配。

    replace_only=True（散户式）：
      持仓股只要仍在 Top-(top_k × buffer) 候选池内就不动；
      只卖出跌出候选池的股票，并用所得现金等额买入候选池中
      当前未持有的最高信号股票。不做存量仓位的权重调整。

    confirm_streak>0（连榜确认）：
      逐交易日维护每只股在 Top-(top_k×1.5) 候选池的连续在榜次数；
      **新建仓**只允许连榜≥confirm_streak 的股票（过滤单日异动"一日游"），
      已持仓股不受影响、按正常退出逻辑处理。冷启动前 confirm_streak-1 个
      调仓日因无票达标可能空仓。
    """
    mask = (signal_df["date"] >= pd.to_datetime(start_date)) & \
           (signal_df["date"] <= pd.to_datetime(end_date))
    df = signal_df[mask].copy()

    dates = sorted(df["date"].unique())
    logger.info(f"回测区间: {dates[0].date()} ~ {dates[-1].date()}，"
                f"共 {len(dates)} 个交易日，调仓间隔 {rebalance_every} 日，持仓 Top-{top_k}")

    # 预先构建板块映射
    segment_map: dict[str, str] = {code: get_segment(code) for code in df["code"].unique()}

    # 用 pivot 加速日内查询
    close_pivot = df.pivot(index="date", columns="code", values="close")
    signal_pivot = df.pivot(index="date", columns="code", values="signal")

    cash = float(INITIAL_CAPITAL)
    holdings: dict[str, float] = {}      # code -> 持仓市值（元）
    equity_list: list[tuple] = []
    trade_log: list[dict] = []

    rebalance_idx = set(range(0, len(dates), rebalance_every))
    buffer_k = int(top_k * 1.5)
    streak: dict[str, int] = {}          # code -> 截至当日连续在候选池次数

    for i, date in enumerate(dates):
        # ── 计算当日组合市值 ─────────────────────────────────────────
        stock_value = sum(holdings.values())
        portfolio_value = cash + stock_value
        equity_list.append((date, portfolio_value))

        # ── 逐日维护连榜（基于当日 Top-buffer 信号成员）──────────────
        if confirm_streak > 0:
            day_sig = signal_pivot.loc[date].dropna()
            pool = set(day_sig.nlargest(buffer_k).index) if not day_sig.empty else set()
            streak = update_streak(streak, pool)

        if i == len(dates) - 1:
            break

        # ── 持仓市值随价格更新 ───────────────────────────────────────
        next_date = dates[i + 1]
        new_holdings: dict[str, float] = {}
        for code, amt in holdings.items():
            if (code in close_pivot.columns
                    and not np.isnan(close_pivot.loc[date, code])
                    and not np.isnan(close_pivot.loc[next_date, code])):
                ratio = close_pivot.loc[next_date, code] / close_pivot.loc[date, code]
                new_holdings[code] = amt * ratio
            else:
                new_holdings[code] = amt   # 停牌保持不动
        holdings = new_holdings

        # ── 是否调仓 ─────────────────────────────────────────────────
        if i not in rebalance_idx:
            continue

        sig_today = signal_pivot.loc[date].dropna()
        if min_signal > 0:
            sig_today = sig_today[sig_today > min_signal]
        if sig_today.empty:
            continue

        current_pv = cash + sum(holdings.values())
        if current_pv <= 0:
            continue

        current_wts = {c: v / current_pv for c, v in holdings.items()}

        if replace_only:
            # ── 散户式：只换人，不再平衡存量 ────────────────────────
            # 候选池：信号排名前 top_k × 1.5，持仓在候选池内就不动
            buffer_k = int(top_k * 1.5)
            candidate_set = set(sig_today.nlargest(buffer_k).index)
            exit_codes = [c for c in holdings if c not in candidate_set]

            # 卖出跌出候选池的股票（全仓卖）
            sell_cash = 0.0
            for code in exit_codes:
                amt = holdings.pop(code)
                cost = _sell_cost(amt)
                sell_cash += amt - cost
                trade_log.append({"date": date, "code": code, "side": "sell",
                                   "amount": amt, "cost": cost})
            cash += sell_cash

            # 用现金等额买入候选池中信号最强、尚未持有的股票
            held = set(holdings.keys())
            n_slots = top_k - len(held)
            if n_slots > 0 and cash > 1.0:
                buyable = sig_today[
                    sig_today.index.isin(candidate_set) & ~sig_today.index.isin(held)
                ]
                if confirm_streak > 0:  # 新建仓须连榜达标，过滤一日游
                    buyable = buyable[[streak.get(c, 0) >= confirm_streak for c in buyable.index]]
                candidates = buyable.nlargest(n_slots)
                per_stock = cash / max(len(candidates), 1) / (1 + COMMISSION_RATE + SLIPPAGE)
                for code in candidates.index:
                    if (code in close_pivot.columns
                            and np.isnan(close_pivot.loc[date, code])):
                        continue
                    buy_amt = min(per_stock, cash / (1 + COMMISSION_RATE + SLIPPAGE))
                    if buy_amt < 1.0:
                        break
                    cost = _buy_cost(buy_amt)
                    cash -= buy_amt + cost
                    holdings[code] = buy_amt
                    trade_log.append({"date": date, "code": code, "side": "buy",
                                       "amount": buy_amt, "cost": cost})
        else:
            # ── 机构式：按目标权重全量再平衡 ────────────────────────
            sig_for_target = sig_today
            if confirm_streak > 0:
                # 候选范围限定为：已持仓 ∪ 连榜达标股；新建仓须连榜确认，持仓不强制卖
                keep = [
                    (streak.get(c, 0) >= confirm_streak) or (c in holdings)
                    for c in sig_today.index
                ]
                sig_for_target = sig_today[keep]
                if sig_for_target.empty:
                    continue
            target_wts = build_target_weights(
                sig_for_target, segment_map,
                top_k=top_k,
                max_stock_weight=max_stock_weight,
                max_sector_weight=max_sector_weight,
                signal_weighted=signal_weighted,
            )
            target_wts = apply_turnover_limit(target_wts, current_wts, max_turnover)
            if not target_wts:
                continue

            target_amounts = {code: w * current_pv for code, w in target_wts.items()}

            # 1. 卖出 / 减仓超出目标的持仓
            sell_cash = 0.0
            for code in list(holdings.keys()):
                tgt = target_amounts.get(code, 0.0)
                cur = holdings[code]
                if cur > tgt + 1.0:
                    reduce_amt = cur - tgt
                    cost = _sell_cost(reduce_amt)
                    sell_cash += reduce_amt - cost
                    trade_log.append({"date": date, "code": code, "side": "sell",
                                       "amount": reduce_amt, "cost": cost})
                    if tgt < 1.0:
                        holdings.pop(code)
                    else:
                        holdings[code] = tgt
            cash += sell_cash

            # 2. 买入 / 增仓不足目标的持仓
            for code, tgt in sorted(target_amounts.items(), key=lambda x: -x[1]):
                cur = holdings.get(code, 0.0)
                if tgt <= cur + 1.0:
                    continue
                if code in close_pivot.columns and np.isnan(close_pivot.loc[date, code]):
                    continue
                buy_need = tgt - cur
                buy_amt = min(buy_need, cash / (1 + COMMISSION_RATE + SLIPPAGE))
                if buy_amt < 1.0:
                    continue
                cost = _buy_cost(buy_amt)
                cash -= buy_amt + cost
                holdings[code] = cur + buy_amt
                trade_log.append({"date": date, "code": code, "side": "buy",
                                   "amount": buy_amt, "cost": cost})
                if cash < 1.0:
                    break

    equity = pd.Series(
        {d: v for d, v in equity_list},
        dtype=float,
    )
    equity.index = pd.to_datetime(equity.index)

    total_ret = equity.iloc[-1] / INITIAL_CAPITAL - 1
    logger.info(f"回测完成 | 最终资产: {equity.iloc[-1]:,.0f} 元 | "
                f"总收益率: {total_ret*100:.2f}%")

    return {
        "equity": equity,
        "trades": pd.DataFrame(trade_log) if trade_log else pd.DataFrame(),
        "initial_capital": INITIAL_CAPITAL,
    }


def build_benchmark(signal_df: pd.DataFrame, start_date: str, end_date: str) -> pd.Series:
    """等权基准：每日等权持有全部有效股票，用 close 价格计算真实日收益"""
    mask = (signal_df["date"] >= pd.to_datetime(start_date)) & \
           (signal_df["date"] <= pd.to_datetime(end_date))
    df = signal_df[mask].copy()
    close_piv = df.pivot(index="date", columns="code", values="close")
    # 日涨跌幅 → 截面均值 → 累积净值
    daily_ret = close_piv.pct_change().mean(axis=1).iloc[1:]
    bench = (1 + daily_ret).cumprod() * INITIAL_CAPITAL
    bench.index = pd.to_datetime(bench.index)
    return bench
