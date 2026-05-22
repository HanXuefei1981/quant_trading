"""测试 RawRepo fundamentals_snapshot CRUD"""
import sys
from datetime import date
from pathlib import Path

import duckdb
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.dal.schema import migrate
from src.dal.raw_repo import RawRepo


@pytest.fixture
def repo():
    conn = duckdb.connect(":memory:")
    migrate(conn)
    yield RawRepo(conn)
    conn.close()


def _snap_df(code: str = "000001", dates: list[str] | None = None) -> pd.DataFrame:
    if dates is None:
        dates = ["2024-01-02", "2024-01-03"]
    n = len(dates)
    return pd.DataFrame({
        "date": pd.to_datetime(dates),
        "code": code,
        "pe_ttm": [15.0] * n,
        "pe_static": [14.0] * n,
        "pb": [1.5] * n,
        "turnover_pct": [2.0] * n,
        "mcap_yi": [500.0] * n,
        "float_mcap_yi": [400.0] * n,
        "price": [10.0] * n,
    })


def test_upsert_returns_row_count(repo):
    assert repo.upsert_fundamentals_snapshot(_snap_df()) == 2


def test_load_returns_inserted_rows(repo):
    repo.upsert_fundamentals_snapshot(_snap_df())
    result = repo.load_fundamentals_snapshot("000001")
    assert len(result) == 2
    assert "pe_ttm" in result.columns


def test_upsert_deduplication(repo):
    df1 = _snap_df(dates=["2024-01-02"])
    df1 = df1.copy()
    df1["price"] = 10.0
    df2 = _snap_df(dates=["2024-01-02"])
    df2 = df2.copy()
    df2["price"] = 99.0
    repo.upsert_fundamentals_snapshot(df1)
    repo.upsert_fundamentals_snapshot(df2)
    result = repo.load_fundamentals_snapshot("000001")
    assert len(result) == 1
    assert float(result.iloc[0]["price"]) == 99.0


def test_load_since_filter(repo):
    repo.upsert_fundamentals_snapshot(_snap_df())
    result = repo.load_fundamentals_snapshot("000001", since=date(2024, 1, 2))
    assert len(result) == 1  # 只返回 2024-01-03


def test_load_empty_for_unknown_code(repo):
    result = repo.load_fundamentals_snapshot("999999")
    assert isinstance(result, pd.DataFrame)
    assert len(result) == 0
