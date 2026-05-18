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
