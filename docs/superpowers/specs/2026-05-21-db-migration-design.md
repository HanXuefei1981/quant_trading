# 量化交易系统数据库化改造设计（Sub-1：DB Schema + DAL）

## 背景与目标

当前系统以 Parquet 文件树作为唯一存储层（约 11GB，分散在 `data/raw/`、`data/fundamentals/`、`data/fund_flow/`、`data/processed/` 下），导致以下问题：

- 采集器与训练/回测业务通过文件路径直接耦合，任何目录结构变化都会破坏下游
- 增量采集进度分散在 `watermark.json` 和各采集器内部缓存，无统一查询接口
- 特征表 `market_features.parquet`（3.5GB）每次重建需全量读写，无法按需加载

**目标**：引入 DuckDB 作为嵌入式数据库，建立统一的数据访问层（DAL），实现采集层与业务层的真正分离。

本文档覆盖 **Sub-1**：DB Schema 定义 + DAL 模块实现。后续 Sub-2（采集器写入 DB）和 Sub-3（Assembler/Trainer/Backtest 读取 DB）将基于本文档的接口展开。

---

## 架构决策

| 决策项 | 选择 | 理由 |
|--------|------|------|
| 数据库 | DuckDB（`data/quant.duckdb`） | 嵌入式、无服务进程、原生 Parquet 支持、列式存储适合分析查询 |
| 迁移策略 | 完全替换 Parquet 文件树 | DB 为唯一数据源，避免双写维护负担 |
| 特征存储 | RAW 层 + FEATURE 层同时存储 | Assembler 计算后写入 features 表，Trainer/Backtest 直接读取，不重复计算 |
| 访问方式 | Repository 模式（DAL） | 所有业务层只调用 DAL 接口，不直接操作 DuckDB 连接 |

---

## 数据库结构总览

```
data/quant.duckdb
├── RAW 层（7 张表）
│   ├── kline           ← data/raw/kline/{code}.parquet（5644 文件，269MB）
│   ├── fundamentals    ← data/fundamentals/{code}.parquet（599MB）
│   ├── fund_flow       ← data/fund_flow/{code}.parquet（4.5MB）
│   ├── northbound      ← data/raw/northbound.parquet（44KB）
│   ├── lhb             ← data/raw/lhb/{date}.parquet（933 文件，7.2MB）
│   ├── reports         ← data/raw/reports/{code}.parquet（18MB）
│   └── eps_snapshot    ← data/raw/eps/{code}.parquet（11MB）
├── FEATURE 层（1 张表）
│   └── features        ← data/processed/*.parquet + market_features.parquet（~11GB）
└── META 层（1 张表）
    └── collect_log     ← watermark.json + 各采集器增量缓存
```

---

## Schema 定义

### RAW 层

```sql
-- 1. K 线（通达信日线，前复权）
CREATE TABLE kline (
    date    DATE    NOT NULL,
    code    VARCHAR NOT NULL,
    open    DOUBLE,
    high    DOUBLE,
    low     DOUBLE,
    close   DOUBLE,
    amount  DOUBLE,
    volume  BIGINT,
    PRIMARY KEY (date, code)
);
CREATE INDEX idx_kline_code ON kline (code);

-- 2. 基本面（东方财富估值）
CREATE TABLE fundamentals (
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
);
CREATE INDEX idx_fundamentals_code ON fundamentals (code);

-- 3. 个股主力资金流向（东方财富）
CREATE TABLE fund_flow (
    date              DATE    NOT NULL,
    code              VARCHAR NOT NULL,
    major_net_inflow  DOUBLE,
    major_net_pct     DOUBLE,
    PRIMARY KEY (date, code)
);
CREATE INDEX idx_fund_flow_code ON fund_flow (code);

-- 4. 北向资金（市场级，无 code 列）
CREATE TABLE northbound (
    date              DATE   NOT NULL PRIMARY KEY,
    north_net_inflow  DOUBLE,
    hgt_yi            DOUBLE,
    sgt_yi            DOUBLE
);

-- 5. 龙虎榜（按日期存储的市场快照）
CREATE TABLE lhb (
    date             DATE    NOT NULL,
    code             VARCHAR NOT NULL,
    lhb_net_buy      DOUBLE,
    lhb_buy_amount   DOUBLE,
    lhb_sell_amount  DOUBLE,
    PRIMARY KEY (date, code)
);
CREATE INDEX idx_lhb_code ON lhb (code);

-- 6. 研报列表（东方财富）
CREATE TABLE reports (
    date         DATE    NOT NULL,
    code         VARCHAR NOT NULL,
    institution  VARCHAR NOT NULL,
    rating       VARCHAR,
    PRIMARY KEY (date, code, institution)
);
CREATE INDEX idx_reports_code ON reports (code);

-- 7. EPS 共识快照（同花顺，每月采集一次）
CREATE TABLE eps_snapshot (
    snapshot_date  DATE    NOT NULL,
    code           VARCHAR NOT NULL,
    eps_cur        DOUBLE,
    eps_next       DOUBLE,
    analyst_count  INTEGER,
    PRIMARY KEY (snapshot_date, code)
);
CREATE INDEX idx_eps_code ON eps_snapshot (code);
```

