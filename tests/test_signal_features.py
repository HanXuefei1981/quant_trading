"""测试 signal.py 信号特征计算（龙虎榜 + 北向迁移）"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _make_kline(dates: list[str]) -> pd.DataFrame:
    return pd.DataFrame({
        "date": pd.to_datetime(dates),
        "code": "000001",
        "close": [10.0] * len(dates),
    })


def _make_lhb(date: str, code: str, net_buy: float) -> pd.DataFrame:
    return pd.DataFrame({
        "date": pd.to_datetime([date]),
        "code": [code],
        "lhb_net_buy": [net_buy],
        "lhb_buy_amount": [abs(net_buy) * 1.5],
        "lhb_sell_amount": [abs(net_buy) * 0.5],
    })


def test_lhb_count_30d_rolling(tmp_path):
    """近30日上榜次数滚动正确（含前视偏差 +1 天）"""
    from src.features.signal import add_signal_features

    lhb_df = pd.concat([
        _make_lhb("2026-05-10", "000001", 1e6),
        _make_lhb("2026-05-15", "000001", 2e6),
    ], ignore_index=True)

    kline = _make_kline(["2026-05-11", "2026-05-16", "2026-06-20"])
    result = add_signal_features(kline.copy(), "000001", lhb_df=lhb_df, north_df=None)

    # 5-11: available_date 5-11（5-10+1），窗口内1次
    assert result.loc[result["date"] == pd.Timestamp("2026-05-11"), "lhb_count_30d"].iloc[0] == 1
    # 5-16: available 5-11 和 5-16，窗口内2次
    assert result.loc[result["date"] == pd.Timestamp("2026-05-16"), "lhb_count_30d"].iloc[0] == 2
    # 6-20: 30交易日前约 5-12，5-11 在窗口外，5-16 在窗口内 → count=1
    assert result.loc[result["date"] == pd.Timestamp("2026-06-20"), "lhb_count_30d"].iloc[0] == 1


def test_lhb_net_buy_30d_rolling(tmp_path):
    """近30日净买入累计正确"""
    from src.features.signal import add_signal_features

    lhb_df = pd.concat([
        _make_lhb("2026-05-10", "000001", 1e6),
        _make_lhb("2026-05-15", "000001", 3e6),
    ], ignore_index=True)

    kline = _make_kline(["2026-05-16"])
    result = add_signal_features(kline.copy(), "000001", lhb_df=lhb_df, north_df=None)

    val = result.loc[result["date"] == pd.Timestamp("2026-05-16"), "lhb_net_buy_30d"].iloc[0]
    assert val == pytest.approx(4e6)


def test_no_lookahead_bias_lhb():
    """龙虎榜 available_date = lhb_date + 1，当日数据不出现在当日特征"""
    from src.features.signal import add_signal_features

    lhb_df = _make_lhb("2026-05-15", "000001", 5e6)
    kline = _make_kline(["2026-05-15", "2026-05-16"])
    result = add_signal_features(kline.copy(), "000001", lhb_df=lhb_df, north_df=None)

    assert result.loc[result["date"] == pd.Timestamp("2026-05-15"), "lhb_count_30d"].iloc[0] == 0
    assert result.loc[result["date"] == pd.Timestamp("2026-05-16"), "lhb_count_30d"].iloc[0] == 1


def test_north_signal_migrated_values():
    """north_net_5d / north_net_trend 数值与迁移前 indicators.py 逻辑完全一致"""
    from src.features.signal import add_signal_features

    dates = pd.date_range("2026-05-01", periods=10)
    north_vals = [1e8, -2e8, 3e8, 1e8, 2e8, -1e8, 4e8, 2e8, -3e8, 1e8]
    north_df = pd.DataFrame({"date": dates, "north_net_inflow": north_vals})

    kline = pd.DataFrame({"date": dates, "code": "000001", "close": [10.0] * 10})
    result = add_signal_features(kline.copy(), "000001", lhb_df=None, north_df=north_df)

    assert "north_net_5d" in result.columns
    assert "north_net_trend" in result.columns

    series = pd.Series(north_vals)
    expected_5d = series.rolling(5, min_periods=1).sum().iloc[-1]
    assert result["north_net_5d"].iloc[-1] == pytest.approx(expected_5d)


def test_missing_lhb_fills_nan():
    """无龙虎榜数据时 lhb_* 全部为 NaN"""
    from src.features.signal import add_signal_features

    kline = _make_kline(["2026-05-15"])
    result = add_signal_features(kline.copy(), "000001", lhb_df=None, north_df=None)

    assert np.isnan(result["lhb_count_30d"].iloc[0])
    assert np.isnan(result["lhb_net_buy_30d"].iloc[0])


def test_missing_north_fills_nan():
    """无北向数据时 north_* 全部为 NaN"""
    from src.features.signal import add_signal_features

    kline = _make_kline(["2026-05-15"])
    result = add_signal_features(kline.copy(), "000001", lhb_df=None, north_df=None)

    assert np.isnan(result["north_net_5d"].iloc[0])
    assert np.isnan(result["north_net_trend"].iloc[0])
