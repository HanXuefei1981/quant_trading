"""测试技术因子计算（indicators.py）"""
import numpy as np
import pandas as pd
import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.features.indicators import add_all_features, get_feature_columns


def make_ohlcv(n=300, seed=42) -> pd.DataFrame:
    """生成模拟日线数据"""
    rng = np.random.default_rng(seed)
    close = 10.0 * np.cumprod(1 + rng.normal(0, 0.015, n))
    high  = close * (1 + rng.uniform(0, 0.03, n))
    low   = close * (1 - rng.uniform(0, 0.03, n))
    open_ = close * (1 + rng.normal(0, 0.01, n))
    volume = rng.integers(1_000_000, 10_000_000, n).astype(float)

    dates = pd.date_range("2023-01-01", periods=n, freq="B")
    return pd.DataFrame({
        "date": dates,
        "open": open_, "high": high, "low": low,
        "close": close, "volume": volume,
    })


@pytest.fixture
def df_with_features():
    df = make_ohlcv()
    return add_all_features(df)


def test_add_all_features_returns_dataframe(df_with_features):
    assert isinstance(df_with_features, pd.DataFrame)
    assert len(df_with_features) == 300


def test_feature_columns_exist(df_with_features):
    expected = [
        "ma5_ratio", "ma10_ratio", "ma20_ratio", "ma60_ratio",
        "macd_dif", "macd_dea", "macd_hist", "macd_cross",
        "rsi", "rsi_oversold", "rsi_overbought",
        "kdj_k", "kdj_d", "kdj_j",
        "boll_width", "boll_pct",
        "atr", "atr_ratio",
        "vol_ratio", "vol_trend",
        "ret1", "ret5", "ret10", "ret20",
        "volatility5", "volatility20",
        "high_low_ratio", "open_close_ratio",
        "upper_shadow", "lower_shadow",
        "future_ret", "label",
    ]
    for col in expected:
        assert col in df_with_features.columns, f"缺少列: {col}"


def test_get_feature_columns_count(df_with_features):
    feats = get_feature_columns(df_with_features)
    assert len(feats) == 30, f"预期30个因子，实际 {len(feats)}"


def test_get_feature_columns_no_leakage(df_with_features):
    feats = get_feature_columns(df_with_features)
    forbidden = {"future_ret", "label", "open", "high", "low", "close", "volume"}
    overlap = set(feats) & forbidden
    assert not overlap, f"特征列含目标/原始价格列: {overlap}"


def test_rsi_range(df_with_features):
    rsi = df_with_features["rsi"].dropna()
    assert (rsi >= 0).all() and (rsi <= 100).all()


def test_label_values(df_with_features):
    valid = df_with_features["label"].dropna()
    assert set(valid.unique()).issubset({-1, 0, 1})


def test_ma_ratio_near_zero_at_ma(df_with_features):
    # 当 close 约等于均线时，ratio 应接近 0
    ratio = df_with_features["ma5_ratio"].dropna()
    assert ratio.abs().median() < 0.5


def test_boll_pct_range(df_with_features):
    # 绝大多数样本应在 [-0.5, 1.5] 之间
    pct = df_with_features["boll_pct"].dropna()
    assert (pct.between(-0.5, 1.5)).mean() > 0.95


def test_vol_ratio_positive(df_with_features):
    vr = df_with_features["vol_ratio"].dropna()
    assert (vr > 0).all()


def test_no_inf_values(df_with_features):
    feats = get_feature_columns(df_with_features)
    has_inf = df_with_features[feats].isin([np.inf, -np.inf]).any().any()
    assert not has_inf, "特征列中存在 inf 值"
