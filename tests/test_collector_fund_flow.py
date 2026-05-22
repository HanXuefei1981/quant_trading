"""测试 FundFlowCollector.collect()"""
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
from src.collectors.fund_flow_collector import FundFlowCollector


@pytest.fixture
def repos():
    conn = duckdb.connect(":memory:")
    migrate(conn)
    return RawRepo(conn), MetaRepo(conn)


def _fake_fund_flow(n: int = 3) -> pd.DataFrame:
    dates = pd.date_range("2024-01-02", periods=n, freq="B")
    return pd.DataFrame({
        "date": dates,
        "major_net_inflow": ([1e7, 2e7, 3e7] * ((n // 3) + 1))[:n],
        "major_net_pct": ([1.0, 2.0, 3.0] * ((n // 3) + 1))[:n],
    })


def test_collect_first_time_writes_all_rows(repos):
    raw_repo, meta_repo = repos
    collector = FundFlowCollector(raw_repo=raw_repo, meta_repo=meta_repo, delay=0)

    with patch("src.data.fund_flow.fetch_fund_flow", return_value=_fake_fund_flow(3)):
        stats = collector.collect(codes=["000001"])

    assert stats.ok == 1
    result = raw_repo.load_fund_flow("000001")
    assert len(result) == 3
    assert meta_repo.get_last_date("fund_flow", "000001") == date(2024, 1, 4)


def test_collect_incremental_skips_old_rows(repos):
    raw_repo, meta_repo = repos
    existing = _fake_fund_flow(2).copy()
    existing["code"] = "000001"
    raw_repo.upsert_fund_flow(existing)
    meta_repo.set_last_date("fund_flow", "000001", date(2024, 1, 3))

    collector = FundFlowCollector(raw_repo=raw_repo, meta_repo=meta_repo, delay=0)
    with patch("src.data.fund_flow.fetch_fund_flow", return_value=_fake_fund_flow(3)):
        stats = collector.collect(codes=["000001"])

    assert stats.ok == 1
    result = raw_repo.load_fund_flow("000001")
    assert len(result) == 3


def test_collect_network_failure_counts_fail(repos):
    raw_repo, meta_repo = repos
    collector = FundFlowCollector(raw_repo=raw_repo, meta_repo=meta_repo, delay=0)

    with patch("src.data.fund_flow.fetch_fund_flow", return_value=None):
        stats = collector.collect(codes=["000001"])

    assert stats.fail == 1
    assert meta_repo.get_last_date("fund_flow", "000001") is None
