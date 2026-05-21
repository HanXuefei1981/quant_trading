# Sub-1 DB Schema + DAL 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 创建 `src/dal/` 模块，包含 DuckDB 连接管理、9 张表的 Schema 定义、以及 RawRepo / FeatureRepo / MetaRepo 三个数据访问类，所有上层业务通过 DAL 接口读写数据库。

**Architecture:** 单例连接管理（`connection.py`）指向 `/Volumes/Elements/5、投资/quant_trading/quant.duckdb`，可通过 `QUANT_DB_PATH` 环境变量覆盖。Schema 用 `CREATE TABLE IF NOT EXISTS` 保证幂等。三个 Repo 类均接受可选 `conn` 参数，方便测试时传入内存数据库。全部测试使用 `duckdb.connect(":memory:")` 隔离，不接触磁盘文件。

**Tech Stack:** Python 3.11、DuckDB >= 1.2.0、pandas >= 3.0.0、pytest

---

## 文件结构

**新建文件：**
- `src/dal/__init__.py` — 对外导出 `get_db`, `RawRepo`, `FeatureRepo`, `MetaRepo`
- `src/dal/connection.py` — 单例连接，读取 `config.settings.DB_PATH`
- `src/dal/schema.py` — 9 张表的 SQL 常量 + `migrate(conn=None)` 入口
- `src/dal/raw_repo.py` — `RawRepo` 类，14 个 CRUD 方法
- `src/dal/feature_repo.py` — `FeatureRepo` 类，3 个方法
- `src/dal/meta_repo.py` — `MetaRepo` 类，2 个方法
- `tests/test_dal_schema.py` — schema 建表测试
- `tests/test_dal_raw_repo.py` — RawRepo 测试
- `tests/test_dal_feature_repo.py` — FeatureRepo 测试
- `tests/test_dal_meta_repo.py` — MetaRepo 测试

**修改文件：**
- `config/settings.py` — 新增 `DB_PATH`
- `requirements.txt` — 新增 `duckdb>=1.2.0`

---

## Task 1：安装 duckdb + 更新配置

**Files:**
- Modify: `requirements.txt`
- Modify: `config/settings.py`

- [ ] **Step 1：向 requirements.txt 添加 duckdb 依赖**

在 `requirements.txt` 末尾追加：

```
# 数据库
duckdb>=1.2.0
```

- [ ] **Step 2：安装到 venv**

```bash
cd "/Users/hanxuefei/7、AI 空间/7-3、GitHub/quant_trading"
.venv/bin/pip install "duckdb>=1.2.0"
```

预期输出（含）：`Successfully installed duckdb-...`

- [ ] **Step 3：在 settings.py 新增 DB_PATH**

在 `config/settings.py` 第 11 行（`TDX_VIPDOC_DIR` 定义之后）插入：

```python
# 数据库路径（存放于 Elements 扩展硬盘，可通过环境变量覆盖）
DB_PATH = Path(os.getenv("QUANT_DB_PATH", "/Volumes/Elements/5、投资/quant_trading/quant.duckdb"))
```

同时确认文件顶部已有 `import os`（当前第 2 行已有，无需新增）。

完整修改后的前 15 行应为：

```python
"""全局配置"""
import os
from pathlib import Path

ROOT_DIR = Path(__file__).parent.parent
DATA_DIR = ROOT_DIR / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"

# 通达信本地数据目录（可通过环境变量 TDX_VIPDOC_DIR 覆盖）
TDX_VIPDOC_DIR = Path(os.getenv("TDX_VIPDOC_DIR", str(Path.home() / "tdx_data")))

# 数据库路径（存放于 Elements 扩展硬盘，可通过环境变量覆盖）
DB_PATH = Path(os.getenv("QUANT_DB_PATH", "/Volumes/Elements/5、投资/quant_trading/quant.duckdb"))
```

- [ ] **Step 4：验证导入**

```bash
.venv/bin/python -c "import duckdb; from config.settings import DB_PATH; print(duckdb.__version__, DB_PATH)"
```

预期输出（含）：`1.2.` 和 `/Volumes/Elements/5、投资/quant_trading/quant.duckdb`

- [ ] **Step 5：提交**

```bash
git add requirements.txt config/settings.py
git commit -m "chore: 添加 duckdb 依赖 + DB_PATH 配置"
```

---

## Task 2：connection.py + schema.py + 建表测试

**Files:**
- Create: `src/dal/connection.py`
- Create: `src/dal/schema.py`
- Create: `tests/test_dal_schema.py`

