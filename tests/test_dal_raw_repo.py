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


# ── fundamentals ──────────────────────────────────────────────────────────────

def _fundamentals_df(code: str = "000001") -> pd.DataFrame:
    return pd.DataFrame({
        "date": pd.to_datetime(["2024-01-02", "2024-01-03"]),
        "code": code,
        "pe_ttm": [12.5, 12.8],
        "pe_static": [13.0, 13.2],
        "pb": [1.2, 1.25],
        "ps": [2.0, 2.1],
        "pcf": [8.0, 8.2],
        "peg": [0.9, 0.92],
        "market_cap": [2e11, 2.1e11],
        "float_market_cap": [1.9e11, 2.0e11],
        "total_shares": [int(1.7e10)] * 2,
        "float_shares": [int(1.65e10)] * 2,
    })


def test_upsert_and_load_fundamentals(repo):
    repo.upsert_fundamentals(_fundamentals_df())
    result = repo.load_fundamentals("000001")
    assert len(result) == 2
    assert "pe_ttm" in result.columns


def test_fundamentals_deduplication(repo):
    df1 = _fundamentals_df().iloc[:1].copy()
    df1["pe_ttm"] = 10.0
    df2 = _fundamentals_df().iloc[:1].copy()
    df2["pe_ttm"] = 20.0
    repo.upsert_fundamentals(df1)
    repo.upsert_fundamentals(df2)
    assert float(repo.load_fundamentals("000001").iloc[0]["pe_ttm"]) == 20.0


# ── fund_flow ─────────────────────────────────────────────────────────────────

def _fund_flow_df(code: str = "000001") -> pd.DataFrame:
    return pd.DataFrame({
        "date": pd.to_datetime(["2024-01-02", "2024-01-03"]),
        "code": code,
        "major_net_inflow": [1e7, -5e6],
        "major_net_pct": [2.5, -1.3],
    })


def test_upsert_and_load_fund_flow(repo):
    repo.upsert_fund_flow(_fund_flow_df())
    result = repo.load_fund_flow("000001")
    assert len(result) == 2
    assert "major_net_inflow" in result.columns


# ── lhb ───────────────────────────────────────────────────────────────────────

def _lhb_df() -> pd.DataFrame:
    return pd.DataFrame({
        "date": pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-03"]),
        "code": ["000001", "000001", "000002"],
        "lhb_net_buy": [1e7, -3e6, 5e6],
        "lhb_buy_amount": [2e7, 1e7, 8e6],
        "lhb_sell_amount": [1e7, 1.3e7, 3e6],
    })


def test_upsert_and_load_lhb(repo):
    repo.upsert_lhb(_lhb_df())
    result = repo.load_lhb("000001")
    assert len(result) == 2


def test_load_lhb_since_filter(repo):
    repo.upsert_lhb(_lhb_df())
    result = repo.load_lhb("000001", since=date(2024, 1, 2))
    assert len(result) == 1  # 只返回 2024-01-03


# ── reports ───────────────────────────────────────────────────────────────────

def _reports_df(code: str = "000001") -> pd.DataFrame:
    return pd.DataFrame({
        "date": pd.to_datetime(["2024-01-02", "2024-01-02", "2024-01-03"]),
        "code": code,
        "institution": ["机构A", "机构B", "机构A"],
        "rating": ["买入", "增持", "买入"],
    })


def test_upsert_and_load_reports(repo):
    repo.upsert_reports(_reports_df())
    result = repo.load_reports("000001")
    assert len(result) == 3


def test_reports_three_column_pk_dedup(repo):
    df1 = _reports_df().iloc[:1].copy()
    df1["rating"] = "买入"
    df2 = _reports_df().iloc[:1].copy()
    df2["rating"] = "卖出"
    repo.upsert_reports(df1)
    repo.upsert_reports(df2)
    result = repo.load_reports("000001")
    assert len(result) == 1
    assert result.iloc[0]["rating"] == "卖出"


# ── eps_snapshot ──────────────────────────────────────────────────────────────

def _eps_df(code: str = "000001") -> pd.DataFrame:
    return pd.DataFrame({
        "snapshot_date": pd.to_datetime(["2024-04-30", "2024-05-31"]),
        "code": code,
        "eps_cur": [2.10, 2.17],
        "eps_next": [2.20, 2.24],
        "analyst_count": [18, 20],
    })


def test_upsert_and_load_eps_snapshots(repo):
    repo.upsert_eps_snapshot(_eps_df())
    result = repo.load_eps_snapshots("000001")
    assert len(result) == 2
    assert "eps_cur" in result.columns


def test_eps_snapshot_deduplication(repo):
    df1 = _eps_df().iloc[:1].copy()
    df1["eps_cur"] = 2.00
    df2 = _eps_df().iloc[:1].copy()
    df2["eps_cur"] = 2.10
    repo.upsert_eps_snapshot(df1)
    repo.upsert_eps_snapshot(df2)
    result = repo.load_eps_snapshots("000001")
    assert len(result) == 1
    assert float(result.iloc[0]["eps_cur"]) == 2.10
