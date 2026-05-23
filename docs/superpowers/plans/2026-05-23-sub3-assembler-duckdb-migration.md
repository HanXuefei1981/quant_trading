# Sub-3: assembler.py DuckDB Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate the feature engineering layer from reading/writing Parquet files to reading via `RawRepo` and writing via `FeatureRepo`, with the `features` watermark stored in `MetaRepo` — no hardcoded dates, no Parquet I/O.

**Architecture:** All 7 raw data tables gain `since: date | None = None` in their `load_*` methods (B1). `assembler.py` and `report.py` replace Parquet calls with RawRepo calls, passing `since=lookback_date` in incremental mode (B2/B3). The write path switches from `to_parquet` to `FeatureRepo.upsert_features` and the watermark moves from `watermark.json` to `MetaRepo` (B4/B5). Downstream scripts switch from `pd.read_parquet` to `FeatureRepo.load_features` (B6).

**Tech Stack:** Python 3.10+, DuckDB, pandas, pytest, `unittest.mock.patch`

---

## File Map

| File | Action | Task |
|------|--------|------|
| `src/dal/raw_repo.py` | Modify — add `since` to 4 methods, add `load_all_lhb` | B1 |
| `tests/test_dal_raw_repo.py` | Modify — add tests for new `since` params and `load_all_lhb` | B1 |
| `src/features/assembler.py` | Modify — replace 5 `_load_*` helpers, `_get_kline_codes`, remove Parquet constants | B2, B4, B5 |
| `tests/test_assembler_read.py` | Create — test DAL-backed read helpers | B2 |
| `src/features/report.py` | Modify — replace `_load_reports`/`_load_eps` with RawRepo, add `since` | B3 |
| `tests/test_report_features.py` | Modify — replace `tmp_path` Parquet fixtures with in-memory RawRepo | B3 |
| `tests/test_assembler_write.py` | Create — test `assemble()` writes to FeatureRepo | B4 |
| `tests/test_assembler_watermark.py` | Create — test incremental watermark lifecycle | B5 |
| `src/data/watermark.py` | Modify — remove `"features"` key | B5 |
| `scripts/g1_no_vol_features.py` | Modify — replace `read_parquet` with `FeatureRepo.load_features` | B6 |
| `scripts/g2_cross_sectional_label.py` | Modify — read/write via FeatureRepo | B6 |
| `scripts/collect_m3_data.py` | Modify — replace `read_parquet` with FeatureRepo date query | B6 |

---

## Task 1 (B1): RawRepo — add `since` to 4 methods + `load_all_lhb`

**Files:**
- Modify: `src/dal/raw_repo.py`
- Modify: `tests/test_dal_raw_repo.py`

**Context:** The existing pattern (`load_kline`, `load_northbound`) already supports `since: date | None = None` with `WHERE date > ?`. Four methods (`load_fundamentals`, `load_fund_flow`, `load_reports`, `load_eps_snapshots`) are missing this param. A fifth method `load_all_lhb` (full-market lhb) needs to be added from scratch. All tests use `from src.dal.schema import migrate` on an in-memory connection.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_dal_raw_repo.py`:

```python
# ── load_fundamentals with since ───────────────────────────────────────────────

def _fund_df(code: str = "000001") -> pd.DataFrame:
    return pd.DataFrame({
        "date": pd.to_datetime(["2024-01-02", "2024-03-01"]),
        "code": code,
        "pe_ttm": [10.0, 11.0], "pe_static": [9.0, 9.5],
        "pb": [2.0, 2.1], "ps": [1.5, 1.6], "pcf": [8.0, 8.5],
        "peg": [0.5, 0.6], "market_cap": [1000.0, 1100.0],
        "float_market_cap": [800.0, 850.0],
        "total_shares": [10000.0, 10000.0], "float_shares": [8000.0, 8000.0],
    })

def test_load_fundamentals_no_since_returns_all(repo):
    repo.upsert_fundamentals(_fund_df())
    assert len(repo.load_fundamentals("000001")) == 2

def test_load_fundamentals_since_filters_rows(repo):
    repo.upsert_fundamentals(_fund_df())
    result = repo.load_fundamentals("000001", since=date(2024, 1, 2))
    assert len(result) == 1
    assert pd.to_datetime(result["date"].iloc[0]).date() == date(2024, 3, 1)

# ── load_fund_flow with since ──────────────────────────────────────────────────

def _flow_df(code: str = "000001") -> pd.DataFrame:
    return pd.DataFrame({
        "date": pd.to_datetime(["2024-01-02", "2024-03-01"]),
        "code": code,
        "major_net_inflow": [1e6, 2e6],
        "major_net_pct": [1.5, 2.5],
    })

def test_load_fund_flow_no_since_returns_all(repo):
    repo.upsert_fund_flow(_flow_df())
    assert len(repo.load_fund_flow("000001")) == 2

def test_load_fund_flow_since_filters_rows(repo):
    repo.upsert_fund_flow(_flow_df())
    result = repo.load_fund_flow("000001", since=date(2024, 1, 2))
    assert len(result) == 1
    assert pd.to_datetime(result["date"].iloc[0]).date() == date(2024, 3, 1)

# ── load_reports with since ────────────────────────────────────────────────────

def _reports_df(code: str = "000001") -> pd.DataFrame:
    return pd.DataFrame({
        "date": pd.to_datetime(["2024-01-02", "2024-03-01"]),
        "code": code,
        "institution": ["国信证券", "招商证券"],
        "rating": ["买入", "中性"],
    })

def test_load_reports_no_since_returns_all(repo):
    repo.upsert_reports(_reports_df())
    assert len(repo.load_reports("000001")) == 2

def test_load_reports_since_filters_rows(repo):
    repo.upsert_reports(_reports_df())
    result = repo.load_reports("000001", since=date(2024, 1, 2))
    assert len(result) == 1

# ── load_eps_snapshots with since ─────────────────────────────────────────────

def _eps_df(code: str = "000001") -> pd.DataFrame:
    return pd.DataFrame({
        "snapshot_date": pd.to_datetime(["2024-01-02", "2024-03-01"]),
        "code": code,
        "eps_cur": [1.0, 1.2],
        "eps_next": [1.1, 1.3],
        "analyst_count": [5, 6],
    })

