# Sub-2：采集器改写为 DAL 写入 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将所有采集器写入路径从 Parquet 文件切换到 DuckDB DAL，新增 fundamentals_snapshot 表，BaseCollector 重设计为单一 collect() 接口。

**Architecture:** Sub-1 已建立 DAL 层（RawRepo/FeatureRepo/MetaRepo + 9 张表）。Sub-2 在此基础上：① 新增 fundamentals_snapshot 表（腾讯财经实时快照）；② BaseCollector 移除旧接口改为单一 collect() 抽象方法；③ 7 个采集器全部改为注入 RawRepo/MetaRepo、通过 DAL 写入。旧 Parquet 文件保留不删除。

**Tech Stack:** Python 3.11, DuckDB 1.5.3, pandas, akshare, mootdx, unittest.mock, pytest

---

## File Structure

| 文件 | 操作 |
|------|------|
| `src/dal/schema.py` | 修改：新增 `_CREATE_FUNDAMENTALS_SNAPSHOT` + index + migrate() |
| `src/dal/raw_repo.py` | 修改：新增 `upsert_fundamentals_snapshot` / `load_fundamentals_snapshot` |
| `src/collectors/base.py` | 修改：移除旧接口，仅保留 `collect()` 抽象方法 |
| `src/collectors/tdx_collector.py` | 重写：collect() + collect_mootdx() |
| `src/collectors/fundamental_collector.py` | 重写：collect() |
| `src/collectors/fund_flow_collector.py` | 重写：collect() |
| `src/collectors/northbound_collector.py` | 重写：collect() |
| `src/collectors/signal_collector.py` | 重写：collect() |
| `src/collectors/report_collector.py` | 重写：collect() |
| `src/collectors/tencent_collector.py` | 重写：collect() → fundamentals_snapshot |
| `src/data/fundamentals.py` | 修改：移除 fetch_fundamentals() 内 Parquet 写入 |
| `src/data/fund_flow.py` | 修改：移除 fetch_fund_flow() 内 Parquet 写入 |
| `tests/test_dal_raw_repo_fundamentals_snapshot.py` | 新建 |
| `tests/test_collector_tdx.py` | 新建 |
| `tests/test_collector_fundamental.py` | 新建 |
| `tests/test_collector_fund_flow.py` | 新建 |
| `tests/test_collector_northbound.py` | 新建 |
| `tests/test_collector_signal.py` | 新建 |
| `tests/test_collector_report.py` | 新建 |
| `tests/test_collector_tencent.py` | 新建 |

---

### Task 1: fundamentals_snapshot 表 + RawRepo 方法 + DAL 测试

**Files:**
- Modify: `src/dal/schema.py`
- Modify: `src/dal/raw_repo.py`
- Create: `tests/test_dal_raw_repo_fundamentals_snapshot.py`

- [ ] **Step 1: 写测试**

```python
# tests/test_dal_raw_repo_fundamentals_snapshot.py
"""测试 RawRepo fundamentals_snapshot CRUD"""
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


def _snap_df(code: str = "000001", dates: list[str] | None = None) -> pd.DataFrame:
    if dates is None:
        dates = ["2024-01-02", "2024-01-03"]
    n = len(dates)
    return pd.DataFrame({
        "date": pd.to_datetime(dates),
        "code": code,
        "pe_ttm": [15.0] * n,
        "pe_static": [14.0] * n,
        "pb": [1.5] * n,
        "turnover_pct": [2.0] * n,
        "mcap_yi": [500.0] * n,
        "float_mcap_yi": [400.0] * n,
        "price": [10.0] * n,
    })


def test_upsert_returns_row_count(repo):
    assert repo.upsert_fundamentals_snapshot(_snap_df()) == 2


def test_load_returns_inserted_rows(repo):
    repo.upsert_fundamentals_snapshot(_snap_df())
    result = repo.load_fundamentals_snapshot("000001")
    assert len(result) == 2
    assert "pe_ttm" in result.columns


def test_upsert_deduplication(repo):
    df1 = _snap_df(dates=["2024-01-02"])
    df1 = df1.copy()
    df1["price"] = 10.0
    df2 = _snap_df(dates=["2024-01-02"])
    df2 = df2.copy()
    df2["price"] = 99.0
    repo.upsert_fundamentals_snapshot(df1)
    repo.upsert_fundamentals_snapshot(df2)
    result = repo.load_fundamentals_snapshot("000001")
    assert len(result) == 1
    assert float(result.iloc[0]["price"]) == 99.0


def test_load_since_filter(repo):
    repo.upsert_fundamentals_snapshot(_snap_df())
    result = repo.load_fundamentals_snapshot("000001", since=date(2024, 1, 2))
    assert len(result) == 1  # 只返回 2024-01-03


def test_load_empty_for_unknown_code(repo):
    result = repo.load_fundamentals_snapshot("999999")
    assert isinstance(result, pd.DataFrame)
    assert len(result) == 0
```

- [ ] **Step 2: 运行测试，确认失败**

```bash
cd "/Users/hanxuefei/7、AI 空间/7-3、GitHub/quant_trading"
pytest tests/test_dal_raw_repo_fundamentals_snapshot.py -v
```

预期：FAIL，`AttributeError: 'RawRepo' object has no attribute 'upsert_fundamentals_snapshot'`

- [ ] **Step 3: 在 schema.py 新增表定义**

在 `src/dal/schema.py` 的 `_CREATE_COLLECT_LOG` 之后添加：

```python
_CREATE_FUNDAMENTALS_SNAPSHOT = """
CREATE TABLE IF NOT EXISTS fundamentals_snapshot (
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
)
"""
```

在 `_INDEXES` 列表末尾追加：

```python
"CREATE INDEX IF NOT EXISTS idx_fundamentals_snapshot_code ON fundamentals_snapshot (code)",
```

在 `migrate()` 的 `for sql in [...]` 列表末尾追加 `_CREATE_FUNDAMENTALS_SNAPSHOT`：

```python
    for sql in [
        _CREATE_KLINE, _CREATE_FUNDAMENTALS, _CREATE_FUND_FLOW,
        _CREATE_NORTHBOUND, _CREATE_LHB, _CREATE_REPORTS,
        _CREATE_EPS_SNAPSHOT, _CREATE_FEATURES, _CREATE_COLLECT_LOG,
        _CREATE_FUNDAMENTALS_SNAPSHOT,
    ]:
```

- [ ] **Step 4: 在 raw_repo.py 新增两个方法**

在 `src/dal/raw_repo.py` 的 `upsert_eps_snapshot` 方法之后添加：

```python
    # ── fundamentals_snapshot ─────────────────────────────────────────────────

    def upsert_fundamentals_snapshot(self, df: pd.DataFrame) -> int:
        return self._insert_or_replace(
            "fundamentals_snapshot",
            ["date", "code", "pe_ttm", "pe_static", "pb",
             "turnover_pct", "mcap_yi", "float_mcap_yi", "price"],
            df,
        )

    def load_fundamentals_snapshot(
        self, code: str, since: date | None = None
    ) -> pd.DataFrame:
        if since is not None:
            return self._conn.execute(
                "SELECT * FROM fundamentals_snapshot "
                "WHERE code = ? AND date > ? ORDER BY date",
                [code, since],
            ).df()
        return self._conn.execute(
            "SELECT * FROM fundamentals_snapshot WHERE code = ? ORDER BY date",
            [code],
        ).df()
```

- [ ] **Step 5: 运行测试，确认通过**

