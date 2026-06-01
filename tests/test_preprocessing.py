"""测试因子预处理（preprocessing.py）"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.features.preprocessing import (
    get_segment,
    _mad_winsorize_block,
    _neutralize_block,
    _zscore_block,
    preprocess_features,
)


# ── 辅助函数 ──────────────────────────────────────────────────────────────────

def make_market_df(n_stocks: int = 50, n_days: int = 10, n_feats: int = 5, seed: int = 0) -> pd.DataFrame:
    """生成带 code / date / feature_* 列的模拟多股票数据"""
    rng = np.random.default_rng(seed)
    codes = [f"{i:06d}" for i in range(n_stocks)]
    # 混入各板块代码
    codes[0] = "600001"    # 沪市主板
    codes[1] = "688001"    # 科创板
    codes[2] = "000001"    # 深市主板
    codes[3] = "300001"    # 创业板
    codes[4] = "830001"    # 北交所

    rows = []
    base = pd.Timestamp("2024-01-01")
    feat_cols = [f"f{i}" for i in range(n_feats)]
    for day in range(n_days):
        date = base + pd.Timedelta(days=day)
        for code in codes:
            row = {"date": date, "code": code,
                   "future_ret": rng.normal(0, 0.03),
                   "label": rng.choice([-1.0, 0.0, 1.0]),
                   "ret1": rng.normal(0, 0.02)}
            for fc in feat_cols:
                row[fc] = rng.normal(0, 1)
            rows.append(row)

    df = pd.DataFrame(rows)
    # 注入极端值
    df.loc[0, "f0"] = 1e6
    df.loc[1, "f0"] = -1e6
    return df


def feat_cols_of(df: pd.DataFrame) -> list[str]:
    return [c for c in df.columns if c.startswith("f") and c != "future_ret"]


# ── get_segment ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("code,expected", [
    ("600519", "沪市主板"),
    ("688981", "科创板"),
    ("000001", "深市主板"),
    ("002594", "深市主板"),
    ("300750", "创业板"),
    ("301010", "创业板"),
    ("830799", "北交所"),
    ("999999", "其他"),
])
def test_get_segment(code, expected):
    assert get_segment(code) == expected


# ── _mad_winsorize_block ──────────────────────────────────────────────────────

def test_winsorize_clips_outliers():
    X = np.array([[0.0, 1.0], [1.0, 2.0], [1000.0, 3.0], [1.0, -1000.0]])
    result = _mad_winsorize_block(X, n_sigma=5.0)
    assert result[2, 0] < 100, "正向极值应被截断"
    assert result[3, 1] > -100, "负向极值应被截断"


def test_winsorize_preserves_normal_values():
    rng = np.random.default_rng(0)
    X = rng.normal(0, 1, (100, 5))
    result = _mad_winsorize_block(X, n_sigma=5.0)
    # 正态分布极少有超过 5σ 的值，绝大多数应保持不变
    assert np.allclose(X, result, atol=1e-6) or (np.abs(X - result) < 0.01).mean() > 0.99


def test_winsorize_output_shape():
    X = np.random.randn(30, 4)
    result = _mad_winsorize_block(X, n_sigma=3.0)
    assert result.shape == X.shape


# ── _zscore_block ─────────────────────────────────────────────────────────────

def test_zscore_zero_mean():
    rng = np.random.default_rng(1)
    X = rng.normal(5, 2, (200, 6))
    result = _zscore_block(X)
    assert np.allclose(np.mean(result, axis=0), 0, atol=1e-6)


def test_zscore_unit_std():
    rng = np.random.default_rng(1)
    X = rng.normal(5, 2, (200, 6))
    result = _zscore_block(X)
    assert np.allclose(np.std(result, axis=0), 1, atol=1e-6)


def test_zscore_output_shape():
    X = np.random.randn(50, 8)
    assert _zscore_block(X).shape == X.shape


# ── _neutralize_block ─────────────────────────────────────────────────────────

def test_neutralize_zero_group_mean():
    X = np.array([[1.0, 2.0], [3.0, 4.0], [10.0, 20.0], [30.0, 40.0]])
    segments = np.array(["A", "A", "B", "B"])
    result = _neutralize_block(X, segments)
    for seg in ["A", "B"]:
        mask = segments == seg
        assert np.allclose(np.mean(result[mask], axis=0), 0, atol=1e-10)


def test_neutralize_output_shape():
    X = np.random.randn(40, 5)
    segs = np.array(["A"] * 20 + ["B"] * 20)
    assert _neutralize_block(X, segs).shape == X.shape


# ── preprocess_features ───────────────────────────────────────────────────────

def test_preprocess_output_shape():
    df = make_market_df()
    fc = feat_cols_of(df)
    result = preprocess_features(df, fc)
    assert result.shape[0] == df.shape[0]


def test_preprocess_adds_segment_column():
    df = make_market_df()
    fc = feat_cols_of(df)
    result = preprocess_features(df, fc)
    assert "segment" in result.columns


def test_preprocess_label_unchanged():
    df = make_market_df()
    fc = feat_cols_of(df)
    result = preprocess_features(df, fc)
    pd.testing.assert_series_equal(df["label"], result["label"])


def test_preprocess_future_ret_unchanged():
    df = make_market_df()
    fc = feat_cols_of(df)
    result = preprocess_features(df, fc)
    pd.testing.assert_series_equal(df["future_ret"], result["future_ret"])


def test_preprocess_no_inf():
    df = make_market_df()
    fc = feat_cols_of(df)
    result = preprocess_features(df, fc)
    assert not result[fc].isin([np.inf, -np.inf]).any().any()


def test_preprocess_cross_sectional_mean_near_zero():
    """Z-score 后每日截面均值应接近 0"""
    df = make_market_df(n_stocks=100, n_days=5)
    fc = feat_cols_of(df)
    result = preprocess_features(df, fc, use_neutralization=False)
    for _, group in result.groupby("date"):
        means = group[fc].mean()
        assert (means.abs() < 0.1).all(), f"截面均值偏离 0: {means.to_dict()}"


def test_preprocess_input_not_mutated():
    df = make_market_df()
    fc = feat_cols_of(df)
    original_f0 = df["f0"].copy()
    preprocess_features(df, fc)
    pd.testing.assert_series_equal(df["f0"], original_f0)


def test_preprocess_without_neutralization():
    df = make_market_df()
    fc = feat_cols_of(df)
    result = preprocess_features(df, fc, use_neutralization=False)
    assert result.shape[0] == df.shape[0]
    assert not result[fc].isin([np.inf, -np.inf]).any().any()


def test_preprocess_handles_nullable_extension_dtype():
    """特征列为 pandas nullable 扩展类型（Int64/Float64，缺失为 pd.NA）时，
    preprocess_features 不得在 .astype(np.float64) 处因 float(pd.NA) 崩溃。
    这是 DuckDB 读取含 NULL 的 BIGINT 列时的真实场景，防止整条流水线末端报错。
    """
    df = make_market_df(n_stocks=30, n_days=4)
    fc = feat_cols_of(df)
    # 将一个特征列转成 nullable Int64 并注入 pd.NA
    df["f0"] = pd.array(
        [pd.NA if i % 7 == 0 else int(round(v)) for i, v in enumerate(df["f0"])],
        dtype="Int64",
    )

    result = preprocess_features(df, fc)  # 不应抛 TypeError

    assert result.shape[0] == df.shape[0]
    assert not result[fc].isin([np.inf, -np.inf]).any().any()