- [ ] **Step 1：写失败测试**

新建 `tests/test_dal_schema.py`：

```python
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
```

- [ ] **Step 2：运行测试验证失败**

```bash
.venv/bin/pytest tests/test_dal_schema.py -v
```

预期：`ImportError: No module named 'src.dal'`

- [ ] **Step 3：创建 src/dal/connection.py**

```python
"""DuckDB 单例连接管理"""
import duckdb

from config.settings import DB_PATH

_conn: duckdb.DuckDBPyConnection | None = None


def get_db() -> duckdb.DuckDBPyConnection:
    global _conn
    if _conn is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _conn = duckdb.connect(str(DB_PATH))
    return _conn
```

- [ ] **Step 4：创建 src/dal/schema.py**

```python
"""DuckDB 表结构定义与迁移"""
from __future__ import annotations

import duckdb

_CREATE_KLINE = """
CREATE TABLE IF NOT EXISTS kline (
    date    DATE    NOT NULL,
    code    VARCHAR NOT NULL,
    open    DOUBLE,
    high    DOUBLE,
    low     DOUBLE,
    close   DOUBLE,
    amount  DOUBLE,
    volume  BIGINT,
    PRIMARY KEY (date, code)
)
"""

_CREATE_FUNDAMENTALS = """
CREATE TABLE IF NOT EXISTS fundamentals (
    date              DATE    NOT NULL,
    code              VARCHAR NOT NULL,
    pe_ttm            DOUBLE,
    pe_static         DOUBLE,
    pb                DOUBLE,
    ps                DOUBLE,
    pcf               DOUBLE,
    peg               DOUBLE,
    market_cap        DOUBLE,
    float_market_cap  DOUBLE,
    total_shares      BIGINT,
    float_shares      BIGINT,
    PRIMARY KEY (date, code)
)
"""

_CREATE_FUND_FLOW = """
CREATE TABLE IF NOT EXISTS fund_flow (
    date              DATE    NOT NULL,
    code              VARCHAR NOT NULL,
    major_net_inflow  DOUBLE,
    major_net_pct     DOUBLE,
    PRIMARY KEY (date, code)
)
"""

_CREATE_NORTHBOUND = """
CREATE TABLE IF NOT EXISTS northbound (
    date              DATE   NOT NULL PRIMARY KEY,
    north_net_inflow  DOUBLE,
    hgt_yi            DOUBLE,
    sgt_yi            DOUBLE
)
"""

_CREATE_LHB = """
CREATE TABLE IF NOT EXISTS lhb (
    date             DATE    NOT NULL,
    code             VARCHAR NOT NULL,
    lhb_net_buy      DOUBLE,
    lhb_buy_amount   DOUBLE,
    lhb_sell_amount  DOUBLE,
    PRIMARY KEY (date, code)
)
"""

_CREATE_REPORTS = """
CREATE TABLE IF NOT EXISTS reports (
    date         DATE    NOT NULL,
    code         VARCHAR NOT NULL,
    institution  VARCHAR NOT NULL,
    rating       VARCHAR,
    PRIMARY KEY (date, code, institution)
)
"""

_CREATE_EPS_SNAPSHOT = """
CREATE TABLE IF NOT EXISTS eps_snapshot (
    snapshot_date  DATE    NOT NULL,
    code           VARCHAR NOT NULL,
    eps_cur        DOUBLE,
    eps_next       DOUBLE,
    analyst_count  INTEGER,
    PRIMARY KEY (snapshot_date, code)
)
"""

# features 表仅预建 PK 列；业务列在 FeatureRepo.upsert_features() 首次调用时动态 ALTER TABLE 添加
_CREATE_FEATURES = """
CREATE TABLE IF NOT EXISTS features (
    date  DATE    NOT NULL,
    code  VARCHAR NOT NULL,
    PRIMARY KEY (date, code)
)
"""

_CREATE_COLLECT_LOG = """
CREATE TABLE IF NOT EXISTS collect_log (
    table_name  VARCHAR   NOT NULL,
    scope       VARCHAR   NOT NULL,
    last_date   DATE,
    row_count   INTEGER,
    updated_at  TIMESTAMP,
    status      VARCHAR,
    PRIMARY KEY (table_name, scope)
)
"""

_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_kline_code ON kline (code)",
    "CREATE INDEX IF NOT EXISTS idx_fundamentals_code ON fundamentals (code)",
    "CREATE INDEX IF NOT EXISTS idx_fund_flow_code ON fund_flow (code)",
    "CREATE INDEX IF NOT EXISTS idx_lhb_code ON lhb (code)",
    "CREATE INDEX IF NOT EXISTS idx_reports_code ON reports (code)",
    "CREATE INDEX IF NOT EXISTS idx_eps_code ON eps_snapshot (code)",
    "CREATE INDEX IF NOT EXISTS idx_features_date ON features (date)",
    "CREATE INDEX IF NOT EXISTS idx_features_code ON features (code)",
]


def migrate(conn: duckdb.DuckDBPyConnection | None = None) -> None:
    """建表（已存在则跳过），可传入外部连接（用于测试）。"""
    from src.dal.connection import get_db
    db = conn if conn is not None else get_db()
    for sql in [
        _CREATE_KLINE, _CREATE_FUNDAMENTALS, _CREATE_FUND_FLOW,
        _CREATE_NORTHBOUND, _CREATE_LHB, _CREATE_REPORTS,
        _CREATE_EPS_SNAPSHOT, _CREATE_FEATURES, _CREATE_COLLECT_LOG,
    ]:
        db.execute(sql)
    for idx_sql in _INDEXES:
        db.execute(idx_sql)
```

