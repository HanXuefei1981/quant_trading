"""测试 TDXCollector.collect()"""
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
from src.collectors.tdx_collector import TDXCollector


@pytest.fixture
def repos():
    conn = duckdb.connect(":memory:")
    migrate(conn)
    return RawRepo(conn), MetaRepo(conn)


def _fake_kline(n: int = 3) -> pd.DataFrame:
    dates = pd.date_range("2024-01-02", periods=n, freq="B")
    return pd.DataFrame({
        "date": dates,
        "open": [10.0] * n,
        "high": [11.0] * n,
        "low": [9.5] * n,
        "close": [10.5] * n,
        "amount": [1e8] * n,
        "volume": [1_000_000] * n,
    })


def test_collect_first_time_writes_all_rows(repos):
    raw_repo, meta_repo = repos
    collector = TDXCollector(raw_repo=raw_repo, meta_repo=meta_repo)

    with patch("src.data.tdx_reader.read_day_file", return_value=_fake_kline(3)):
        stats = collector.collect(codes=["000001"])

    assert stats.ok == 1
    assert stats.fail == 0
    result = raw_repo.load_kline("000001")
    assert len(result) == 3
    assert meta_repo.get_last_date("kline", "000001") == date(2024, 1, 4)


def test_collect_incremental_skips_old_rows(repos):
    raw_repo, meta_repo = repos
    existing = _fake_kline(2).assign(code="000001")
    raw_repo.upsert_kline(existing)
    meta_repo.set_last_date("kline", "000001", date(2024, 1, 3))

    collector = TDXCollector(raw_repo=raw_repo, meta_repo=meta_repo)
    with patch("src.data.tdx_reader.read_day_file", return_value=_fake_kline(3)):
        stats = collector.collect(codes=["000001"])

    assert stats.ok == 1
    result = raw_repo.load_kline("000001")
    assert len(result) == 3
    assert meta_repo.get_last_date("kline", "000001") == date(2024, 1, 4)


def test_collect_handles_read_failure(repos):
    raw_repo, meta_repo = repos
    collector = TDXCollector(raw_repo=raw_repo, meta_repo=meta_repo)

    with patch("src.data.tdx_reader.read_day_file", return_value=None):
        stats = collector.collect(codes=["000001"])

    assert stats.skipped == 1
    assert stats.ok == 0
    assert meta_repo.get_last_date("kline", "000001") is None
