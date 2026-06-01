# Data Ingestion Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 hsjday.zip K 线数据写入 DuckDB，修复 phase2 读取来源，跑通完整训练流程（跳过 fund_flow）。

**Architecture:** `hsjday.zip → DuckDB kline → assembler → DuckDB features → trainer`。新增 `ingest` 子命令解析 hsjday.zip；修复 `phase1/phase2` 两处断链（错误的 assembler 调用 + 读取 parquet 而非 DuckDB）；所有历史数据通过 zip 文件一次性写入，日常增量走已有的 NorthboundCollector / SignalCollector。

**Tech Stack:** Python 3.11+, DuckDB, struct (stdlib), zipfile (stdlib), pandas, pytest

---

## 当前已知缺陷（必须全部修复）

1. `market_features.parquet` 被测试数据污染（仅 2 行）
2. `phase1` 调用 `assemble(use_cache=False)` — `assemble()` 无此参数，运行即 TypeError
3. `phase2` 调用 `load_processed_data()` 读 `market_features.parquet`，与 DuckDB FeatureRepo 断连
4. `collect` 命令调用 `tdx.fetch_all()` — `TDXCollector` 无此方法（遗留 stub）

---

## File Structure

| 操作 | 文件 | 职责 |
|------|------|------|
| **Create** | `src/data/ingest_zip.py` | 解析 hsjday.zip → DuckDB kline，纯本地操作 |
| **Create** | `tests/test_ingest_zip.py` | ingest_kline 单元 + 集成测试 |
| **Modify** | `main.py` | 新增 `ingest` 命令；修复 `phase1` (assembler 调用) + `phase2` (读取来源) |
| **Modify** | `src/data/pipeline.py` | 新增 `load_features_from_db()` |

---

## Task 1: 创建 hsjday.zip 解析器

**Files:**
- Create: `src/data/ingest_zip.py`
- Test: `tests/test_ingest_zip.py`

### 背景

`hsjday.zip` 内含 `sh/lday/sh*.day`、`sz/lday/sz*.day`、`bj/lday/bj*.day` 文件，每个文件一只股票，每条记录 32 字节：

```
offset 0:  uint32  date (YYYYMMDD)
offset 4:  uint32  open * 100
offset 8:  uint32  high * 100
offset 12: uint32  low  * 100
offset 16: uint32  close * 100
offset 20: float32 amount
offset 24: uint32  volume
offset 28: uint32  reserved
```

- [ ] **Step 1: 写失败测试**

```python
# tests/test_ingest_zip.py
import struct, zipfile, io, tempfile
from pathlib import Path
import duckdb, pandas as pd, pytest, sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.dal.schema import migrate
from src.dal.raw_repo import RawRepo
from src.data.ingest_zip import ingest_kline


def _make_day_bytes(records: list[tuple]) -> bytes:
    """records: [(date_int, open_i, high_i, low_i, close_i, amount_f, volume_i)]"""
    buf = bytearray()
    for d, o, h, l, c, a, v in records:
        buf += struct.pack('<IIIIIfII', d, o, h, l, c, a, v, 0)
    return bytes(buf)


@pytest.fixture
def fake_zip(tmp_path):
    """构造只含 2 只股票 (sh000001, sz000002) 各 3 条记录的测试 zip。"""
    sh_records = [
        (20240102, 300, 310, 295, 305, 1.5e9, 5000),
        (20240103, 305, 315, 300, 312, 1.6e9, 5200),
        (20240104, 312, 320, 308, 318, 1.7e9, 5400),
    ]
    sz_records = [
        (20240102, 100, 105, 98, 103, 5e8, 3000),
        (20240103, 103, 108, 100, 106, 5.2e8, 3100),
        (20240104, 106, 110, 104, 108, 5.4e8, 3200),
    ]
    zip_path = tmp_path / "test_hsjday.zip"
    with zipfile.ZipFile(zip_path, 'w') as zf:
        zf.writestr("sh/lday/sh000001.day", _make_day_bytes(sh_records))
        zf.writestr("sz/lday/sz000002.day", _make_day_bytes(sz_records))
    return zip_path


def test_ingest_kline_writes_all_rows(fake_zip):
    conn = duckdb.connect(":memory:")
    migrate(conn)
    raw_repo = RawRepo(conn)

    from src.data.ingest_zip import ingest_kline
    stats = ingest_kline(fake_zip, raw_repo)

    assert stats.ok == 2          # 2 只股票
    assert stats.fail == 0

    df1 = raw_repo.load_kline("000001")
    assert len(df1) == 3
    assert abs(df1.iloc[0]["close"] - 3.05) < 0.01   # 300/100

    df2 = raw_repo.load_kline("000002")
    assert len(df2) == 3


def test_ingest_kline_filters_zero_close(fake_zip, tmp_path):
    """zero-close 记录应被过滤。"""
    bad_records = [
        (20240102, 0, 0, 0, 0, 0.0, 0),   # 全零异常
        (20240103, 100, 105, 98, 103, 5e8, 3000),
    ]
    zip2 = tmp_path / "bad.zip"
    with zipfile.ZipFile(zip2, 'w') as zf:
        zf.writestr("sz/lday/sz999999.day", _make_day_bytes(bad_records))

    conn = duckdb.connect(":memory:")
    migrate(conn)
    raw_repo = RawRepo(conn)
    ingest_kline(zip2, raw_repo)

    df = raw_repo.load_kline("999999")
    assert len(df) == 1   # 只有 1 条有效记录


def test_ingest_kline_date_range(fake_zip):
    """START_DATE 过滤：只保留 >= 20210101 的记录（这里全部保留）。"""
    conn = duckdb.connect(":memory:")
    migrate(conn)
    raw_repo = RawRepo(conn)
    stats = ingest_kline(fake_zip, raw_repo)

    df = raw_repo.load_kline("000001")
    assert all(str(d)[:10] >= "2024-01-01" for d in df["date"])
```

