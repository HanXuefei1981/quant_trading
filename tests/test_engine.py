"""测试回测引擎工具函数（engine.py）"""
import numpy as np
import pandas as pd
import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.backtest.engine import _buy_cost, _sell_cost, _is_stock, build_benchmark
from config.settings import COMMISSION_RATE, STAMP_DUTY, SLIPPAGE


# ── 成本函数 ──────────────────────────────────────────────────────────────────

def test_buy_cost_formula():
    amount = 100_000.0
    expected = amount * (COMMISSION_RATE + SLIPPAGE)
    assert _buy_cost(amount) == pytest.approx(expected)


def test_sell_cost_formula():
    amount = 100_000.0
    expected = amount * (COMMISSION_RATE + STAMP_DUTY + SLIPPAGE)
    assert _sell_cost(amount) == pytest.approx(expected)


def test_sell_cost_greater_than_buy_cost():
    amount = 50_000.0
    assert _sell_cost(amount) > _buy_cost(amount)


def test_cost_proportional():
    assert _buy_cost(200_000) == pytest.approx(2 * _buy_cost(100_000))
    assert _sell_cost(200_000) == pytest.approx(2 * _sell_cost(100_000))


def test_zero_amount_zero_cost():
    assert _buy_cost(0) == 0.0
    assert _sell_cost(0) == 0.0


# ── 股票代码过滤 ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("code,expected", [
    ("600519", True),   # 沪市主板（茅台）
    ("688981", True),   # 科创板
    ("000001", True),   # 深市主板
    ("002594", True),   # 深市中小板
    ("300750", True),   # 创业板
    ("830799", True),   # 北交所
    ("399001", False),  # 深证成指（指数）
    ("510300", False),  # ETF
    ("160xxx", False),  # 非个股代码
])
def test_is_stock(code, expected):
    assert _is_stock(code) == expected


# ── build_benchmark ───────────────────────────────────────────────────────────

def make_signal_df(n_days=20, n_stocks=10) -> pd.DataFrame:
    rng = np.random.default_rng(42)
    dates = pd.date_range("2024-01-01", periods=n_days, freq="B")
    codes = [f"60{i:04d}" for i in range(n_stocks)]
    rows = []
    for d in dates:
        for c in codes:
            rows.append({
                "date": d, "code": c,
                "close": 10.0,
                "signal": rng.uniform(-0.1, 0.1),
                "ret1": rng.normal(0, 0.01),
            })
    return pd.DataFrame(rows)


def test_build_benchmark_length():
    df = make_signal_df(n_days=20)
    start = df["date"].min().strftime("%Y-%m-%d")
    end   = df["date"].max().strftime("%Y-%m-%d")
    bench = build_benchmark(df, start, end)
    assert len(bench) == 20


def test_build_benchmark_starts_near_initial():
    from config.settings import INITIAL_CAPITAL
    df = make_signal_df(n_days=20)
    start = df["date"].min().strftime("%Y-%m-%d")
    end   = df["date"].max().strftime("%Y-%m-%d")
    bench = build_benchmark(df, start, end)
    # 第一个值应接近初始资金（当日平均收益乘以初始资金）
    assert bench.iloc[0] == pytest.approx(INITIAL_CAPITAL, rel=0.1)


def test_build_benchmark_is_series():
    df = make_signal_df()
    start = df["date"].min().strftime("%Y-%m-%d")
    end   = df["date"].max().strftime("%Y-%m-%d")
    bench = build_benchmark(df, start, end)
    assert isinstance(bench, pd.Series)