def test_load_eps_snapshots_no_since_returns_all(repo):
    repo.upsert_eps_snapshot(_eps_df())
    assert len(repo.load_eps_snapshots("000001")) == 2

def test_load_eps_snapshots_since_filters_rows(repo):
    repo.upsert_eps_snapshot(_eps_df())
    result = repo.load_eps_snapshots("000001", since=date(2024, 1, 2))
    assert len(result) == 1
    assert pd.to_datetime(result["snapshot_date"].iloc[0]).date() == date(2024, 3, 1)

# ── load_all_lhb ──────────────────────────────────────────────────────────────

def _lhb_df() -> pd.DataFrame:
    return pd.DataFrame({
        "date": pd.to_datetime(["2024-01-02", "2024-01-02", "2024-03-01"]),
        "code": ["000001", "000002", "000001"],
        "lhb_net_buy": [1e6, 2e6, 3e6],
        "lhb_buy_amount": [5e6, 6e6, 7e6],
        "lhb_sell_amount": [4e6, 4e6, 4e6],
    })

def test_load_all_lhb_returns_all_codes(repo):
    repo.upsert_lhb(_lhb_df())
    result = repo.load_all_lhb()
    assert len(result) == 3
    assert set(result["code"].tolist()) == {"000001", "000002"}

def test_load_all_lhb_since_filters(repo):
    repo.upsert_lhb(_lhb_df())
    result = repo.load_all_lhb(since=date(2024, 1, 2))
    assert len(result) == 1
    assert pd.to_datetime(result["date"].iloc[0]).date() == date(2024, 3, 1)

def test_load_all_lhb_empty_table(repo):
    result = repo.load_all_lhb()
    assert isinstance(result, pd.DataFrame)
    assert result.empty
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd "/Users/hanxuefei/7、AI 空间/7-3、GitHub/quant_trading"
source .venv/bin/activate
pytest tests/test_dal_raw_repo.py -k "since or all_lhb" -v 2>&1 | tail -20
```

Expected: `FAILED` — `load_fundamentals() takes 2 positional arguments but 3 were given` (and similar).

- [ ] **Step 3: Implement the 5 method changes in `src/dal/raw_repo.py`**

Replace the 4 existing methods and add the new one:

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

def load_all_lhb(self, since: date | None = None) -> pd.DataFrame:
    """全市场龙虎榜，供 assembler 一次性加载后按 code 过滤。"""
    if since is not None:
        return self._conn.execute(
            "SELECT * FROM lhb WHERE date > ? ORDER BY date, code", [since]
        ).df()
    return self._conn.execute(
        "SELECT * FROM lhb ORDER BY date, code"
    ).df()
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_dal_raw_repo.py -v 2>&1 | tail -20
```

Expected: all tests PASS, no regressions on existing tests.

- [ ] **Step 5: Commit**

```bash
git add src/dal/raw_repo.py tests/test_dal_raw_repo.py
git commit -m "feat: add since param to 4 RawRepo load methods and load_all_lhb"
```

---

## Task 2 (B2): assembler.py — migrate read path

**Files:**
- Modify: `src/features/assembler.py`
- Create: `tests/test_assembler_read.py`

**Context:** Replace 5 Parquet-based `_load_*` helpers and `_get_kline_codes` with RawRepo calls. Add `raw_repo: RawRepo | None = None` to `assemble()` and `assemble_incremental()`. Full mode passes `since=None`; incremental mode passes `since=lookback_date` to all reads. Remove Parquet path constants. The `use_cache` parameter is removed (FeatureRepo upsert is idempotent). Feature-engineering functions (`add_all_features`, `add_report_features`, `add_signal_features`) are patched in tests.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_assembler_read.py`:

```python
"""Tests for assembler.py read-path DAL migration."""
import sys
from datetime import date
from pathlib import Path
from unittest.mock import patch, MagicMock

import duckdb
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.dal.schema import migrate
from src.dal.raw_repo import RawRepo


@pytest.fixture
def conn():
    c = duckdb.connect(":memory:")
    migrate(c)
    yield c
    c.close()


def _insert_kline(conn, code: str, dates: list[str]) -> None:
    for d in dates:
        conn.execute(
            "INSERT INTO kline VALUES (?, ?, 10.0, 11.0, 9.5, 10.5, 1e8, 1e6)",
            [d, code],
        )


# ── _get_kline_codes ───────────────────────────────────────────────────────────

def test_get_kline_codes_returns_distinct_sorted(conn):
    from src.features.assembler import _get_kline_codes

    _insert_kline(conn, "000002", ["2024-01-02"])
    _insert_kline(conn, "000001", ["2024-01-02"])

    raw_repo = RawRepo(conn)
    codes = _get_kline_codes(raw_repo)
    assert codes == ["000001", "000002"]


def test_get_kline_codes_empty_returns_empty(conn):
    from src.features.assembler import _get_kline_codes

    raw_repo = RawRepo(conn)
    assert _get_kline_codes(raw_repo) == []


# ── _load_northbound ──────────────────────────────────────────────────────────

def test_load_northbound_returns_none_when_empty(conn):
    from src.features.assembler import _load_northbound

    raw_repo = RawRepo(conn)
    assert _load_northbound(raw_repo) is None


def test_load_northbound_with_since(conn):
    from src.features.assembler import _load_northbound

    conn.execute("INSERT INTO northbound VALUES ('2024-01-02', 1e8, 5e7, 5e7)")
    conn.execute("INSERT INTO northbound VALUES ('2024-03-01', 2e8, 1e8, 1e8)")
    raw_repo = RawRepo(conn)

    result = _load_northbound(raw_repo, since=date(2024, 1, 2))
    assert result is not None
    assert len(result) == 1
    assert pd.to_datetime(result["date"].iloc[0]).date() == date(2024, 3, 1)


# ── _load_lhb_all ─────────────────────────────────────────────────────────────

def test_load_lhb_all_returns_none_when_empty(conn):
    from src.features.assembler import _load_lhb_all

    raw_repo = RawRepo(conn)
    assert _load_lhb_all(raw_repo) is None


