"""get_db 连接管理测试。"""
import sys
from pathlib import Path

import duckdb
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import src.dal.connection as conn_mod


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """把 DB_PATH 指到临时文件并重置 _conn 单例（测试后自动恢复）。"""
    db_path = tmp_path / "t.duckdb"
    monkeypatch.setattr(conn_mod, "DB_PATH", db_path)
    monkeypatch.setattr(conn_mod, "_conn", None)
    yield db_path
    cur = conn_mod._conn
    if cur is not None:
        try:
            cur.close()
        except Exception:
            pass


def test_readwrite_singleton_unchanged(tmp_db):
    """连续两次 get_db() 返回同一读写单例（回归既有行为）。"""
    a = conn_mod.get_db()
    b = conn_mod.get_db()
    assert a is b


def test_readonly_reuses_existing_readwrite_singleton(tmp_db):
    """进程已有读写单例时，get_db(read_only=True) 复用同一对象（不另开连接）。"""
    rw = conn_mod.get_db()
    ro = conn_mod.get_db(read_only=True)
    assert ro is rw


def test_readonly_fresh_when_no_singleton(tmp_db):
    """无读写单例时，get_db(read_only=True) 新开可用只读连接，且不创建读写单例。"""
    with duckdb.connect(str(tmp_db)):  # DuckDB 只读要求库文件已存在，先建之
        pass
    assert conn_mod._conn is None
    ro = conn_mod.get_db(read_only=True)
    try:
        assert ro.execute("SELECT 1").fetchone()[0] == 1
        assert conn_mod._conn is None  # 只读分支不得设置读写单例
    finally:
        ro.close()