```bash
pytest tests/test_dal_raw_repo_fundamentals_snapshot.py -v
```

预期：5 passed

- [ ] **Step 6: 确认所有已有 DAL 测试仍通过**

```bash
pytest tests/test_dal_schema.py tests/test_dal_meta_repo.py tests/test_dal_raw_repo.py tests/test_dal_feature_repo.py -v
```

预期：33 passed

- [ ] **Step 7: 提交**

```bash
git add src/dal/schema.py src/dal/raw_repo.py tests/test_dal_raw_repo_fundamentals_snapshot.py
git commit -m "feat: add fundamentals_snapshot table and RawRepo methods"
```

---

### Task 2: BaseCollector 接口重设计

**Files:**
- Modify: `src/collectors/base.py`

- [ ] **Step 1: 用以下内容完整替换 src/collectors/base.py**

```python
"""采集器基类与统计数据类"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date


@dataclass
class CollectStats:
    """单次采集统计"""
    ok: int = 0
    fail: int = 0
    cached: int = 0
    skipped: int = 0

    def __str__(self) -> str:
        return (
            f"新拉取={self.ok} 缓存={self.cached} "
            f"跳过={self.skipped} 失败={self.fail}"
        )

    @property
    def total(self) -> int:
        return self.ok + self.fail + self.cached + self.skipped


class BaseCollector(ABC):
    """所有采集器的抽象基类。

    子类必须实现 collect()，将数据写入 DAL（DuckDB）。
    """

    @abstractmethod
    def collect(self, codes: list[str] = [], since: date | None = None) -> CollectStats:
        """执行采集并写入 DAL。

        codes: 需要采集的股票代码列表；市场级采集器忽略此参数。
        since: 覆盖增量起点；None 时采集器自行从 MetaRepo 查询上次日期。
        """
```

- [ ] **Step 2: 运行已有 DAL 测试确认没有破坏**

```bash
pytest tests/test_dal_schema.py tests/test_dal_meta_repo.py tests/test_dal_raw_repo.py tests/test_dal_feature_repo.py tests/test_dal_raw_repo_fundamentals_snapshot.py -v
```

预期：38 passed（原 33 + 新 5）

- [ ] **Step 3: 提交**

```bash
git add src/collectors/base.py
git commit -m "refactor: redesign BaseCollector to single collect() interface"
```

---

### Task 3: 移除 src/data 模块的 Parquet 写入

**Files:**
- Modify: `src/data/fundamentals.py:86-87`
- Modify: `src/data/fund_flow.py:120`

- [ ] **Step 1: 修改 src/data/fundamentals.py**

在 `fetch_fundamentals()` 函数中，找到并删除以下两行（位于 `df = _normalize_fundamentals(raw)` 之后）：

```python
    FUNDAMENTALS_DIR.mkdir(parents=True, exist_ok=True)
    df.to_parquet(cache_path, index=False)
```

删除后该函数末尾应为：

```python
    df = _normalize_fundamentals(raw)
    return df
```

- [ ] **Step 2: 修改 src/data/fund_flow.py**

在 `fetch_fund_flow()` 函数中，找到并删除以下一行（位于 `if df is None or df.empty: return None` 之后）：

```python
    df.to_parquet(cache_path, index=False)
```

删除后该函数末尾应为：

```python
    df = _normalize_fund_flow(raw)
    if df is None or df.empty:
        return None
    return df
```

- [ ] **Step 3: 运行全部已有测试确认无破坏**

```bash
pytest tests/ -v --ignore=tests/test_collector_tdx.py --ignore=tests/test_collector_fundamental.py --ignore=tests/test_collector_fund_flow.py --ignore=tests/test_collector_northbound.py --ignore=tests/test_collector_signal.py --ignore=tests/test_collector_report.py --ignore=tests/test_collector_tencent.py
```

预期：38 passed

- [ ] **Step 4: 提交**

```bash
git add src/data/fundamentals.py src/data/fund_flow.py
git commit -m "refactor: remove Parquet writes from fetch_fundamentals and fetch_fund_flow"
```

---

### Task 4: TDXCollector 重写 + 测试

**Files:**
- Create: `tests/test_collector_tdx.py`
- Modify: `src/collectors/tdx_collector.py`

- [ ] **Step 1: 写测试**

```python
# tests/test_collector_tdx.py
"""测试 TDXCollector.collect()"""
import sys
from datetime import date
from pathlib import Path
from unittest.mock import patch

import duckdb
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.dal.schema import migrate
from src.dal.raw_repo import RawRepo
from src.dal.meta_repo import MetaRepo
from src.collectors.tdx_collector import TDXCollector


@pytest.fixture
def repos():
    conn = duckdb.connect(":memory:")
    migrate(conn)
    return RawRepo(conn), MetaRepo(conn)


def _fake_kline(n: int = 3) -> pd.DataFrame:
    dates = pd.date_range("2024-01-02", periods=n, freq="B")
    return pd.DataFrame({
        "date": dates,
        "open": [10.0] * n,
        "high": [11.0] * n,
        "low": [9.5] * n,
        "close": [10.5] * n,
        "amount": [1e8] * n,
        "volume": [1_000_000] * n,
    })


def test_collect_first_time_writes_all_rows(repos):
    raw_repo, meta_repo = repos
    collector = TDXCollector(raw_repo=raw_repo, meta_repo=meta_repo)

    with patch("src.data.tdx_reader.read_day_file", return_value=_fake_kline(3)):
        stats = collector.collect(codes=["000001"])

    assert stats.ok == 1
    assert stats.fail == 0
    result = raw_repo.load_kline("000001")
    assert len(result) == 3
    assert meta_repo.get_last_date("kline", "000001") == date(2024, 1, 4)


def test_collect_incremental_skips_old_rows(repos):
    raw_repo, meta_repo = repos
    existing = _fake_kline(2).assign(code="000001")
    raw_repo.upsert_kline(existing)
    meta_repo.set_last_date("kline", "000001", date(2024, 1, 3))

    collector = TDXCollector(raw_repo=raw_repo, meta_repo=meta_repo)
    with patch("src.data.tdx_reader.read_day_file", return_value=_fake_kline(3)):
        stats = collector.collect(codes=["000001"])

    assert stats.ok == 1
    result = raw_repo.load_kline("000001")
    assert len(result) == 3
    assert meta_repo.get_last_date("kline", "000001") == date(2024, 1, 4)


def test_collect_handles_read_failure(repos):
    raw_repo, meta_repo = repos
    collector = TDXCollector(raw_repo=raw_repo, meta_repo=meta_repo)

    with patch("src.data.tdx_reader.read_day_file", return_value=None):
        stats = collector.collect(codes=["000001"])

    assert stats.skipped == 1
    assert stats.ok == 0
    assert meta_repo.get_last_date("kline", "000001") is None
```

- [ ] **Step 2: 运行测试，确认失败**

```bash
pytest tests/test_collector_tdx.py -v
```

预期：FAIL，`TypeError: Can't instantiate abstract class TDXCollector`

- [ ] **Step 3: 完整替换 src/collectors/tdx_collector.py**

