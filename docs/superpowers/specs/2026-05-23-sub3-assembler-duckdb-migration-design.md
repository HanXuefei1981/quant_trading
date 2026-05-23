# Sub-3 设计规格：features/assembler.py 读写路径迁移到 DuckDB DAL

日期：2026-05-23

## 背景

Sub-1 建立了 DuckDB DAL（RawRepo / FeatureRepo / MetaRepo）。
Sub-2 将所有 7 个 collector 的写入路径从 Parquet 迁移到 DuckDB。
Sub-3 完成闭环：将特征工程层的读写路径也全部迁移到 DuckDB，彻底告别 Parquet 文件。

## 目标

- 读取路径：`assembler.py` 和 `report.py` 从 `RawRepo` 读原始数据，不再依赖 `data/raw/` 下任何 Parquet 文件
- 写入路径：特征计算结果写入 `FeatureRepo`，不再写 `data/processed/*.parquet` 和 `market_features.parquet`
- 水位：`features` 水位从硬编码的 `watermark.py` 字典迁移到 `MetaRepo`（`collect_log` 表），无任何硬编码日期
- 下游：`scripts/g1_*`、`scripts/g2_*`、`scripts/collect_m3_data.py` 从 `FeatureRepo` 读取，不再依赖 Parquet 文件
- 增量模式与 collector 端保持统一模式，幂等可重跑

## 范围外

- `signal.py`：接收已加载的 DataFrame，不直接读文件，无需改动
- `watermark.py` 文件本身：只移除 `"features"` 键的使用，不删除文件（其他键可能仍在用）
- `src/models/trainer.py`：trainer 通过 `main.py` 接收传参，不直接读 Parquet，无需改动

## 数据更新模式：全量 + 每日增量

系统所有 DuckDB 表统一遵循「首次全量、此后每日增量」模式：

| 表 | Collector 写入 | 增量判断依据 | load 方法 since 支持 |
|---|---|---|---|
| kline | TdxCollector，per code watermark | MetaRepo kline/{code} | ✅ `load_kline(code, since)` |
| northbound | NorthboundCollector，market watermark | MetaRepo northbound/\_\_market\_\_ | ✅ `load_northbound(since)` |
| lhb | SignalCollector，market watermark | MetaRepo lhb/\_\_market\_\_ | ✅ `load_all_lhb(since)`（B1 新增）|
| fundamentals | FundamentalCollector，per code watermark | MetaRepo fundamentals/{code} | ✅ `load_fundamentals(code, since)`（B1 新增）|
| fund_flow | FundFlowCollector，per code watermark | MetaRepo fund_flow/{code} | ✅ `load_fund_flow(code, since)`（B1 新增）|
| reports | ReportCollector，per code watermark | MetaRepo reports/{code} | ✅ `load_reports(code, since)`（B1 新增）|
| eps_snapshot | ReportCollector，per code watermark | MetaRepo eps_snapshot/{code} | ✅ `load_eps_snapshots(code, since)`（B1 新增）|
| fundamentals_snapshot | TencentCollector，每日快照 | MetaRepo fundamentals_snapshot/{code} | ✅ 已有 since（assembler 不使用此表）|
| **features** | assembler，market watermark | MetaRepo features/\_\_market\_\_ | FeatureRepo.load_features(date_from, date_to) |

assembler 增量模式读取所有表时，统一使用 `since=lookback_date`（`last_feature_date - 300天`），确保滚动窗口指标有足够历史，同时避免读取无关历史数据：

```
全量（首次运行）：
  MetaRepo.get_last_date("features", "__market__") → None
  → assemble()：所有 RawRepo 读取均不传 since（加载全部历史）
  → FeatureRepo.upsert_features(combined)
  → MetaRepo.set_last_date("features", "__market__", max_date)

每日增量：
  MetaRepo.get_last_date("features", "__market__") → last_date（如 2026-05-20）
  lookback_date = last_date - 300天
  → load_kline(code, since=lookback_date)
  → load_fundamentals(code, since=lookback_date)
  → load_fund_flow(code, since=lookback_date)
  → load_northbound(since=lookback_date)
  → load_all_lhb(since=lookback_date)
  → load_reports(code, since=lookback_date)        # 由 report.py 内部调用
  → load_eps_snapshots(code, since=lookback_date)  # 由 report.py 内部调用
  → 计算特征，过滤出 date > last_date 的新行
  → FeatureRepo.upsert_features(new_rows)           # ON CONFLICT 幂等
  → MetaRepo.set_last_date("features", "__market__", new_max_date)
```