### FEATURE 层

```sql
-- 8. 预计算特征表（Assembler 写入，Trainer/Backtest 读取）
--    列定义在运行时由 assembler 动态写入，约 100+ 个数值列
CREATE TABLE features (
    date  DATE    NOT NULL,
    code  VARCHAR NOT NULL,
    PRIMARY KEY (date, code)
);
CREATE INDEX idx_features_date ON features (date);
CREATE INDEX idx_features_code ON features (code);
```

> 注：features 表的业务列（ma5、rsi14、label 等）在 `schema.py` 的 `migrate()` 中动态生成：
> 调用 `src.features.indicators.get_feature_columns()` 获取列名列表，为每列追加
> `DOUBLE` 类型定义，拼入 CREATE TABLE 语句。这样 schema 始终与 assembler 输出同步，
> 新增因子只需更新 `get_feature_columns()`，无需手动修改建表 SQL。

### META 层

```sql
-- 9. 采集进度记录（替换 watermark.json + 各采集器内部缓存）
CREATE TABLE collect_log (
    table_name  VARCHAR   NOT NULL,  -- 'kline'/'lhb'/'northbound' 等
    scope       VARCHAR   NOT NULL,  -- 股票代码 或 '__market__'（市场级数据）
    last_date   DATE,                -- 已采集到的最新交易日
    row_count   INTEGER,             -- 本次更新写入的行数
    updated_at  TIMESTAMP,           -- 最后一次更新时间
    status      VARCHAR,             -- 'ok' | 'partial' | 'failed'
    PRIMARY KEY (table_name, scope)
);
```

**使用示例**：
- `('kline', '000001', '2026-05-20', 1, '2026-05-21 08:00:00', 'ok')`
- `('northbound', '__market__', '2026-05-20', 1, '2026-05-21 08:00:00', 'ok')`
- `('lhb', '__market__', '2026-05-20', 75, '2026-05-21 08:00:00', 'ok')`

---

## DAL 模块结构

```
src/dal/
├── __init__.py       # 对外暴露 get_db(), RawRepo, FeatureRepo, MetaRepo
├── connection.py     # 单例连接管理
├── raw_repo.py       # RAW 层 CRUD
├── feature_repo.py   # FEATURE 层读写
├── meta_repo.py      # META 层（collect_log）
└── schema.py         # 建表 SQL + migrate() 入口
```

### connection.py

```python
import duckdb
from config.settings import DATA_DIR

_DB_PATH = DATA_DIR / "quant.duckdb"
_conn: duckdb.DuckDBPyConnection | None = None

def get_db() -> duckdb.DuckDBPyConnection:
    global _conn
    if _conn is None:
        _conn = duckdb.connect(str(_DB_PATH))
    return _conn
```

### schema.py

```python
def migrate() -> None:
    """创建所有表（已存在则跳过）。"""
    db = get_db()
    db.execute(CREATE_KLINE_SQL)
    db.execute(CREATE_FUNDAMENTALS_SQL)
    # ... 其余建表语句 ...
    db.execute(CREATE_COLLECT_LOG_SQL)
```

### RawRepo 接口（raw_repo.py）