- [ ] **Step 5：运行测试验证通过**

```bash
.venv/bin/pytest tests/test_dal_schema.py -v
```

预期：`4 passed`

- [ ] **Step 6：提交**

```bash
git add src/dal/connection.py src/dal/schema.py tests/test_dal_schema.py
git commit -m "feat(dal): connection + schema + migrate()"
```

---

## Task 3：MetaRepo + 测试

**Files:**
- Create: `src/dal/meta_repo.py`
- Create: `tests/test_dal_meta_repo.py`

- [ ] **Step 1：写失败测试**

新建 `tests/test_dal_meta_repo.py`：

```python
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
```

- [ ] **Step 2：运行测试验证失败**

```bash
.venv/bin/pytest tests/test_dal_meta_repo.py -v
```

预期：`ImportError: cannot import name 'MetaRepo'`

- [ ] **Step 3：创建 src/dal/meta_repo.py**

```python
"""MetaRepo：采集进度记录（collect_log 表）"""
from __future__ import annotations

from datetime import date, datetime

import duckdb


class MetaRepo:
    def __init__(self, conn: duckdb.DuckDBPyConnection | None = None) -> None:
        if conn is not None:
            self._conn = conn
        else:
            from src.dal.connection import get_db
            self._conn = get_db()

    def get_last_date(self, table_name: str, scope: str) -> date | None:
        """返回已采集到的最新日期，无记录时返回 None。"""
        row = self._conn.execute(
            "SELECT last_date FROM collect_log WHERE table_name = ? AND scope = ?",
            [table_name, scope],
        ).fetchone()
        if row is None or row[0] is None:
            return None
        val = row[0]
        return val if isinstance(val, date) else val.date()

    def set_last_date(
        self,
        table_name: str,
        scope: str,
        last_date: date,
        row_count: int = 0,
        status: str = "ok",
    ) -> None:
        """写入/更新进度记录（upsert）。"""
        self._conn.execute(
            """
            INSERT OR REPLACE INTO collect_log
                (table_name, scope, last_date, row_count, updated_at, status)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [table_name, scope, last_date, row_count, datetime.now(), status],
        )
```

- [ ] **Step 4：运行测试验证通过**

```bash
.venv/bin/pytest tests/test_dal_meta_repo.py -v
```

预期：`5 passed`

- [ ] **Step 5：提交**

```bash
git add src/dal/meta_repo.py tests/test_dal_meta_repo.py
git commit -m "feat(dal): MetaRepo（collect_log upsert/get）"
```

---

## Task 4：RawRepo（kline + northbound）+ 测试

**Files:**
- Create: `src/dal/raw_repo.py`（仅 kline + northbound 方法）
- Create: `tests/test_dal_raw_repo.py`（仅 kline + northbound 测试）

- [ ] **Step 1：写失败测试**

新建 `tests/test_dal_raw_repo.py`：