## 任务分解（B1 → B2 → B3 → B4 → B5 → B6）

### B1：RawRepo — 补齐 since 参数 + load_all_lhb

**文件**：`src/dal/raw_repo.py`

**变更 1 — 新增 `load_all_lhb`**：

```python
def load_all_lhb(self, since: date | None = None) -> pd.DataFrame:
    """加载全市场龙虎榜（供 assembler 一次性加载后按 code 过滤）。"""
    if since is not None:
        return self._conn.execute(
            "SELECT * FROM lhb WHERE date > ? ORDER BY date, code", [since]
        ).df()
    return self._conn.execute(
        "SELECT * FROM lhb ORDER BY date, code"
    ).df()
```

原因：`signal.py` 的 `_add_lhb_features()` 需要全市场 lhb DataFrame，现有 `load_lhb(code)` 只支持单股。

**变更 2 — 补齐 since 参数（4 个方法）**：

```python
def load_fundamentals(self, code: str, since: date | None = None) -> pd.DataFrame:
    if since is not None:
        return self._conn.execute(
            "SELECT * FROM fundamentals WHERE code = ? AND date > ? ORDER BY date",
            [code, since],
        ).df()
    return self._conn.execute(
        "SELECT * FROM fundamentals WHERE code = ? ORDER BY date", [code]
    ).df()

def load_fund_flow(self, code: str, since: date | None = None) -> pd.DataFrame:
    if since is not None:
        return self._conn.execute(
            "SELECT * FROM fund_flow WHERE code = ? AND date > ? ORDER BY date",
            [code, since],
        ).df()
    return self._conn.execute(
        "SELECT * FROM fund_flow WHERE code = ? ORDER BY date", [code]
    ).df()

def load_reports(self, code: str, since: date | None = None) -> pd.DataFrame:
    if since is not None:
        return self._conn.execute(
            "SELECT * FROM reports WHERE code = ? AND date > ? ORDER BY date",
            [code, since],
        ).df()
    return self._conn.execute(
        "SELECT * FROM reports WHERE code = ? ORDER BY date", [code]
    ).df()

def load_eps_snapshots(self, code: str, since: date | None = None) -> pd.DataFrame:
    if since is not None:
        return self._conn.execute(
            "SELECT * FROM eps_snapshot WHERE code = ? AND snapshot_date > ? ORDER BY snapshot_date",
            [code, since],
        ).df()
    return self._conn.execute(
        "SELECT * FROM eps_snapshot WHERE code = ? ORDER BY snapshot_date", [code]
    ).df()
```

**测试**：
- 每个方法：`since=None` 返回全部数据；`since=某日期` 只返回该日期之后的行
- `load_all_lhb`：空表返回空 DataFrame；有数据时按 date/code 排序；since 参数正确过滤

---

### B2：assembler.py 读取路径

**文件**：`src/features/assembler.py`

**函数签名变更**：

```python
def assemble(
    raw_repo: RawRepo | None = None,
    feature_repo: FeatureRepo | None = None,
    codes: list[str] | None = None,
    sample_size: int | None = None,
) -> pd.DataFrame: ...

def assemble_incremental(
    raw_repo: RawRepo | None = None,
    feature_repo: FeatureRepo | None = None,
    meta_repo: MetaRepo | None = None,
) -> pd.DataFrame: ...

def assemble_inference(
    feature_repo: FeatureRepo | None = None,
) -> pd.DataFrame: ...
```

`None` 时各自在函数内部通过 `get_db()` 创建，共享同一个连接。

**5 个私有函数替换（全量模式 since=None，增量模式 since=lookback_date）**：

| 旧实现 | 新实现（全量） | 新实现（增量，since=lookback_date）|
|--------|--------|--------|
| `pd.read_parquet(KLINE_DIR / f"{code}.parquet")` | `raw_repo.load_kline(code)` | `raw_repo.load_kline(code, since=lookback_date)` |
| `_load_fundamentals(code)` 读 Parquet | `raw_repo.load_fundamentals(code)` | `raw_repo.load_fundamentals(code, since=lookback_date)` |
| `_load_fund_flow(code)` 读 Parquet | `raw_repo.load_fund_flow(code)` | `raw_repo.load_fund_flow(code, since=lookback_date)` |
| `_load_northbound()` 读 Parquet | `raw_repo.load_northbound()` | `raw_repo.load_northbound(since=lookback_date)` |
| `_load_lhb_all()` glob + concat | `raw_repo.load_all_lhb()` | `raw_repo.load_all_lhb(since=lookback_date)` |

