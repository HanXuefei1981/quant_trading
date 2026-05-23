# tests/test_collector_signal.py
"""测试 SignalCollector.collect()"""
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
from src.collectors.signal_collector import SignalCollector


@pytest.fixture
def repos():
    conn = duckdb.connect(":memory:")
    migrate(conn)
    return RawRepo(conn), MetaRepo(conn)


def _fake_lhb_raw() -> pd.DataFrame:
    return pd.DataFrame({
        "代码": ["000001", "000002"],
        "龙虎榜净买额": [1e6, 2e6],
        "买入额合计": [3e6, 4e6],
        "卖出额合计": [2e6, 2e6],
    })


def test_collect_writes_today_row(repos):
    raw_repo, meta_repo = repos
    collector = SignalCollector(raw_repo=raw_repo, meta_repo=meta_repo)

    with patch("akshare.stock_lhb_detail_em", return_value=_fake_lhb_raw()):
        stats = collector.collect()

    assert stats.ok == 1
    result = raw_repo.load_lhb("000001")
    assert len(result) == 1


def test_collect_skips_if_already_collected_today(repos):
    raw_repo, meta_repo = repos
    meta_repo.set_last_date("lhb", "__market__", date.today())
    collector = SignalCollector(raw_repo=raw_repo, meta_repo=meta_repo)

    with patch("akshare.stock_lhb_detail_em") as mock_ak:
        stats = collector.collect()

    assert stats.cached == 1
    assert not mock_ak.called


def test_collect_handles_network_failure(repos):
    raw_repo, meta_repo = repos
    collector = SignalCollector(raw_repo=raw_repo, meta_repo=meta_repo)

    with patch("akshare.stock_lhb_detail_em", side_effect=Exception("Connection error")):
        stats = collector.collect()

    assert stats.fail == 1
    assert meta_repo.get_last_date("lhb", "__market__") is None