def test_load_lhb_all_with_since(conn):
    from src.features.assembler import _load_lhb_all

    conn.execute("INSERT INTO lhb VALUES ('2024-01-02', '000001', 1e6, 5e6, 4e6)")
    conn.execute("INSERT INTO lhb VALUES ('2024-03-01', '000001', 2e6, 6e6, 4e6)")
    raw_repo = RawRepo(conn)

    result = _load_lhb_all(raw_repo, since=date(2024, 1, 2))
    assert result is not None
    assert len(result) == 1


# ── assemble() raises when kline is empty ─────────────────────────────────────

def test_assemble_raises_when_no_kline(conn):
    from src.features.assembler import assemble
    from src.dal.feature_repo import FeatureRepo

    raw_repo = RawRepo(conn)
    feature_repo = FeatureRepo(conn)

    with pytest.raises(RuntimeError, match="kline"):
        assemble(raw_repo=raw_repo, feature_repo=feature_repo)


# ── incremental mode passes since to all reads ────────────────────────────────

def test_incremental_passes_since_to_load_kline(conn):
    """In incremental mode, load_kline is called with since=lookback_date."""
    from src.features.assembler import assemble_incremental
    from src.dal.feature_repo import FeatureRepo
    from src.dal.meta_repo import MetaRepo

    raw_repo = RawRepo(conn)
    feature_repo = FeatureRepo(conn)
    meta_repo = MetaRepo(conn)
    meta_repo.set_last_date("features", "__market__", date(2024, 3, 1))

    with patch.object(raw_repo, "load_kline", return_value=pd.DataFrame()) as mock_load:
        with patch("src.features.assembler._get_kline_codes", return_value=["000001"]):
            with patch("src.features.assembler._load_northbound", return_value=None):
                with patch("src.features.assembler._load_lhb_all", return_value=None):
                    assemble_incremental(
                        raw_repo=raw_repo,
                        feature_repo=feature_repo,
                        meta_repo=meta_repo,
                    )
    mock_load.assert_called_once()
    call_kwargs = mock_load.call_args
    # since should be lookback_date = date(2024, 3, 1) - 300 days
    from datetime import timedelta
    expected_since = date(2024, 3, 1) - timedelta(days=300)
    assert call_kwargs[1].get("since") == expected_since or call_kwargs[0][1] == expected_since
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_assembler_read.py -v 2>&1 | tail -20
```

Expected: `ImportError` or `AttributeError` — `_get_kline_codes`, `_load_northbound`, `_load_lhb_all` still have old signatures.

- [ ] **Step 3: Rewrite the read-path in `src/features/assembler.py`**

**3a. Update imports** — remove Parquet-specific imports, add DAL imports:

```python
# Remove these imports:
#   from config.settings import PROCESSED_DIR, DATA_DIR, MIN_TRADE_DAYS
#   (DATA_DIR and PROCESSED_DIR no longer needed)
# Keep:
from config.settings import MIN_TRADE_DAYS

# Add:
from src.dal.raw_repo import RawRepo
from src.dal.feature_repo import FeatureRepo
```

**3b. Remove Parquet path constants** — delete these 5 lines:

```python
# DELETE:
KLINE_DIR = DATA_DIR / "raw" / "kline"
FUNDAMENTALS_DIR = DATA_DIR / "fundamentals"
FUND_FLOW_DIR = DATA_DIR / "fund_flow"
NORTHBOUND_PATH = DATA_DIR / "raw" / "northbound.parquet"
LHB_DIR = DATA_DIR / "raw" / "lhb"
```

**3c. Replace `_get_kline_codes`**:

```python
def _get_kline_codes(raw_repo: RawRepo) -> list[str]:
    return [r[0] for r in raw_repo._conn.execute(
        "SELECT DISTINCT code FROM kline ORDER BY code"
    ).fetchall()]
```

**3d. Replace `_load_fundamentals`**:

```python
def _load_fundamentals(code: str, raw_repo: RawRepo, since: date | None = None) -> Optional[pd.DataFrame]:
    df = raw_repo.load_fundamentals(code, since=since)
    if df.empty:
        return None
    df["date"] = pd.to_datetime(df["date"])
    return df
```

**3e. Replace `_load_fund_flow`**:

```python
def _load_fund_flow(code: str, raw_repo: RawRepo, since: date | None = None) -> Optional[pd.DataFrame]:
    df = raw_repo.load_fund_flow(code, since=since)
    if df.empty:
        return None
    df["date"] = pd.to_datetime(df["date"])
    return df
```

**3f. Replace `_load_northbound`**:

```python
def _load_northbound(raw_repo: RawRepo, since: date | None = None) -> Optional[pd.DataFrame]:
    df = raw_repo.load_northbound(since=since)
    if df.empty:
        return None
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date").reset_index(drop=True)
```

**3g. Replace `_load_lhb_all`**:

```python
def _load_lhb_all(raw_repo: RawRepo, since: date | None = None) -> Optional[pd.DataFrame]:
    df = raw_repo.load_all_lhb(since=since)
    if df.empty:
        return None
    df["date"] = pd.to_datetime(df["date"])
    return df.drop_duplicates(subset=["date", "code"]).sort_values("date")
```

**3h. Update `_merge_fundamentals` and `_merge_fund_flow`** to accept `raw_repo` and `since`:

```python
def _merge_fundamentals(kline: pd.DataFrame, code: str, raw_repo: RawRepo, since: date | None = None) -> pd.DataFrame:
    fund_df = _load_fundamentals(code, raw_repo, since=since)
    if fund_df is None or fund_df.empty:
        return kline
    fund_df = fund_df.copy()
    cols = ["date"] + [c for c in _FUND_VALUE_COLS if c in fund_df.columns]
    merged = kline.merge(fund_df[cols], on="date", how="left")
    if "float_shares" in merged.columns and "turnover" not in merged.columns:
        with np.errstate(divide="ignore", invalid="ignore"):
            merged["turnover"] = merged["volume"] / (merged["float_shares"] + 1e-9) * 100.0
        merged.loc[~np.isfinite(merged["turnover"]), "turnover"] = np.nan
    return merged


def _merge_fund_flow(kline: pd.DataFrame, code: str, raw_repo: RawRepo, since: date | None = None) -> pd.DataFrame:
    flow_df = _load_fund_flow(code, raw_repo, since=since)
    if flow_df is None or flow_df.empty:
        return kline
    flow_df = flow_df.copy()
    cols = ["date"] + [c for c in _FLOW_COLS if c in flow_df.columns]
    return kline.merge(flow_df[cols], on="date", how="left")
