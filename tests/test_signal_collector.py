"""测试 SignalCollector 龙虎榜采集"""
import sys
from pathlib import Path
import pandas as pd
import pytest
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

FAKE_LHB_DF = pd.DataFrame({
    "代码": ["000001", "600036"],
    "名称": ["平安银行", "招商银行"],
    "上榜日期": ["2026-05-16", "2026-05-16"],
    "收盘价": [12.5, 38.2],
    "涨跌幅": [3.5, 2.1],
    "龙虎榜净买额": [5000000.0, -2000000.0],
    "买入额合计": [8000000.0, 3000000.0],
    "卖出额合计": [3000000.0, 5000000.0],
})


def test_fetch_all_saves_daily_parquet(tmp_path):
    """fetch_all 后 lhb/2026-05-16.parquet 存在"""
    from src.collectors.signal_collector import SignalCollector
    import datetime

    collector = SignalCollector(base_dir=tmp_path)

    with patch("akshare.stock_lhb_detail_em", return_value=FAKE_LHB_DF):
        stats = collector.fetch_all(
            codes=[], date=datetime.date(2026, 5, 16), incremental=False
        )

    out = tmp_path / "lhb" / "2026-05-16.parquet"
    assert out.exists()
    df = pd.read_parquet(out)
    assert set(["date", "code", "lhb_net_buy", "lhb_buy_amount", "lhb_sell_amount"]).issubset(df.columns)
    assert len(df) == 2
    assert stats.ok == 1


def test_fetch_all_correct_filename(tmp_path):
    """文件名格式为 YYYY-MM-DD.parquet"""
    from src.collectors.signal_collector import SignalCollector
    import datetime

    collector = SignalCollector(base_dir=tmp_path)

    with patch("akshare.stock_lhb_detail_em", return_value=FAKE_LHB_DF):
        collector.fetch_all(codes=[], date=datetime.date(2026, 5, 9), incremental=False)

    assert (tmp_path / "lhb" / "2026-05-09.parquet").exists()


def test_fetch_all_dedup_same_date(tmp_path):
    """同日重复拉取不产生重复行（drop_duplicates by code + date）"""
    from src.collectors.signal_collector import SignalCollector
    import datetime

    collector = SignalCollector(base_dir=tmp_path)

    with patch("akshare.stock_lhb_detail_em", return_value=FAKE_LHB_DF):
        collector.fetch_all(codes=[], date=datetime.date(2026, 5, 16), incremental=False)
        collector.fetch_all(codes=[], date=datetime.date(2026, 5, 16), incremental=False)

    df = pd.read_parquet(tmp_path / "lhb" / "2026-05-16.parquet")
    assert len(df) == 2  # 没有重复


def test_fetch_all_incremental_skips_existing(tmp_path):
    """incremental=True 时已有该日文件则跳过"""
    from src.collectors.signal_collector import SignalCollector
    import datetime

    collector = SignalCollector(base_dir=tmp_path)
    lhb_dir = tmp_path / "lhb"
    lhb_dir.mkdir(parents=True, exist_ok=True)
    (lhb_dir / "2026-05-16.parquet").touch()

    with patch("akshare.stock_lhb_detail_em", return_value=FAKE_LHB_DF) as mock_api:
        collector.fetch_all(codes=[], date=datetime.date(2026, 5, 16), incremental=True)

    mock_api.assert_not_called()


def test_load_aggregates_across_dates(tmp_path):
    """load(code) 聚合多日文件，返回该股所有上榜记录"""
    from src.collectors.signal_collector import SignalCollector

    collector = SignalCollector(base_dir=tmp_path)
    lhb_dir = tmp_path / "lhb"
    lhb_dir.mkdir(parents=True, exist_ok=True)

    for date_str, net_buy in [("2026-05-15", 1000000.0), ("2026-05-16", 2000000.0)]:
        df = pd.DataFrame({
            "date": pd.to_datetime([date_str]),
            "code": ["000001"],
            "lhb_net_buy": [net_buy],
            "lhb_buy_amount": [3000000.0],
            "lhb_sell_amount": [1000000.0],
        })
        df.to_parquet(lhb_dir / f"{date_str}.parquet", index=False)

    result = collector.load("000001")
    assert result is not None
    assert len(result) == 2
    assert set(result["lhb_net_buy"]) == {1000000.0, 2000000.0}


def test_load_returns_none_when_no_data(tmp_path):
    """该股从未上榜时 load 返回 None"""
    from src.collectors.signal_collector import SignalCollector

    collector = SignalCollector(base_dir=tmp_path)
    (tmp_path / "lhb").mkdir(parents=True, exist_ok=True)

    assert collector.load("999999") is None


def test_load_market_reads_single_date(tmp_path):
    """load_market(date) 读取指定日期的全市场文件"""
    from src.collectors.signal_collector import SignalCollector
    import datetime

    collector = SignalCollector(base_dir=tmp_path)
    lhb_dir = tmp_path / "lhb"
    lhb_dir.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame({
        "date": pd.to_datetime(["2026-05-16"]),
        "code": ["000001"],
        "lhb_net_buy": [1000000.0],
        "lhb_buy_amount": [2000000.0],
        "lhb_sell_amount": [1000000.0],
    })
    df.to_parquet(lhb_dir / "2026-05-16.parquet", index=False)

    result = collector.load_market(datetime.date(2026, 5, 16))
    assert result is not None
    assert len(result) == 1


def test_load_market_returns_none_when_missing(tmp_path):
    """指定日期无文件时 load_market 返回 None"""
    from src.collectors.signal_collector import SignalCollector
    import datetime

    collector = SignalCollector(base_dir=tmp_path)
    (tmp_path / "lhb").mkdir(parents=True, exist_ok=True)

    assert collector.load_market(datetime.date(2026, 5, 16)) is None
