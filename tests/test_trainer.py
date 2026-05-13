"""测试模型训练工具函数（trainer.py）"""
import numpy as np
import pandas as pd
import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.models.trainer import (
    time_split, prepare_xy, prepare_xy_regression,
    train_ridge, _calc_weights_from_ics,
    LABEL_MAP, LABEL_MAP_INV,
)


def make_mock_df(n_days=100, n_stocks=50, seed=0) -> pd.DataFrame:
    """生成模拟 market_features 数据"""
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2022-01-01", periods=n_days, freq="B")
    codes = [f"{i:06d}" for i in range(n_stocks)]

    rows = []
    for d in dates:
        for c in codes:
            rows.append({
                "date": d, "code": c,
                "feature_a": rng.normal(),
                "feature_b": rng.normal(),
                "future_ret": rng.normal(0, 0.03),
                "label": rng.choice([-1, 0, 1]),
            })
    return pd.DataFrame(rows)


@pytest.fixture
def mock_df():
    return make_mock_df()


def test_time_split_three_sets(mock_df):
    train, val, test = time_split(mock_df)
    assert len(train) > 0
    assert len(val) > 0
    assert len(test) > 0


def test_time_split_no_overlap(mock_df):
    train, val, test = time_split(mock_df)
    assert train["date"].max() < val["date"].min()
    assert val["date"].max() < test["date"].min()


def test_time_split_covers_all_rows(mock_df):
    train, val, test = time_split(mock_df)
    total = len(train) + len(val) + len(test)
    assert total == len(mock_df)


def test_time_split_ratio(mock_df):
    train, val, test = time_split(mock_df)
    n_days = mock_df["date"].nunique()
    train_days = train["date"].nunique()
    # 训练集应占约 70% 的交易日
    assert abs(train_days / n_days - 0.70) < 0.05


def test_prepare_xy_shape(mock_df):
    feature_cols = ["feature_a", "feature_b"]
    X, y = prepare_xy(mock_df, feature_cols)
    assert X.shape == (len(mock_df), 2)
    assert y.shape == (len(mock_df),)


def test_prepare_xy_label_mapping(mock_df):
    feature_cols = ["feature_a", "feature_b"]
    _, y = prepare_xy(mock_df, feature_cols)
    # LightGBM 标签必须为 0/1/2
    assert set(y).issubset({0, 1, 2})


def test_prepare_xy_dtype(mock_df):
    feature_cols = ["feature_a", "feature_b"]
    X, y = prepare_xy(mock_df, feature_cols)
    assert X.dtype == np.float32
    assert y.dtype == np.int32


def test_label_map_invertible():
    for orig, mapped in LABEL_MAP.items():
        assert LABEL_MAP_INV[mapped] == orig


def test_label_map_complete():
    assert set(LABEL_MAP.keys()) == {-1, 0, 1}
    assert set(LABEL_MAP.values()) == {0, 1, 2}


# ── prepare_xy_regression ─────────────────────────────────────────────────────

def test_prepare_xy_regression_shape(mock_df):
    feature_cols = ["feature_a", "feature_b"]
    X, y = prepare_xy_regression(mock_df, feature_cols)
    assert X.shape == (len(mock_df), 2)
    assert y.shape == (len(mock_df),)


def test_prepare_xy_regression_y_range(mock_df):
    feature_cols = ["feature_a", "feature_b"]
    _, y = prepare_xy_regression(mock_df, feature_cols)
    assert float(y.min()) > 0.0
    assert float(y.max()) <= 1.0


def test_prepare_xy_regression_dtype(mock_df):
    feature_cols = ["feature_a", "feature_b"]
    X, y = prepare_xy_regression(mock_df, feature_cols)
    assert X.dtype == np.float32
    assert y.dtype == np.float32


# ── train_ridge ───────────────────────────────────────────────────────────────

def test_train_ridge_returns_model(mock_df):
    from sklearn.linear_model import Ridge
    feature_cols = ["feature_a", "feature_b"]
    X, y = prepare_xy_regression(mock_df, feature_cols)
    model = train_ridge(X, y)
    assert isinstance(model, Ridge)


def test_train_ridge_can_predict(mock_df):
    feature_cols = ["feature_a", "feature_b"]
    X, y = prepare_xy_regression(mock_df, feature_cols)
    model = train_ridge(X, y)
    preds = model.predict(X)
    assert preds.shape == (len(mock_df),)
    assert np.all(np.isfinite(preds))


# ── _calc_weights_from_ics ────────────────────────────────────────────────────

def test_weights_sum_to_one():
    w_l, w_r = _calc_weights_from_ics(0.05, 0.03)
    assert abs(w_l + w_r - 1.0) < 1e-9


def test_negative_ic_gets_zero_weight():
    w_l, w_r = _calc_weights_from_ics(-0.02, 0.04)
    assert w_l == 0.0
    assert abs(w_r - 1.0) < 1e-9


def test_both_negative_ic_equal_weights():
    w_l, w_r = _calc_weights_from_ics(-0.1, -0.2)
    assert abs(w_l - 0.5) < 1e-9
    assert abs(w_r - 0.5) < 1e-9


def test_weights_proportional_to_ic():
    w_l, w_r = _calc_weights_from_ics(0.06, 0.04)
    assert abs(w_l / w_r - 1.5) < 1e-6