```python
"""通达信 K 线采集器（DAL 版）

两种数据来源：
  - 本地 .day 二进制文件：collect() 读取 TDX 文件写入 DAL
  - mootdx TCP 网络：collect_mootdx() 每日增量写入 DAL
"""
from __future__ import annotations

import logging
from datetime import date

import pandas as pd
from tqdm import tqdm

from src.collectors.base import BaseCollector, CollectStats
from src.dal.meta_repo import MetaRepo
from src.dal.raw_repo import RawRepo

logger = logging.getLogger(__name__)

_MARKET_SH = 1
_MARKET_SZ = 0
_MARKET_BJ = 2


def _get_market(code: str) -> int:
    if code.startswith(("6", "9")):
        return _MARKET_SH
    if code.startswith("8"):
        return _MARKET_BJ
    return _MARKET_SZ


class TDXCollector(BaseCollector):
    """通达信本地 .day 文件 → DuckDB kline 表。"""

    def __init__(
        self,
        raw_repo: RawRepo | None = None,
        meta_repo: MetaRepo | None = None,
    ) -> None:
        if raw_repo is None:
            from src.dal.connection import get_db
            conn = get_db()
            raw_repo = RawRepo(conn)
            if meta_repo is None:
                meta_repo = MetaRepo(conn)
        if meta_repo is None:
            meta_repo = MetaRepo(raw_repo._conn)
        self._raw_repo = raw_repo
        self._meta_repo = meta_repo

    def collect(self, codes: list[str] = [], since: date | None = None) -> CollectStats:
        """读取 TDX 本地 .day 文件，增量写入 DAL kline 表。"""
        from src.data.tdx_reader import read_day_file

        stats = CollectStats()
        for code in tqdm(codes, desc="TDX→DAL"):
            last_date = since if since is not None else self._meta_repo.get_last_date("kline", code)
            raw = read_day_file(code)
            if raw is None or raw.empty:
                stats.skipped += 1
                continue

            raw["date"] = pd.to_datetime(raw["date"])
            raw["code"] = code
            df = raw.sort_values("date").reset_index(drop=True)

            if last_date is not None:
                df = df[df["date"] > pd.Timestamp(last_date)]
            if df.empty:
                stats.cached += 1
                continue

            self._raw_repo.upsert_kline(df)
            self._meta_repo.set_last_date("kline", code, df["date"].max().date(), len(df))
            stats.ok += 1

        logger.info(f"TDX 转换完成：{stats}")
        return stats

    def collect_mootdx(
        self,
        codes: list[str],
        since: date,
        delay: float = 0.0,
    ) -> CollectStats:
        """通过 mootdx TCP 拉取增量 K 线，写入 DAL kline 表。"""
        import time
        from mootdx.quotes import Quotes

        calendar_days = (date.today() - since).days
        offset = max(int(calendar_days * 5 / 7) + 10, 15)
        since_ts = pd.Timestamp(since)
        logger.info(f"mootdx 增量拉取：since={since}，offset={offset} bars，共 {len(codes)} 只")

        try:
            client = Quotes.factory(market="std")
        except Exception as exc:
            logger.error(f"mootdx 连接失败: {exc}")
            stats = CollectStats()
            stats.fail = len(codes)
            return stats

        stats = CollectStats()
        for code in tqdm(codes, desc="mootdx增量K线"):
            try:
                market = _get_market(code)
                raw = client.bars(symbol=code, category=4, market=market, offset=offset)
                if raw is None or raw.empty:
                    stats.skipped += 1
                    continue

                df = raw.copy()
                df["date"] = pd.to_datetime(df.index).normalize()
                df = df.reset_index(drop=True)
                keep = ["date", "open", "high", "low", "close", "amount", "volume"]
                df = df[[c for c in keep if c in df.columns]].copy()
                if "volume" in df.columns:
                    df["volume"] = df["volume"] * 100
                df["code"] = code

                new_rows = df[df["date"] > since_ts]
                if new_rows.empty:
                    stats.skipped += 1
                    continue

                self._raw_repo.upsert_kline(new_rows)
                self._meta_repo.set_last_date(
                    "kline", code, new_rows["date"].max().date(), len(new_rows)
                )
                stats.ok += 1

            except Exception as exc:
                logger.debug(f"mootdx 拉取失败 {code}: {exc}")
                stats.fail += 1

            if delay > 0:
                time.sleep(delay)

        logger.info(f"mootdx 增量完成：{stats}")
        return stats
```

- [ ] **Step 4: 运行测试，确认通过**

```bash
pytest tests/test_collector_tdx.py -v
```

预期：3 passed

- [ ] **Step 5: 提交**

```bash
git add src/collectors/tdx_collector.py tests/test_collector_tdx.py
git commit -m "feat: rewrite TDXCollector with collect() DAL interface"
```

---

### Task 5: FundamentalCollector 重写 + 测试

**Files:**
- Create: `tests/test_collector_fundamental.py`
- Modify: `src/collectors/fundamental_collector.py`

- [ ] **Step 1: 写测试**

```python
# tests/test_collector_fundamental.py
"""测试 FundamentalCollector.collect()"""
import sys
from datetime import date
from pathlib import Path
from unittest.mock import patch

import duckdb
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.dal.schema import migrate
from src.dal.raw_repo import RawRepo
from src.dal.meta_repo import MetaRepo
from src.collectors.fundamental_collector import FundamentalCollector


@pytest.fixture
def repos():
    conn = duckdb.connect(":memory:")
    migrate(conn)
    return RawRepo(conn), MetaRepo(conn)


def _fake_fundamentals(n: int = 3) -> pd.DataFrame:
    dates = pd.date_range("2024-01-02", periods=n, freq="B")
    return pd.DataFrame({
        "date": dates,
        "pe_ttm": [15.0] * n,
        "pe_static": [14.0] * n,
        "pb": [1.5] * n,
        "ps": [2.0] * n,
        "pcf": [5.0] * n,
        "peg": [0.8] * n,
        "market_cap": [5e10] * n,
        "float_market_cap": [4e10] * n,
        "total_shares": [5_000_000_000] * n,
        "float_shares": [4_000_000_000] * n,
    })


def test_collect_first_time_writes_all_rows(repos):
    raw_repo, meta_repo = repos
    collector = FundamentalCollector(raw_repo=raw_repo, meta_repo=meta_repo)

    with patch("src.data.fundamentals.fetch_fundamentals", return_value=_fake_fundamentals(3)):
        stats = collector.collect(codes=["000001"])

    assert stats.ok == 1
    result = raw_repo.load_fundamentals("000001")
    assert len(result) == 3
    assert meta_repo.get_last_date("fundamentals", "000001") == date(2024, 1, 4)


def test_collect_incremental_skips_old_rows(repos):
    raw_repo, meta_repo = repos
    existing = _fake_fundamentals(2).copy()
    existing["code"] = "000001"
    raw_repo.upsert_fundamentals(existing)
    meta_repo.set_last_date("fundamentals", "000001", date(2024, 1, 3))

    collector = FundamentalCollector(raw_repo=raw_repo, meta_repo=meta_repo)
    with patch("src.data.fundamentals.fetch_fundamentals", return_value=_fake_fundamentals(3)):
        stats = collector.collect(codes=["000001"])

    assert stats.ok == 1
    result = raw_repo.load_fundamentals("000001")
    assert len(result) == 3


def test_collect_network_failure_counts_fail(repos):
    raw_repo, meta_repo = repos
    collector = FundamentalCollector(raw_repo=raw_repo, meta_repo=meta_repo)

    with patch("src.data.fundamentals.fetch_fundamentals", return_value=None):
        stats = collector.collect(codes=["000001"])

    assert stats.fail == 1
    assert stats.ok == 0
    assert meta_repo.get_last_date("fundamentals", "000001") is None
```

- [ ] **Step 2: 运行测试，确认失败**

