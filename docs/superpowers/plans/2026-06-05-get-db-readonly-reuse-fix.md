# get_db 只读复用读写单例修复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 `main.py daily` 链内 scan 步崩溃——让 `get_db(read_only=True)` 在进程已持读写单例时复用之，避免 DuckDB 同进程同库异配置冲突。

**Architecture:** 单点改 `src/dal/connection.py`：read_only 分支先检查读写单例 `_conn`，存在则返回它（读写连接亦可读），否则新开只读连接（保留跨进程并存设计）。新增 `tests/test_connection.py`。

**Tech Stack:** Python 3.11, DuckDB, pytest（tmp_path + monkeypatch）。

约定：项目根 `/Users/hanxuefei/7、AI 空间/7-3、GitHub/quant_trading`，用 `.venv/bin/python`。spec：`docs/superpowers/specs/2026-06-05-get-db-readonly-reuse-fix-design.md`。

---

## 文件结构

| 文件 | 改动 |
|------|------|
| `src/dal/connection.py` | `get_db()` read_only 分支：有读写单例则复用；更新 docstring |
| `tests/test_connection.py` | 新建：复用/新开/单例回归三测 |

---

## Task 1: get_db 只读复用读写单例

**Files:**
- Modify: `src/dal/connection.py`（`get_db()`）
- Create: `tests/test_connection.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_connection.py`：

```python
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
    # 关闭本测试创建的临时单例，避免连接泄漏（monkeypatch 随后恢复原 _conn）
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
    duckdb.connect(str(tmp_db)).close()  # DuckDB 只读要求库文件已存在，先建之
    assert conn_mod._conn is None
    ro = conn_mod.get_db(read_only=True)
    try:
        assert ro.execute("SELECT 1").fetchone()[0] == 1
        assert conn_mod._conn is None  # 只读分支不得设置读写单例
    finally:
        ro.close()
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_connection.py -v`
Expected: `test_readonly_reuses_existing_readwrite_singleton` FAIL —— 现状 read_only 总是新开连接，在已有读写单例时会抛 `duckdb.ConnectionException`（同进程异配置），不会返回同一对象。

- [ ] **Step 3: 实现修复**

把 `src/dal/connection.py` 的 `get_db()` 整个函数替换为：

```python
def get_db(read_only: bool = False) -> duckdb.DuckDBPyConnection:
    """返回 DuckDB 连接。

    read_only=True：
      - 若本进程已持有读写单例 `_conn`，**复用它**（读写连接亦可读）——避免
        DuckDB「同进程对同一库不能用不同配置再开连接」的冲突（如 daily 链内
        update/Phase1 已开读写连接后，scan 再请求只读）。
      - 否则新开一个全新只读连接：DuckDB 单写多读，只读连接可跨进程并存，
        让独立运行的只读步骤（scan / Phase2/3）与开着的 monitor 并行、不抢写锁。
        调用方负责关闭。

    read_only=False（默认）：返回读写单例，供写入命令（fetch/Phase1/upsert）使用。
    """
    global _conn
    if read_only:
        if _conn is not None:
            return _conn
        return duckdb.connect(str(DB_PATH), read_only=True)
    if _conn is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _conn = duckdb.connect(str(DB_PATH))
    return _conn
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/test_connection.py -v`
Expected: PASS（3 passed）。

- [ ] **Step 5: 局部回归（dal 相关）**

Run: `.venv/bin/python -m pytest tests/test_connection.py tests/test_dal_schema.py tests/test_dal_raw_repo.py tests/test_dal_feature_repo.py tests/test_dal_meta_repo.py -q`
Expected: 全部 PASS（确认改动不破坏既有 DAL 单例使用）。

- [ ] **Step 6: Commit**

```bash
git add src/dal/connection.py tests/test_connection.py
git commit -m "fix: get_db 只读请求在进程已有读写连接时复用之，修复 daily 内 scan 连接冲突"
```

---

## Task 2: 全量回归 + 端到端确认

**Files:** 无（验证）

- [ ] **Step 1: 全量回归**

Run: `.venv/bin/python -m pytest -q -p no:cacheprovider`
Expected: 全部 PASS（应为 341 passed：原 338 + 3 新增）。

- [ ] **Step 2: 同进程「先写后只读」端到端冒烟（真实库，不写业务数据）**

Run:
```bash
.venv/bin/python -c "
from src.dal.connection import get_db
rw = get_db()                       # 读写单例（同 daily 链前段）
ro = get_db(read_only=True)         # 修复前此处会抛 ConnectionException
print('reuse OK:', ro is rw)
print(ro.execute('select count(*) from features').fetchone())
"
```
Expected: 打印 `reuse OK: True` 与 features 行数，无 `ConnectionException`。（只读查询，不修改数据。）

> 可选完整确认：择机重跑 `.venv/bin/python main.py daily`（约 20 分钟），观察 scan 步不再崩、正常输出 Top 榜。非本计划必须步骤。

---

## Self-Review 记录

- **Spec 覆盖**：①read_only 复用读写单例 → Task1 Step3；②无单例时新开只读 → Task1 Step3 + test_readonly_fresh；③docstring 更新 → Task1 Step3；④三测 → Task1 Step1；⑤行为矩阵三场景均被测试覆盖。无遗漏。
- **占位扫描**：无 TBD/TODO；每步含完整代码与命令。
- **类型/命名一致**：`conn_mod._conn` / `DB_PATH` / `get_db(read_only=...)` 与 `src/dal/connection.py` 现有命名一致；测试 fixture `tmp_db` 在三测中一致使用。
- **测试要点**：`test_readonly_fresh_when_no_singleton` 先用 `duckdb.connect(...).close()` 建库文件（DuckDB 只读不创建文件），否则只读打开不存在的文件会失败。
