"""测试 FundamentalCollector.collect()"""
import sys
from datetime import date
from pathlib import Path
from unittest.mock import patch

import duckdb
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.dal.schema import migrate
from src.dal.raw_repo import RawRepo
from src.dal.meta_repo import MetaRepo
from src.collectors.fundamental_collector import FundamentalCollector


@pytest.fixture
def repos():
    conn = duckdb.connect(":memory:")
    migrate(conn)
    return RawRepo(conn), MetaRepo(conn)


def _fake_fundamentals(n: int = 3, code: str = "000001") -> pd.DataFrame:
    """模拟 fetch_daily_basic 的返回（含 code 列，与 fundamentals 表 12 列对齐）。"""
    dates = pd.date_range("2024-01-02", periods=n, freq="B")
    return pd.DataFrame({
        "date": dates,
        "code": code,
        "pe_ttm": [15.0] * n,
        "pe_static": [14.0] * n,
        "pb": [1.5] * n,
        "ps": [2.0] * n,
        "pcf": [5.0] * n,
        "peg": [0.8] * n,
        "market_cap": [5e10] * n,
        "float_market_cap": [4e10] * n,
        "total_shares": [5_000_000_000] * n,
        "float_shares": [4_000_000_000] * n,
    })


def test_collect_first_time_writes_all_rows(repos):
    raw_repo, meta_repo = repos
    collector = FundamentalCollector(raw_repo=raw_repo, meta_repo=meta_repo)

    with patch("src.data.tushare_fetchers.fetch_daily_basic", return_value=_fake_fundamentals(3)):
        stats = collector.collect(codes=["000001"])

    assert stats.ok == 1
    result = raw_repo.load_fundamentals("000001")
    assert len(result) == 3
    assert meta_repo.get_last_date("fundamentals", "000001") == date(2024, 1, 4)


def test_collect_incremental_skips_old_rows(repos):
    raw_repo, meta_repo = repos
    existing = _fake_fundamentals(2).copy()
    existing["code"] = "000001"
    raw_repo.upsert_fundamentals(existing)
    meta_repo.set_last_date("fundamentals", "000001", date(2024, 1, 3))

    collector = FundamentalCollector(raw_repo=raw_repo, meta_repo=meta_repo)
    with patch("src.data.tushare_fetchers.fetch_daily_basic", return_value=_fake_fundamentals(3)):
        stats = collector.collect(codes=["000001"])

    assert stats.ok == 1
    result = raw_repo.load_fundamentals("000001")
    assert len(result) == 3


def test_collect_network_failure_counts_fail(repos):
    raw_repo, meta_repo = repos
    collector = FundamentalCollector(raw_repo=raw_repo, meta_repo=meta_repo)

    with patch("src.data.tushare_fetchers.fetch_daily_basic", return_value=None):
        stats = collector.collect(codes=["000001"])

    assert stats.fail == 1
    assert stats.ok == 0
    assert meta_repo.get_last_date("fundamentals", "000001") is None