```python
"""测试 RawRepo CRUD（kline、northbound）"""
import sys
from datetime import date
from pathlib import Path

import duckdb
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.dal.schema import migrate
from src.dal.raw_repo import RawRepo


@pytest.fixture
def repo():
    conn = duckdb.connect(":memory:")
    migrate(conn)
    yield RawRepo(conn)
    conn.close()


def _kline_df(code: str = "000001", dates: list[str] | None = None) -> pd.DataFrame:
    if dates is None:
        dates = ["2024-01-02", "2024-01-03", "2024-01-04"]
    n = len(dates)
    return pd.DataFrame({
        "date": pd.to_datetime(dates),
        "code": code,
        "open": [10.0] * n,
        "high": [11.0] * n,
        "low":  [9.5]  * n,
        "close": [10.5, 10.8, 11.0][:n],
        "amount": [1e8] * n,
        "volume": [1_000_000] * n,
    })


# ── kline ──────────────────────────────────────────────────────────────────────

def test_upsert_kline_returns_row_count(repo):
    df = _kline_df()
    assert repo.upsert_kline(df) == 3


def test_load_kline_returns_inserted_rows(repo):
    repo.upsert_kline(_kline_df())
    result = repo.load_kline("000001")
    assert len(result) == 3
    assert list(result.columns) == ["date", "code", "open", "high", "low", "close", "amount", "volume"]


def test_upsert_kline_deduplication(repo):
    df1 = _kline_df(dates=["2024-01-02"])
    df1["close"] = 10.5
    df2 = _kline_df(dates=["2024-01-02"])
    df2["close"] = 99.0
    repo.upsert_kline(df1)
    repo.upsert_kline(df2)
    result = repo.load_kline("000001")
    assert len(result) == 1
    assert float(result.iloc[0]["close"]) == 99.0


def test_load_kline_since_filter(repo):
    repo.upsert_kline(_kline_df())
    result = repo.load_kline("000001", since=date(2024, 1, 2))
    assert len(result) == 2  # 只返回 2024-01-03、2024-01-04


def test_load_kline_returns_empty_for_unknown_code(repo):
    result = repo.load_kline("999999")
    assert isinstance(result, pd.DataFrame)
    assert len(result) == 0


# ── northbound ────────────────────────────────────────────────────────────────

def _northbound_df() -> pd.DataFrame:
    return pd.DataFrame({
        "date": pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04"]),
        "north_net_inflow": [10.5, -3.2, 7.8],
        "hgt_yi": [5.0, -1.5, 4.0],
        "sgt_yi": [5.5, -1.7, 3.8],
    })


def test_upsert_northbound_returns_row_count(repo):
    assert repo.upsert_northbound(_northbound_df()) == 3


def test_load_northbound_since_filter(repo):
    repo.upsert_northbound(_northbound_df())
    result = repo.load_northbound(since=date(2024, 1, 2))
    assert len(result) == 2  # 返回 2024-01-03、2024-01-04


def test_northbound_deduplication(repo):
    df1 = _northbound_df().iloc[:1].copy()
    df1["north_net_inflow"] = 10.5
    df2 = _northbound_df().iloc[:1].copy()
    df2["north_net_inflow"] = 99.9
    repo.upsert_northbound(df1)
    repo.upsert_northbound(df2)
    result = repo.load_northbound()
    assert len(result) == 1
    assert float(result.iloc[0]["north_net_inflow"]) == 99.9
```

- [ ] **Step 2：运行测试验证失败**

```bash
.venv/bin/pytest tests/test_dal_raw_repo.py -v
```

预期：`ImportError: cannot import name 'RawRepo'`

- [ ] **Step 3：创建 src/dal/raw_repo.py（kline + northbound 部分）**

```python
"""RawRepo：RAW 层 7 张表的 CRUD"""
from __future__ import annotations

from datetime import date

import duckdb
import pandas as pd


class RawRepo:
    def __init__(self, conn: duckdb.DuckDBPyConnection | None = None) -> None:
        if conn is not None:
            self._conn = conn
        else:
            from src.dal.connection import get_db
            self._conn = get_db()

    def _insert_or_replace(self, table: str, cols: list[str], df: pd.DataFrame) -> int:
        col_str = ", ".join(cols)
        self._conn.register("_tmp", df)
        self._conn.execute(f"INSERT OR REPLACE INTO {table} SELECT {col_str} FROM _tmp")
        self._conn.unregister("_tmp")
        return len(df)

    # ── kline ──────────────────────────────────────────────────────────────────

    def upsert_kline(self, df: pd.DataFrame) -> int:
        return self._insert_or_replace(
            "kline",
            ["date", "code", "open", "high", "low", "close", "amount", "volume"],
            df,
        )

    def load_kline(self, code: str, since: date | None = None) -> pd.DataFrame:
        if since is not None:
            return self._conn.execute(
                "SELECT date, code, open, high, low, close, amount, volume "
                "FROM kline WHERE code = ? AND date > ? ORDER BY date",
                [code, since],
            ).df()
        return self._conn.execute(
            "SELECT date, code, open, high, low, close, amount, volume "
            "FROM kline WHERE code = ? ORDER BY date",
            [code],
        ).df()

    # ── northbound ────────────────────────────────────────────────────────────

    def upsert_northbound(self, df: pd.DataFrame) -> int:
        return self._insert_or_replace(
            "northbound",
            ["date", "north_net_inflow", "hgt_yi", "sgt_yi"],
            df,
        )

    def load_northbound(self, since: date | None = None) -> pd.DataFrame:
        if since is not None:
            return self._conn.execute(
                "SELECT date, north_net_inflow, hgt_yi, sgt_yi "
                "FROM northbound WHERE date > ? ORDER BY date",
                [since],
            ).df()
        return self._conn.execute(
            "SELECT date, north_net_inflow, hgt_yi, sgt_yi FROM northbound ORDER BY date"
        ).df()
```

