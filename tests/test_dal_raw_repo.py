"""测试 RawRepo CRUD（kline、northbound）"""
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


def _kline_df(code: str = "000001", dates: list[str] | None = None) -> pd.DataFrame:
    if dates is None:
        dates = ["2024-01-02", "2024-01-03", "2024-01-04"]
    n = len(dates)
    return pd.DataFrame({
        "date": pd.to_datetime(dates),
        "code": code,
        "open": [10.0] * n,
        "high": [11.0] * n,
        "low":  [9.5]  * n,
        "close": ([10.5, 10.8, 11.0] * ((n // 3) + 1))[:n],
        "amount": [1e8] * n,
        "volume": [1_000_000] * n,
    })


# ── kline ──────────────────────────────────────────────────────────────────────

def test_upsert_kline_returns_row_count(repo):
    df = _kline_df()
    assert repo.upsert_kline(df) == 3


def test_load_kline_returns_inserted_rows(repo):
    repo.upsert_kline(_kline_df())
    result = repo.load_kline("000001")
    assert len(result) == 3
    assert list(result.columns) == ["date", "code", "open", "high", "low", "close", "amount", "volume"]


def test_upsert_kline_deduplication(repo):
    df1 = _kline_df(dates=["2024-01-02"])
    df1["close"] = 10.5
    df2 = _kline_df(dates=["2024-01-02"])
    df2["close"] = 99.0
    repo.upsert_kline(df1)
    repo.upsert_kline(df2)
    result = repo.load_kline("000001")
    assert len(result) == 1
    assert float(result.iloc[0]["close"]) == 99.0


def test_load_kline_since_filter(repo):
    repo.upsert_kline(_kline_df())
    result = repo.load_kline("000001", since=date(2024, 1, 2))
    assert len(result) == 2  # 只返回 2024-01-03、2024-01-04


def test_load_kline_returns_empty_for_unknown_code(repo):
    result = repo.load_kline("999999")
    assert isinstance(result, pd.DataFrame)
    assert len(result) == 0


# ── northbound ────────────────────────────────────────────────────────────────

def _northbound_df() -> pd.DataFrame:
    return pd.DataFrame({
        "date": pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04"]),
        "north_net_inflow": [10.5, -3.2, 7.8],
        "hgt_yi": [5.0, -1.5, 4.0],
        "sgt_yi": [5.5, -1.7, 3.8],
    })


def test_upsert_northbound_returns_row_count(repo):
    assert repo.upsert_northbound(_northbound_df()) == 3


def test_load_northbound_since_filter(repo):
    repo.upsert_northbound(_northbound_df())
    result = repo.load_northbound(since=date(2024, 1, 2))
    assert len(result) == 2  # 返回 2024-01-03、2024-01-04


def test_northbound_deduplication(repo):
    df1 = _northbound_df().iloc[:1].copy()
    df1["north_net_inflow"] = 10.5
    df2 = _northbound_df().iloc[:1].copy()
    df2["north_net_inflow"] = 99.9
    repo.upsert_northbound(df1)
    repo.upsert_northbound(df2)
    result = repo.load_northbound()
    assert len(result) == 1
    assert float(result.iloc[0]["north_net_inflow"]) == 99.9