```bash
pytest tests/test_collector_fundamental.py -v
```

预期：FAIL，`TypeError: Can't instantiate abstract class FundamentalCollector`

- [ ] **Step 3: 完整替换 src/collectors/fundamental_collector.py**

```python
"""基本面采集器（DAL 版）"""
from __future__ import annotations

import logging
import time
from datetime import date

import pandas as pd
from tqdm import tqdm

from src.collectors.base import BaseCollector, CollectStats
from src.dal.meta_repo import MetaRepo
from src.dal.raw_repo import RawRepo

logger = logging.getLogger(__name__)


class FundamentalCollector(BaseCollector):
    """akshare 东方财富基本面数据 → DuckDB fundamentals 表。"""

    def __init__(
        self,
        raw_repo: RawRepo | None = None,
        meta_repo: MetaRepo | None = None,
        delay: float = 0.3,
    ) -> None:
        if raw_repo is None:
            from src.dal.connection import get_db
            conn = get_db()
            raw_repo = RawRepo(conn)
            if meta_repo is None:
                meta_repo = MetaRepo(conn)
        if meta_repo is None:
            meta_repo = MetaRepo(raw_repo._conn)
        self._raw_repo = raw_repo
        self._meta_repo = meta_repo
        self.delay = delay

    def collect(self, codes: list[str] = [], since: date | None = None) -> CollectStats:
        """拉取基本面，增量写入 DAL fundamentals 表。"""
        from src.data.fundamentals import fetch_fundamentals

        stats = CollectStats()
        consecutive_errors = 0

        for i, code in enumerate(tqdm(codes, desc="基本面→DAL")):
            last_date = since if since is not None else self._meta_repo.get_last_date("fundamentals", code)

            try:
                df = fetch_fundamentals(code, use_cache=False)
            except Exception as exc:
                logger.debug(f"基本面拉取异常 {code}: {exc}")
                df = None

            if df is None or df.empty:
                stats.fail += 1
                consecutive_errors += 1
                if consecutive_errors >= 100:
                    logger.error("连续 100 次失败，疑似被限频，终止拉取")
                    break
                time.sleep(self.delay)
                continue

            consecutive_errors = 0
            if last_date is not None:
                df = df[df["date"] > pd.Timestamp(last_date)]
            if df.empty:
                stats.cached += 1
                time.sleep(self.delay)
                continue

            df = df.copy()
            df["code"] = code
            self._raw_repo.upsert_fundamentals(df)
            self._meta_repo.set_last_date("fundamentals", code, df["date"].max().date(), len(df))
            stats.ok += 1

            if (i + 1) % 100 == 0:
                logger.info(f"基本面进度 {i + 1}/{len(codes)}  {stats}")

            time.sleep(self.delay)

        logger.info(f"基本面拉取完成：{stats}")
        return stats
```

- [ ] **Step 4: 运行测试，确认通过**

```bash
pytest tests/test_collector_fundamental.py -v
```

预期：3 passed

- [ ] **Step 5: 提交**

```bash
git add src/collectors/fundamental_collector.py tests/test_collector_fundamental.py
git commit -m "feat: rewrite FundamentalCollector with collect() DAL interface"
```

---

### Task 6: FundFlowCollector 重写 + 测试

**Files:**
- Create: `tests/test_collector_fund_flow.py`
- Modify: `src/collectors/fund_flow_collector.py`

- [ ] **Step 1: 写测试**

```python
# tests/test_collector_fund_flow.py
"""测试 FundFlowCollector.collect()"""
import sys
from datetime import date
from pathlib import Path
from unittest.mock import patch

import duckdb
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.dal.schema import migrate
from src.dal.raw_repo import RawRepo
from src.dal.meta_repo import MetaRepo
from src.collectors.fund_flow_collector import FundFlowCollector


@pytest.fixture
def repos():
    conn = duckdb.connect(":memory:")
    migrate(conn)
    return RawRepo(conn), MetaRepo(conn)


def _fake_fund_flow(n: int = 3) -> pd.DataFrame:
    dates = pd.date_range("2024-01-02", periods=n, freq="B")
    return pd.DataFrame({
        "date": dates,
        "major_net_inflow": ([1e7, 2e7, 3e7] * ((n // 3) + 1))[:n],
        "major_net_pct": ([1.0, 2.0, 3.0] * ((n // 3) + 1))[:n],
    })


def test_collect_first_time_writes_all_rows(repos):
    raw_repo, meta_repo = repos
    collector = FundFlowCollector(raw_repo=raw_repo, meta_repo=meta_repo)

    with patch("src.data.fund_flow.fetch_fund_flow", return_value=_fake_fund_flow(3)):
        stats = collector.collect(codes=["000001"])

    assert stats.ok == 1
    result = raw_repo.load_fund_flow("000001")
    assert len(result) == 3
    assert meta_repo.get_last_date("fund_flow", "000001") == date(2024, 1, 4)


def test_collect_incremental_skips_old_rows(repos):
    raw_repo, meta_repo = repos
    existing = _fake_fund_flow(2).copy()
    existing["code"] = "000001"
    raw_repo.upsert_fund_flow(existing)
    meta_repo.set_last_date("fund_flow", "000001", date(2024, 1, 3))

    collector = FundFlowCollector(raw_repo=raw_repo, meta_repo=meta_repo)
    with patch("src.data.fund_flow.fetch_fund_flow", return_value=_fake_fund_flow(3)):
        stats = collector.collect(codes=["000001"])

    assert stats.ok == 1
    result = raw_repo.load_fund_flow("000001")
    assert len(result) == 3


def test_collect_network_failure_counts_fail(repos):
    raw_repo, meta_repo = repos
    collector = FundFlowCollector(raw_repo=raw_repo, meta_repo=meta_repo)

    with patch("src.data.fund_flow.fetch_fund_flow", return_value=None):
        stats = collector.collect(codes=["000001"])

    assert stats.fail == 1
    assert meta_repo.get_last_date("fund_flow", "000001") is None
```

- [ ] **Step 2: 运行测试，确认失败**

```bash
pytest tests/test_collector_fund_flow.py -v
```

预期：FAIL，`TypeError: Can't instantiate abstract class FundFlowCollector`

- [ ] **Step 3: 完整替换 src/collectors/fund_flow_collector.py**

