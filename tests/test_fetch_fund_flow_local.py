"""测试 fetch_fund_flow_local 核心工具函数"""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.fetch_fund_flow_local import get_all_codes, get_pending_codes


def test_get_all_codes_returns_stems(tmp_path):
    """应返回目录下所有 parquet 文件的 stem，按字母排序"""
    (tmp_path / "000001.parquet").touch()
    (tmp_path / "600036.parquet").touch()
    (tmp_path / "readme.txt").touch()  # 非 parquet 应被忽略

    result = get_all_codes(tmp_path)

    assert result == ["000001", "600036"]


def test_get_all_codes_empty_dir(tmp_path):
    assert get_all_codes(tmp_path) == []


def test_get_pending_codes_filters_cached(tmp_path):
    """已有 parquet 的股票应被过滤掉"""
    (tmp_path / "000001.parquet").touch()
    all_codes = ["000001", "600036", "300001"]

    result = get_pending_codes(all_codes, tmp_path)

    assert result == ["600036", "300001"]


def test_get_pending_codes_all_cached(tmp_path):
    (tmp_path / "000001.parquet").touch()
    result = get_pending_codes(["000001"], tmp_path)
    assert result == []


def test_get_pending_codes_none_cached(tmp_path):
    result = get_pending_codes(["000001", "600036"], tmp_path)
    assert result == ["000001", "600036"]


from unittest.mock import patch

from scripts.fetch_fund_flow_local import download_all


def test_download_all_counts_ok_and_fail(tmp_path):
    """成功和失败分别计入统计，failed_codes 收集失败股票"""
    codes = ["000001", "600036", "300001"]

    def fake_fetch(code, use_cache):
        if code == "600036":
            return None  # 模拟失败
        return pd.DataFrame({"date": ["2026-01-01"], "major_net_inflow": [1.0]})

    with patch("scripts.fetch_fund_flow_local.fetch_fund_flow", side_effect=fake_fetch):
        result = download_all(codes, delay=0.0, max_errors=200)

    assert result["ok"] == 2
    assert result["fail"] == 1
    assert "600036" in result["failed_codes"]


def test_download_all_stops_on_max_consecutive_errors(tmp_path):
    """连续失败达到 max_errors 时提前终止"""
    codes = ["000001", "000002", "000003", "000004", "000005"]

    with patch("scripts.fetch_fund_flow_local.fetch_fund_flow", return_value=None):
        result = download_all(codes, delay=0.0, max_errors=3)

    # 应在第 3 次连续失败后停止，不处理全部 5 只
    assert result["fail"] == 3
    assert result["ok"] == 0


def test_download_all_resets_consecutive_on_success():
    """成功后连续失败计数应重置，不提前终止"""
    # 失败-失败-成功-失败-失败 → 连续最多 2，不触发 max_errors=3
    dummy_df = pd.DataFrame({"date": ["2026-01-01"], "major_net_inflow": [1.0]})
    side_effects = [None, None, dummy_df, None, None]
    codes = ["a", "b", "c", "d", "e"]

    with patch("scripts.fetch_fund_flow_local.fetch_fund_flow", side_effect=side_effects):
        result = download_all(codes, delay=0.0, max_errors=3)

    assert result["ok"] == 1
    assert result["fail"] == 4
    assert len(result["failed_codes"]) == 4


from scripts.fetch_fund_flow_local import print_header, print_report


def test_print_header_shows_eta(capsys):
    """启动摘要应包含总量、缓存数、待下载数、预计时长"""
    print_header(total=5641, cached_count=531, pending_count=5110, delay=0.3)
    out = capsys.readouterr().out
    assert "5641" in out
    assert "531" in out
    assert "5110" in out
    assert "分钟" in out


def test_print_report_shows_coverage(capsys, tmp_path):
    """结束报告应包含覆盖率百分比"""
    print_report(ok=4000, fail=200, cached_count=531, total=5641,
                 failed_codes=[], fund_flow_dir=tmp_path)
    out = capsys.readouterr().out
    assert "4531" in out   # covered = 4000 + 531
    assert "5641" in out
    assert "%" in out


def test_print_report_writes_failed_txt(tmp_path, capsys):
    """有失败股票时应写入 _failed.txt"""
    failed = ["000999", "600999"]
    print_report(ok=100, fail=2, cached_count=0, total=102,
                 failed_codes=failed, fund_flow_dir=tmp_path)
    failed_file = tmp_path / "_failed.txt"
    assert failed_file.exists()
    content = failed_file.read_text().splitlines()
    assert content == ["000999", "600999"]


def test_print_report_no_failed_txt_when_empty(tmp_path, capsys):
    """无失败时不应创建 _failed.txt"""
    print_report(ok=100, fail=0, cached_count=0, total=100,
                 failed_codes=[], fund_flow_dir=tmp_path)
    assert not (tmp_path / "_failed.txt").exists()


from scripts.fetch_fund_flow_local import main


def test_main_no_pending_exits_early(tmp_path, capsys, monkeypatch):
    """无待下载股票时应打印提示后退出，不调用 download_all"""
    monkeypatch.setattr(
        "scripts.fetch_fund_flow_local.get_all_codes",
        lambda kline_dir=None: ["000001"],
    )
    monkeypatch.setattr(
        "scripts.fetch_fund_flow_local.get_pending_codes",
        lambda codes, fund_flow_dir=None: [],
    )
    main([])  # 空 argv
    out = capsys.readouterr().out
    assert "无需重新下载" in out


def test_main_custom_codes_bypass_pending_filter(monkeypatch, capsys):
    """指定 --codes 时直接使用指定列表，不经过 get_pending_codes 过滤"""
    import pandas as pd
    dummy = pd.DataFrame({"date": ["2026-01-01"], "major_net_inflow": [1.0]})

    monkeypatch.setattr(
        "scripts.fetch_fund_flow_local.get_all_codes",
        lambda kline_dir=None: ["000001", "600036"],
    )
    monkeypatch.setattr(
        "scripts.fetch_fund_flow_local.fetch_fund_flow",
        lambda code, use_cache: dummy,
    )
    main(["--codes", "600036", "--delay", "0"])
    out = capsys.readouterr().out
    assert "下载完成" in out
