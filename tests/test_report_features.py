"""测试 report.py 研报特征计算"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _make_kline(dates: list[str]) -> pd.DataFrame:
    """构造最简 K 线 DataFrame"""
    return pd.DataFrame({
        "date": pd.to_datetime(dates),
        "code": "000001",
        "close": [10.0] * len(dates),
    })


def test_report_count_30d_rolling(tmp_path):
    """近30日研报计数正确"""
    from src.features.report import add_report_features

    # 研报发布于 2026-05-01 和 2026-05-10
    reports = pd.DataFrame({
        "date": pd.to_datetime(["2026-05-01", "2026-05-10"]),
        "code": ["000001", "000001"],
        "institution": ["国信证券", "招商证券"],
        "rating": ["买入", "中性"],
    })
    (tmp_path / "reports").mkdir(parents=True)
    reports.to_parquet(tmp_path / "reports" / "000001.parquet", index=False)
    (tmp_path / "eps").mkdir(parents=True)

    kline = _make_kline(["2026-05-12", "2026-05-13", "2026-06-15"])
    result = add_report_features(kline.copy(), "000001", base_dir=tmp_path)

    # 2026-05-12: available 5-02 和 5-11，窗口内2篇
    assert result.loc[result["date"] == pd.Timestamp("2026-05-12"), "report_count_30d"].iloc[0] == 2
    # 2026-06-15: 5-02 超出30交易日窗口，只有 5-11（5-10研报的 available_date）在窗口内
    assert result.loc[result["date"] == pd.Timestamp("2026-06-15"), "report_count_30d"].iloc[0] == 1


def test_no_lookahead_bias_report(tmp_path):
    """available_date = report_date + 1，当日发布的研报不出现在当日特征"""
    from src.features.report import add_report_features

    reports = pd.DataFrame({
        "date": pd.to_datetime(["2026-05-15"]),
        "code": ["000001"],
        "institution": ["国信证券"],
        "rating": ["买入"],
    })
    (tmp_path / "reports").mkdir(parents=True)
    reports.to_parquet(tmp_path / "reports" / "000001.parquet", index=False)
    (tmp_path / "eps").mkdir(parents=True)

    # K 线日期 = 研报发布日（当日不应该能看到这篇研报）
    kline = _make_kline(["2026-05-15", "2026-05-16"])
    result = add_report_features(kline.copy(), "000001", base_dir=tmp_path)

    # 5-15 当日看不到（available_date = 5-16）
    assert result.loc[result["date"] == pd.Timestamp("2026-05-15"), "report_count_30d"].iloc[0] == 0
    # 5-16 可以看到
    assert result.loc[result["date"] == pd.Timestamp("2026-05-16"), "report_count_30d"].iloc[0] == 1


def test_missing_report_data_fills_nan(tmp_path):
    """无研报缓存时 report_count_30d 等全部为 NaN"""
    from src.features.report import add_report_features

    (tmp_path / "reports").mkdir(parents=True)
    (tmp_path / "eps").mkdir(parents=True)

    kline = _make_kline(["2026-05-15"])
    result = add_report_features(kline.copy(), "999999", base_dir=tmp_path)

    assert np.isnan(result["report_count_30d"].iloc[0])
    assert np.isnan(result["analyst_count"].iloc[0])


def test_eps_consensus_forward_filled(tmp_path):
    """EPS 快照前向填充到每日 K 线"""
    from src.features.report import add_report_features

    (tmp_path / "reports").mkdir(parents=True)
    eps_dir = tmp_path / "eps"
    eps_dir.mkdir(parents=True)
    eps_df = pd.DataFrame({
        "snapshot_date": pd.to_datetime(["2026-04-01", "2026-05-01"]),
        "code": ["000001", "000001"],
        "eps_cur": [2.10, 2.17],
        "eps_next": [2.20, 2.24],
        "analyst_count": [18, 20],
    })
    eps_df.to_parquet(eps_dir / "000001.parquet", index=False)

    kline = _make_kline(["2026-04-15", "2026-05-10", "2026-06-01"])
    result = add_report_features(kline.copy(), "000001", base_dir=tmp_path)

    # 4-15: 使用 4-01 快照（前向填充）
    assert result.loc[result["date"] == pd.Timestamp("2026-04-15"), "eps_consensus_cur"].iloc[0] == pytest.approx(2.10)
    # 5-10: 使用 5-01 快照
    assert result.loc[result["date"] == pd.Timestamp("2026-05-10"), "eps_consensus_cur"].iloc[0] == pytest.approx(2.17)


def test_eps_revision_direction(tmp_path):
    """EPS 上调时 eps_revision = +1，下调时 = -1"""
    from src.features.report import add_report_features

    (tmp_path / "reports").mkdir(parents=True)
    eps_dir = tmp_path / "eps"
    eps_dir.mkdir(parents=True)
    eps_df = pd.DataFrame({
        "snapshot_date": pd.to_datetime(["2026-04-01", "2026-05-01"]),
        "code": ["000001", "000001"],
        "eps_cur": [2.10, 2.17],   # 上调
        "eps_next": [2.20, 2.24],
        "analyst_count": [18, 20],
    })
    eps_df.to_parquet(eps_dir / "000001.parquet", index=False)

    kline = _make_kline(["2026-04-15", "2026-05-10"])
    result = add_report_features(kline.copy(), "000001", base_dir=tmp_path)

    # 4-01 快照没有前一期对比，revision = 0
    assert result.loc[result["date"] == pd.Timestamp("2026-04-15"), "eps_revision"].iloc[0] == 0
    # 5-01 快照相对 4-01 上调，revision = +1
    assert result.loc[result["date"] == pd.Timestamp("2026-05-10"), "eps_revision"].iloc[0] == 1


def test_missing_eps_data_fills_nan(tmp_path):
    """无 EPS 缓存时 eps_consensus_cur 为 NaN"""
    from src.features.report import add_report_features

    (tmp_path / "reports").mkdir(parents=True)
    (tmp_path / "eps").mkdir(parents=True)

    kline = _make_kline(["2026-05-15"])
    result = add_report_features(kline.copy(), "999999", base_dir=tmp_path)

    assert np.isnan(result["eps_consensus_cur"].iloc[0])
