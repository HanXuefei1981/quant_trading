# tests/test_collector_tencent.py
"""测试 TencentCollector.collect()"""
import sys
from datetime import date
from pathlib import Path
from unittest.mock import patch

import duckdb
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.dal.schema import migrate
from src.dal.raw_repo import RawRepo
from src.dal.meta_repo import MetaRepo
from src.collectors.tencent_collector import TencentCollector


@pytest.fixture
def repos():
    conn = duckdb.connect(":memory:")
    migrate(conn)
    return RawRepo(conn), MetaRepo(conn)


_FAKE_BATCH = {
    "000001": {
        "pe_ttm": 15.0, "pe_static": 14.0, "pb": 1.5,
        "turnover_pct": 2.0, "mcap_yi": 500.0,
        "float_mcap_yi": 400.0, "price": 10.0,
    }
}


def test_collect_writes_today_snapshot(repos):
    raw_repo, meta_repo = repos
    collector = TencentCollector(raw_repo=raw_repo, meta_repo=meta_repo, delay=0)

    with patch("src.collectors.tencent_collector._fetch_batch", return_value=_FAKE_BATCH):
        stats = collector.collect(codes=["000001"])

    assert stats.ok == 1
    result = raw_repo.load_fundamentals_snapshot("000001")
    assert len(result) == 1
    assert float(result.iloc[0]["pe_ttm"]) == 15.0


def test_collect_skips_if_already_collected_today(repos):
    raw_repo, meta_repo = repos
    meta_repo.set_last_date("fundamentals_snapshot", "000001", date.today())
    collector = TencentCollector(raw_repo=raw_repo, meta_repo=meta_repo, delay=0)

    with patch("src.collectors.tencent_collector._fetch_batch") as mock_fetch:
        stats = collector.collect(codes=["000001"])

    assert stats.cached == 1
    assert not mock_fetch.called


def test_collect_handles_network_failure(repos):
    raw_repo, meta_repo = repos
    collector = TencentCollector(raw_repo=raw_repo, meta_repo=meta_repo, delay=0)

    with patch("src.collectors.tencent_collector._fetch_batch", return_value={}):
        stats = collector.collect(codes=["000001"])

    assert stats.fail == 1
    assert meta_repo.get_last_date("fundamentals_snapshot", "000001") is None
