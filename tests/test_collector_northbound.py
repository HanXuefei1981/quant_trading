# tests/test_collector_northbound.py
"""测试 NorthboundCollector.collect()"""
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
from src.collectors.northbound_collector import NorthboundCollector


@pytest.fixture
def repos():
    conn = duckdb.connect(":memory:")
    migrate(conn)
    return RawRepo(conn), MetaRepo(conn)


_FAKE_SNAPSHOT = {
    "date": "2024-01-04",
    "north_net_inflow": 1e9,
    "hgt_yi": 5.0,
    "sgt_yi": 5.0,
}


def test_collect_writes_today_row(repos):
    raw_repo, meta_repo = repos
    collector = NorthboundCollector(raw_repo=raw_repo, meta_repo=meta_repo)

    with patch("src.collectors.northbound_collector._fetch_today_snapshot", return_value=_FAKE_SNAPSHOT):
        stats = collector.collect()

    assert stats.ok == 1
    result = raw_repo.load_northbound()
    assert len(result) == 1
    assert result.iloc[0]["north_net_inflow"] == pytest.approx(1e9)
    assert result.iloc[0]["hgt_yi"] == pytest.approx(5.0)


def test_collect_skips_if_already_collected_today(repos):
    raw_repo, meta_repo = repos
    meta_repo.set_last_date("northbound", "__market__", date.today())
    collector = NorthboundCollector(raw_repo=raw_repo, meta_repo=meta_repo)

    with patch("src.collectors.northbound_collector._fetch_today_snapshot") as mock_fetch:
        stats = collector.collect()

    assert stats.cached == 1
    assert not mock_fetch.called


def test_collect_handles_network_failure(repos):
    raw_repo, meta_repo = repos
    collector = NorthboundCollector(raw_repo=raw_repo, meta_repo=meta_repo)

    with patch("src.collectors.northbound_collector._fetch_today_snapshot", return_value=None):
        stats = collector.collect()

    assert stats.fail == 1
    assert meta_repo.get_last_date("northbound", "__market__") is None