- [ ] **Step 2: 运行确认测试失败**

```bash
cd ~/'7、AI 空间/7-3、GitHub/quant_trading'
python -m pytest tests/test_ingest_zip.py -v 2>&1 | head -20
```

Expected: `ImportError: cannot import name 'ingest_kline'`

- [ ] **Step 3: 实现 `src/data/ingest_zip.py`**

```python
"""hsjday.zip → DuckDB kline 直接解析器

解析规则：
  - 仅处理路径匹配 `*/lday/[sh|sz|bj]<6位数字>.day` 的文件
  - 每条记录 32 字节，date/open/high/low/close / amount / volume
  - 过滤 close == 0 的异常行
  - 过滤 date < START_DATE 的历史记录
  - 每处理 500 只股票批量写入 DuckDB（减少事务开销）
"""
from __future__ import annotations

import re
import struct
import zipfile
import logging
from pathlib import Path

import pandas as pd

from config.settings import START_DATE
from src.collectors.base import CollectStats
from src.dal.raw_repo import RawRepo

logger = logging.getLogger(__name__)

_RECORD_FMT = "<IIIIIfII"
_RECORD_SIZE = struct.calcsize(_RECORD_FMT)   # 32 bytes
_DAY_PATTERN = re.compile(r'(?:sh|sz|bj)/lday/(?:sh|sz|bj)(\d{6})\.day$', re.IGNORECASE)
_START_DATE_INT = int(START_DATE)             # e.g. 20210101
_BATCH_STOCKS = 500


def _parse_day_bytes(data: bytes, code: str) -> pd.DataFrame | None:
    """将单只股票的 .day 二进制内容解析为 DataFrame。"""
    n = len(data) // _RECORD_SIZE
    if n == 0:
        return None

    rows = []
    for i in range(n):
        off = i * _RECORD_SIZE
        date_int, open_i, high_i, low_i, close_i, amount, volume, _ = \
            struct.unpack_from(_RECORD_FMT, data, off)
        if date_int < _START_DATE_INT:
            continue
        close = close_i / 100.0
        if close <= 0:
            continue
        rows.append({
            "date":   pd.Timestamp(str(date_int)[:4] + "-"
                                   + str(date_int)[4:6] + "-"
                                   + str(date_int)[6:8]),
            "code":   code,
            "open":   open_i / 100.0,
            "high":   high_i / 100.0,
            "low":    low_i / 100.0,
            "close":  close,
            "amount": float(amount),
            "volume": int(volume),
        })

    if not rows:
        return None
    return pd.DataFrame(rows)


def ingest_kline(
    zip_path: str | Path,
    raw_repo: RawRepo | None = None,
    batch_size: int = _BATCH_STOCKS,
) -> CollectStats:
    """解析 hsjday.zip，将所有股票的 K 线数据批量写入 DuckDB kline 表。

    Args:
        zip_path:   hsjday.zip 的完整路径
        raw_repo:   RawRepo 实例；None 时从默认连接自动创建
        batch_size: 每批写入的股票数量（控制内存峰值）

    Returns:
        CollectStats: ok=成功股票数, fail=解析失败数, skipped=空文件数
    """
    if raw_repo is None:
        from src.dal.connection import get_db
        raw_repo = RawRepo(get_db())

    stats = CollectStats()
    batch_dfs: list[pd.DataFrame] = []

    def _flush():
        if batch_dfs:
            combined = pd.concat(batch_dfs, ignore_index=True)
            raw_repo.upsert_kline(combined)
            batch_dfs.clear()

    with zipfile.ZipFile(zip_path, 'r') as zf:
        names = zf.namelist()
        day_files = [(name, m.group(1))
                     for name in names
                     if (m := _DAY_PATTERN.search(name))]
        total = len(day_files)
        logger.info("hsjday.zip: 共 %d 只股票文件", total)

        for i, (name, code) in enumerate(day_files, 1):
            try:
                data = zf.read(name)
                df = _parse_day_bytes(data, code)
                if df is None:
                    stats.skipped += 1
                    continue
                batch_dfs.append(df)
                stats.ok += 1
            except Exception as exc:
                logger.debug("解析失败 %s: %s", name, exc)
                stats.fail += 1

            if len(batch_dfs) >= batch_size:
                _flush()
                logger.info("进度 %d/%d  %s", i, total, stats)

        _flush()   # 最后一批

    logger.info("ingest_kline 完成：%s", stats)
    return stats
```

