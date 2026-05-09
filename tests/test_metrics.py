"""测试回测绩效指标计算（metrics.py）"""
import numpy as np
import pandas as pd
import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.backtest.metrics import calc_metrics


def make_equity(returns: list, initial=1_000_000) -> pd.Series:
    """由日收益率序列生成净值曲线"""
    arr = np.array([initial] + returns, dtype=float)
    equity = initial * np.cumprod(np.concatenate([[1.0], 1 + np.array(returns)]))
    dates = pd.date_range("2024-01-01", periods=len(equity), freq="B")
    return pd.Series(equity, index=dates)


def test_flat_equity_zero_return():
    equity = make_equity([0.0] * 100)
    m = calc_metrics(equity, 1_000_000)
    assert float(m["总收益率"].strip("%")) == pytest.approx(0.0, abs=0.01)


def test_positive_return():
    # 每日 +0.5%，100天
    equity = make_equity([0.005] * 100)
    m = calc_metrics(equity, 1_000_000)
    total = float(m["总收益率"].strip("%"))
    assert total > 0


def test_negative_return():
    equity = make_equity([-0.005] * 100)
    m = calc_metrics(equity, 1_000_000)
    total = float(m["总收益率"].strip("%"))
    assert total < 0


def test_max_drawdown_is_negative_or_zero():
    equity = make_equity([0.01, -0.02, 0.01, -0.03, 0.02] * 20)
    m = calc_metrics(equity, 1_000_000)
    mdd = float(m["最大回撤"].strip("%"))
    assert mdd <= 0


def test_sharpe_positive_for_good_strategy():
    equity = make_equity([0.003] * 252)  # 每日稳定盈利
    m = calc_metrics(equity, 1_000_000)
    sharpe = float(m["夏普比率"])
    assert sharpe > 0


def test_win_rate_range():
    equity = make_equity([0.01, -0.01] * 50)
    m = calc_metrics(equity, 1_000_000)
    wr = float(m["日胜率"].strip("%"))
    assert 0 <= wr <= 100


def test_calmar_infinite_when_no_drawdown():
    equity = make_equity([0.001] * 50)  # 单调上涨，无回撤
    m = calc_metrics(equity, 1_000_000)
    calmar = m["卡玛比率"]
    assert calmar == "inf" or float(calmar) > 100


def test_output_keys():
    equity = make_equity([0.001] * 50)
    m = calc_metrics(equity, 1_000_000)
    expected_keys = {"总收益率", "年化收益率", "年化波动率", "夏普比率",
                     "最大回撤", "卡玛比率", "日胜率", "交易天数"}
    assert expected_keys == set(m.keys())


def test_trade_days_count():
    n = 60
    equity = make_equity([0.001] * n)
    m = calc_metrics(equity, 1_000_000)
    # equity 长度 n+1，日收益率长度 n，dropna 后仍为 n
    assert m["交易天数"] == n