- [ ] **Step 4：运行测试验证通过**

```bash
.venv/bin/pytest tests/test_dal_raw_repo.py -v
```

预期：`8 passed`

- [ ] **Step 5：提交**

```bash
git add src/dal/raw_repo.py tests/test_dal_raw_repo.py
git commit -m "feat(dal): RawRepo kline + northbound CRUD"
```

---

## Task 5：RawRepo（fundamentals / fund_flow / lhb / reports / eps_snapshot）+ 测试

**Files:**
- Modify: `src/dal/raw_repo.py`（追加 10 个方法）
- Modify: `tests/test_dal_raw_repo.py`（追加测试）

- [ ] **Step 1：追加失败测试到 tests/test_dal_raw_repo.py**

在文件末尾追加：

```python
# ── fundamentals ──────────────────────────────────────────────────────────────

def _fundamentals_df(code: str = "000001") -> pd.DataFrame:
    return pd.DataFrame({
        "date": pd.to_datetime(["2024-01-02", "2024-01-03"]),
        "code": code,
        "pe_ttm": [12.5, 12.8],
        "pe_static": [13.0, 13.2],
        "pb": [1.2, 1.25],
        "ps": [2.0, 2.1],
        "pcf": [8.0, 8.2],
        "peg": [0.9, 0.92],
        "market_cap": [2e11, 2.1e11],
        "float_market_cap": [1.9e11, 2.0e11],
        "total_shares": [int(1.7e10)] * 2,
        "float_shares": [int(1.65e10)] * 2,
    })


def test_upsert_and_load_fundamentals(repo):
    repo.upsert_fundamentals(_fundamentals_df())
    result = repo.load_fundamentals("000001")
    assert len(result) == 2
    assert "pe_ttm" in result.columns


def test_fundamentals_deduplication(repo):
    df1 = _fundamentals_df().iloc[:1].copy()
    df1["pe_ttm"] = 10.0
    df2 = _fundamentals_df().iloc[:1].copy()
    df2["pe_ttm"] = 20.0
    repo.upsert_fundamentals(df1)
    repo.upsert_fundamentals(df2)
    assert float(repo.load_fundamentals("000001").iloc[0]["pe_ttm"]) == 20.0


# ── fund_flow ─────────────────────────────────────────────────────────────────

def _fund_flow_df(code: str = "000001") -> pd.DataFrame:
    return pd.DataFrame({
        "date": pd.to_datetime(["2024-01-02", "2024-01-03"]),
        "code": code,
        "major_net_inflow": [1e7, -5e6],
        "major_net_pct": [2.5, -1.3],
    })


def test_upsert_and_load_fund_flow(repo):
    repo.upsert_fund_flow(_fund_flow_df())
    result = repo.load_fund_flow("000001")
    assert len(result) == 2
    assert "major_net_inflow" in result.columns


# ── lhb ───────────────────────────────────────────────────────────────────────

def _lhb_df() -> pd.DataFrame:
    return pd.DataFrame({
        "date": pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-03"]),
        "code": ["000001", "000001", "000002"],
        "lhb_net_buy": [1e7, -3e6, 5e6],
        "lhb_buy_amount": [2e7, 1e7, 8e6],
        "lhb_sell_amount": [1e7, 1.3e7, 3e6],
    })


def test_upsert_and_load_lhb(repo):
    repo.upsert_lhb(_lhb_df())
    result = repo.load_lhb("000001")
    assert len(result) == 2


def test_load_lhb_since_filter(repo):
    repo.upsert_lhb(_lhb_df())
    result = repo.load_lhb("000001", since=date(2024, 1, 2))
    assert len(result) == 1  # 只返回 2024-01-03


# ── reports ───────────────────────────────────────────────────────────────────

def _reports_df(code: str = "000001") -> pd.DataFrame:
    return pd.DataFrame({
        "date": pd.to_datetime(["2024-01-02", "2024-01-02", "2024-01-03"]),
        "code": code,
        "institution": ["机构A", "机构B", "机构A"],
        "rating": ["买入", "增持", "买入"],
    })


def test_upsert_and_load_reports(repo):
    repo.upsert_reports(_reports_df())
    result = repo.load_reports("000001")
    assert len(result) == 3


def test_reports_three_column_pk_dedup(repo):
    df1 = _reports_df().iloc[:1].copy()
    df1["rating"] = "买入"
    df2 = _reports_df().iloc[:1].copy()
    df2["rating"] = "卖出"
    repo.upsert_reports(df1)
    repo.upsert_reports(df2)
    result = repo.load_reports("000001")
    assert len(result) == 1
    assert result.iloc[0]["rating"] == "卖出"


# ── eps_snapshot ──────────────────────────────────────────────────────────────

def _eps_df(code: str = "000001") -> pd.DataFrame:
    return pd.DataFrame({
        "snapshot_date": pd.to_datetime(["2024-04-30", "2024-05-31"]),
        "code": code,
        "eps_cur": [2.10, 2.17],
        "eps_next": [2.20, 2.24],
        "analyst_count": [18, 20],
    })


def test_upsert_and_load_eps_snapshots(repo):
    repo.upsert_eps_snapshot(_eps_df())
    result = repo.load_eps_snapshots("000001")
    assert len(result) == 2
    assert "eps_cur" in result.columns


def test_eps_snapshot_deduplication(repo):
    df1 = _eps_df().iloc[:1].copy()
    df1["eps_cur"] = 2.00
    df2 = _eps_df().iloc[:1].copy()
    df2["eps_cur"] = 2.10
    repo.upsert_eps_snapshot(df1)
    repo.upsert_eps_snapshot(df2)
    result = repo.load_eps_snapshots("000001")
    assert len(result) == 1
    assert float(result.iloc[0]["eps_cur"]) == 2.10
```