```python
"""个股主力资金流向采集器（DAL 版）"""
from __future__ import annotations

import logging
import time
from datetime import date

import pandas as pd
from tqdm import tqdm

from src.collectors.base import BaseCollector, CollectStats
from src.dal.meta_repo import MetaRepo
from src.dal.raw_repo import RawRepo

logger = logging.getLogger(__name__)


class FundFlowCollector(BaseCollector):
    """akshare 东方财富资金流向 → DuckDB fund_flow 表。"""

    def __init__(
        self,
        raw_repo: RawRepo | None = None,
        meta_repo: MetaRepo | None = None,
        delay: float = 0.5,
    ) -> None:
        if raw_repo is None:
            from src.dal.connection import get_db
            conn = get_db()
            raw_repo = RawRepo(conn)
            if meta_repo is None:
                meta_repo = MetaRepo(conn)
        if meta_repo is None:
            meta_repo = MetaRepo(raw_repo._conn)
        self._raw_repo = raw_repo
        self._meta_repo = meta_repo
        self.delay = delay

    def collect(self, codes: list[str] = [], since: date | None = None) -> CollectStats:
        """拉取资金流向，增量写入 DAL fund_flow 表。"""
        from src.data.fund_flow import fetch_fund_flow

        stats = CollectStats()
        consecutive_errors = 0

        for i, code in enumerate(tqdm(codes, desc="资金流向→DAL")):
            last_date = since if since is not None else self._meta_repo.get_last_date("fund_flow", code)

            try:
                df = fetch_fund_flow(code, use_cache=False)
            except Exception as exc:
                logger.debug(f"资金流向拉取异常 {code}: {exc}")
                df = None

            if df is None or df.empty:
                stats.fail += 1
                consecutive_errors += 1
                if consecutive_errors >= 100:
                    logger.error("连续 100 次失败，疑似被限频，终止拉取")
                    break
                time.sleep(self.delay)
                continue

            consecutive_errors = 0
            if last_date is not None:
                df = df[df["date"] > pd.Timestamp(last_date)]
            if df.empty:
                stats.cached += 1
                time.sleep(self.delay)
                continue

            df = df.copy()
            df["code"] = code
            self._raw_repo.upsert_fund_flow(df)
            self._meta_repo.set_last_date("fund_flow", code, df["date"].max().date(), len(df))
            stats.ok += 1

            if (i + 1) % 200 == 0:
                logger.info(f"资金流向进度 {i + 1}/{len(codes)}  {stats}")

            time.sleep(self.delay)

        logger.info(f"资金流向拉取完成：{stats}")
        return stats
```

- [ ] **Step 4: 运行测试，确认通过**

```bash
pytest tests/test_collector_fund_flow.py -v
```

预期：3 passed

- [ ] **Step 5: 提交**

```bash
git add src/collectors/fund_flow_collector.py tests/test_collector_fund_flow.py
git commit -m "feat: rewrite FundFlowCollector with collect() DAL interface"
```

---

### Task 7: NorthboundCollector 重写 + 测试

**Files:**
- Create: `tests/test_collector_northbound.py`
- Modify: `src/collectors/northbound_collector.py`

- [ ] **Step 1: 写测试**

```python
# tests/test_collector_northbound.py
"""测试 NorthboundCollector.collect()"""
import sys
from datetime import date
from pathlib import Path
from unittest.mock import patch

import duckdb
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.dal.schema import migrate
from src.dal.raw_repo import RawRepo
from src.dal.meta_repo import MetaRepo
from src.collectors.northbound_collector import NorthboundCollector


@pytest.fixture
def repos():
    conn = duckdb.connect(":memory:")
    migrate(conn)
    return RawRepo(conn), MetaRepo(conn)


_FAKE_SNAPSHOT = {
    "date": "2024-01-04",
    "north_net_inflow": 1e9,
    "hgt_yi": 5.0,
    "sgt_yi": 5.0,
}


def test_collect_writes_today_row(repos):
    raw_repo, meta_repo = repos
    collector = NorthboundCollector(raw_repo=raw_repo, meta_repo=meta_repo)

    with patch("src.collectors.northbound_collector._fetch_today_snapshot", return_value=_FAKE_SNAPSHOT):
        stats = collector.collect()

    assert stats.ok == 1
    result = raw_repo.load_northbound()
    assert len(result) == 1


def test_collect_skips_if_already_collected_today(repos):
    raw_repo, meta_repo = repos
    meta_repo.set_last_date("northbound", "__market__", date.today())
    collector = NorthboundCollector(raw_repo=raw_repo, meta_repo=meta_repo)

    with patch("src.collectors.northbound_collector._fetch_today_snapshot") as mock_fetch:
        stats = collector.collect()

    assert stats.cached == 1
    assert not mock_fetch.called


def test_collect_handles_network_failure(repos):
    raw_repo, meta_repo = repos
    collector = NorthboundCollector(raw_repo=raw_repo, meta_repo=meta_repo)

    with patch("src.collectors.northbound_collector._fetch_today_snapshot", return_value=None):
        stats = collector.collect()

    assert stats.fail == 1
    assert meta_repo.get_last_date("northbound", "__market__") is None
```

- [ ] **Step 2: 运行测试，确认失败**

```bash
pytest tests/test_collector_northbound.py -v
```

预期：FAIL，`TypeError: Can't instantiate abstract class NorthboundCollector`

- [ ] **Step 3: 完整替换 src/collectors/northbound_collector.py**

```python
"""北向资金采集器（DAL 版）"""
from __future__ import annotations

import logging
from datetime import date, datetime

import pandas as pd
import requests

from src.collectors.base import BaseCollector, CollectStats
from src.dal.meta_repo import MetaRepo
from src.dal.raw_repo import RawRepo

logger = logging.getLogger(__name__)

_HSGT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/117.0.0.0 Safari/537.36"
    ),
    "Host": "data.hexin.cn",
    "Referer": "https://data.hexin.cn/",
}

_HSGT_URL = "https://data.hexin.cn/market/hsgtApi/method/dayChart/"


def _fetch_today_snapshot() -> dict | None:
    """从同花顺拉取今日实时北向流向，返回 {date, north_net_inflow, hgt_yi, sgt_yi} 或 None。"""
    try:
        r = requests.get(_HSGT_URL, headers=_HSGT_HEADERS, timeout=10)
        d = r.json()
    except Exception as exc:
        logger.warning(f"同花顺 hsgtApi 请求失败: {exc}")
        return None

    times = d.get("time", [])
    hgt = d.get("hgt", [])
    sgt = d.get("sgt", [])

    if not times:
        logger.warning("同花顺 hsgtApi 返回空数据")
        return None

    n = len(times)
    hgt_vals = hgt[:n] + [None] * max(0, n - len(hgt))
    sgt_vals = sgt[:n] + [None] * max(0, n - len(sgt))

    last_hgt = next((v for v in reversed(hgt_vals) if v is not None), None)
    last_sgt = next((v for v in reversed(sgt_vals) if v is not None), None)

    if last_hgt is None and last_sgt is None:
        return None

    hgt_val = float(last_hgt) if last_hgt is not None else 0.0
    sgt_val = float(last_sgt) if last_sgt is not None else 0.0
    return {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "north_net_inflow": (hgt_val + sgt_val) * 1e8,
        "hgt_yi": hgt_val,
        "sgt_yi": sgt_val,
    }


class NorthboundCollector(BaseCollector):
    """同花顺北向资金日度快照 → DuckDB northbound 表（市场级）。"""

    def __init__(
        self,
        raw_repo: RawRepo | None = None,
        meta_repo: MetaRepo | None = None,
    ) -> None:
        if raw_repo is None:
            from src.dal.connection import get_db
            conn = get_db()
            raw_repo = RawRepo(conn)
            if meta_repo is None:
                meta_repo = MetaRepo(conn)
        if meta_repo is None:
            meta_repo = MetaRepo(raw_repo._conn)
        self._raw_repo = raw_repo
        self._meta_repo = meta_repo

    def collect(self, codes: list[str] = [], since: date | None = None) -> CollectStats:
        """拉取今日北向快照，写入 DAL northbound 表。codes 参数忽略。"""
        stats = CollectStats()
        today = datetime.now().date()

        last_date = self._meta_repo.get_last_date("northbound", "__market__")
        if last_date is not None and last_date >= today:
            stats.cached += 1
            return stats

        snapshot = _fetch_today_snapshot()
        if snapshot is None:
            stats.fail += 1
            logger.warning("北向资金今日快照拉取失败")
            return stats

        df = pd.DataFrame([{
            "date": pd.Timestamp(snapshot["date"]),
            "north_net_inflow": snapshot["north_net_inflow"],
            "hgt_yi": snapshot["hgt_yi"],
            "sgt_yi": snapshot["sgt_yi"],
        }])
        self._raw_repo.upsert_northbound(df)
        self._meta_repo.set_last_date("northbound", "__market__", today, 1)
        stats.ok += 1
        logger.info(f"北向资金已更新：{today}")
        return stats
```