```

**3i. Update `assemble()` signature** — add `raw_repo` and `feature_repo`, remove `use_cache`:

```python
def assemble(
    raw_repo: RawRepo | None = None,
    feature_repo: FeatureRepo | None = None,
    codes: Optional[list[str]] = None,
    sample_size: Optional[int] = None,
) -> pd.DataFrame:
    if raw_repo is None or feature_repo is None:
        from src.dal.connection import get_db
        conn = get_db()
        if raw_repo is None:
            raw_repo = RawRepo(conn)
        if feature_repo is None:
            feature_repo = FeatureRepo(conn)
```

**3j. Replace Parquet reads inside `assemble()` loop**:

Replace the kline file check and `pd.read_parquet(kline_path)` block with:
```python
raw = raw_repo.load_kline(code)
if raw.empty or len(raw) < MIN_TRADE_DAYS:
    skipped += 1
    continue
```

Replace `_merge_fundamentals(raw, code)` with `_merge_fundamentals(raw, code, raw_repo)`.
Replace `_merge_fund_flow(raw, code)` with `_merge_fund_flow(raw, code, raw_repo)`.

Replace `north_df = _load_northbound()` with `north_df = _load_northbound(raw_repo)`.
Replace `lhb_df = _load_lhb_all()` with `lhb_df = _load_lhb_all(raw_repo)`.

Replace `codes = _get_kline_codes()` with `codes = _get_kline_codes(raw_repo)`.
Replace error message: `"kline 表无数据，请先运行: python main.py collect"`.

Also update the `add_report_features` call to pass `raw_repo`:
```python
df = add_report_features(df, code, raw_repo=raw_repo)
```

**3k. Update `assemble_incremental()` read path** (write path done in B4, watermark in B5):

```python
def assemble_incremental(
    raw_repo: RawRepo | None = None,
    feature_repo: FeatureRepo | None = None,
    meta_repo=None,
) -> pd.DataFrame:
    if raw_repo is None or feature_repo is None:
        from src.dal.connection import get_db
        conn = get_db()
        if raw_repo is None:
            raw_repo = RawRepo(conn)
        if feature_repo is None:
            feature_repo = FeatureRepo(conn)
        if meta_repo is None:
            from src.dal.meta_repo import MetaRepo as _MetaRepo
            meta_repo = _MetaRepo(conn)
```

Inside the incremental loop, replace `pd.read_parquet(kline_path)` with:
```python
raw = raw_repo.load_kline(code, since=lookback_date)
if raw.empty:
    skipped += 1
    continue
```

Replace `_merge_fundamentals(raw, code)` with `_merge_fundamentals(raw, code, raw_repo, since=lookback_date)`.
Replace `_merge_fund_flow(raw, code)` with `_merge_fund_flow(raw, code, raw_repo, since=lookback_date)`.
Replace `_load_northbound()` with `_load_northbound(raw_repo, since=lookback_date)`.
Replace `_load_lhb_all()` with `_load_lhb_all(raw_repo, since=lookback_date)`.
Replace `add_report_features(df, code)` with `add_report_features(df, code, raw_repo=raw_repo, since=lookback_date)`.
Replace `_get_kline_codes()` with `_get_kline_codes(raw_repo)`.

Remove the `kline_path.exists()` check and `PROCESSED_DIR.mkdir()` call.

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_assembler_read.py -v 2>&1 | tail -20
```

Expected: all PASS.

- [ ] **Step 5: Run full test suite to check for regressions**

```bash
pytest tests/ -x --ignore=tests/test_db_populate.py -q 2>&1 | tail -20
```

Expected: no new failures.

- [ ] **Step 6: Commit**

```bash
git add src/features/assembler.py tests/test_assembler_read.py
git commit -m "feat: assembler read path — replace Parquet with RawRepo, add since support"
```

---

## Task 3 (B3): report.py — migrate read path

**Files:**
- Modify: `src/features/report.py`
- Modify: `tests/test_report_features.py`

**Context:** `add_report_features(df, code, base_dir=None)` currently loads Parquet from `base_dir/reports/{code}.parquet` and `base_dir/eps/{code}.parquet`. Replace with `RawRepo` calls. Add `since: date | None = None` to both private loaders and propagate through the public API. Existing tests use `tmp_path` to write Parquet fixtures — replace with in-memory RawRepo fixtures.

- [ ] **Step 1: Write the failing tests**

Replace the content of `tests/test_report_features.py` with the following (keep the existing `_make_kline` helper, replace Parquet fixtures with in-memory DB):