```python
class RawRepo:
    # ── 写入（upsert：主键冲突时覆盖） ────────────────────────────────────────
    def upsert_kline(self, df: pd.DataFrame) -> int: ...
    def upsert_fundamentals(self, df: pd.DataFrame) -> int: ...
    def upsert_fund_flow(self, df: pd.DataFrame) -> int: ...
    def upsert_northbound(self, df: pd.DataFrame) -> int: ...
    def upsert_lhb(self, df: pd.DataFrame) -> int: ...
    def upsert_reports(self, df: pd.DataFrame) -> int: ...
    def upsert_eps_snapshot(self, df: pd.DataFrame) -> int: ...

    # ── 读取 ──────────────────────────────────────────────────────────────────
    def load_kline(self, code: str, since: date | None = None) -> pd.DataFrame: ...
    def load_fundamentals(self, code: str) -> pd.DataFrame: ...
    def load_fund_flow(self, code: str) -> pd.DataFrame: ...
    def load_northbound(self, since: date | None = None) -> pd.DataFrame: ...
    def load_lhb(self, code: str, since: date | None = None) -> pd.DataFrame: ...
    def load_reports(self, code: str) -> pd.DataFrame: ...
    def load_eps_snapshots(self, code: str) -> pd.DataFrame: ...
```

所有 `upsert_*` 统一使用 DuckDB 语法：
```sql
INSERT INTO {table} SELECT * FROM df_view
ON CONFLICT DO UPDATE SET col1 = EXCLUDED.col1, ...
```

### FeatureRepo 接口（feature_repo.py）

```python
class FeatureRepo:
    def upsert_features(self, df: pd.DataFrame) -> int:
        """批量写入预计算特征，主键冲突时覆盖。"""

    def load_features(
        self,
        date_from: date,
        date_to: date,
        codes: list[str] | None = None,
    ) -> pd.DataFrame:
        """按时间范围（+可选股票列表）读取特征，供 trainer 和 backtest 使用。"""

    def get_feature_date_range(self) -> tuple[date, date] | None:
        """返回 features 表覆盖的最早/最晚日期，None 表示表为空。"""
```

### MetaRepo 接口（meta_repo.py）

```python
class MetaRepo:
    def get_last_date(self, table_name: str, scope: str) -> date | None:
        """查询增量起点，None 表示尚无记录（需全量采集）。"""

    def set_last_date(
        self,
        table_name: str,
        scope: str,
        last_date: date,
        row_count: int = 0,
        status: str = "ok",
    ) -> None:
        """采集完成后写入/更新进度记录。"""
```

---

## 数据流

```
[采集层] ──写入──▶ RawRepo ──▶ quant.duckdb（RAW 层）
                                        │
                              [Assembler 读取 RawRepo]
                                        │
                              [Assembler 计算特征]
                                        │
                              FeatureRepo.upsert_features()
                                        │
                              quant.duckdb（FEATURE 层）
                                        │
                    ┌───────────────────┤
                    ▼                   ▼
             Trainer                Backtest
       load_features()          load_features()
```

采集器增量逻辑统一简化为：
```python
since = meta_repo.get_last_date("kline", code)   # None → 全量
df = fetch_from_api(code, since)
raw_repo.upsert_kline(df)
meta_repo.set_last_date("kline", code, df["date"].max())
```

---

## 测试策略

每个 DAL 方法配套单元测试，使用内存数据库（`duckdb.connect(":memory:")`）隔离：

```python
@pytest.fixture
def db():
    conn = duckdb.connect(":memory:")
    migrate(conn)  # 建表
    return conn

def test_upsert_kline_deduplication(db):
    # 写入同一 (date, code) 两次，只保留最新一行
    ...

def test_load_kline_since_filter(db):
    # since 参数正确过滤日期范围
    ...

def test_meta_repo_get_last_date_returns_none_when_empty(db):
    ...
```

---

## 不在本文档范围内

- Sub-2：各采集器改写为调用 DAL 写入（替换 Parquet 落盘逻辑）
- Sub-3：Assembler / Trainer / Backtest 改写为调用 DAL 读取（替换 pd.read_parquet）
- 历史 Parquet 数据迁移脚本（`scripts/migrate_parquet_to_db.py`）
- DuckDB 版本锁定与依赖更新（`requirements.txt`）
