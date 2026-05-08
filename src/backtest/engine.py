"""Phase 3: 回测引擎 —— 基于模型信号的多股票组合策略"""
import logging
from typing import Optional

import lightgbm as lgb
import numpy as np
import pandas as pd

from config.settings import (
    COMMISSION_RATE, STAMP_DUTY, SLIPPAGE, INITIAL_CAPITAL
)

logger = logging.getLogger(__name__)


def _is_stock(code: str) -> bool:
    """排除指数、ETF，仅保留 A 股个股代码"""
    if code.startswith("6"):            # 沪市主板 / 科创板
        return True
    if code.startswith(("000", "001", "002", "003")):   # 深市主板
        return True
    if code.startswith(("300", "301")):  # 创业板
        return True
    if code.startswith("8"):             # 北交所
        return True
    return False


def generate_signals(
    df: pd.DataFrame,
    model: lgb.Booster,
    feature_cols: list[str],
) -> pd.DataFrame:
    """对全量数据生成每日每股模型得分（signal = P_涨 - P_跌）"""
    logger.info("生成模型信号...")
    stock_mask = df["code"].apply(_is_stock)
    df_valid = df[stock_mask].copy().reset_index(drop=True)

    X = df_valid[feature_cols].values.astype(np.float32)
    proba = model.predict(X)          # shape (n, 3): [P_跌, P_震荡, P_涨]
    df_valid["signal"] = proba[:, 2] - proba[:, 0]

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
) -> dict:
    """
    回测主函数（每 rebalance_every 个交易日调仓一次）。

    调仓规则：
      - 卖出信号不在新 Top-K 中的持仓
      - 用回收现金 + 空闲现金等额买入新 Top-K 中的未持股票
      - 已持有且仍在 Top-K 中的股票保持不动（减少换手）
    """
    mask = (signal_df["date"] >= pd.to_datetime(start_date)) & \
           (signal_df["date"] <= pd.to_datetime(end_date))
    df = signal_df[mask].copy()

    dates = sorted(df["date"].unique())
    logger.info(f"回测区间: {dates[0].date()} ~ {dates[-1].date()}，"
                f"共 {len(dates)} 个交易日，调仓间隔 {rebalance_every} 日，持仓 Top-{top_k}")

    # 用 pivot 加速日内查询
    close_pivot = df.pivot(index="date", columns="code", values="close")
    signal_pivot = df.pivot(index="date", columns="code", values="signal")

    cash = float(INITIAL_CAPITAL)
    holdings: dict[str, float] = {}      # code -> 持仓金额（元）
    equity_list: list[tuple] = []
    trade_log: list[dict] = []

    rebalance_idx = set(range(0, len(dates), rebalance_every))

    for i, date in enumerate(dates):
        # ── 计算当日组合市值 ─────────────────────────────────────────
        stock_value = 0.0
        for code, amt in holdings.items():
            if code in close_pivot.columns and not np.isnan(close_pivot.loc[date, code]):
                # 用持仓金额 * 价格变化比率更新市值
                stock_value += amt
        portfolio_value = cash + stock_value
        equity_list.append((date, portfolio_value))

        if i == len(dates) - 1:
            break

        # ── 持仓日收益更新（下一行）──────────────────────────────────
        # 为了日内净值正确，用实际 ret1 更新持仓金额
        next_date = dates[i + 1]
        new_holdings: dict[str, float] = {}
        for code, amt in holdings.items():
            if (code in close_pivot.columns
                    and not np.isnan(close_pivot.loc[date, code])
                    and code in close_pivot.columns
                    and not np.isnan(close_pivot.loc[next_date, code])):
                ratio = close_pivot.loc[next_date, code] / close_pivot.loc[date, code]
                new_holdings[code] = amt * ratio
            else:
                new_holdings[code] = amt   # 停牌保持不动
        holdings = new_holdings

        # ── 是否调仓 ─────────────────────────────────────────────────
        if i not in rebalance_idx:
            continue

        # 选出 Top-K 信号股票
        sig_today = signal_pivot.loc[date].dropna()
        sig_today = sig_today[sig_today > min_signal]
        target_codes = set(sig_today.nlargest(top_k).index)
        current_codes = set(holdings.keys())

        # 1. 卖出不在目标组合的股票
        sell_cash = 0.0
        for code in list(current_codes - target_codes):
            amt = holdings.pop(code)
            cost = _sell_cost(amt)
            net = amt - cost
            sell_cash += net
            trade_log.append({"date": date, "code": code, "side": "sell",
                               "amount": amt, "cost": cost})
        cash += sell_cash

        # 2. 等额分配现金给新增股票
        new_codes = target_codes - set(holdings.keys())
        n_new = len(new_codes)
        if n_new == 0 or cash <= 0:
            continue

        per_stock = cash / n_new                      # 每只股票分配额
        reserve = per_stock * (COMMISSION_RATE + SLIPPAGE) * n_new
        investable = cash - reserve                   # 扣除预估手续费后的可投金额
        per_stock = investable / n_new

        for code in new_codes:
            if (code not in close_pivot.columns
                    or np.isnan(close_pivot.loc[date, code])
                    or close_pivot.loc[date, code] <= 0):
                continue
            buy_amt = per_stock
            cost = _buy_cost(buy_amt)
            cash -= (buy_amt + cost)
            holdings[code] = buy_amt
            trade_log.append({"date": date, "code": code, "side": "buy",
                               "amount": buy_amt, "cost": cost})

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
    """等权基准：每日等权持有全部有效股票"""
    mask = (signal_df["date"] >= pd.to_datetime(start_date)) & \
           (signal_df["date"] <= pd.to_datetime(end_date))
    df = signal_df[mask].copy()
    daily_ret = df.groupby("date")["ret1"].mean()
    bench = (1 + daily_ret).cumprod() * INITIAL_CAPITAL
    bench.index = pd.to_datetime(bench.index)
    return bench
