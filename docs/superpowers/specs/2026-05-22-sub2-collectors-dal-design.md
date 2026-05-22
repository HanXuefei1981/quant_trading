# Sub-2：采集器改写为 DAL 写入 设计文档

## 背景与目标

Sub-1 已完成 DuckDB DAL 层（`RawRepo`、`FeatureRepo`、`MetaRepo`），建立了 9 张表的 Schema。Sub-2 的目标是将所有采集器的**写入路径**从 Parquet 文件改为调用 DAL，使新增数据直接持久化到 `quant.duckdb`。

旧 Parquet 文件保留不删除，但采集器不再写入 Parquet。历史数据**不迁移**——采集器从当下时点起写入 DAL，历史数据缺口由后续补采自然填充。

---

## 架构决策

### BaseCollector 接口重设计

现有接口（`fetch_one / fetch_all / load`）为 Parquet 文件设计，与 DAL 模式不匹配。采集器是**内部模块**，无公开 API 约束，可以彻底重设计。

**新接口：单一 `collect()` 方法**

```python
class BaseCollector(ABC):
    @abstractmethod
    def collect(self, codes: list[str] = [], since: date | None = None) -> CollectStats:
        """执行采集并写入 DAL。

        codes: 空列表 = 全市场（仅市场级采集器有效）。
        since: 覆盖增量起点；None 时采集器自行从 MetaRepo 查询上次日期。
        """
```

`CollectStats` 保持不变（ok / fail / cached / skipped + total）。

旧方法（`fetch_one / fetch_all / load / load_market`）全部移除。

### 两种采集粒度

| 粒度 | 采集器 | 写入表 | MetaRepo scope |
|------|--------|--------|----------------|
| 逐码（股票级） | TDX、Fundamental、FundFlow、Report、EPS、Tencent | kline / fundamentals / fund_flow / reports / eps_snapshot / tencent | `code`（如 `"000001"`） |
| 市场级 | Northbound、LHB（Signal） | northbound / lhb | `"__market__"` |

逐码采集器在 `collect()` 内循环各 `code`，为每个 code 调用 `meta_repo.get_last_date(table, code)` 确定增量起点，写入后调用 `meta_repo.set_last_date(table, code, last_date)`。

市场级采集器在 `collect()` 内调用 `meta_repo.get_last_date(table, "__market__")` 确定增量起点。

### 构造函数注入

所有采集器均支持 `raw_repo` 和 `meta_repo` 构造注入，方便测试时传入内存数据库实例：

```python
def __init__(self, raw_repo: RawRepo | None = None, meta_repo: MetaRepo | None = None): ...
```

`None` 时内部调用全局 `get_db()` 创建默认连接。

---

## tencent 表

TencentCollector 采集的字段（`pe_ttm, pe_static, pb, turnover_pct, mcap_yi, float_mcap_yi, price`）与现有 `fundamentals` 表不同，需要新建独立表。

### Schema

```sql
CREATE TABLE IF NOT EXISTS tencent (
    date             DATE    NOT NULL,
    code             VARCHAR NOT NULL,
    pe_ttm           DOUBLE,
    pe_static        DOUBLE,
    pb               DOUBLE,
    turnover_pct     DOUBLE,
    mcap_yi          DOUBLE,
    float_mcap_yi    DOUBLE,
    price            DOUBLE,
    PRIMARY KEY (date, code)
);
```

单位与腾讯财经原始字段一致：市值为亿元（`_yi`），换手率为百分比（`turnover_pct`）。

`schema.py` 的 `migrate()` 函数需新增此表的 `CREATE TABLE IF NOT EXISTS` 语句。

`RawRepo` 需新增 `upsert_tencent(df) -> int` 和 `load_tencent(code, since=None) -> pd.DataFrame`。

---

## src/data 模块修改

`src/data/fundamentals.py` 和 `src/data/fund_flow.py` 中的 `fetch_*()` 函数在拉取数据后会调用 `df.to_parquet(cache_path)` 写入本地文件。Sub-2 需要**移除这两处 Parquet 写入**，让 `fetch_*()` 仅返回 DataFrame，由采集器层负责调用 DAL 写入。

`load_*()` 函数保持不变，留待 Sub-3 统一迁移读路径。

`src/data/watermark.py` 保持现状（deprecated），不在 Sub-2 中删除。

---

## 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/dal/schema.py` | 修改 | 新增 `tencent` 表 DDL |
| `src/dal/raw_repo.py` | 修改 | 新增 `upsert_tencent` / `load_tencent` |
| `src/collectors/base.py` | 修改 | 移除旧接口，仅保留 `collect()` 抽象方法 |
| `src/collectors/tdx_collector.py` | 修改 | 实现 `collect()`，写 `RawRepo.upsert_kline` |
| `src/collectors/fundamental_collector.py` | 修改 | 实现 `collect()`，写 `RawRepo.upsert_fundamentals` |
| `src/collectors/fund_flow_collector.py` | 修改 | 实现 `collect()`，写 `RawRepo.upsert_fund_flow` |
| `src/collectors/northbound_collector.py` | 修改 | 实现 `collect()`，写 `RawRepo.upsert_northbound` |
| `src/collectors/signal_collector.py` | 修改 | 实现 `collect()`，写 `RawRepo.upsert_lhb` |
| `src/collectors/report_collector.py` | 修改 | 实现 `collect()`，写 `RawRepo.upsert_reports` + `upsert_eps_snapshot` |
| `src/collectors/tencent_collector.py` | 修改 | 实现 `collect()`，写 `RawRepo.upsert_tencent` |
| `src/data/fundamentals.py` | 修改 | 移除 `fetch_fundamentals()` 内的 Parquet 写入 |
| `src/data/fund_flow.py` | 修改 | 移除 `fetch_fund_flow()` 内的 Parquet 写入 |
| `tests/test_dal_raw_repo_tencent.py` | 新建 | tencent 表单元测试 |
| `tests/test_collector_tdx.py` | 新建 | TDX 采集器测试 |
| `tests/test_collector_fundamental.py` | 新建 | Fundamental 采集器测试 |
| `tests/test_collector_fund_flow.py` | 新建 | FundFlow 采集器测试 |
| `tests/test_collector_northbound.py` | 新建 | Northbound 采集器测试 |
| `tests/test_collector_signal.py` | 新建 | Signal（LHB）采集器测试 |
| `tests/test_collector_report.py` | 新建 | Report 采集器测试 |
| `tests/test_collector_tencent.py` | 新建 | Tencent 采集器测试 |

---

## 测试策略

- **网络调用全部 mock**：`unittest.mock.patch` 替换 `_fetch_batch`、akshare 函数、mootdx API 等。
- **DAL 使用内存库**：`duckdb.connect(":memory:")` + `migrate(conn)` 初始化 Schema，通过构造函数注入。
- **每个采集器 3 个测试场景**：
  1. 首次全量采集：MetaRepo 无历史，`since=None` → 写入所有行，MetaRepo 更新。
  2. 增量采集：MetaRepo 有历史日期 → 只拉取并写入新数据，旧数据不重复。
  3. 网络失败：mock 抛出异常 → `stats.fail` 正确计数，MetaRepo 不更新。
- **tencent 表 DAL 测试** 与其余表测试风格一致（upsert + load + date range）。
