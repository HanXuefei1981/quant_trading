"""测试 MetaRepo（collect_log CRUD）"""
import sys
from datetime import date
from pathlib import Path

import duckdb
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.dal.schema import migrate
from src.dal.meta_repo import MetaRepo


@pytest.fixture
def repo():
    conn = duckdb.connect(":memory:")
    migrate(conn)
    yield MetaRepo(conn)
    conn.close()


def test_get_last_date_returns_none_when_empty(repo):
    result = repo.get_last_date("kline", "000001")
    assert result is None


def test_set_and_get_last_date(repo):
    repo.set_last_date("kline", "000001", date(2024, 5, 20), row_count=10)
    result = repo.get_last_date("kline", "000001")
    assert result == date(2024, 5, 20)


def test_set_last_date_updates_existing(repo):
    repo.set_last_date("kline", "000001", date(2024, 5, 20))
    repo.set_last_date("kline", "000001", date(2024, 5, 21), row_count=5)
    result = repo.get_last_date("kline", "000001")
    assert result == date(2024, 5, 21)


def test_market_scope_is_independent(repo):
    repo.set_last_date("northbound", "__market__", date(2024, 5, 20))
    repo.set_last_date("lhb", "__market__", date(2024, 5, 19))
    assert repo.get_last_date("northbound", "__market__") == date(2024, 5, 20)
    assert repo.get_last_date("lhb", "__market__") == date(2024, 5, 19)


def test_different_tables_are_independent(repo):
    repo.set_last_date("kline", "000001", date(2024, 5, 20))
    assert repo.get_last_date("fundamentals", "000001") is None