```python
"""Tests for report.py — DAL-backed version."""
import sys
from datetime import date
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.dal.schema import migrate
from src.dal.raw_repo import RawRepo


@pytest.fixture
def conn():
    c = duckdb.connect(":memory:")
    migrate(c)
    yield c
    c.close()


def _make_kline(dates: list[str]) -> pd.DataFrame:
    return pd.DataFrame({
        "date": pd.to_datetime(dates),
        "code": "000001",
        "close": [10.0] * len(dates),
    })


def _insert_reports(conn, dates: list[str], code: str = "000001") -> None:
    for i, d in enumerate(dates):
        conn.execute(
            "INSERT INTO reports VALUES (?, ?, ?, ?)",
            [d, code, f"机构{i}", "买入"],
        )


def _insert_eps(conn, snapshot_dates: list[str], eps_vals: list[float], code: str = "000001") -> None:
    for d, eps in zip(snapshot_dates, eps_vals):
        conn.execute(
            "INSERT INTO eps_snapshot VALUES (?, ?, ?, ?, ?)",
            [d, code, eps, eps + 0.1, 5],
        )


# ── report_count_30d ──────────────────────────────────────────────────────────

def test_report_count_30d_rolling(conn):
    from src.features.report import add_report_features

    _insert_reports(conn, ["2026-05-01", "2026-05-10"])
    kline = _make_kline(["2026-05-11", "2026-05-12", "2026-05-13"])
    raw_repo = RawRepo(conn)

    result = add_report_features(kline, "000001", raw_repo=raw_repo)
    assert "report_count_30d" in result.columns
    assert result["report_count_30d"].iloc[0] == 2


def test_no_reports_fills_nan(conn):
    from src.features.report import add_report_features

    kline = _make_kline(["2026-05-11"])
    raw_repo = RawRepo(conn)

    result = add_report_features(kline, "000001", raw_repo=raw_repo)
    assert result["report_count_30d"].isna().all()
    assert result["analyst_count"].isna().all()


# ── eps features ──────────────────────────────────────────────────────────────

def test_eps_consensus_forward_fill(conn):
    from src.features.report import add_report_features

    _insert_eps(conn, ["2026-01-01", "2026-04-01"], [1.0, 1.2])
    kline = _make_kline(["2026-02-01", "2026-05-01"])
    raw_repo = RawRepo(conn)

    result = add_report_features(kline, "000001", raw_repo=raw_repo)
    assert result["eps_consensus_cur"].iloc[0] == pytest.approx(1.0)
    assert result["eps_consensus_cur"].iloc[1] == pytest.approx(1.2)


def test_eps_revision_direction(conn):
    from src.features.report import add_report_features

    _insert_eps(conn, ["2026-01-01", "2026-04-01"], [1.0, 1.2])
    kline = _make_kline(["2026-05-01"])
    raw_repo = RawRepo(conn)

    result = add_report_features(kline, "000001", raw_repo=raw_repo)
    assert result["eps_revision"].iloc[0] == 1  # upward revision


# ── since param propagation ───────────────────────────────────────────────────

def test_since_filters_reports(conn):
    """With since=date, only reports after since are loaded."""
    from src.features.report import add_report_features

    _insert_reports(conn, ["2024-01-01", "2024-06-01"])
    kline = _make_kline(["2024-07-01"])
    raw_repo = RawRepo(conn)

    # With since filtering to only June report
    result = add_report_features(kline, "000001", raw_repo=raw_repo, since=date(2024, 1, 1))
    # Only the 2024-06-01 report is visible (available_date = 2024-06-02 ≤ 2024-07-01)
    assert result["report_count_30d"].iloc[0] == 1
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_report_features.py -v 2>&1 | tail -20
```

Expected: `FAILED` — `add_report_features()` still expects `base_dir` not `raw_repo`.

- [ ] **Step 3: Rewrite `src/features/report.py`**

**3a. Update imports** — remove `Path`-based loading, add RawRepo:

```python
# Remove: from pathlib import Path
# Add:
from datetime import date
from src.dal.raw_repo import RawRepo
```

**3b. Replace `_load_reports`**:

```python
def _load_reports(code: str, raw_repo: RawRepo, since: date | None = None) -> Optional[pd.DataFrame]:
    df = raw_repo.load_reports(code, since=since)
    if df.empty:
        return None
    df["date"] = pd.to_datetime(df["date"])
    return df
```

**3c. Replace `_load_eps`**:

```python
def _load_eps(code: str, raw_repo: RawRepo, since: date | None = None) -> Optional[pd.DataFrame]:
    df = raw_repo.load_eps_snapshots(code, since=since)
    if df.empty:
        return None
    df["snapshot_date"] = pd.to_datetime(df["snapshot_date"])
    return df.sort_values("snapshot_date").reset_index(drop=True)
```

**3d. Update `add_report_features` signature**:

```python
def add_report_features(
    df: pd.DataFrame,
    code: str,
    raw_repo: RawRepo | None = None,
    since: date | None = None,
) -> pd.DataFrame:
    if raw_repo is None:
        from src.dal.connection import get_db
        from src.dal.raw_repo import RawRepo as _RawRepo
        raw_repo = _RawRepo(get_db())
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = _add_report_counts(df, code, raw_repo, since=since)
    df = _add_eps_features(df, code, raw_repo, since=since)
    return df
```

**3e. Update `_add_report_counts` and `_add_eps_features`** — replace `base: Path` with `raw_repo: RawRepo, since: date | None = None` and call the new private loaders. The rolling-window and merge_asof logic is unchanged:

```python
def _add_report_counts(df: pd.DataFrame, code: str, raw_repo: RawRepo, since: date | None = None) -> pd.DataFrame:
    reports = _load_reports(code, raw_repo, since=since)
    if reports is None or reports.empty:
        df["analyst_count"] = np.nan
        df["report_count_30d"] = np.nan
        return df

    reports = reports.copy()
    reports["available_date"] = reports["date"] + pd.Timedelta(days=1)
    dates = df["date"].values
    report_counts = []
    analyst_counts = []
    for d in dates:
        d_ts = pd.Timestamp(d)
        window_start = pd.bdate_range(end=d_ts, periods=_REPORT_WINDOW + 1)[0]
        visible = reports[
            (reports["available_date"] <= d_ts)
            & (reports["available_date"] > window_start)
        ]
        report_counts.append(len(visible))
        analyst_counts.append(visible["institution"].nunique() if not visible.empty else 0)
    df["report_count_30d"] = report_counts
    df["analyst_count"] = analyst_counts
    return df


def _add_eps_features(df: pd.DataFrame, code: str, raw_repo: RawRepo, since: date | None = None) -> pd.DataFrame:
    eps = _load_eps(code, raw_repo, since=since)
    if eps is None or eps.empty:
        df["eps_consensus_cur"] = np.nan
        df["eps_revision"] = np.nan
        return df

    eps = eps.drop_duplicates("snapshot_date", keep="last").sort_values("snapshot_date").reset_index(drop=True)
    eps["eps_revision"] = np.sign(eps["eps_cur"].diff()).fillna(0).astype(int)
    df_sorted = df.sort_values("date").reset_index(drop=True)
    merged = pd.merge_asof(
        df_sorted,
        eps[["snapshot_date", "eps_cur", "eps_revision", "analyst_count"]].rename(
            columns={
                "snapshot_date": "date",
                "eps_cur": "_eps_cur",
                "eps_revision": "_eps_revision",
                "analyst_count": "_analyst_count_eps",
            }
        ),
        on="date",
        direction="backward",
    )
    if "analyst_count" not in df.columns or df["analyst_count"].isna().all():
        merged["analyst_count"] = merged.pop("_analyst_count_eps")
    else:
        merged = merged.drop(columns=["_analyst_count_eps"], errors="ignore")
    merged["eps_consensus_cur"] = merged.pop("_eps_cur")
    merged["eps_revision"] = merged.pop("_eps_revision")
    return merged.sort_values("date").reset_index(drop=True)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_report_features.py -v 2>&1 | tail -20
```

