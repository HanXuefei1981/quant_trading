"""信号因子计算：龙虎榜 + 北向资金

北向信号（north_net_5d, north_net_trend）从 indicators.py 迁移而来，逻辑完全不变。
龙虎榜信号（lhb_net_buy_30d, lhb_count_30d）为新增因子。

前视偏差：龙虎榜 available_date = lhb_date + 1 交易日。
窗口：30 交易日（与 report.py 一致）。
"""
import logging
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

_LHB_WINDOW = 30   # 近30交易日龙虎榜滚动窗口


def add_signal_features(
    df: pd.DataFrame,
    code: str,
    lhb_df: Optional[pd.DataFrame],
    north_df: Optional[pd.DataFrame],
) -> pd.DataFrame:
    """左连接信号因子到单股 K 线，返回新 DataFrame（不修改原始）。

    Args:
        df:       单股 K 线 DataFrame，含 date 列
        code:     股票代码，用于从 lhb_df 过滤
        lhb_df:   全市场龙虎榜历史（所有日期合并），含 date, code, lhb_net_buy 等
        north_df: 北向资金历史，含 date, north_net_inflow
    """
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = _add_lhb_features(df, code, lhb_df)
    df = _add_north_features(df, north_df)
    return df


def _add_lhb_features(
    df: pd.DataFrame,
    code: str,
    lhb_df: Optional[pd.DataFrame],
) -> pd.DataFrame:
    if lhb_df is None or lhb_df.empty:
        df["lhb_net_buy_30d"] = np.nan
        df["lhb_count_30d"] = np.nan
        return df

    stock_lhb = lhb_df[lhb_df["code"] == code].copy()
    if stock_lhb.empty:
        df["lhb_net_buy_30d"] = np.nan
        df["lhb_count_30d"] = np.nan
        return df

    stock_lhb["date"] = pd.to_datetime(stock_lhb["date"])
    # +1 calendar day: K 线仅含交易日，周五+1=周六不会命中当日行，但 <= 周一条件成立，行为正确
    stock_lhb["available_date"] = stock_lhb["date"] + pd.Timedelta(days=1)

    dates = df["date"].values
    net_buy_30d = []
    count_30d = []

    for d in dates:
        d_ts = pd.Timestamp(d)
        window_start = pd.bdate_range(end=d_ts, periods=_LHB_WINDOW + 1)[0]
        visible = stock_lhb[
            (stock_lhb["available_date"] <= d_ts)
            & (stock_lhb["available_date"] > window_start)
        ]
        count_30d.append(len(visible))
        net_buy_30d.append(visible["lhb_net_buy"].sum() if not visible.empty else np.nan)

    df["lhb_net_buy_30d"] = net_buy_30d
    df["lhb_count_30d"] = count_30d
    return df


def _add_north_features(
    df: pd.DataFrame,
    north_df: Optional[pd.DataFrame],
) -> pd.DataFrame:
    """北向资金因子（逻辑完全迁移自 indicators._add_fund_flow_features）。"""
    if north_df is not None and not north_df.empty:
        north_slim = north_df[["date", "north_net_inflow"]].copy()
        north_slim["date"] = pd.to_datetime(north_slim["date"])
        df = df.merge(north_slim, on="date", how="left")

    if "north_net_inflow" in df.columns:
        df["north_net_5d"] = df["north_net_inflow"].rolling(5, min_periods=1).sum()
        ma5 = df["north_net_inflow"].rolling(5, min_periods=1).mean()
        ma20 = df["north_net_inflow"].rolling(20, min_periods=5).mean()
        df["north_net_trend"] = ma5 / (ma20.abs() + 1e-6)
    else:
        df["north_net_5d"] = np.nan
        df["north_net_trend"] = np.nan

    return df