- [ ] **Step 4: 运行测试确认通过**

```bash
cd ~/'7、AI 空间/7-3、GitHub/quant_trading'
python -m pytest tests/test_ingest_zip.py -v
```

Expected: `3 passed`

- [ ] **Step 5: Commit**

```bash
git add src/data/ingest_zip.py tests/test_ingest_zip.py
git commit -m "feat: add hsjday.zip direct ingestor to DuckDB kline"
```

---

## Task 2: 新增 `ingest` 命令并修复 `phase1`

**Files:**
- Modify: `main.py`

### 问题

1. `phase1` 调用 `assemble(use_cache=False)` — `assemble()` 无此参数
2. `phase1` 要求 `_SYNC_STATE_FILE` 存在（`sync` 命令创建），但我们走 `ingest` 命令跳过了 `sync`
3. `phase1` 检查 `data/raw/kline/*.parquet` 为空时会触发旧 TDXCollector 转换

- [ ] **Step 1: 写失败测试**

```python
# 在 tests/test_ingest_zip.py 末尾添加（或新建 tests/test_main_commands.py）

def test_phase1_assemble_signature():
    """确认 assemble() 不接受 use_cache 参数（回归测试）。"""
    import inspect
    from src.features.assembler import assemble
    sig = inspect.signature(assemble)
    assert "use_cache" not in sig.parameters, \
        "assemble() 不应有 use_cache 参数——phase1 的调用会 TypeError"
```

- [ ] **Step 2: 运行确认测试失败**

```bash
python -m pytest tests/test_ingest_zip.py::test_phase1_assemble_signature -v
```

Expected: `PASSED`（因为 assembler 确实没有 use_cache 参数）

注：此测试本身会通过，作为文档测试。下一步修复 `main.py` 后它依然通过。

- [ ] **Step 3: 修改 `main.py` — 新增 `ingest` 函数**

在 `main.py` 中 `sync()` 函数之前插入 `ingest()` 函数：

