"""测试 ReportCollector 研报分支"""
import sys
from pathlib import Path
import pandas as pd
import pytest
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

FAKE_REPORT_DF = pd.DataFrame({
    "日期": ["2026-05-15", "2026-05-10"],
    "机构": ["国信证券", "招商证券"],
    "近一月个股研报数": [5, 5],
    "报告名称": ["点评报告A", "深度报告B"],
    "东财评级": ["买入", "中性"],
})


def test_fetch_one_report_saves_parquet(tmp_path):
    """fetch_one mode='report' 后 reports/{code}.parquet 存在且列齐全"""
    from src.collectors.report_collector import ReportCollector

    collector = ReportCollector(base_dir=tmp_path)

    with patch("akshare.stock_research_report_em", return_value=FAKE_REPORT_DF):
        result = collector.fetch_one("000001", mode="report")

    out = tmp_path / "reports" / "000001.parquet"
    assert out.exists()
    df = pd.read_parquet(out)
    assert set(["date", "code", "institution", "rating"]).issubset(df.columns)
    assert len(df) == 2
    assert result is not None


def test_fetch_one_returns_none_on_api_error(tmp_path):
    """API 抛异常时 fetch_one 返回 None，不抛出"""
    from src.collectors.report_collector import ReportCollector

    collector = ReportCollector(base_dir=tmp_path)

    with patch("akshare.stock_research_report_em", side_effect=Exception("network")):
        result = collector.fetch_one("000001", mode="report")

    assert result is None


def test_load_report_returns_dataframe(tmp_path):
    """load mode='report' 读缓存返回 DataFrame"""
    from src.collectors.report_collector import ReportCollector

    collector = ReportCollector(base_dir=tmp_path)
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    df_saved = pd.DataFrame({
        "date": pd.to_datetime(["2026-05-15"]),
        "code": ["000001"],
        "institution": ["国信证券"],
        "rating": ["买入"],
    })
    df_saved.to_parquet(reports_dir / "000001.parquet", index=False)

    result = collector.load("000001", mode="report")
    assert result is not None
    assert len(result) == 1


def test_load_returns_none_when_missing(tmp_path):
    """缓存不存在时 load 返回 None"""
    from src.collectors.report_collector import ReportCollector

    collector = ReportCollector(base_dir=tmp_path)
    assert collector.load("999999", mode="report") is None
