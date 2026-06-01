# tests/test_collector_northbound.py
"""测试 NorthboundCollector.collect()"""
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
from src.collectors.northbound_collector import NorthboundCollector

# collect() 调用 tushare fetch_northbound_hsgt（方法体内延迟 import），patch 模块级对象
_FETCH = "src.data.tushare_fetchers.fetch_northbound_hsgt"


def _fake_northbound(d: str = "2024-01-04") -> pd.DataFrame:
    """模拟 fetch_northbound_hsgt 的输出列：date, north_net_inflow(元), hgt_yi(亿), sgt_yi(亿)。"""
    return pd.DataFrame({
        "date": pd.to_datetime([d]),
        "north_net_inflow": [1e9],
        "hgt_yi": [5.0],
        "sgt_yi": [5.0],
    })


@pytest.fixture
def repos():
    conn = duckdb.connect(":memory:")
    migrate(conn)
    return RawRepo(conn), MetaRepo(conn)


def test_collect_writes_today_row(repos):
    raw_repo, meta_repo = repos
    collector = NorthboundCollector(raw_repo=raw_repo, meta_repo=meta_repo)

    with patch(_FETCH, return_value=_fake_northbound()):
        stats = collector.collect()

    assert stats.ok == 1
    result = raw_repo.load_northbound()
    assert len(result) == 1
    assert result.iloc[0]["north_net_inflow"] == pytest.approx(1e9)
    assert result.iloc[0]["hgt_yi"] == pytest.approx(5.0)


def test_collect_skips_when_no_new_dates(repos):
    """已采集到最新：collect 仍拉取，但按水位过滤后无新行 → cached（不再写表）。"""
    raw_repo, meta_repo = repos
    meta_repo.set_last_date("northbound", "__market__", date.today())
    collector = NorthboundCollector(raw_repo=raw_repo, meta_repo=meta_repo)

    # 拉回的历史日期均早于水位（today），过滤后为空
    with patch(_FETCH, return_value=_fake_northbound("2024-01-04")) as mock_fetch:
        stats = collector.collect()

    assert stats.cached == 1
    assert mock_fetch.called  # 新语义：先拉取再按 date 过滤，故 fetch 会被调用
    assert len(raw_repo.load_northbound()) == 0


def test_collect_handles_network_failure(repos):
    raw_repo, meta_repo = repos
    collector = NorthboundCollector(raw_repo=raw_repo, meta_repo=meta_repo)

    with patch(_FETCH, return_value=None):
        stats = collector.collect()

    assert stats.fail == 1
    assert meta_repo.get_last_date("northbound", "__market__") is None