```python
def ingest(args):
    """从 hsjday.zip 解析 K 线数据直接写入 DuckDB（无需 sync 命令）。

    用法:
      python main.py ingest --zip /Volumes/Elements/5、投资/tdx_data/2026-05-21/hsjday.zip
    """
    from src.dal.schema import migrate
    from src.dal.connection import get_db
    from src.dal.raw_repo import RawRepo
    from src.data.ingest_zip import ingest_kline

    zip_path = Path(args.zip)
    if not zip_path.exists():
        logger.error("找不到 zip 文件: %s", zip_path)
        return

    logger.info("建立 DuckDB 并迁移 schema...")
    conn = get_db()
    migrate(conn)

    logger.info("解析 %s → DuckDB kline ...", zip_path.name)
    raw_repo = RawRepo(conn)
    stats = ingest_kline(zip_path, raw_repo)
    logger.info("ingest 完成: %s", stats)

    # 创建 sync_state 标记，使 phase1 可以通过门控
    import json
    from datetime import datetime
    state = {
        "confirmed_date": "auto",
        "expected_date": "auto",
        "zip_file": zip_path.name,
        "confirmed_at": datetime.now().isoformat(timespec="seconds"),
        "source": "ingest_command",
    }
    _SYNC_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _SYNC_STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    logger.info(
        "ingest 完成，DuckDB kline 已写入 %d 只股票。\n"
        "下一步:\n"
        "  python main.py 1           # 特征工程（Phase 1）\n"
        "  python main.py 2 --rolling # 模型训练（Phase 2）\n"
        "  python main.py 3           # 回测（Phase 3）",
        stats.ok,
    )
```

- [ ] **Step 4: 修改 `main.py` — 修复 `phase1` 函数**

找到 `phase1()` 函数，做两处修改：

**修改 1**: 门控改为容忍 `ingest` 来源的 sync_state：

```python
def phase1(args):
    """Phase 1: 特征工程（须先通过 sync 或 ingest 确认数据就绪）"""
    if not _SYNC_STATE_FILE.exists():
        logger.error("未找到数据同步确认记录，请先运行:")
        logger.error("  python main.py ingest --zip <hsjday.zip路径>")
        logger.error("  或: python main.py sync --zip <通达信压缩包路径>")
        return

    state = json.loads(_SYNC_STATE_FILE.read_text(encoding="utf-8"))
    logger.info(f"数据来源: {state.get('zip_file', '未知')}  时间: {state.get('confirmed_at', '未知')}")
```

**修改 2**: kline 检查改为同时检查 DuckDB 表：

```python
    # 检查数据来源：DuckDB 优先，兼容旧 raw/kline/ parquet
    from src.dal.connection import get_db
    from src.dal.raw_repo import RawRepo
    conn = get_db()
    raw_repo = RawRepo(conn)
    db_kline_count = conn.execute("SELECT COUNT(DISTINCT code) FROM kline").fetchone()[0]

    kline_dir = Path(__file__).parent / "data" / "raw" / "kline"
    parquet_count = len(list(kline_dir.glob("*.parquet"))) if kline_dir.exists() else 0

    if db_kline_count == 0 and parquet_count == 0:
        logger.error("DuckDB kline 表和 data/raw/kline/ 均无数据。")
        logger.error("请先运行: python main.py ingest --zip <hsjday.zip路径>")
        return
    elif db_kline_count > 0:
        logger.info(f"DuckDB kline 表已有 {db_kline_count} 只股票数据")
    else:
        logger.info(f"使用旧 data/raw/kline/ ({parquet_count} 只)，触发 TDX 转换...")
        from src.collectors.tdx_collector import TDXCollector
        from src.data.tdx_reader import get_active_tdx_codes
        tdx = TDXCollector(raw_repo=raw_repo)
        codes = get_active_tdx_codes()
        if args.sample:
            codes = codes[:args.sample]
        tdx.collect(codes)
```

**修改 3**: 修复 `assemble` 调用（移除不存在的 `use_cache` 参数）：

```python
    from src.features.assembler import assemble
    df = assemble(
        sample_size=args.sample,
    )
```

- [ ] **Step 5: 在 `main.py` parser 中添加 `ingest` 命令**

找到 `parser.add_argument("phase", choices=[...])` 这行，在 choices 列表和 phases 字典中添加 `"ingest"`：

```python
parser.add_argument("phase", choices=["ingest", "sync", "collect", "fetch-fund", "fetch-flow", "update", "1", "2", "3", "scan"],
                    help="运行阶段：ingest=从zip解析写DuckDB | sync=同步TDX数据 | ...")
```

在 `phases` 字典中添加：
```python
phases = {
    "ingest": ingest,
    "sync": sync,
    ...
}
```

还需要在 `main()` 函数中为 `ingest` 添加 zip 参数检查：

```python
if args.phase == "ingest" and not args.zip:
    parser.error("ingest 命令需要提供 --zip 参数")
```

- [ ] **Step 6: 运行测试确认**

```bash
cd ~/'7、AI 空间/7-3、GitHub/quant_trading'
python -m pytest tests/test_ingest_zip.py -v
```