- [ ] **Step 4: 运行测试，确认通过**

```bash
pytest tests/test_collector_northbound.py -v
```

预期：3 passed

- [ ] **Step 5: 提交**

```bash
git add src/collectors/northbound_collector.py tests/test_collector_northbound.py
git commit -m "feat: rewrite NorthboundCollector with collect() DAL interface"
```

---

### Task 8: SignalCollector 重写 + 测试

**Files:**
- Create: `tests/test_collector_signal.py`
- Modify: `src/collectors/signal_collector.py`

- [ ] **Step 1: 写测试**

```python
# tests/test_collector_signal.py
"""测试 SignalCollector.collect()"""
import sys
from datetime import date
from pathlib import Path
from unittest.mock import patch

import duckdb
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.dal.schema import migrate
from src.dal.raw_repo import RawRepo
from src.dal.meta_repo import MetaRepo
from src.collectors.signal_collector import SignalCollector


@pytest.fixture
def repos():
    conn = duckdb.connect(":memory:")
    migrate(conn)
    return RawRepo(conn), MetaRepo(conn)


def _fake_lhb_raw() -> pd.DataFrame:
    return pd.DataFrame({
        "代码": ["000001", "000002"],
        "龙虎榜净买额": [1e6, 2e6],
        "买入额合计": [3e6, 4e6],
        "卖出额合计": [2e6, 2e6],
    })


def test_collect_writes_today_row(repos):
    raw_repo, meta_repo = repos
    collector = SignalCollector(raw_repo=raw_repo, meta_repo=meta_repo)

    with patch("akshare.stock_lhb_detail_em", return_value=_fake_lhb_raw()):
        stats = collector.collect()

    assert stats.ok == 1
    result = raw_repo.load_lhb("000001")
    assert len(result) == 1


def test_collect_skips_if_already_collected_today(repos):
    raw_repo, meta_repo = repos
    meta_repo.set_last_date("lhb", "__market__", date.today())
    collector = SignalCollector(raw_repo=raw_repo, meta_repo=meta_repo)

    with patch("akshare.stock_lhb_detail_em") as mock_ak:
        stats = collector.collect()

    assert stats.cached == 1
    assert not mock_ak.called


def test_collect_handles_network_failure(repos):
    raw_repo, meta_repo = repos
    collector = SignalCollector(raw_repo=raw_repo, meta_repo=meta_repo)

    with patch("akshare.stock_lhb_detail_em", side_effect=Exception("Connection error")):
        stats = collector.collect()

    assert stats.fail == 1
    assert meta_repo.get_last_date("lhb", "__market__") is None
```

- [ ] **Step 2: 运行测试，确认失败**

```bash
pytest tests/test_collector_signal.py -v
```

预期：FAIL，`TypeError: Can't instantiate abstract class SignalCollector`

- [ ] **Step 3: 完整替换 src/collectors/signal_collector.py**

```python
"""龙虎榜全市场日报采集器（DAL 版）"""
from __future__ import annotations

import logging
from datetime import date, datetime

import akshare as ak
import pandas as pd

from src.collectors.base import BaseCollector, CollectStats
from src.dal.meta_repo import MetaRepo
from src.dal.raw_repo import RawRepo

logger = logging.getLogger(__name__)

_COL_MAP = {
    "代码": "code",
    "股票代码": "code",
    "上榜日期": "date",
    "龙虎榜净买额": "lhb_net_buy",
    "买入额合计": "lhb_buy_amount",
    "卖出额合计": "lhb_sell_amount",
}


class SignalCollector(BaseCollector):
    """东财龙虎榜日报 → DuckDB lhb 表（市场级）。

    ⚠️ akshare.stock_lhb_detail_em 在 VPN 开启时有 SSL 错误，需关 VPN 后运行。
    """

    def __init__(
        self,
        raw_repo: RawRepo | None = None,
        meta_repo: MetaRepo | None = None,
    ) -> None:
        if raw_repo is None:
            from src.dal.connection import get_db
            conn = get_db()
            raw_repo = RawRepo(conn)
            if meta_repo is None:
                meta_repo = MetaRepo(conn)
        if meta_repo is None:
            meta_repo = MetaRepo(raw_repo._conn)
        self._raw_repo = raw_repo
        self._meta_repo = meta_repo

    def _fetch_daily(self, target_date: date) -> pd.DataFrame | None:
        date_str = target_date.strftime("%Y%m%d")
        try:
            raw = ak.stock_lhb_detail_em(start_date=date_str, end_date=date_str)
        except Exception as exc:
            logger.warning(f"龙虎榜拉取失败 {date_str}: {exc}")
            return None

        if raw is None or raw.empty:
            return None

        df = raw.rename(columns={k: v for k, v in _COL_MAP.items() if k in raw.columns})
        if not {"code", "lhb_net_buy"}.issubset(df.columns):
            logger.warning(f"龙虎榜字段不全 {date_str}，已有列: {list(df.columns)}")
            return None

        df["date"] = pd.to_datetime(target_date)
        df["code"] = df["code"].astype(str).str.zfill(6)
        df["lhb_net_buy"] = pd.to_numeric(df["lhb_net_buy"], errors="coerce")
        df["lhb_buy_amount"] = pd.to_numeric(df.get("lhb_buy_amount", 0), errors="coerce")
        df["lhb_sell_amount"] = pd.to_numeric(df.get("lhb_sell_amount", 0), errors="coerce")

        keep = ["date", "code", "lhb_net_buy", "lhb_buy_amount", "lhb_sell_amount"]
        df = df[[c for c in keep if c in df.columns]]
        df = df.dropna(subset=["code"]).drop_duplicates(subset=["code"])
        return df if not df.empty else None

    def collect(self, codes: list[str] = [], since: date | None = None) -> CollectStats:
        """拉取今日龙虎榜，写入 DAL lhb 表。codes 参数忽略。"""
        stats = CollectStats()
        today = datetime.now().date()

        last_date = self._meta_repo.get_last_date("lhb", "__market__")
        if last_date is not None and last_date >= today:
            stats.cached += 1
            return stats

        df = self._fetch_daily(today)
        if df is None:
            stats.fail += 1
            return stats

        self._raw_repo.upsert_lhb(df)
        self._meta_repo.set_last_date("lhb", "__market__", today, len(df))
        stats.ok += 1
        logger.info(f"龙虎榜 {today} 已保存：{len(df)} 只股票")
        return stats
```

- [ ] **Step 4: 运行测试，确认通过**

```bash
pytest tests/test_collector_signal.py -v
```

预期：3 passed

- [ ] **Step 5: 提交**

```bash
git add src/collectors/signal_collector.py tests/test_collector_signal.py
git commit -m "feat: rewrite SignalCollector with collect() DAL interface"
```

---

### Task 9: ReportCollector 重写 + 测试

**Files:**
- Create: `tests/test_collector_report.py`
- Modify: `src/collectors/report_collector.py`

- [ ] **Step 1: 写测试**

