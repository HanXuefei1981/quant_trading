"""回测引擎连榜（持续性）建仓条件：过滤一日游股票。"""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.backtest.engine import run_backtest


def _make_df():
    """5 只股票，不同板块避免板块上限干扰；300001 在第 6 个交易日（调仓日）单日异动冲榜。"""
    dates = pd.date_range("2024-01-01", periods=12, freq="B")
    base = {"600001": 0.90, "000001": 0.85, "300001": 0.10, "600002": 0.30, "002001": 0.20}
    rows = []
    for i, d in enumerate(dates):
        for c, b in base.items():
            sig = 0.95 if (c == "300001" and i == 5) else b   # 一日游：仅第6日暴涨冲榜
            rows.append({"date": d, "code": c, "close": 10.0, "signal": sig})
    return pd.DataFrame(rows)


def _buys(result):
    t = result["trades"]
    if t.empty:
        return set()
    return set(t[t["side"] == "buy"]["code"])


def test_confirm_streak_filters_one_day_spike():
    df = _make_df()
    common = dict(
        start_date=str(df["date"].min().date()),
        end_date=str(df["date"].max().date()),
        top_k=2, rebalance_every=5,
        max_sector_weight=1.0, max_turnover=1.0, max_stock_weight=1.0,
    )
    buys_off = _buys(run_backtest(df, confirm_streak=0, **common))
    buys_on = _buys(run_backtest(df, confirm_streak=2, **common))

    assert "300001" in buys_off          # 无过滤：一日游股（第6日暴涨）被买入
    assert "300001" not in buys_on       # 连榜≥2：一日游股被过滤掉
    assert "600001" in buys_on           # 持续高信号股仍正常建仓


def test_confirm_streak_default_off_unchanged():
    """confirm_streak=0（默认）行为与不传一致，不破坏既有回测。"""
    df = _make_df()
    common = dict(
        start_date=str(df["date"].min().date()),
        end_date=str(df["date"].max().date()),
        top_k=2, rebalance_every=5,
        max_sector_weight=1.0, max_turnover=1.0, max_stock_weight=1.0,
    )
    a = run_backtest(df, **common)
    b = run_backtest(df, confirm_streak=0, **common)
    assert _buys(a) == _buys(b)