Expected: all PASS.

- [ ] **Step 5: Run full suite**

```bash
pytest tests/ -x --ignore=tests/test_db_populate.py -q 2>&1 | tail -20
```

- [ ] **Step 6: Commit**

```bash
git add src/features/report.py tests/test_report_features.py
git commit -m "feat: report.py read path — replace Parquet with RawRepo, add since param"
```

---

## Task 4 (B4): assembler.py — migrate write path

**Files:**
- Modify: `src/features/assembler.py`
- Create: `tests/test_assembler_write.py`

**Context:** Replace all `to_parquet` calls with `FeatureRepo.upsert_features`. Rewrite `assemble_inference()` to load the latest cross-section from `FeatureRepo`. Remove `PROCESSED_DIR` usage entirely. The watermark (`wm.get_since` / `wm.update`) is left for B5 — this task only touches the I/O path.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_assembler_write.py`:

```python
"""Tests for assembler.py write-path DAL migration."""
import sys
from datetime import date
from pathlib import Path
from unittest.mock import patch, MagicMock
import numpy as np
import duckdb
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.dal.schema import migrate
from src.dal.raw_repo import RawRepo
from src.dal.feature_repo import FeatureRepo


@pytest.fixture
def conn():
    c = duckdb.connect(":memory:")
    migrate(c)
    yield c
    c.close()


def _seed_kline(conn, code: str = "000001", n: int = 300) -> None:
    """Insert n rows of synthetic kline data."""
    from datetime import timedelta
    base = date(2023, 1, 1)
    for i in range(n):
        d = base + timedelta(days=i)
        conn.execute(
            "INSERT OR IGNORE INTO kline VALUES (?, ?, 10.0, 11.0, 9.5, 10.5, 1e8, 1e6)",
            [d.isoformat(), code],
        )


def _make_combined(code: str = "000001") -> pd.DataFrame:
    """Minimal features DataFrame that FeatureRepo can store."""
    return pd.DataFrame({
        "date": pd.to_datetime(["2024-01-02", "2024-01-03"]),
        "code": code,
        "close": [10.5, 11.0],
        "future_ret": [0.01, 0.02],
        "ret1": [0.005, 0.01],
        "label": [1, 2],
    })


# ── assemble() writes to FeatureRepo ─────────────────────────────────────────

def test_assemble_writes_to_feature_repo(conn):
    from src.features.assembler import assemble

    _seed_kline(conn, "000001", n=300)
    raw_repo = RawRepo(conn)
    feature_repo = FeatureRepo(conn)
    combined = _make_combined()

    with patch("src.features.assembler.add_all_features", return_value=combined), \
         patch("src.features.assembler.add_report_features", side_effect=lambda df, c, **kw: df), \
         patch("src.features.assembler.add_signal_features", side_effect=lambda df, c, **kw: df), \
         patch("src.features.assembler.add_cross_sectional_label", side_effect=lambda df: df.assign(label=1)), \
         patch("src.features.assembler.preprocess_features", side_effect=lambda df, cols: df), \
         patch("src.features.assembler._load_northbound", return_value=None), \
         patch("src.features.assembler._load_lhb_all", return_value=None):
        assemble(raw_repo=raw_repo, feature_repo=feature_repo)

    date_range = feature_repo.get_feature_date_range()
    assert date_range is not None, "features table should have data after assemble()"


# ── assemble() is idempotent ──────────────────────────────────────────────────

def test_assemble_is_idempotent(conn):
    from src.features.assembler import assemble

    _seed_kline(conn, "000001", n=300)
    raw_repo = RawRepo(conn)
    feature_repo = FeatureRepo(conn)
    combined = _make_combined()

    kwargs = dict(
        raw_repo=raw_repo,
        feature_repo=feature_repo,
    )
    patches = [
        patch("src.features.assembler.add_all_features", return_value=combined),
        patch("src.features.assembler.add_report_features", side_effect=lambda df, c, **kw: df),
        patch("src.features.assembler.add_signal_features", side_effect=lambda df, c, **kw: df),
        patch("src.features.assembler.add_cross_sectional_label", side_effect=lambda df: df.assign(label=1)),
        patch("src.features.assembler.preprocess_features", side_effect=lambda df, cols: df),
        patch("src.features.assembler._load_northbound", return_value=None),
        patch("src.features.assembler._load_lhb_all", return_value=None),
    ]
    import contextlib
    with contextlib.ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        assemble(**kwargs)
        assemble(**kwargs)  # second run — should not duplicate rows

    result = conn.execute("SELECT COUNT(*) FROM features").fetchone()[0]
    assert result == len(combined), "idempotent upsert must not duplicate rows"


# ── assemble_inference() loads latest cross-section ──────────────────────────

def test_assemble_inference_returns_latest_date(conn):
    from src.features.assembler import assemble_inference

    feature_repo = FeatureRepo(conn)
    df1 = _make_combined("000001")
    df2 = pd.DataFrame({
        "date": pd.to_datetime(["2024-01-05"]),
        "code": "000002",
        "close": [20.0],
        "future_ret": [0.03],
        "ret1": [0.015],
        "label": [0],
    })
    feature_repo.upsert_features(pd.concat([df1, df2], ignore_index=True))

    with patch("src.features.assembler.get_feature_columns", return_value=[]), \
         patch("src.features.assembler.preprocess_features", side_effect=lambda df, cols: df):
        result = assemble_inference(feature_repo=feature_repo)

    # Latest date in features is 2024-01-05 (from df2)
    assert pd.to_datetime(result["date"]).dt.date.max() == date(2024, 1, 5)


def test_assemble_inference_raises_when_empty(conn):
    from src.features.assembler import assemble_inference

    feature_repo = FeatureRepo(conn)
    with pytest.raises(FileNotFoundError, match="features"):
        assemble_inference(feature_repo=feature_repo)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_assembler_write.py -v 2>&1 | tail -20
