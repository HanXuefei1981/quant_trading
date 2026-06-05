# 设计文档：修复 daily 内 scan 的 DuckDB 同进程连接冲突

- 日期：2026-06-05
- 状态：已批准
- 背景：2026-06-05 运营 `main.py daily`（06-04）时，链路跑完 update/fetch/Phase1 后在 **scan 步崩溃**：
  `_duckdb.ConnectionException: Can't open a connection to same database file with a different configuration than existing connections`。
  根因：`get_db(read_only=True)` 总是新开一个只读连接；但 daily 单进程里前面步骤已持有读写单例 `_conn`，DuckDB 不允许同进程对同一库用不同配置（读写 vs 只读）再开连接。独立 `main.py scan`（单连接进程）无此问题，但 daily 链内每次必崩。

## 1. 目标

`daily` 链内 scan 步不再因连接配置冲突崩溃；`get_db(read_only=True)` 在进程已持读写单例时复用之，否则仍新开只读连接（保留跨进程并存设计）。

## 2. 非目标（YAGNI）

- 不改 4 个只读调用点（`assemble_inference`、`pipeline.load_features_from_db`、scan 的 `enrich_signals`、scan 北向查询）。
- 不改 monitor/runner（monitor 是独立进程，跑子进程，不受影响）。
- 不重构 daily 把 scan 移到子进程。

## 3. 现状（事实基线）

`src/dal/connection.py`：
```python
_conn = None
def get_db(read_only: bool = False):
    if read_only:
        return duckdb.connect(str(DB_PATH), read_only=True)   # 总是新开只读连接
    global _conn
    if _conn is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _conn = duckdb.connect(str(DB_PATH))
    return _conn
```
- read_only=True 的设计意图（docstring）：让只读步骤与独立进程的 monitor 跨进程并存、不抢写锁。
- read_only=True 调用点：`src/features/assembler.py:411`（assemble_inference→scan）、`src/data/pipeline.py:304`（Phase2/3）、`main.py:602` 与 `main.py:692`（scan 富信息/北向）。
- **关键安全事实**：以上只读调用方均**不** `.close()` 连接（全项目只读调用点无 `.close()`）——故复用读写单例不会被误关。

## 4. 设计（方案 A）

在 `get_db(read_only=True)` 中：进程已有读写单例 `_conn` 时返回它（读写连接亦可读），否则新开只读连接。

```python
def get_db(read_only: bool = False) -> duckdb.DuckDBPyConnection:
    global _conn
    if read_only:
        if _conn is not None:
            return _conn   # 进程已持读写连接，复用（读写亦可读），避免同进程异配置冲突
        return duckdb.connect(str(DB_PATH), read_only=True)
    if _conn is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _conn = duckdb.connect(str(DB_PATH))
    return _conn
```
同步更新 docstring，说明"进程已持读写单例时只读请求复用之"的新行为。

## 5. 行为矩阵

| 场景 | 进程内是否已有读写 `_conn` | `get_db(read_only=True)` 返回 |
|------|------|------|
| daily 链（update/phase1 后 scan） | 有 | **复用读写单例** → 不冲突 |
| 独立 `main.py scan` | 无 | 新开只读连接（同今） |
| monitor 进程 / 独立 Phase2/3 | 无 | 新开只读连接（同今，跨进程并存不变） |

## 6. 测试

新增 `tests/test_connection.py`，每测 monkeypatch `src.dal.connection.DB_PATH` 到 tmp 文件 + 重置 `connection._conn = None`：
- `test_readonly_reuses_existing_readwrite_singleton`：先 `get_db()`（读写单例），再 `get_db(read_only=True)` 返回**同一对象**（`is`）。
- `test_readonly_fresh_when_no_singleton`：无单例时 `get_db(read_only=True)` 返回可用连接（能 `SELECT 1`），且不把它设为读写单例（`connection._conn` 仍为 None）。
- `test_readwrite_singleton_unchanged`：连续两次 `get_db()` 返回同一对象（回归既有单例行为）。

## 7. 影响面 / 风险

- 单点改 `connection.py`，逻辑收敛；只读调用方不 close，复用安全。
- 跨进程并存设计保留（无读写单例时仍新开只读）。
- 修复后 daily 链可一次性跑完到 scan 输出。
