# tests/test_collector_report.py
"""测试 ReportCollector.collect()"""
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
from src.collectors.report_collector import ReportCollector


@pytest.fixture
def repos():
    conn = duckdb.connect(":memory:")
    migrate(conn)
    return RawRepo(conn), MetaRepo(conn)


def _fake_reports_raw() -> pd.DataFrame:
    """模拟 fetch_report_rc 的输出列：date, code, institution, rating。"""
    return pd.DataFrame({
        "date": pd.to_datetime(["2024-01-02", "2024-01-03"]),
        "code": "000001",
        "institution": ["华泰证券", "中信证券"],
        "rating": ["买入", "增持"],
    })


def _fake_eps_raw() -> pd.DataFrame:
    return pd.DataFrame({
        "年度": ["2024", "2025"],
        "均值": [1.5, 2.0],
        "预测机构数": [10, 10],
    })


def test_collect_report_writes_rows(repos):
    raw_repo, meta_repo = repos
    collector = ReportCollector(raw_repo=raw_repo, meta_repo=meta_repo, mode="report", delay=0)

    with patch("src.data.tushare_fetchers.fetch_report_rc", return_value=_fake_reports_raw()):
        stats = collector.collect(codes=["000001"])

    assert stats.ok == 1
    result = raw_repo.load_reports("000001")
    assert len(result) == 2


def test_collect_eps_writes_snapshot(repos):
    raw_repo, meta_repo = repos
    collector = ReportCollector(raw_repo=raw_repo, meta_repo=meta_repo, mode="eps", delay=0)

    with patch("akshare.stock_profit_forecast_ths", return_value=_fake_eps_raw()):
        stats = collector.collect(codes=["000001"])

    assert stats.ok == 1
    result = raw_repo.load_eps_snapshots("000001")
    assert len(result) == 1
    assert float(result.iloc[0]["eps_cur"]) == 1.5


def test_collect_report_network_failure(repos):
    raw_repo, meta_repo = repos
    collector = ReportCollector(raw_repo=raw_repo, meta_repo=meta_repo, mode="report", delay=0)

    with patch("src.data.tushare_fetchers.fetch_report_rc", return_value=None):
        stats = collector.collect(codes=["000001"])

    assert stats.fail == 1
    assert meta_repo.get_last_date("reports", "000001") is None