```

Expected: `FAILED` — `assemble()` still tries to write Parquet; `assemble_inference()` still reads from `PROCESSED_DIR`.

- [ ] **Step 3: Rewrite write path in `src/features/assembler.py`**

**3a. In `assemble()`** — remove `PROCESSED_DIR.mkdir()` and per-stock Parquet write; remove `use_cache` logic; remove market-features Parquet write. Add FeatureRepo write at the end:

```python
# REMOVE these lines in assemble():
#   PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
#   processed_path = PROCESSED_DIR / f"{code}.parquet"
#   if use_cache and processed_path.exists(): ...
#   df.to_parquet(processed_path, index=False)
#   out_path = PROCESSED_DIR / "market_features.parquet"
#   combined.to_parquet(out_path, index=False)

# REPLACE the final to_parquet with:
feature_repo.upsert_features(combined)
logger.info(f"全市场特征已写入 FeatureRepo，共 {len(combined)} 行")
return combined
```

**3b. In `assemble_incremental()`** — remove per-stock Parquet append and market_features Parquet append:

```python
# REMOVE in assemble_incremental():
#   processed_path = PROCESSED_DIR / f"{code}.parquet"
#   if processed_path.exists(): existing = pd.read_parquet(...)
#   combined_stock.to_parquet(processed_path, index=False)
#   mf_path = PROCESSED_DIR / "market_features.parquet"
#   if mf_path.exists(): existing_mf = pd.read_parquet(...)
#   merged_mf.to_parquet(mf_path, index=False)

# REPLACE with:
feature_repo.upsert_features(combined)
```

**3c. Rewrite `assemble_inference()`**:

```python
def assemble_inference(feature_repo: FeatureRepo | None = None) -> pd.DataFrame:
    """推断模式：从 FeatureRepo 加载最新截面特征（scan 专用，无需标签）。"""
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

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_assembler_write.py -v 2>&1 | tail -20
```

Expected: all PASS.

- [ ] **Step 5: Run full suite**

```bash
pytest tests/ -x --ignore=tests/test_db_populate.py -q 2>&1 | tail -20
```

- [ ] **Step 6: Commit**

```bash
git add src/features/assembler.py tests/test_assembler_write.py
git commit -m "feat: assembler write path — replace to_parquet with FeatureRepo, rewrite assemble_inference"
```

---

## Task 5 (B5): assembler.py — watermark migration to MetaRepo

**Files:**
- Modify: `src/features/assembler.py`
- Modify: `src/data/watermark.py`
- Create: `tests/test_assembler_watermark.py`

**Context:** `assemble_incremental()` currently uses `wm.get_since("features")` and `wm.update("features", date)` from a JSON file with hardcoded dates. Replace with `MetaRepo.get_last_date("features", "__market__")` / `set_last_date(...)`. The `"features"` key is removed from `watermark.py`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_assembler_watermark.py`:

```python
"""Tests for assembler.py watermark lifecycle via MetaRepo."""
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
from src.dal.feature_repo import FeatureRepo
from src.dal.meta_repo import MetaRepo


@pytest.fixture
def conn():
    c = duckdb.connect(":memory:")
    migrate(c)
    yield c
    c.close()


# ── first run (no watermark) triggers full assemble ──────────────────────────

def test_incremental_no_watermark_delegates_to_full_assemble(conn):
    """When features watermark is absent, assemble_incremental() delegates to assemble()
    and writes the watermark afterwards."""
    from src.features.assembler import assemble_incremental

    raw_repo = RawRepo(conn)
    feature_repo = FeatureRepo(conn)
    meta_repo = MetaRepo(conn)

    assert meta_repo.get_last_date("features", "__market__") is None

    mock_result = pd.DataFrame({
        "date": pd.to_datetime(["2024-05-01"]),
        "code": "000001",
        "future_ret": [0.01],
        "ret1": [0.005],
        "label": [1],
    })

    with patch("src.features.assembler.assemble", return_value=mock_result) as mock_assemble:
        assemble_incremental(raw_repo=raw_repo, feature_repo=feature_repo, meta_repo=meta_repo)

    mock_assemble.assert_called_once()
    since = meta_repo.get_last_date("features", "__market__")
    assert since == date(2024, 5, 1), "watermark must be set to max date from assemble() result"


# ── incremental run reads watermark from MetaRepo ────────────────────────────

def test_incremental_reads_watermark_from_metarepo(conn):
    from src.features.assembler import assemble_incremental

    raw_repo = RawRepo(conn)
    feature_repo = FeatureRepo(conn)
    meta_repo = MetaRepo(conn)
    meta_repo.set_last_date("features", "__market__", date(2024, 3, 1))

    called_since = {}

    original_load = RawRepo.load_kline
    def spy_load_kline(self, code, since=None):
        called_since["since"] = since
        return pd.DataFrame()  # empty → skipped

    with patch.object(RawRepo, "load_kline", spy_load_kline), \
         patch("src.features.assembler._get_kline_codes", return_value=["000001"]), \
         patch("src.features.assembler._load_northbound", return_value=None), \
         patch("src.features.assembler._load_lhb_all", return_value=None):
        assemble_incremental(
            raw_repo=raw_repo,
            feature_repo=feature_repo,
            meta_repo=meta_repo,
        )

    from datetime import timedelta
    expected = date(2024, 3, 1) - timedelta(days=300)
    assert called_since.get("since") == expected, \
        f"Expected since={expected}, got {called_since.get('since')}"


# ── watermark is updated after incremental run ────────────────────────────────

def test_watermark_updated_after_incremental(conn):
    from src.features.assembler import assemble_incremental

    raw_repo = RawRepo(conn)
    feature_repo = FeatureRepo(conn)
    meta_repo = MetaRepo(conn)
    meta_repo.set_last_date("features", "__market__", date(2024, 4, 1))

    new_data = pd.DataFrame({
        "date": pd.to_datetime(["2024-05-01"]),
        "code": "000001",
        "future_ret": [0.01],
        "ret1": [0.005],
        "label": [1],
    })

    import contextlib
    with contextlib.ExitStack() as stack:
        for p in _noop_patches():
            stack.enter_context(p)
        assemble_incremental(
            raw_repo=raw_repo,
            feature_repo=feature_repo,
            meta_repo=meta_repo,
        )

    updated = meta_repo.get_last_date("features", "__market__")
    assert updated is not None
    assert updated >= date(2024, 4, 1), "watermark must advance after incremental run"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_assembler_watermark.py -v 2>&1 | tail -20
```