所有 5 个数据源在增量模式下统一传 `since=lookback_date`（`last_feature_date - 300天`），全量模式传 `since=None`。

**`_get_kline_codes()` 替换**：

```python
def _get_kline_codes(raw_repo: RawRepo) -> list[str]:
    return [r[0] for r in raw_repo._conn.execute(
        "SELECT DISTINCT code FROM kline ORDER BY code"
    ).fetchall()]
```

**增量路径优化**：`load_kline(code, since=lookback_date)` 的 `WHERE date > ?` 直接在 DuckDB 层过滤，省去文件级别 max-date 检查。其余 4 张表同样受益于 since 过滤，避免加载无关历史数据。

**删除**：
- 常量 `KLINE_DIR`、`FUNDAMENTALS_DIR`、`FUND_FLOW_DIR`、`NORTHBOUND_PATH`、`LHB_DIR`
- `assemble()` 的 `use_cache` 参数（Parquet 缓存语义消失，FeatureRepo upsert 本身幂等）
- `PROCESSED_DIR.mkdir()` 调用

**测试**：
- `_get_kline_codes()` 从 DuckDB 返回正确的代码列表
- `assemble()` 正确调用 RawRepo 各方法并写入 FeatureRepo
- 增量模式：lookback 窗口正确（since - 300天）；只写 date > since 的新行

---

### B3：report.py 读取路径

**文件**：`src/features/report.py`

**签名变更**：

```python
def add_report_features(
    df: pd.DataFrame,
    code: str,
    raw_repo: RawRepo | None = None,   # 替换 base_dir 参数
) -> pd.DataFrame: ...
```

**两个私有函数替换（均接收 since 参数，全量 None，增量传 lookback_date）**：

```python
def _load_reports(code: str, raw_repo: RawRepo, since: date | None = None) -> pd.DataFrame | None:
    df = raw_repo.load_reports(code, since=since)
    if df.empty:
        return None
    df["date"] = pd.to_datetime(df["date"])
    return df

def _load_eps(code: str, raw_repo: RawRepo, since: date | None = None) -> pd.DataFrame | None:
    df = raw_repo.load_eps_snapshots(code, since=since)   # 返回 snapshot_date 列，与现有逻辑一致
    if df.empty:
        return None
    df["snapshot_date"] = pd.to_datetime(df["snapshot_date"])
    return df.sort_values("snapshot_date").reset_index(drop=True)
```

`add_report_features()` 也对应添加 `since: date | None = None` 参数并透传给两个私有函数。全量模式传 `None`，增量模式由 `assembler.py` 传入 `lookback_date`。

**删除**：`base_dir` 参数及其默认值逻辑、`from config.settings import DATA_DIR` 的懒加载、`Path` 相关导入。

**调用方更新**：`assembler.py` 中调用 `add_report_features(df, code)` 改为 `add_report_features(df, code, raw_repo=raw_repo)`。

**测试**：
- `_load_reports` 空表返回 None；有数据时 date 列为 datetime 类型
- `_load_eps` 空表返回 None；snapshot_date 列为 datetime 类型且按日期升序
- `add_report_features` 正确左连接，缺失时填 NaN

---

### B4：assembler.py 写入路径

**文件**：`src/features/assembler.py`

**`assemble()` 写入变更**：
- 删除 `df.to_parquet(processed_path, index=False)` per-stock 写入
- 删除 `combined.to_parquet(out_path, index=False)` 市场汇总写入
- 末尾改为：`feature_repo.upsert_features(combined)`

**`assemble_incremental()` 写入变更**：
- 删除 per-stock Parquet append 逻辑（`existing = pd.read_parquet(processed_path)` 等）
- 删除 `market_features.parquet` append 逻辑
- 改为：`feature_repo.upsert_features(combined)`（ON CONFLICT 语义已在 FeatureRepo 保证幂等）

**`assemble_inference()` 完全重写**：

```python
def assemble_inference(feature_repo: FeatureRepo | None = None) -> pd.DataFrame:
    if feature_repo is None:
        feature_repo = FeatureRepo()
    date_range = feature_repo.get_feature_date_range()
    if date_range is None:
        raise FileNotFoundError("features 表为空，请先运行 Phase 1")
    latest_date = date_range[1]
    combined = feature_repo.load_features(latest_date, latest_date)
    if combined.empty:
        raise RuntimeError(f"特征截面 {latest_date} 无数据")
    logger.info(f"推断截面日期: {latest_date}，共 {len(combined)} 只股票")
    feature_cols = get_feature_columns(combined)
    return preprocess_features(combined, feature_cols)
```