- [ ] **Step 2：运行测试验证失败**

```bash
.venv/bin/pytest tests/test_dal_raw_repo.py -v -k "fundamentals or fund_flow or lhb or reports or eps"
```

预期：`AttributeError: 'RawRepo' object has no attribute 'upsert_fundamentals'`

- [ ] **Step 3：在 raw_repo.py 末尾追加 10 个方法**

在 `RawRepo` 类内 `load_northbound` 之后追加：

```python
    # ── fundamentals ──────────────────────────────────────────────────────────

    def upsert_fundamentals(self, df: pd.DataFrame) -> int:
        return self._insert_or_replace(
            "fundamentals",
            ["date", "code", "pe_ttm", "pe_static", "pb", "ps", "pcf", "peg",
             "market_cap", "float_market_cap", "total_shares", "float_shares"],
            df,
        )

    def load_fundamentals(self, code: str) -> pd.DataFrame:
        return self._conn.execute(
            "SELECT * FROM fundamentals WHERE code = ? ORDER BY date", [code]
        ).df()

    # ── fund_flow ─────────────────────────────────────────────────────────────

    def upsert_fund_flow(self, df: pd.DataFrame) -> int:
        return self._insert_or_replace(
            "fund_flow",
            ["date", "code", "major_net_inflow", "major_net_pct"],
            df,
        )

    def load_fund_flow(self, code: str) -> pd.DataFrame:
        return self._conn.execute(
            "SELECT * FROM fund_flow WHERE code = ? ORDER BY date", [code]
        ).df()

    # ── lhb ───────────────────────────────────────────────────────────────────

    def upsert_lhb(self, df: pd.DataFrame) -> int:
        return self._insert_or_replace(
            "lhb",
            ["date", "code", "lhb_net_buy", "lhb_buy_amount", "lhb_sell_amount"],
            df,
        )

    def load_lhb(self, code: str, since: date | None = None) -> pd.DataFrame:
        if since is not None:
            return self._conn.execute(
                "SELECT * FROM lhb WHERE code = ? AND date > ? ORDER BY date",
                [code, since],
            ).df()
        return self._conn.execute(
            "SELECT * FROM lhb WHERE code = ? ORDER BY date", [code]
        ).df()

    # ── reports ───────────────────────────────────────────────────────────────

    def upsert_reports(self, df: pd.DataFrame) -> int:
        return self._insert_or_replace(
            "reports",
            ["date", "code", "institution", "rating"],
            df,
        )

    def load_reports(self, code: str) -> pd.DataFrame:
        return self._conn.execute(
            "SELECT * FROM reports WHERE code = ? ORDER BY date", [code]
        ).df()

    # ── eps_snapshot ──────────────────────────────────────────────────────────

    def upsert_eps_snapshot(self, df: pd.DataFrame) -> int:
        return self._insert_or_replace(
            "eps_snapshot",
            ["snapshot_date", "code", "eps_cur", "eps_next", "analyst_count"],
            df,
        )

    def load_eps_snapshots(self, code: str) -> pd.DataFrame:
        return self._conn.execute(
            "SELECT * FROM eps_snapshot WHERE code = ? ORDER BY snapshot_date", [code]
        ).df()
```

