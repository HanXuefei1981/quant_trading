import sys, duckdb
from pathlib import Path
from datetime import date
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.dal.schema import migrate
from src.dal.feature_repo import FeatureRepo
from src.data.pipeline import load_features_from_db


@pytest.fixture
def feature_db():
    conn = duckdb.connect(":memory:")
    migrate(conn)
    repo = FeatureRepo(conn)
    df = pd.DataFrame({
        "date": pd.to_datetime(["2024-01-02", "2024-01-02"]),
        "code": ["000001", "000002"],
        "close": [10.0, 20.0],
        "label": [1, 0],
    })
    repo.upsert_features(df)
    return repo


def test_load_features_from_db_returns_dataframe(feature_db):
    df = load_features_from_db(feature_repo=feature_db)
    assert isinstance(df, pd.DataFrame)
    assert len(df) == 2
    assert "close" in df.columns
    assert "label" in df.columns


def test_load_features_from_db_raises_when_empty():
    conn = duckdb.connect(":memory:")
    migrate(conn)
    empty_repo = FeatureRepo(conn)
    with pytest.raises(FileNotFoundError, match="features 表为空"):
        load_features_from_db(feature_repo=empty_repo)


def test_load_features_from_db_filters_unlabeled_rows():
    """Phase 1 把最近无标签交易日（label=NaN）写入表供 scan；训练加载时须过滤掉。"""
    conn = duckdb.connect(":memory:")
    migrate(conn)
    repo = FeatureRepo(conn)
    df = pd.DataFrame({
        "date": pd.to_datetime(["2024-01-02", "2024-01-02", "2024-01-09", "2024-01-09"]),
        "code": ["000001", "000002", "000001", "000002"],
        "close": [10.0, 20.0, 11.0, 21.0],
        # 2024-01-09 为最近交易日，无标签（label=NaN）
        "label": [1, 0, float("nan"), float("nan")],
    })
    repo.upsert_features(df)

    result = load_features_from_db(feature_repo=repo)

    assert len(result) == 2                                   # 仅保留有标签行
    assert result["label"].notna().all()
    assert pd.to_datetime(result["date"]).dt.date.max() == date(2024, 1, 2)