**测试**：
- `assemble()` 写入 FeatureRepo 后，`FeatureRepo.load_features()` 可读回正确数据
- `assemble_incremental()` 幂等：重跑不产生重复行
- `assemble_inference()` 返回最新截面，columns 包含所有特征列

---

### B5：水位迁移到 MetaRepo

**文件**：`src/features/assembler.py`（`assemble_incremental()` 函数内部）

**替换**：

```python
# 旧
from src.data import watermark as wm
since = wm.get_since("features")
...
wm.update("features", new_max_date)

# 新
since = meta_repo.get_last_date("features", "__market__")  # None = 首次运行
...
meta_repo.set_last_date("features", "__market__", new_max_date, row_count=len(combined))
```

**`assemble_incremental()` 首次运行处理**（注意：全量完成后必须写水位再返回）：
```python
since = meta_repo.get_last_date("features", "__market__")
if since is None:
    logger.info("features 水位为空，执行全量组装")
    result = assemble(raw_repo=raw_repo, feature_repo=feature_repo)
    if not result.empty:
        new_max = result["date"].max().date()
        meta_repo.set_last_date("features", "__market__", new_max, row_count=len(result))
    return result
```

**`watermark.py` 改动**：移除 `"features"` 键及其注释。文件本身保留。

**测试**：
- 首次运行（水位 None）触发 `assemble()`
- 增量运行后 `MetaRepo.get_last_date("features", "__market__")` 返回正确日期
- 重复运行不会重复写入（幂等）

---

### B6：下游脚本迁移

**文件**：`scripts/g1_no_vol_features.py`、`scripts/g2_cross_sectional_label.py`、`scripts/collect_m3_data.py`

**g1_no_vol_features.py**：
```python
# 旧
df = pd.read_parquet(PROCESSED_DIR / "market_features.parquet")

# 新
from src.dal.feature_repo import FeatureRepo
repo = FeatureRepo()
date_range = repo.get_feature_date_range()
if date_range is None:
    raise FileNotFoundError("features 表为空")
df = repo.load_features(date_range[0], date_range[1])
```

**g2_cross_sectional_label.py**：
```python
# 旧：读 Parquet → 改写 label → to_parquet

# 新：读 FeatureRepo → 改写 label → upsert_features（只写 date/code/label 列）
repo = FeatureRepo()
date_range = repo.get_feature_date_range()
df = repo.load_features(date_range[0], date_range[1])
# ... 重算 label ...
repo.upsert_features(df[["date", "code", "label"]])
```

**collect_m3_data.py**：
```python
# 旧：pd.read_parquet(mf_path, columns=["date"])

# 新
from src.dal.feature_repo import FeatureRepo
repo = FeatureRepo()
date_range = repo.get_feature_date_range()
if date_range is None:
    logger.warning("features 表为空，使用 bdate_range 近似交易日")
    # fallback 逻辑保持不变
else:
    df_dates = repo.load_features(date_range[0], date_range[1])[["date"]].drop_duplicates()
```

**测试**：每个脚本能在有 FeatureRepo 数据的情况下正确执行，无 FileNotFoundError。

---

## 测试策略

每个任务的测试均使用 in-memory DuckDB（`duckdb.connect()`）预置种子数据，不依赖文件系统和实际 DB 路径。

- B1：`pytest tests/test_raw_repo_lhb.py`（新文件）
- B2：`pytest tests/test_assembler_read.py`（新文件）
- B3：`pytest tests/test_report_dal.py`（新文件，替换旧的 Parquet 测试）
- B4：`pytest tests/test_assembler_write.py`（新文件）
- B5：`pytest tests/test_assembler_incremental.py`（新文件）
- B6：各脚本 smoke test，验证无 import error 且 FeatureRepo 调用正确

## 成功标准

1. `python main.py collect` 后，`python main.py assemble` 能从 DuckDB 完成全量特征组装
2. 再次运行 `assemble` 触发增量模式，水位从 MetaRepo 读取，无硬编码日期
3. `scripts/g1_*`、`g2_*`、`collect_m3_data.py` 均能正常运行
4. `data/processed/` 目录不再被写入
5. 所有新增测试通过，覆盖率 ≥ 80%