- [ ] **Step 4：运行全部 RawRepo 测试**

```bash
.venv/bin/pytest tests/test_dal_raw_repo.py -v
```

预期：`17 passed`

- [ ] **Step 5：提交**

```bash
git add src/dal/raw_repo.py tests/test_dal_raw_repo.py
git commit -m "feat(dal): RawRepo 全部 7 张 RAW 表 CRUD 完成"
```

---

## Task 6：FeatureRepo + 测试

**Files:**
- Create: `src/dal/feature_repo.py`
- Create: `tests/test_dal_feature_repo.py`

- [ ] **Step 1：写失败测试**

新建 `tests/test_dal_feature_repo.py`：

```python
"""测试 FeatureRepo（features 表读写）"""
import sys
from datetime import date
from pathlib import Path

import duckdb
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.dal.schema import migrate
from src.dal.feature_repo import FeatureRepo


@pytest.fixture
def repo():
    conn = duckdb.connect(":memory:")
    migrate(conn)
    yield FeatureRepo(conn)
    conn.close()


def _features_df(n: int = 5, start: str = "2024-01-02") -> pd.DataFrame:
    dates = pd.date_range(start, periods=n, freq="B")
    codes = ["000001"] * n
    return pd.DataFrame({
        "date": dates,
        "code": codes,
        "ma5_ratio": [1.01] * n,
        "rsi14": [55.0] * n,
        "label": [1] * n,
    })


def test_get_feature_date_range_returns_none_when_empty(repo):
    assert repo.get_feature_date_range() is None


def test_upsert_features_returns_row_count(repo):
    df = _features_df(5)
    assert repo.upsert_features(df) == 5


def test_upsert_features_adds_columns_dynamically(repo):
    repo.upsert_features(_features_df())
    cols = {row[0] for row in repo._conn.execute("DESCRIBE features").fetchall()}
    assert "ma5_ratio" in cols
    assert "rsi14" in cols
    assert "label" in cols


def test_load_features_date_range(repo):
    repo.upsert_features(_features_df(10, start="2024-01-02"))
    result = repo.load_features(date(2024, 1, 5), date(2024, 1, 12))
    assert len(result) > 0
    assert result["date"].min() >= pd.Timestamp("2024-01-05")
    assert result["date"].max() <= pd.Timestamp("2024-01-12")


def test_load_features_code_filter(repo):
    df1 = _features_df(3)
    df2 = _features_df(3)
    df2["code"] = "000002"
    repo.upsert_features(pd.concat([df1, df2], ignore_index=True))
    result = repo.load_features(date(2024, 1, 1), date(2025, 1, 1), codes=["000001"])
    assert set(result["code"].unique()) == {"000001"}


def test_upsert_features_deduplication(repo):
    df1 = _features_df(1)
    df1["rsi14"] = 50.0
    df2 = _features_df(1)
    df2["rsi14"] = 80.0
    repo.upsert_features(df1)
    repo.upsert_features(df2)
    result = repo.load_features(date(2024, 1, 1), date(2025, 1, 1))
    assert len(result) == 1
    assert float(result.iloc[0]["rsi14"]) == 80.0


def test_get_feature_date_range_returns_min_max(repo):
    repo.upsert_features(_features_df(5, start="2024-01-02"))
    range_ = repo.get_feature_date_range()
    assert range_ is not None
    d_min, d_max = range_
    assert d_min <= d_max
```