```python
# tests/test_collector_report.py
"""测试 ReportCollector.collect()"""
import sys
from datetime import date
from pathlib import Path
from unittest.mock import patch

import duckdb
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.dal.schema import migrate
from src.dal.raw_repo import RawRepo
from src.dal.meta_repo import MetaRepo
from src.collectors.report_collector import ReportCollector


@pytest.fixture
def repos():
    conn = duckdb.connect(":memory:")
    migrate(conn)
    return RawRepo(conn), MetaRepo(conn)


def _fake_reports_raw() -> pd.DataFrame:
    return pd.DataFrame({
        "日期": ["2024-01-02", "2024-01-03"],
        "机构": ["华泰证券", "中信证券"],
        "东财评级": ["买入", "增持"],
    })


def _fake_eps_raw() -> pd.DataFrame:
    return pd.DataFrame({
        "年度": ["2024", "2025"],
        "均值": [1.5, 2.0],
        "预测机构数": [10, 10],
    })


def test_collect_report_writes_rows(repos):
    raw_repo, meta_repo = repos
    collector = ReportCollector(raw_repo=raw_repo, meta_repo=meta_repo, mode="report")

    with patch("akshare.stock_research_report_em", return_value=_fake_reports_raw()):
        stats = collector.collect(codes=["000001"])

    assert stats.ok == 1
    result = raw_repo.load_reports("000001")
    assert len(result) == 2


def test_collect_eps_writes_snapshot(repos):
    raw_repo, meta_repo = repos
    collector = ReportCollector(raw_repo=raw_repo, meta_repo=meta_repo, mode="eps")

    with patch("akshare.stock_profit_forecast_ths", return_value=_fake_eps_raw()):
        stats = collector.collect(codes=["000001"])

    assert stats.ok == 1
    result = raw_repo.load_eps_snapshots("000001")
    assert len(result) == 1
    assert float(result.iloc[0]["eps_cur"]) == 1.5


def test_collect_report_network_failure(repos):
    raw_repo, meta_repo = repos
    collector = ReportCollector(raw_repo=raw_repo, meta_repo=meta_repo, mode="report")

    with patch("akshare.stock_research_report_em", return_value=None):
        stats = collector.collect(codes=["000001"])

    assert stats.fail == 1
    assert meta_repo.get_last_date("reports", "000001") is None
```

- [ ] **Step 2: 运行测试，确认失败**

```bash
pytest tests/test_collector_report.py -v
```

预期：FAIL，`TypeError: Can't instantiate abstract class ReportCollector`

- [ ] **Step 3: 完整替换 src/collectors/report_collector.py**

```python
"""研报 + EPS 共识采集器（DAL 版）"""
from __future__ import annotations

import logging
import time
from datetime import date

import akshare as ak
import pandas as pd
from tqdm import tqdm

from src.collectors.base import BaseCollector, CollectStats
from src.dal.meta_repo import MetaRepo
from src.dal.raw_repo import RawRepo

logger = logging.getLogger(__name__)

_REPORT_COL_MAP = {
    "日期": "date",
    "机构": "institution",
    "东财评级": "rating",
}


class ReportCollector(BaseCollector):
    """研报列表 + EPS 共识 → DuckDB reports / eps_snapshot 表。

    mode='report': 东财研报列表
    mode='eps':    同花顺 EPS 共识快照
    """

    def __init__(
        self,
        raw_repo: RawRepo | None = None,
        meta_repo: MetaRepo | None = None,
        delay: float = 0.5,
        mode: str = "report",
    ) -> None:
        if raw_repo is None:
            from src.dal.connection import get_db
            conn = get_db()
            raw_repo = RawRepo(conn)
            if meta_repo is None:
                meta_repo = MetaRepo(conn)
        if meta_repo is None:
            meta_repo = MetaRepo(raw_repo._conn)
        self._raw_repo = raw_repo
        self._meta_repo = meta_repo
        self.delay = delay
        self.mode = mode

    def _fetch_report(self, code: str) -> pd.DataFrame | None:
        try:
            raw = ak.stock_research_report_em(symbol=code)
        except Exception as exc:
            logger.debug(f"研报拉取失败 {code}: {exc}")
            return None
        if raw is None or raw.empty:
            return None
        cols_needed = [c for c in _REPORT_COL_MAP if c in raw.columns]
        df = raw[cols_needed].rename(columns=_REPORT_COL_MAP).copy()
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df["code"] = code
        df = df.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)
        return df if not df.empty else None

    def _fetch_eps(self, code: str) -> pd.DataFrame | None:
        try:
            raw = ak.stock_profit_forecast_ths(symbol=code, indicator="预测年报每股收益")
        except Exception as exc:
            logger.debug(f"EPS 共识拉取失败 {code}: {exc}")
            return None
        if raw is None or raw.empty:
            return None
        today = pd.Timestamp.today().normalize()
        rows = raw.sort_values("年度").reset_index(drop=True)
        eps_cur = rows["均值"].iloc[0] if len(rows) >= 1 else float("nan")
        eps_next = rows["均值"].iloc[1] if len(rows) >= 2 else float("nan")
        analyst_count = int(rows["预测机构数"].iloc[0]) if len(rows) >= 1 else 0
        return pd.DataFrame([{
            "snapshot_date": today,
            "code": code,
            "eps_cur": eps_cur,
            "eps_next": eps_next,
            "analyst_count": analyst_count,
        }])

    def collect(self, codes: list[str] = [], since: date | None = None) -> CollectStats:
        """批量采集研报或 EPS 共识并写入 DAL。"""
        stats = CollectStats()
        consecutive_errors = 0
        table = "reports" if self.mode == "report" else "eps_snapshot"

        for code in tqdm(codes, desc=f"研报采集[{self.mode}]"):
            last_date = since if since is not None else self._meta_repo.get_last_date(table, code)

            df = self._fetch_report(code) if self.mode == "report" else self._fetch_eps(code)

            if df is None or df.empty:
                stats.fail += 1
                consecutive_errors += 1
                if consecutive_errors >= 50:
                    logger.error("连续 50 次失败，终止采集")
                    break
                time.sleep(self.delay)
                continue

            consecutive_errors = 0
            if self.mode == "report":
                if last_date is not None:
                    df = df[df["date"] > pd.Timestamp(last_date)]
                if df.empty:
                    stats.cached += 1
                    time.sleep(self.delay)
                    continue
                self._raw_repo.upsert_reports(df)
                self._meta_repo.set_last_date("reports", code, df["date"].max().date(), len(df))
            else:
                self._raw_repo.upsert_eps_snapshot(df)
                self._meta_repo.set_last_date(
                    "eps_snapshot", code, df["snapshot_date"].max().date(), len(df)
                )

            stats.ok += 1
            time.sleep(self.delay)

        logger.info(f"研报采集[{self.mode}]完成：{stats}")
        return stats
```

- [ ] **Step 4: 运行测试，确认通过**

```bash
pytest tests/test_collector_report.py -v
```

预期：3 passed

- [ ] **Step 5: 提交**

```bash
git add src/collectors/report_collector.py tests/test_collector_report.py
git commit -m "feat: rewrite ReportCollector with collect() DAL interface"
```

---

### Task 10: TencentCollector 重写 + 测试

**Files:**
- Create: `tests/test_collector_tencent.py`
- Modify: `src/collectors/tencent_collector.py`

- [ ] **Step 1: 写测试**

