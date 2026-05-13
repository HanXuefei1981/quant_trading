"""测试截面分位标签（label.py）"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.features.label import add_cross_sectional_label


def make_multi_stock_df(n_stocks: int = 10, n_days: int = 20) -> pd.DataFrame:
    rows = []
    base = pd.Timestamp("2024-01-01")
    rng = np.random.default_rng(0)
    for day in range(n_days):
        date = base + pd.Timedelta(days=day)
        rets = rng.normal(0, 0.03, n_stocks)
        for i, ret in enumerate(rets):
            rows.append({"date": date, "code": f"{i:06d}", "future_ret": ret, "ret1": 0.01})
    return pd.DataFrame(rows)


def test_label_values_in_valid_set():
    df = make_multi_stock_df()
    result = add_cross_sectional_label(df)
    valid = result["label"].dropna()
    assert set(valid.unique()).issubset({-1.0, 0.0, 1.0})


def test_label_distribution_balanced():
    """每日截面内正负样本比例应接近 top_pct / bottom_pct"""
    df = make_multi_stock_df(n_stocks=100, n_days=10)
    result = add_cross_sectional_label(df, top_pct=0.3, bottom_pct=0.3)
    for _, group in result.groupby("date"):
        valid = group["label"].dropna()
        assert abs((valid == 1).mean() - 0.3) < 0.05
        assert abs((valid == -1).mean() - 0.3) < 0.05


def test_top_stocks_labeled_positive():
    """截面最高 future_ret 的股票应获得 label=1"""
    df = make_multi_stock_df(n_stocks=10, n_days=5)
    result = add_cross_sectional_label(df, top_pct=0.3)
    for date, group in result.groupby("date"):
        top_idx = group.nlargest(3, "future_ret").index
        assert (result.loc[top_idx, "label"] == 1.0).all()


def test_bottom_stocks_labeled_negative():
    """截面最低 future_ret 的股票应获得 label=-1"""
    df = make_multi_stock_df(n_stocks=10, n_days=5)
    result = add_cross_sectional_label(df, bottom_pct=0.3)
    for date, group in result.groupby("date"):
        bottom_idx = group.nsmallest(3, "future_ret").index
        assert (result.loc[bottom_idx, "label"] == -1.0).all()


def test_nan_future_ret_produces_nan_label():
    df = make_multi_stock_df(n_stocks=10, n_days=5)
    df.loc[df.index[0], "future_ret"] = np.nan
    result = add_cross_sectional_label(df)
    assert np.isnan(result.loc[df.index[0], "label"])


def test_input_not_mutated():
    """原始 DataFrame 不应被修改"""
    df = make_multi_stock_df()
    original = df.copy()
    add_cross_sectional_label(df)
    pd.testing.assert_frame_equal(df, original)


def test_returns_dataframe():
    df = make_multi_stock_df()
    result = add_cross_sectional_label(df)
    assert isinstance(result, pd.DataFrame)
    assert "label" in result.columns


def test_label_column_count_matches_input():
    df = make_multi_stock_df()
    result = add_cross_sectional_label(df)
    assert len(result) == len(df)