Expected: `FAILED` — `assemble_incremental()` still imports `watermark.py`.

- [ ] **Step 3: Replace watermark calls in `src/features/assembler.py`**

In `assemble_incremental()`, remove these lines:
```python
# REMOVE:
from src.data import watermark as wm
since = wm.get_since("features")
since_ts = pd.Timestamp(since) if since is not None else None
lookback_ts = pd.Timestamp(since - timedelta(days=300)) if since is not None else None

if since_ts is not None:
    logger.info(...)
else:
    logger.info("无特征水位记录，执行全量组装（等同于 assemble()）")
    return assemble()
```

Replace with:
```python
since = meta_repo.get_last_date("features", "__market__")
if since is None:
    logger.info("features 水位为空，执行全量组装")
    result = assemble(raw_repo=raw_repo, feature_repo=feature_repo)
    if not result.empty:
        new_max = result["date"].max().date()
        meta_repo.set_last_date("features", "__market__", new_max, row_count=len(result))
    return result

since_ts = pd.Timestamp(since)
lookback_date = since - timedelta(days=300)
lookback_ts = pd.Timestamp(lookback_date)
logger.info(f"增量模式：处理 date > {since} 的新数据（回看窗口起点: {lookback_date}）")
```

Remove the trailing `wm.update("features", new_max_date)` call and replace with:
```python
meta_repo.set_last_date("features", "__market__", new_max_date, row_count=len(combined))
```

Remove the existing log line that referenced `market_features.parquet`.

- [ ] **Step 4: Remove `"features"` key from `src/data/watermark.py`**

In `watermark.py`, find and remove the `"features"` entry from the watermark JSON/dict. The comment block also references `market_features.parquet` — update it to note this key is now in MetaRepo:

```python
# "features" key removed — features watermark is now managed by MetaRepo
# (collect_log table, scope "__market__")
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
pytest tests/test_assembler_watermark.py -v 2>&1 | tail -20
```

Expected: all PASS.

- [ ] **Step 6: Run full suite**

```bash
pytest tests/ -x --ignore=tests/test_db_populate.py -q 2>&1 | tail -20
```

- [ ] **Step 7: Commit**

```bash
git add src/features/assembler.py src/data/watermark.py tests/test_assembler_watermark.py
git commit -m "feat: assembler watermark — migrate features watermark from watermark.json to MetaRepo"
```

---

## Task 6 (B6): Downstream scripts — migrate to FeatureRepo

**Files:**
- Modify: `scripts/g1_no_vol_features.py`
- Modify: `scripts/g2_cross_sectional_label.py`
- Modify: `scripts/collect_m3_data.py`

**Context:** Three scripts read `market_features.parquet` directly via `pd.read_parquet`. Replace with `FeatureRepo.load_features()`. No TDD for these — verify imports and guard clauses pass. Each script gets a quick smoke-test command.

- [ ] **Step 1: Update `scripts/g1_no_vol_features.py`**

Find the block:
```python
df = pd.read_parquet(PROCESSED_DIR / "market_features.parquet")
```

Replace with:
```python
from src.dal.feature_repo import FeatureRepo
repo = FeatureRepo()
date_range = repo.get_feature_date_range()
if date_range is None:
    raise FileNotFoundError("features 表为空，请先运行 Phase 1")
df = repo.load_features(date_range[0], date_range[1])
```

Remove any `from config.settings import PROCESSED_DIR` import if it's no longer used in the file.

- [ ] **Step 2: Update `scripts/g2_cross_sectional_label.py`**

Find the block that reads `market_features.parquet` and writes it back. Replace with:

```python
from src.dal.feature_repo import FeatureRepo
repo = FeatureRepo()
date_range = repo.get_feature_date_range()
if date_range is None:
    raise FileNotFoundError("features 表为空，请先运行 Phase 1")
df = repo.load_features(date_range[0], date_range[1])
```

After recalculating the `label` column, replace `df.to_parquet(...)` with:
```python
repo.upsert_features(df[["date", "code", "label"]])
logger.info(f"label 列已写回 FeatureRepo，共 {len(df)} 行")
```

- [ ] **Step 3: Update `scripts/collect_m3_data.py`**

Find the block:
```python
mf_path = PROCESSED_DIR / "market_features.parquet"
if not mf_path.exists():
    logger.warning("market_features.parquet 不存在，使用 bdate_range 近似交易日")
    # fallback
df = pd.read_parquet(mf_path, columns=["date"])
```

Replace with:
```python
from src.dal.feature_repo import FeatureRepo
repo = FeatureRepo()
date_range = repo.get_feature_date_range()
if date_range is None:
    logger.warning("features 表为空，使用 bdate_range 近似交易日")
    # fallback logic (keep existing bdate_range fallback unchanged)
else:
    df = repo.load_features(date_range[0], date_range[1])[["date"]].drop_duplicates()
```

- [ ] **Step 4: Smoke-test each script (dry-run import check)**

```bash
python -c "import scripts.g1_no_vol_features" 2>&1 | head -5
python -c "import scripts.g2_cross_sectional_label" 2>&1 | head -5
python -c "import scripts.collect_m3_data" 2>&1 | head -5
```

Expected: no `ImportError` or `SyntaxError`.

- [ ] **Step 5: Run full test suite one final time**

```bash
pytest tests/ -x --ignore=tests/test_db_populate.py -q 2>&1 | tail -20
```

Expected: all tests PASS, no regressions.

- [ ] **Step 6: Commit**

```bash
git add scripts/g1_no_vol_features.py scripts/g2_cross_sectional_label.py scripts/collect_m3_data.py
git commit -m "feat: migrate downstream scripts from market_features.parquet to FeatureRepo"
```

---

## Success Criteria

1. `pytest tests/ --ignore=tests/test_db_populate.py` passes with no failures
2. `python main.py assemble` reads from DuckDB and writes to `features` table (not `data/processed/`)
3. Running `assemble_incremental()` twice produces no duplicate rows in the `features` table
4. `MetaRepo.get_last_date("features", "__market__")` returns the correct date after each run (no hardcoded dates anywhere)
5. `data/processed/` directory is no longer written by any production code path