```python
# tests/test_collector_tencent.py
"""测试 TencentCollector.collect()"""
import sys
from datetime import date
from pathlib import Path
from unittest.mock import patch

import duckdb
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.dal.schema import migrate
from src.dal.raw_repo import RawRepo
from src.dal.meta_repo import MetaRepo
from src.collectors.tencent_collector import TencentCollector


@pytest.fixture
def repos():
    conn = duckdb.connect(":memory:")
    migrate(conn)
    return RawRepo(conn), MetaRepo(conn)


_FAKE_BATCH = {
    "000001": {
        "pe_ttm": 15.0, "pe_static": 14.0, "pb": 1.5,
        "turnover_pct": 2.0, "mcap_yi": 500.0,
        "float_mcap_yi": 400.0, "price": 10.0,
    }
}


def test_collect_writes_today_snapshot(repos):
    raw_repo, meta_repo = repos
    collector = TencentCollector(raw_repo=raw_repo, meta_repo=meta_repo)

    with patch("src.collectors.tencent_collector._fetch_batch", return_value=_FAKE_BATCH):
        stats = collector.collect(codes=["000001"])

    assert stats.ok == 1
    result = raw_repo.load_fundamentals_snapshot("000001")
    assert len(result) == 1
    assert float(result.iloc[0]["pe_ttm"]) == 15.0


def test_collect_skips_if_already_collected_today(repos):
    raw_repo, meta_repo = repos
    meta_repo.set_last_date("fundamentals_snapshot", "000001", date.today())
    collector = TencentCollector(raw_repo=raw_repo, meta_repo=meta_repo)

    with patch("src.collectors.tencent_collector._fetch_batch") as mock_fetch:
        stats = collector.collect(codes=["000001"])

    assert stats.cached == 1
    assert not mock_fetch.called


def test_collect_handles_network_failure(repos):
    raw_repo, meta_repo = repos
    collector = TencentCollector(raw_repo=raw_repo, meta_repo=meta_repo)

    with patch("src.collectors.tencent_collector._fetch_batch", return_value={}):
        stats = collector.collect(codes=["000001"])

    assert stats.fail == 1
    assert meta_repo.get_last_date("fundamentals_snapshot", "000001") is None
```

- [ ] **Step 2: 运行测试，确认失败**

```bash
pytest tests/test_collector_tencent.py -v
```

预期：FAIL，`TypeError: Can't instantiate abstract class TencentCollector`

- [ ] **Step 3: 完整替换 src/collectors/tencent_collector.py**

```python
"""腾讯财经采集器（DAL 版）

腾讯财经 qt.gtimg.cn 接口特点：
- HTTP GET，GBK 编码，~分隔 88 字段，不封 IP
- 仅提供当前时点数据，无法拉取历史
- 每批最多 100 支，建议分批请求

写入表：fundamentals_snapshot（与 fundamentals 历史序列形成对应）
"""
from __future__ import annotations

import logging
import time
import urllib.request
from datetime import date, datetime

import pandas as pd
from tqdm import tqdm

from src.collectors.base import BaseCollector, CollectStats
from src.dal.meta_repo import MetaRepo
from src.dal.raw_repo import RawRepo

logger = logging.getLogger(__name__)

_BATCH_SIZE = 80
_TENCENT_URL = "https://qt.gtimg.cn/q="


def _get_prefix(code: str) -> str:
    if code.startswith(("6", "9")):
        return "sh"
    elif code.startswith("8"):
        return "bj"
    return "sz"


def _fetch_batch(codes: list[str]) -> dict[str, dict]:
    """批量拉取腾讯财经实时行情，返回 {code: {pe_ttm, pe_static, pb, ...}}。"""
    prefixed = [f"{_get_prefix(c)}{c}" for c in codes]
    url = _TENCENT_URL + ",".join(prefixed)
    req = urllib.request.Request(url)
    req.add_header("User-Agent", "Mozilla/5.0")
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        data = resp.read().decode("gbk")
    except Exception as exc:
        logger.warning(f"腾讯财经请求失败: {exc}")
        return {}

    result = {}
    for line in data.strip().split(";"):
        if not line.strip() or "=" not in line or '"' not in line:
            continue
        key = line.split("=")[0].split("_")[-1]
        vals = line.split('"')[1].split("~")
        if len(vals) < 53:
            continue
        code = key[2:]

        def _f(idx: int) -> float | None:
            v = vals[idx] if idx < len(vals) else ""
            try:
                return float(v) if v else None
            except ValueError:
                return None

        result[code] = {
            "pe_ttm":        _f(39),
            "pe_static":     _f(52),
            "pb":            _f(46),
            "turnover_pct":  _f(38),
            "mcap_yi":       _f(44),
            "float_mcap_yi": _f(45),
            "price":         _f(3),
        }
    return result


class TencentCollector(BaseCollector):
    """腾讯财经每日 PE/PB/市值快照 → DuckDB fundamentals_snapshot 表。"""

    def __init__(
        self,
        raw_repo: RawRepo | None = None,
        meta_repo: MetaRepo | None = None,
        delay: float = 0.1,
    ) -> None:
        if raw_repo is None:
            from src.dal.connection import get_db
            conn = get_db()
            raw_repo = RawRepo(conn)
            if meta_repo is None:
                meta_repo = MetaRepo(conn)
        if meta_repo is None:
            meta_repo = MetaRepo(raw_repo._conn)
        self._raw_repo = raw_repo
        self._meta_repo = meta_repo
        self.delay = delay

    def collect(self, codes: list[str] = [], since: date | None = None) -> CollectStats:
        """批量拉取今日腾讯快照，写入 DAL fundamentals_snapshot 表。"""
        stats = CollectStats()
        today = datetime.now().date()
        errors = 0

        for i in range(0, len(codes), _BATCH_SIZE):
            batch_codes = codes[i:i + _BATCH_SIZE]

            all_cached = all(
                self._meta_repo.get_last_date("fundamentals_snapshot", code) == today
                for code in batch_codes
            )
            if all_cached:
                stats.cached += len(batch_codes)
                continue

            rows = _fetch_batch(batch_codes)
            if not rows:
                errors += len(batch_codes)
                stats.fail += len(batch_codes)
                if errors >= 100:
                    logger.error(f"腾讯财经连续失败 {errors} 次，终止")
                    break
                continue

            errors = 0
            batch_rows = []
            for code in batch_codes:
                if code not in rows:
                    stats.fail += 1
                    continue
                row = dict(rows[code])
                row["date"] = pd.Timestamp(today)
                row["code"] = code
                batch_rows.append(row)

            if batch_rows:
                df = pd.DataFrame(batch_rows)
                self._raw_repo.upsert_fundamentals_snapshot(df)
                for code in df["code"].tolist():
                    self._meta_repo.set_last_date("fundamentals_snapshot", code, today, 1)
                stats.ok += len(batch_rows)

            if (i // _BATCH_SIZE + 1) % 10 == 0:
                logger.info(f"腾讯快照进度 {i + len(batch_codes)}/{len(codes)}  {stats}")

            time.sleep(self.delay)

        logger.info(f"腾讯快照采集完成：{stats}")
        return stats
```

- [ ] **Step 4: 运行测试，确认通过**

```bash
pytest tests/test_collector_tencent.py -v
```

预期：3 passed

- [ ] **Step 5: 运行全部测试确认无回归**

```bash
pytest tests/ -v
```

预期：全部通过（原 38 + 新 21 = 59 tests）

- [ ] **Step 6: 提交**

```bash
git add src/collectors/tencent_collector.py tests/test_collector_tencent.py
git commit -m "feat: rewrite TencentCollector to write fundamentals_snapshot via DAL"
```