- [ ] **Step 2：运行测试验证失败**

```bash
.venv/bin/pytest tests/test_dal_feature_repo.py -v
```

预期：`ImportError: cannot import name 'FeatureRepo'`

- [ ] **Step 3：创建 src/dal/feature_repo.py**

```python
"""FeatureRepo：预计算特征表（features）读写"""
from __future__ import annotations

from datetime import date

import duckdb
import pandas as pd


class FeatureRepo:
    def __init__(self, conn: duckdb.DuckDBPyConnection | None = None) -> None:
        if conn is not None:
            self._conn = conn
        else:
            from src.dal.connection import get_db
            self._conn = get_db()

    def upsert_features(self, df: pd.DataFrame) -> int:
        """写入特征数据；新列自动追加到 features 表 schema。"""
        existing = {row[0] for row in self._conn.execute("DESCRIBE features").fetchall()}
        for col in df.columns:
            if col not in existing:
                dtype = "INTEGER" if col == "label" else "DOUBLE"
                self._conn.execute(f'ALTER TABLE features ADD COLUMN "{col}" {dtype}')
        self._conn.register("_feat_tmp", df)
        cols = ", ".join(f'"{c}"' for c in df.columns)
        self._conn.execute(f"INSERT OR REPLACE INTO features SELECT {cols} FROM _feat_tmp")
        self._conn.unregister("_feat_tmp")
        return len(df)

    def load_features(
        self,
        date_from: date,
        date_to: date,
        codes: list[str] | None = None,
    ) -> pd.DataFrame:
        """按时间范围（+可选股票列表）读取特征，供 trainer / backtest 使用。"""
        if codes is not None:
            placeholders = ", ".join("?" for _ in codes)
            return self._conn.execute(
                f"SELECT * FROM features WHERE date >= ? AND date <= ? "
                f"AND code IN ({placeholders}) ORDER BY date, code",
                [date_from, date_to, *codes],
            ).df()
        return self._conn.execute(
            "SELECT * FROM features WHERE date >= ? AND date <= ? ORDER BY date, code",
            [date_from, date_to],
        ).df()

    def get_feature_date_range(self) -> tuple[date, date] | None:
        """返回 features 表中最早/最晚日期；表为空时返回 None。"""
        row = self._conn.execute("SELECT MIN(date), MAX(date) FROM features").fetchone()
        if row is None or row[0] is None:
            return None
        d_min, d_max = row
        to_date = lambda v: v if isinstance(v, date) else v.date()
        return (to_date(d_min), to_date(d_max))
```

- [ ] **Step 4：运行测试验证通过**

```bash
.venv/bin/pytest tests/test_dal_feature_repo.py -v
```

预期：`7 passed`

- [ ] **Step 5：提交**

```bash
git add src/dal/feature_repo.py tests/test_dal_feature_repo.py
git commit -m "feat(dal): FeatureRepo（动态列写入 + 范围查询）"
```

---

## Task 7：dal/__init__.py + 全量测试验收

**Files:**
- Create: `src/dal/__init__.py`

- [ ] **Step 1：创建 src/dal/__init__.py**

```python
"""DAL 包：统一对外接口"""
from src.dal.connection import get_db
from src.dal.schema import migrate
from src.dal.raw_repo import RawRepo
from src.dal.feature_repo import FeatureRepo
from src.dal.meta_repo import MetaRepo

__all__ = ["get_db", "migrate", "RawRepo", "FeatureRepo", "MetaRepo"]
```

- [ ] **Step 2：运行全量 DAL 测试**

```bash
.venv/bin/pytest tests/test_dal_schema.py tests/test_dal_meta_repo.py \
    tests/test_dal_raw_repo.py tests/test_dal_feature_repo.py -v
```

预期：`33 passed`（schema 4 + meta 5 + raw 17 + feature 7）

- [ ] **Step 3：验证包导入正常**

```bash
.venv/bin/python -c "
from src.dal import get_db, migrate, RawRepo, FeatureRepo, MetaRepo
import duckdb
conn = duckdb.connect(':memory:')
migrate(conn)
r = RawRepo(conn)
f = FeatureRepo(conn)
m = MetaRepo(conn)
print('dal import OK')
"
```

预期输出：`dal import OK`

- [ ] **Step 4：提交**

```bash
git add src/dal/__init__.py
git commit -m "feat(dal): Sub-1 完成——DB Schema + DAL 全部实现"
```