Expected: All tests pass

- [ ] **Step 7: Commit**

```bash
git add main.py
git commit -m "feat: add ingest command; fix phase1 kline gate and assemble() call"
```

---

## Task 3: 修复 phase2 读取来源（parquet → DuckDB）

**Files:**
- Modify: `src/data/pipeline.py`
- Modify: `main.py`

### 问题

`phase2` 调用 `load_processed_data()` 读取 `market_features.parquet`（已被测试数据污染，只有 2 行），但 `phase1` 的 `assemble()` 把特征写入了 DuckDB `features` 表。两者完全断连。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_pipeline_db_loader.py
import sys, duckdb
from pathlib import Path
from datetime import date
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.dal.schema import migrate
from src.dal.feature_repo import FeatureRepo
from src.data.pipeline import load_features_from_db


@pytest.fixture
def feature_db():
    conn = duckdb.connect(":memory:")
    migrate(conn)
    repo = FeatureRepo(conn)
    # 插入 2 行假特征
    df = pd.DataFrame({
        "date": pd.to_datetime(["2024-01-02", "2024-01-02"]),
        "code": ["000001", "000002"],
        "close": [10.0, 20.0],
        "label": [1, 0],
    })
    repo.upsert_features(df)
    return repo


def test_load_features_from_db_returns_dataframe(feature_db):
    df = load_features_from_db(feature_repo=feature_db)
    assert isinstance(df, pd.DataFrame)
    assert len(df) == 2
    assert "close" in df.columns
    assert "label" in df.columns


def test_load_features_from_db_raises_when_empty():
    conn = duckdb.connect(":memory:")
    migrate(conn)
    empty_repo = FeatureRepo(conn)
    with pytest.raises(FileNotFoundError, match="features 表为空"):
        load_features_from_db(feature_repo=empty_repo)
```

- [ ] **Step 2: 运行确认失败**

```bash
python -m pytest tests/test_pipeline_db_loader.py -v 2>&1 | head -15
```

Expected: `ImportError: cannot import name 'load_features_from_db'`

- [ ] **Step 3: 在 `src/data/pipeline.py` 末尾添加新函数**

在 `load_inference_data()` 函数之后添加：

```python
def load_features_from_db(feature_repo=None) -> pd.DataFrame:
    """从 DuckDB features 表加载全量特征数据，供 Phase 2 训练使用。

    这是 load_processed_data()（读 market_features.parquet）的 DuckDB 替代版本。
    Phase 1 assembler 写入 DuckDB features 表后，Phase 2 通过此函数读取。

    Args:
        feature_repo: FeatureRepo 实例；None 时从默认连接自动创建。

    Raises:
        FileNotFoundError: features 表为空时（Phase 1 尚未运行）。
    """
    from src.dal.feature_repo import FeatureRepo as _FeatureRepo
    if feature_repo is None:
        from src.dal.connection import get_db
        feature_repo = _FeatureRepo(get_db())

    date_range = feature_repo.get_feature_date_range()
    if date_range is None:
        raise FileNotFoundError(
            "features 表为空，请先运行 Phase 1: python main.py 1"
        )

    date_from, date_to = date_range
    df = feature_repo.load_features(date_from, date_to)
    logger.info(
        "从 DuckDB features 表加载完成：%d 行，"
        "时间范围 %s ~ %s，%d 只股票",
        len(df), date_from, date_to, df["code"].nunique() if "code" in df.columns else 0,
    )
    return df
```

- [ ] **Step 4: 修改 `main.py` 中的 `phase2` 函数**

找到 `phase2()` 函数中的数据加载部分：

```python
    # 旧代码（删除）：
    from src.data.pipeline import load_processed_data
    ...
    try:
        df = load_processed_data()
    except FileNotFoundError:
        logger.error("未找到处理后数据，请先运行 phase1 生成特征数据")
        return
```

替换为：

```python
    from src.data.pipeline import load_features_from_db
    logger.info("Phase 2 开始：从 DuckDB features 表加载数据")
    try:
        df = load_features_from_db()
    except FileNotFoundError as exc:
        logger.error(str(exc))
        return
