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


# collect() 经 _fetch_daily 调用 tushare fetch_lhb_daily（方法体内延迟 import）
_FETCH = "src.data.tushare_fetchers.fetch_lhb_daily"


def _fake_lhb_daily() -> pd.DataFrame:
    """模拟 fetch_lhb_daily 的输出列：date, code, lhb_net_buy(元), lhb_buy_amount(元), lhb_sell_amount(元)。"""
    return pd.DataFrame({
        "date": pd.to_datetime([date.today(), date.today()]),
        "code": ["000001", "000002"],
        "lhb_net_buy": [1e6, 2e6],
        "lhb_buy_amount": [3e6, 4e6],
        "lhb_sell_amount": [2e6, 2e6],
    })


def test_collect_writes_today_row(repos):
    raw_repo, meta_repo = repos
    collector = SignalCollector(raw_repo=raw_repo, meta_repo=meta_repo)

    with patch(_FETCH, return_value=_fake_lhb_daily()):
        stats = collector.collect()

    assert stats.ok == 1
    result = raw_repo.load_lhb("000001")
    assert len(result) == 1


def test_collect_skips_if_already_collected_today(repos):
    raw_repo, meta_repo = repos
    meta_repo.set_last_date("lhb", "__market__", date.today())
    collector = SignalCollector(raw_repo=raw_repo, meta_repo=meta_repo)

    with patch(_FETCH) as mock_fetch:
        stats = collector.collect()

    assert stats.cached == 1
    assert not mock_fetch.called  # 水位已是今日，collect 在拉取前短路


def test_collect_handles_network_failure(repos):
    raw_repo, meta_repo = repos
    collector = SignalCollector(raw_repo=raw_repo, meta_repo=meta_repo)

    with patch(_FETCH, return_value=None):
        stats = collector.collect()

    assert stats.fail == 1
    assert meta_repo.get_last_date("lhb", "__market__") is None
