"""测试 DAL schema 建表"""
import sys
from pathlib import Path

import duckdb
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.dal.schema import migrate


@pytest.fixture
def db():
    conn = duckdb.connect(":memory:")
    yield conn
    conn.close()


def test_migrate_creates_all_tables(db):
    migrate(db)
    tables = {row[0] for row in db.execute("SHOW TABLES").fetchall()}
    expected = {
        "kline", "fundamentals", "fund_flow", "northbound",
        "lhb", "reports", "eps_snapshot", "features", "collect_log",
    }
    assert expected.issubset(tables)


def test_migrate_is_idempotent(db):
    migrate(db)
    migrate(db)  # 第二次调用不应抛出异常


def test_kline_has_primary_key(db):
    migrate(db)
    # 写入两行相同主键，INSERT OR REPLACE 后只保留最新一行
    db.execute("""
        INSERT INTO kline VALUES ('2024-01-02', '000001', 10.0, 11.0, 9.5, 10.5, 1e8, 1000000);
        INSERT OR REPLACE INTO kline VALUES ('2024-01-02', '000001', 10.0, 11.0, 9.5, 11.0, 1e8, 1000000);
    """)
    count = db.execute("SELECT COUNT(*) FROM kline").fetchone()[0]
    assert count == 1
    close = db.execute("SELECT close FROM kline").fetchone()[0]
    assert close == 11.0


def test_collect_log_has_primary_key(db):
    migrate(db)
    db.execute("""
        INSERT INTO collect_log VALUES ('kline', '000001', '2024-01-01', 1, NOW(), 'ok');
        INSERT OR REPLACE INTO collect_log VALUES ('kline', '000001', '2024-01-05', 5, NOW(), 'ok');
    """)
    count = db.execute("SELECT COUNT(*) FROM collect_log").fetchone()[0]
    assert count == 1
    last = db.execute("SELECT last_date FROM collect_log").fetchone()[0]
    assert str(last) == "2024-01-05"