```

- [ ] **Step 5: 运行新测试确认通过**

```bash
python -m pytest tests/test_pipeline_db_loader.py -v
```

Expected: `2 passed`

- [ ] **Step 6: 运行所有测试确认无回归**

```bash
python -m pytest tests/ -v --ignore=tests/test_collector_fund_flow.py 2>&1 | tail -20
```

Expected: 所有已有测试通过（跳过 fund_flow 相关测试）

- [ ] **Step 7: Commit**

```bash
git add src/data/pipeline.py main.py tests/test_pipeline_db_loader.py
git commit -m "fix: phase2 reads features from DuckDB instead of corrupted parquet"
```

---

## Task 4: 端到端验证——跑通完整流程

**Files:**
- 无代码改动，验证运行

### 前置确认

- [ ] **Step 1: 确认外置硬盘已挂载**

```bash
ls /Volumes/Elements/5、投资/tdx_data/2026-05-21/
```

Expected: 输出包含 `hsjday.zip  ScJyData_zbca.zip  tdxfin.zip  tdxgp.zip`

- [ ] **Step 2: 确认 DuckDB 目录可写**

```bash
mkdir -p /Volumes/Elements/5、投资/quant_trading/
ls /Volumes/Elements/5、投资/quant_trading/
```

Expected: 目录创建成功（空或已有 quant.duckdb）

- [ ] **Step 3: 运行 ingest（解析 hsjday.zip → DuckDB kline）**

```bash
cd ~/'7、AI 空间/7-3、GitHub/quant_trading'
python main.py ingest --zip /Volumes/Elements/5、投资/tdx_data/2026-05-21/hsjday.zip
```

Expected 输出：
```
ingest 完成: 新拉取=6250 缓存=0 跳过=0 失败=0
ingest 完成，DuckDB kline 已写入 6250 只股票。
```

- [ ] **Step 4: 采集今日北向资金和龙虎榜**

```bash
python main.py collect
```

Expected: 北向资金 + 龙虎榜今日快照写入 DuckDB

注意：`collect` 命令中有 `tdx.fetch_all()` 调用，会报 AttributeError。这里需要临时跳过或修复。  
临时修复（在 collect 函数开头改为直接调用 NorthboundCollector + SignalCollector）:

如果 `collect` 命令因 `fetch_all` AttributeError 崩溃，直接用 Python 脚本采集：

```python
# python 直接执行
from src.dal.connection import get_db
from src.dal.schema import migrate
from src.collectors.northbound_collector import NorthboundCollector
from src.collectors.signal_collector import SignalCollector

conn = get_db()
migrate(conn)
NorthboundCollector().collect()
SignalCollector().collect()
print("北向 + 龙虎榜今日数据已写入 DuckDB")
```

- [ ] **Step 5: 运行 Phase 1 特征工程（全量，预计 30-60 分钟）**

```bash
python main.py 1
```

Expected：
```
DuckDB kline 表已有 6250 只股票数据
开始特征组装，共 6250 只股票...
有效股票：5xxx 只，跳过 xxx 只...
全市场特征已写入 FeatureRepo，共 xxxxxx 行
Phase 1 完成，数据集形状: (xxxxxx, xxx)
```

- [ ] **Step 6: 运行 Phase 2 模型训练**

```bash
python main.py 2 --rolling
```

Expected：
```
从 DuckDB features 表加载完成：xxxxxx 行...
=== 滚动窗口训练（最近 2 年，跳过 WF CV）===
...
Phase 2 完成，模型已保存至 data/models/
```

- [ ] **Step 7: 运行 Phase 3 回测**

```bash
python main.py 3
```

Expected：
```
Phase 3 开始：加载数据与模型
回测区间: XXXX-XX-XX ~ XXXX-XX-XX
...
Phase 3 完成，结果已保存至 data/backtest/
```

- [ ] **Step 8: 验证回测结果文件**

```bash
ls ~/'7、AI 空间/7-3、GitHub/quant_trading/data/backtest/'
```

Expected: 包含 `equity_curve.png`、`report.txt` 等文件

---

## Task 5: 修复 `collect` 命令中的 AttributeError（兼容补丁）

**Files:**
- Modify: `main.py`

`collect` 命令调用了 `TDXCollector.fetch_all()` 和 `TDXCollector.fetch_incremental_mootdx()` 但这两个方法不存在。由于 `collect` 在新流程中用于北向+LHB，需修复这个断层。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_tdx_collector_methods.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.collectors.tdx_collector import TDXCollector


def test_tdx_collector_has_collect():
    """确认 collect() 方法存在（基础回归）。"""
    assert hasattr(TDXCollector, "collect")


def test_collect_command_does_not_call_nonexistent_fetch_all():
    """collect 命令不应调用 TDXCollector.fetch_all()（此方法不存在）。"""
    import inspect
    import main as main_module
    source = inspect.getsource(main_module.collect)
    assert "fetch_all" not in source, \
        "collect() 仍在调用不存在的 TDXCollector.fetch_all()，请修复"
```

