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


FAKE_EPS_DF = pd.DataFrame({
    "年度": ["2026", "2027"],
    "预测机构数": [20, 18],
    "最小值": [2.08, 2.09],
    "均值": [2.17, 2.24],
    "最大值": [2.27, 2.36],
    "行业平均数": [1.89, 2.03],
})


def test_fetch_one_eps_saves_parquet(tmp_path):
    """fetch_one mode='eps' 后 eps/{code}.parquet 存在且列齐全"""
    from src.collectors.report_collector import ReportCollector

    collector = ReportCollector(base_dir=tmp_path)

    with patch("akshare.stock_profit_forecast_ths", return_value=FAKE_EPS_DF):
        result = collector.fetch_one("000001", mode="eps")

    out = tmp_path / "eps" / "000001.parquet"
    assert out.exists()
    df = pd.read_parquet(out)
    assert set(["snapshot_date", "code", "eps_cur", "eps_next", "analyst_count"]).issubset(df.columns)
    assert df["eps_cur"].iloc[0] == pytest.approx(2.17)
    assert df["analyst_count"].iloc[0] == 20
    assert result is not None


def test_fetch_one_eps_returns_none_on_api_error(tmp_path):
    """EPS API 失败时 fetch_one 返回 None"""
    from src.collectors.report_collector import ReportCollector

    collector = ReportCollector(base_dir=tmp_path)

    with patch("akshare.stock_profit_forecast_ths", side_effect=Exception("timeout")):
        result = collector.fetch_one("000001", mode="eps")

    assert result is None


def test_load_eps_returns_dataframe(tmp_path):
    """load mode='eps' 读缓存返回 DataFrame"""
    from src.collectors.report_collector import ReportCollector

    collector = ReportCollector(base_dir=tmp_path)
    eps_dir = tmp_path / "eps"
    eps_dir.mkdir(parents=True, exist_ok=True)
    df_saved = pd.DataFrame({
        "snapshot_date": pd.to_datetime(["2026-05-19"]),
        "code": ["000001"],
        "eps_cur": [2.17],
        "eps_next": [2.24],
        "analyst_count": [20],
    })
    df_saved.to_parquet(eps_dir / "000001.parquet", index=False)

    result = collector.load("000001", mode="eps")
    assert result is not None
    assert result["eps_cur"].iloc[0] == pytest.approx(2.17)


def test_fetch_all_skips_cached_incremental(tmp_path):
    """incremental=True 时已有缓存的股票跳过，stats.cached 递增"""
    from src.collectors.report_collector import ReportCollector

    collector = ReportCollector(base_dir=tmp_path, delay=0)
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    (reports_dir / "000001.parquet").touch()  # 模拟已缓存

    with patch("akshare.stock_research_report_em", return_value=FAKE_REPORT_DF) as mock_api:
        stats = collector.fetch_all(["000001", "600036"], mode="report", incremental=True)

    assert stats.cached == 1
    assert mock_api.call_count == 1  # 只拉了 600036


def test_fetch_all_circuit_breaker(tmp_path):
    """连续失败达到 max_errors 后熔断，不再继续"""
    from src.collectors.report_collector import ReportCollector

    collector = ReportCollector(base_dir=tmp_path, delay=0)
    codes = [f"{i:06d}" for i in range(20)]

    with patch("akshare.stock_research_report_em", side_effect=Exception("blocked")):
        stats = collector.fetch_all(codes, mode="report", incremental=False, max_errors=3)

    assert stats.fail == 3
    assert stats.ok == 0
