"""测试 FeatureRepo（features 表读写）"""
import sys
from datetime import date
from pathlib import Path

import duckdb
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.dal.schema import migrate
from src.dal.feature_repo import FeatureRepo


@pytest.fixture
def repo():
    conn = duckdb.connect(":memory:")
    migrate(conn)
    yield FeatureRepo(conn)
    conn.close()


def _features_df(n: int = 5, start: str = "2024-01-02") -> pd.DataFrame:
    dates = pd.date_range(start, periods=n, freq="B")
    codes = ["000001"] * n
    return pd.DataFrame({
        "date": dates,
        "code": codes,
        "ma5_ratio": [1.01] * n,
        "rsi14": [55.0] * n,
        "label": [1] * n,
    })


def test_get_feature_date_range_returns_none_when_empty(repo):
    assert repo.get_feature_date_range() is None


def test_upsert_features_returns_row_count(repo):
    df = _features_df(5)
    assert repo.upsert_features(df) == 5


def test_upsert_features_adds_columns_dynamically(repo):
    repo.upsert_features(_features_df())
    cols = {row[0] for row in repo._conn.execute("DESCRIBE features").fetchall()}
    assert "ma5_ratio" in cols
    assert "rsi14" in cols
    assert "label" in cols


def test_load_features_date_range(repo):
    repo.upsert_features(_features_df(10, start="2024-01-02"))
    result = repo.load_features(date(2024, 1, 5), date(2024, 1, 12))
    assert len(result) > 0
    assert result["date"].min() >= pd.Timestamp("2024-01-05")
    assert result["date"].max() <= pd.Timestamp("2024-01-12")


def test_load_features_code_filter(repo):
    df1 = _features_df(3)
    df2 = _features_df(3)
    df2["code"] = "000002"
    repo.upsert_features(pd.concat([df1, df2], ignore_index=True))
    result = repo.load_features(date(2024, 1, 1), date(2025, 1, 1), codes=["000001"])
    assert set(result["code"].unique()) == {"000001"}


def test_upsert_features_deduplication(repo):
    df1 = _features_df(1)
    df1["rsi14"] = 50.0
    df2 = _features_df(1)
    df2["rsi14"] = 80.0
    repo.upsert_features(df1)
    repo.upsert_features(df2)
    result = repo.load_features(date(2024, 1, 1), date(2025, 1, 1))
    assert len(result) == 1
    assert float(result.iloc[0]["rsi14"]) == 80.0


def test_get_feature_date_range_returns_min_max(repo):
    repo.upsert_features(_features_df(5, start="2024-01-02"))
    range_ = repo.get_feature_date_range()
    assert range_ is not None
    d_min, d_max = range_
    assert d_min <= d_max