- [ ] **Step 2: 运行确认失败**

```bash
python -m pytest tests/test_tdx_collector_methods.py -v
```

Expected: `test_collect_command_does_not_call_nonexistent_fetch_all` FAILED

- [ ] **Step 3: 修改 `main.py` 的 `collect` 函数**

将 `collect()` 函数替换为只调用存在的方法：

```python
def collect(args):
    """采集今日增量数据到 DuckDB（北向资金 + 龙虎榜快照）。

    注意：K 线数据通过 `ingest` 命令从 zip 文件写入，不在此处处理。
    """
    from src.dal.connection import get_db
    from src.dal.schema import migrate
    from src.collectors.northbound_collector import NorthboundCollector
    from src.collectors.signal_collector import SignalCollector

    conn = get_db()
    migrate(conn)

    # Step 1: 北向资金今日快照
    logger.info("Step 1: 更新北向资金快照 → DuckDB northbound")
    north = NorthboundCollector()
    north_stats = north.collect()
    logger.info(f"北向资金更新完成：{north_stats}")

    # Step 2: 龙虎榜今日数据
    logger.info("Step 2: 更新龙虎榜 → DuckDB lhb")
    signal = SignalCollector()
    signal_stats = signal.collect()
    logger.info(f"龙虎榜更新完成：{signal_stats}")

    # Step 3: 腾讯财经 PE/PB 快照（可选）
    if getattr(args, "with_tencent", False):
        from src.collectors.tencent_collector import TencentCollector
        from src.data.tdx_reader import get_active_tdx_codes
        logger.info("Step 3: 采集腾讯财经 PE/PB/市值快照")
        codes = get_active_tdx_codes()
        if args.sample:
            codes = codes[:args.sample]
        tencent = TencentCollector()
        tencent_stats = tencent.collect(codes)
        logger.info(f"腾讯快照完成：{tencent_stats}")

    logger.info("collect 完成，现在可以运行: python main.py 1")
```

- [ ] **Step 4: 运行测试确认通过**

```bash
python -m pytest tests/test_tdx_collector_methods.py -v
```

Expected: `2 passed`

- [ ] **Step 5: 运行所有测试**

```bash
python -m pytest tests/ -v \
    --ignore=tests/test_collector_fund_flow.py \
    --ignore=tests/test_collector_northbound.py \
    2>&1 | tail -25
```

Expected: 所有非网络测试通过

- [ ] **Step 6: Commit**

```bash
git add main.py tests/test_tdx_collector_methods.py
git commit -m "fix: remove nonexistent TDXCollector.fetch_all() call in collect command"
```

---

## Self-Review

### Spec Coverage

| 需求 | 覆盖 Task |
|------|-----------|
| 不从旧 parquet 读取 | Task 3 (load_features_from_db) |
| hsjday.zip → DuckDB kline | Task 1 (ingest_zip.py) |
| 北向/LHB → DuckDB | Task 4 Step 4 (NorthboundCollector + SignalCollector) |
| 跳过 fund_flow | 不修改 assembler，flow 表为空时 NaN 即可 |
| 跑通完整流程 | Task 4 全程验证 |
| phase2 读 DuckDB | Task 3 (load_features_from_db) |

### Placeholder Scan

- 无 TBD / TODO / "similar to Task N" 
- 所有代码段完整可运行
- Task 4 Step 4 的 Python 片段为兜底手段，有完整代码

### Type Consistency

- `ingest_kline()` 返回 `CollectStats` ✅（与其他 collector 一致）
- `load_features_from_db()` 返回 `pd.DataFrame` ✅
- `FeatureRepo.load_features()` 接收 `date, date` → Task 3 中 `date_from, date_to = date_range` ✅
- `RawRepo.upsert_kline()` 接收含 `date, code, open, high, low, close, amount, volume` 的 DataFrame ✅（`_parse_day_bytes` 输出与此匹配）
