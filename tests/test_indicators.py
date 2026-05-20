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
        # 均线
        "ma5_ratio", "ma10_ratio", "ma20_ratio", "ma60_ratio",
        # MACD（含新增）
        "macd_dif", "macd_dea", "macd_hist", "macd_cross",
        "macd_positive", "macd_hist_slope",
        # RSI
        "rsi", "rsi_oversold", "rsi_overbought",
        # KDJ（含新增）
        "kdj_k", "kdj_d", "kdj_j", "kdj_cross",
        # 布林（含新增）
        "boll_width", "boll_pct", "boll_breakout",
        # ATR
        "atr", "atr_ratio",
        # 量能
        "vol_ratio", "vol_trend",
        # 多周期收益（含新增）
        "ret1", "ret3", "ret5", "ret10", "ret20", "ret60", "ret120",
        # 波动率
        "volatility5", "volatility20",
        # 价格结构（含新增）
        "high_low_ratio", "open_close_ratio", "upper_shadow", "lower_shadow",
        "amplitude_ma5", "log_price",
        # 衍生动量（新增）
        "momentum_quality", "reversal",
        # 量能情绪（新增）
        "price_vol_agree", "up_streak",
        # 均线形态（新增）
        "bull_score",
        # 目标列
        "future_ret", "label",
    ]
    for col in expected:
        assert col in df_with_features.columns, f"缺少列: {col}"


def test_get_feature_columns_count(df_with_features):
    feats = get_feature_columns(df_with_features)
    assert len(feats) == 58, f"预期58个因子，实际 {len(feats)}: {feats}"


def test_get_feature_columns_no_leakage(df_with_features):
    feats = get_feature_columns(df_with_features)
    forbidden = {"future_ret", "label", "open", "high", "low", "close", "volume"}
    overlap = set(feats) & forbidden
    assert not overlap, f"特征列含目标/原始价格列: {overlap}"


def test_rsi_range(df_with_features):
    rsi = df_with_features["rsi"].dropna()
    assert (rsi >= 0).all() and (rsi <= 100).all()


def test_future_ret_computed(df_with_features):
    # label 占位为 NaN（由流水线截面步骤填充），future_ret 本身应有有效值
    valid = df_with_features["future_ret"].dropna()
    assert len(valid) > 0
    assert np.isfinite(valid).all()


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


def test_bull_score_range(df_with_features):
    score = df_with_features["bull_score"].dropna()
    assert score.isin([0, 1, 2, 3]).all(), "bull_score 取值应在 0~3"


def test_up_streak_range(df_with_features):
    streak = df_with_features["up_streak"]
    assert (streak >= 0).all() and (streak <= 10).all()


def test_up_streak_resets_on_down_day(df_with_features):
    streak = df_with_features["up_streak"].values
    ret1 = df_with_features["ret1"].values
    for i in range(1, len(streak)):
        if not np.isnan(ret1[i]) and ret1[i] <= 0:
            assert streak[i] == 0, f"下跌日 streak 应归零，第 {i} 行"


def test_macd_positive_binary(df_with_features):
    mp = df_with_features["macd_positive"]
    assert set(mp.unique()).issubset({0, 1})


def test_kdj_cross_values(df_with_features):
    cross = df_with_features["kdj_cross"].dropna()
    assert set(cross.unique()).issubset({-2.0, -1.0, 0.0, 1.0, 2.0})


def test_boll_breakout_binary(df_with_features):
    bb = df_with_features["boll_breakout"]
    assert set(bb.unique()).issubset({0, 1})


def test_ret_multi_period_exists(df_with_features):
    for period in [3, 60, 120]:
        col = f"ret{period}"
        valid = df_with_features[col].dropna()
        assert len(valid) > 0, f"{col} 无有效值"


def test_reversal_is_neg_ret3(df_with_features):
    diff = (df_with_features["reversal"] + df_with_features["ret3"]).dropna()
    assert (diff.abs() < 1e-10).all(), "reversal 应等于 -ret3"
