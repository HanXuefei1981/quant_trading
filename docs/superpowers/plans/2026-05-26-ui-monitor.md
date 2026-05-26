# UI 监控台实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a FastAPI + native HTML web monitoring dashboard for the quant trading pipeline: trigger commands, monitor data watermarks, and stream real-time logs from the browser.

**Architecture:** FastAPI backend with 3 API routes (status/run/stream); reader modules isolate data-file access; a `TaskManager` uses asyncio subprocess + per-task Queue for SSE log delivery; a single-page HTML frontend renders 5 UI sections and connects via SSE on command execution.

**Tech Stack:** Python 3.10+, FastAPI ≥ 0.111, uvicorn[standard] ≥ 0.30, asyncio, pandas, pathlib; native HTML/CSS/JS (zero build step, zero JS framework).

**Design reference:** `.superpowers/brainstorm/53101-1779761405/content/design-v3.html` — CSS/layout template.

**Startup:** `python monitor.py` → http://127.0.0.1:8765

---

## File Map

```
monitor.py                         # entry: FastAPI app assembly + uvicorn launch
monitor/
├── __init__.py
├── readers/
│   ├── __init__.py
│   ├── watermark.py               # read data/watermark.json + count parquet files
│   ├── metrics.py                 # read data/models/eval_results.json + ensemble_meta.json
│   ├── backtest.py                # read data/backtest/equity_curve.csv
│   └── scan.py                   # find latest data/backtest/scan_*.csv, enrich fund_flow
├── runner.py                      # TaskManager: whitelist, asyncio subprocess, SSE queue
└── api/
    ├── __init__.py
    ├── status.py                  # GET /api/status
    ├── run.py                     # POST /api/run/{cmd}
    └── stream.py                  # GET /api/stream/{task_id}  (SSE)
monitor_ui/
└── index.html                     # single-page app (HTML + CSS + JS, inline)
tests/
└── monitor/
    ├── __init__.py
    ├── test_watermark.py
    ├── test_metrics.py
    ├── test_backtest.py
    ├── test_scan.py
    └── test_runner.py
```

---

## Task 0: Install dependencies

**Files:** `requirements.txt`

- [ ] **Step 1: Install FastAPI and uvicorn**

```bash
pip install "fastapi>=0.111" "uvicorn[standard]>=0.30"
```

- [ ] **Step 2: Verify**

```bash
python -c "import fastapi, uvicorn; print(fastapi.__version__, uvicorn.__version__)"
```

Expected: two version numbers printed, no ImportError.

- [ ] **Step 3: Add to requirements.txt** (append if file exists, create if not)

```
fastapi>=0.111
uvicorn[standard]>=0.30
```

- [ ] **Step 4: Commit**

```bash
git add requirements.txt
git commit -m "chore: add fastapi + uvicorn for monitor UI"
```

---

## Task 1: Scaffold package directories

**Files:** `monitor/__init__.py`, `monitor/readers/__init__.py`, `monitor/api/__init__.py`, `tests/monitor/__init__.py`

- [ ] **Step 1: Create dirs and empty init files**

```bash
mkdir -p monitor/readers monitor/api monitor_ui tests/monitor
touch monitor/__init__.py monitor/readers/__init__.py monitor/api/__init__.py tests/monitor/__init__.py
```

- [ ] **Step 2: Verify**

```bash
find monitor tests/monitor -name "*.py" | sort
```

Expected:
```
monitor/__init__.py
monitor/api/__init__.py
monitor/readers/__init__.py
tests/monitor/__init__.py
```

- [ ] **Step 3: Commit**

```bash
git add monitor/ monitor_ui/ tests/monitor/
git commit -m "chore: scaffold monitor package"
```

---

## Task 2: readers/watermark.py

Reads `data/watermark.json` (dates for kline/features/northbound), counts parquet files in `data/fund_flow/` and `data/fundamentals/`, checks model file existence.

**Files:**
- Create: `monitor/readers/watermark.py`
- Create: `tests/monitor/test_watermark.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/monitor/test_watermark.py
import json
import pytest
from pathlib import Path
from unittest.mock import patch


@pytest.fixture
def mock_data(tmp_path):
    (tmp_path / "watermark.json").write_text(json.dumps({
        "kline": "2026-05-15",
        "features": "2026-05-08",
        "northbound": "2026-05-18",
    }))
    ff = tmp_path / "fund_flow"
    ff.mkdir()
    for name in ["000001.parquet", "000002.parquet", "_northbound.parquet", "_failed.txt"]:
        (ff / name).touch()
    fu = tmp_path / "fundamentals"
    fu.mkdir()
    for i in range(3):
        (fu / f"00000{i}.parquet").touch()
    models = tmp_path / "models"
    models.mkdir()
    (models / "eval_results.json").touch()
    return tmp_path


def _read_all(mock_data):
    with patch("monitor.readers.watermark.DATA_DIR", mock_data):
        import importlib, monitor.readers.watermark as m
        importlib.reload(m)
        return m.read_all()


def test_returns_six_sections(mock_data):
    result = _read_all(mock_data)
    assert set(result.keys()) == {"kline", "features", "northbound", "fundamentals", "fund_flow", "models"}


def test_kline_date_and_status_ok(mock_data):
    result = _read_all(mock_data)
    assert result["kline"]["date"] == "2026-05-15"
    # May 15 is recent enough — status may vary by today's date; just check it's a valid value
    assert result["kline"]["status"] in {"ok", "warn", "err"}


def test_fund_flow_excludes_underscore_files(mock_data):
    result = _read_all(mock_data)
    # _northbound.parquet and _failed.txt both start with _ → excluded
    assert result["fund_flow"]["count"] == 2


def test_features_behind_kline_is_warn(mock_data):
    result = _read_all(mock_data)
    # features=2026-05-08 != kline=2026-05-15 → warn
    assert result["features"]["status"] == "warn"


def test_models_ok_when_file_exists(mock_data):
    result = _read_all(mock_data)
    assert result["models"]["status"] == "ok"


def test_models_err_when_file_missing(tmp_path):
    # no models dir
    (tmp_path / "watermark.json").write_text("{}")
    result = _read_all(tmp_path)
    assert result["models"]["status"] == "err"
```

- [ ] **Step 2: Run to verify it fails**

```bash
pytest tests/monitor/test_watermark.py -v 2>&1 | head -20
```

Expected: `ModuleNotFoundError` or `ImportError`.

- [ ] **Step 3: Implement watermark.py**

```python
# monitor/readers/watermark.py
import json
from datetime import date, datetime
from pathlib import Path

from config.settings import DATA_DIR

UNIVERSE_SIZE = 5641


def _load_watermark_json() -> dict:
    p = DATA_DIR / "watermark.json"
    return json.loads(p.read_text()) if p.exists() else {}


def _count_parquet(directory: Path) -> int:
    if not directory.exists():
        return 0
    return sum(1 for f in directory.glob("*.parquet") if not f.name.startswith("_"))


def _days_behind(date_str: str) -> int:
    if not date_str:
        return 999
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d").date()
        return (date.today() - d).days
    except ValueError:
        return 999


def read_all() -> dict:
    wm = _load_watermark_json()
    kline_date = wm.get("kline", "")
    features_date = wm.get("features", "")
    north_date = wm.get("northbound", "")

    kline_behind = _days_behind(kline_date)
    features_behind = _days_behind(features_date)
    north_behind = _days_behind(north_date)

    ff_count = _count_parquet(DATA_DIR / "fund_flow")
    fu_count = _count_parquet(DATA_DIR / "fundamentals")
    models_exist = (DATA_DIR / "models" / "eval_results.json").exists()

    return {
        "kline": {
            "date": kline_date,
            "detail": f"{UNIVERSE_SIZE:,} 只",
            "status": "ok" if kline_behind <= 3 else "warn" if kline_behind <= 7 else "err",
        },
        "features": {
            "date": features_date,
            "detail": "需重跑 Phase 1" if features_date != kline_date else "已同步",
            "status": "ok" if features_date == kline_date else "warn" if features_behind <= 10 else "err",
        },
        "northbound": {
            "date": north_date,
            "detail": "沪深港通市场净流入",
            "status": "ok" if north_behind <= 3 else "warn",
        },
        "fundamentals": {
            "count": fu_count,
            "coverage": round(fu_count / UNIVERSE_SIZE, 3),
            "detail": f"{fu_count:,} / {UNIVERSE_SIZE:,}",
            "status": "ok" if fu_count / UNIVERSE_SIZE >= 0.9 else "warn" if fu_count / UNIVERSE_SIZE >= 0.7 else "err",
        },
        "fund_flow": {
            "count": ff_count,
            "coverage": round(ff_count / UNIVERSE_SIZE, 3),
            "detail": f"{ff_count:,} / {UNIVERSE_SIZE:,}",
            "status": "ok" if ff_count / UNIVERSE_SIZE >= 0.5 else "warn" if ff_count / UNIVERSE_SIZE >= 0.2 else "err",
        },
        "models": {
            "detail": "lgbm + ridge 集成" if models_exist else "未找到模型文件",
            "status": "ok" if models_exist else "err",
        },
    }
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/monitor/test_watermark.py -v
```

Expected: 6 tests PASSED.

- [ ] **Step 5: Commit**

```bash
git add monitor/readers/watermark.py tests/monitor/test_watermark.py
git commit -m "feat: monitor watermark reader"
```

---

## Task 3: readers/metrics.py

**Files:**
- Create: `monitor/readers/metrics.py`
- Create: `tests/monitor/test_metrics.py`

`eval_results.json` is a list of 3 dicts: `{"split":"训练集", "ic":…, "accuracy":…, "f1_weighted":…}`.  
`ensemble_meta.json` has keys `w_lgbm`, `w_ridge`, `feature_cols`.

- [ ] **Step 1: Write the failing test**

```python
# tests/monitor/test_metrics.py
import json
import pytest
from pathlib import Path
from unittest.mock import patch


@pytest.fixture
def mock_models(tmp_path):
    models = tmp_path / "models"
    models.mkdir()
    (models / "eval_results.json").write_text(json.dumps([
        {"split": "训练集", "n_samples": 100, "accuracy": 0.39, "f1_weighted": 0.36, "ic": -0.013},
        {"split": "验证集", "n_samples": 40, "accuracy": 0.38, "f1_weighted": 0.35, "ic": -0.021},
        {"split": "测试集", "n_samples": 40, "accuracy": 0.40, "f1_weighted": 0.36, "ic": -0.023},
    ]))
    (models / "ensemble_meta.json").write_text(json.dumps({
        "w_lgbm": 0.54,
        "w_ridge": 0.46,
        "feature_cols": ["ma5_ratio", "rsi", "north_net_5d"],
    }))
    return tmp_path


def _read_metrics(mock_data):
    with patch("monitor.readers.metrics.DATA_DIR", mock_data):
        import importlib, monitor.readers.metrics as m
        importlib.reload(m)
        return m.read_metrics()


def test_ic_values(mock_models):
    result = _read_metrics(mock_models)
    assert result["train_ic"] == -0.013
    assert result["val_ic"] == -0.021
    assert result["test_ic"] == -0.023


def test_weights(mock_models):
    result = _read_metrics(mock_models)
    assert result["w_lgbm"] == 0.54
    assert result["w_ridge"] == 0.46


def test_feature_count(mock_models):
    result = _read_metrics(mock_models)
    assert result["n_features"] == 3


def test_missing_file_returns_error(tmp_path):
    result = _read_metrics(tmp_path)
    assert "error" in result
```

- [ ] **Step 2: Run to verify fails**

```bash
pytest tests/monitor/test_metrics.py -v 2>&1 | head -10
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement metrics.py**

```python
# monitor/readers/metrics.py
import json
from pathlib import Path

from config.settings import DATA_DIR


def read_metrics() -> dict:
    eval_path = DATA_DIR / "models" / "eval_results.json"
    meta_path = DATA_DIR / "models" / "ensemble_meta.json"

    if not eval_path.exists():
        return {"error": "eval_results.json not found"}

    eval_data = json.loads(eval_path.read_text())
    by_split = {item["split"]: item for item in eval_data}
    train = by_split.get("训练集", {})
    val = by_split.get("验证集", {})
    test = by_split.get("测试集", {})

    meta = {}
    if meta_path.exists():
        meta = json.loads(meta_path.read_text())

    return {
        "train_ic": round(train.get("ic", 0), 4),
        "val_ic": round(val.get("ic", 0), 4),
        "test_ic": round(test.get("ic", 0), 4),
        "val_accuracy": round(val.get("accuracy", 0), 4),
        "val_f1": round(val.get("f1_weighted", 0), 4),
        "n_features": len(meta.get("feature_cols", [])),
        "w_lgbm": round(meta.get("w_lgbm", 0), 3),
        "w_ridge": round(meta.get("w_ridge", 0), 3),
    }
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/monitor/test_metrics.py -v
```

Expected: 4 tests PASSED.

- [ ] **Step 5: Commit**

```bash
git add monitor/readers/metrics.py tests/monitor/test_metrics.py
git commit -m "feat: monitor metrics reader"
```

---

## Task 4: readers/backtest.py + readers/scan.py

**Files:**
- Create: `monitor/readers/backtest.py`
- Create: `monitor/readers/scan.py`
- Create: `tests/monitor/test_backtest.py`
- Create: `tests/monitor/test_scan.py`

`equity_curve.csv`: index=date, columns=strategy,benchmark (float, first benchmark row may be NaN).  
`scan_*.csv`: columns=`date,code,close,signal,ret1,rank,segment,signal_pct`. Rank 1–50 = hold, 51+ = buffer.  
Enrichment: for each code, try to read `data/fund_flow/{code}.parquet` latest `major_net_inflow`.

- [ ] **Step 1: Write tests for backtest.py**

```python
# tests/monitor/test_backtest.py
import pytest
import pandas as pd
from pathlib import Path
from unittest.mock import patch


@pytest.fixture
def mock_backtest(tmp_path):
    bt = tmp_path / "backtest"
    bt.mkdir()
    df = pd.DataFrame({
        "strategy":  [1_000_000, 1_010_000, 1_020_000, 1_050_000],
        "benchmark": [float("nan"), 990_000, 1_000_000, 1_020_000],
    }, index=["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"])
    df.index.name = None
    df.to_csv(bt / "equity_curve.csv")
    return tmp_path


def _read_backtest(mock_data):
    with patch("monitor.readers.backtest.DATA_DIR", mock_data):
        import importlib, monitor.readers.backtest as m
        importlib.reload(m)
        return m.read_backtest()


def test_strategy_return(mock_backtest):
    result = _read_backtest(mock_backtest)
    # (1_050_000 / 1_000_000) - 1 = 0.05
    assert abs(result["strategy_return"] - 0.05) < 0.001


def test_max_drawdown_is_negative(mock_backtest):
    result = _read_backtest(mock_backtest)
    assert result["max_drawdown"] <= 0


def test_equity_curve_has_dates(mock_backtest):
    result = _read_backtest(mock_backtest)
    assert len(result["equity_curve"]["dates"]) > 0
    assert len(result["equity_curve"]["strategy"]) == len(result["equity_curve"]["dates"])


def test_missing_file_returns_error(tmp_path):
    result = _read_backtest(tmp_path)
    assert "error" in result
```

- [ ] **Step 2: Write tests for scan.py**

```python
# tests/monitor/test_scan.py
import pytest
import pandas as pd
from pathlib import Path
from unittest.mock import patch


@pytest.fixture
def mock_scan(tmp_path):
    bt = tmp_path / "backtest"
    bt.mkdir()
    rows = []
    for i in range(1, 56):
        rows.append({
            "date": "2026-05-18", "code": str(300000 + i).zfill(6),
            "close": 10.0, "signal": 2.0 - i * 0.01,
            "ret1": 0.01, "rank": i, "segment": "创业板", "signal_pct": 1.0 - i * 0.01,
        })
    df = pd.DataFrame(rows)
    df.to_csv(bt / "scan_2026-05-18.csv", index=False)
    # fund_flow for first stock
    ff = tmp_path / "fund_flow"
    ff.mkdir()
    code = str(300001).zfill(6)
    pd.DataFrame({"date": ["2026-05-18"], "major_net_inflow": [5_000_000.0], "major_net_pct": [3.5]}).to_parquet(ff / f"{code}.parquet")
    return tmp_path


def _read_scan(mock_data, top_n=55):
    with patch("monitor.readers.scan.DATA_DIR", mock_data):
        import importlib, monitor.readers.scan as m
        importlib.reload(m)
        return m.read_latest_scan(top_n=top_n)


def test_returns_correct_date(mock_scan):
    result = _read_scan(mock_scan)
    assert result["date"] == "2026-05-18"


def test_hold_and_buffer_status(mock_scan):
    result = _read_scan(mock_scan)
    statuses = {s["rank"]: s["status"] for s in result["signals"]}
    assert statuses[1] == "hold"
    assert statuses[50] == "hold"
    assert statuses[51] == "buffer"


def test_fund_flow_enriched_for_known_code(mock_scan):
    result = _read_scan(mock_scan)
    row = next(s for s in result["signals"] if s["rank"] == 1)
    # stock 300001 has fund_flow data
    assert row["fund_flow"] is not None


def test_missing_dir_returns_error(tmp_path):
    result = _read_scan(tmp_path)
    assert "error" in result
```

- [ ] **Step 3: Run to verify both test files fail**

```bash
pytest tests/monitor/test_backtest.py tests/monitor/test_scan.py -v 2>&1 | head -15
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 4: Implement backtest.py**

```python
# monitor/readers/backtest.py
import pandas as pd
from pathlib import Path

from config.settings import DATA_DIR


def read_backtest() -> dict:
    eq_path = DATA_DIR / "backtest" / "equity_curve.csv"
    if not eq_path.exists():
        return {"error": "equity_curve.csv not found"}

    df = pd.read_csv(eq_path, index_col=0)
    # Drop rows where strategy is NaN
    df = df.dropna(subset=["strategy"])

    initial = df["strategy"].iloc[0]
    strategy_return = (df["strategy"].iloc[-1] / initial) - 1
    benchmark_return = (df["benchmark"].dropna().iloc[-1] / initial) - 1 if df["benchmark"].notna().any() else 0.0

    rolling_max = df["strategy"].cummax()
    max_drawdown = ((df["strategy"] - rolling_max) / rolling_max).min()

    # Downsample to ≤200 points to keep payload small
    step = max(1, len(df) // 200)
    sampled = df.iloc[::step]

    return {
        "strategy_return": round(strategy_return, 4),
        "benchmark_return": round(benchmark_return, 4),
        "excess_return": round(strategy_return - benchmark_return, 4),
        "max_drawdown": round(float(max_drawdown), 4),
        "period_start": str(df.index[0]),
        "period_end": str(df.index[-1]),
        "equity_curve": {
            "dates": list(sampled.index),
            "strategy": [round(v / initial, 4) for v in sampled["strategy"].tolist()],
            "benchmark": [
                round(v / initial, 4) if pd.notna(v) else None
                for v in sampled["benchmark"].tolist()
            ],
        },
    }
```

- [ ] **Step 5: Implement scan.py**

```python
# monitor/readers/scan.py
import pandas as pd
from pathlib import Path

from config.settings import DATA_DIR


def _latest_fund_flow(code: str) -> float | None:
    """Return latest major_net_inflow for a stock, or None if unavailable."""
    p = DATA_DIR / "fund_flow" / f"{code}.parquet"
    if not p.exists():
        return None
    try:
        df = pd.read_parquet(p, columns=["date", "major_net_inflow"])
        df = df.dropna(subset=["major_net_inflow"])
        if df.empty:
            return None
        return float(df.sort_values("date").iloc[-1]["major_net_inflow"])
    except Exception:
        return None


def read_latest_scan(top_n: int = 55) -> dict:
    backtest_dir = DATA_DIR / "backtest"
    scan_files = sorted(backtest_dir.glob("scan_*.csv")) if backtest_dir.exists() else []
    if not scan_files:
        return {"error": "No scan_*.csv files found in data/backtest/"}

    latest = scan_files[-1]
    scan_date = latest.stem.replace("scan_", "")

    df = pd.read_csv(latest)
    df = df.sort_values("rank").head(top_n)
    df["code"] = df["code"].astype(str).str.zfill(6)

    signals = []
    for _, row in df.iterrows():
        rank = int(row["rank"])
        code = str(row["code"])
        signals.append({
            "rank": rank,
            "code": code,
            "segment": str(row.get("segment", "—")),
            "close": round(float(row["close"]), 2),
            "signal": round(float(row["signal"]), 3),
            "signal_pct": round(float(row.get("signal_pct", 0)), 3),
            "fund_flow": _latest_fund_flow(code),
            "status": "hold" if rank <= 50 else "buffer",
        })

    return {
        "date": scan_date,
        "hold_n": 50,
        "buffer_n": 25,
        "signals": signals,
    }
```

- [ ] **Step 6: Run tests**

```bash
pytest tests/monitor/test_backtest.py tests/monitor/test_scan.py -v
```

Expected: all tests PASSED.

- [ ] **Step 7: Commit**

```bash
git add monitor/readers/backtest.py monitor/readers/scan.py tests/monitor/test_backtest.py tests/monitor/test_scan.py
git commit -m "feat: monitor backtest and scan readers"
```

---

## Task 5: runner.py — subprocess + SSE queue

**Files:**
- Create: `monitor/runner.py`
- Create: `tests/monitor/test_runner.py`

`TaskManager.run(cmd)` validates against a whitelist, launches a subprocess with `asyncio.create_subprocess_exec`, pipes stdout/stderr to an `asyncio.Queue`, and sends a final `{"type":"done","exit_code":N}` event.

- [ ] **Step 1: Write the failing test**

```python
# tests/monitor/test_runner.py
import asyncio
import pytest
from monitor.runner import TaskManager, CMD_MAP


@pytest.mark.asyncio
async def test_unknown_cmd_raises():
    mgr = TaskManager()
    with pytest.raises(ValueError, match="Unknown command"):
        await mgr.run("nonexistent_cmd")


@pytest.mark.asyncio
async def test_concurrent_run_raises():
    mgr = TaskManager()
    # Manually set a running task
    from monitor.runner import Task
    mgr._current = Task(task_id="x", cmd="phase1", status="running")
    with pytest.raises(RuntimeError, match="already running"):
        await mgr.run("phase1")


def test_cmd_map_contains_all_expected():
    expected = {
        "update", "ingest", "collect", "fetch-fund", "fetch-flow",
        "phase1", "phase2-rolling", "phase2-final", "phase3", "scan",
    }
    assert expected == set(CMD_MAP.keys())


@pytest.mark.asyncio
async def test_run_echo_produces_output(tmp_path):
    """Run a simple echo command and verify SSE events arrive."""
    mgr = TaskManager()
    # Override CMD_MAP to use a simple echo for testing
    import monitor.runner as runner_mod
    original = runner_mod.CMD_MAP.copy()
    runner_mod.CMD_MAP["test-echo"] = ["python", "-c", "print('hello monitor')"]
    try:
        task = await mgr.run("test-echo")
        # Collect events until done
        events = []
        for _ in range(20):  # safety limit
            ev = await asyncio.wait_for(task.queue.get(), timeout=5.0)
            events.append(ev)
            if ev.get("type") == "done":
                break
        msgs = [e.get("msg", "") for e in events if "msg" in e]
        assert any("hello monitor" in m for m in msgs)
        assert events[-1].get("type") == "done"
        assert events[-1].get("exit_code") == 0
    finally:
        runner_mod.CMD_MAP = original
```

- [ ] **Step 2: Install pytest-asyncio**

```bash
pip install pytest-asyncio
```

Add `asyncio_mode = "auto"` to `pytest.ini` (create if absent):

```ini
[pytest]
asyncio_mode = auto
```

- [ ] **Step 3: Run to verify test fails**

```bash
pytest tests/monitor/test_runner.py -v 2>&1 | head -15
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 4: Implement runner.py**

```python
# monitor/runner.py
import asyncio
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent

CMD_MAP: dict[str, list[str]] = {
    "update":         [sys.executable, "main.py", "update"],
    "ingest":         [sys.executable, "main.py", "ingest"],
    "collect":        [sys.executable, "main.py", "collect"],
    "fetch-fund":     [sys.executable, "main.py", "fetch-fund"],
    "fetch-flow":     [sys.executable, "main.py", "fetch-flow"],
    "phase1":         [sys.executable, "main.py", "1"],
    "phase2-rolling": [sys.executable, "main.py", "2", "--rolling"],
    "phase2-final":   [sys.executable, "main.py", "2", "--final"],
    "phase3":         [sys.executable, "main.py", "3"],
    "scan":           [sys.executable, "main.py", "scan", "--top-k", "50"],
}


@dataclass
class Task:
    task_id: str
    cmd: str
    status: str = "running"          # running | done | error
    queue: asyncio.Queue = field(default_factory=asyncio.Queue)


class TaskManager:
    def __init__(self) -> None:
        self._current: Task | None = None

    def is_running(self) -> bool:
        return self._current is not None and self._current.status == "running"

    def get_task(self, task_id: str) -> Task | None:
        if self._current and self._current.task_id == task_id:
            return self._current
        return None

    async def run(self, cmd: str) -> Task:
        if self.is_running():
            raise RuntimeError("A task is already running")
        if cmd not in CMD_MAP:
            raise ValueError(f"Unknown command: {cmd!r}")

        task = Task(task_id=str(uuid.uuid4())[:8], cmd=cmd)
        self._current = task
        asyncio.create_task(self._execute(task))
        return task

    async def _execute(self, task: Task) -> None:
        args = CMD_MAP[task.cmd]
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(PROJECT_ROOT),
        )
        assert proc.stdout is not None
        async for raw_line in proc.stdout:
            text = raw_line.decode("utf-8", errors="replace").rstrip()
            await task.queue.put({
                "ts": datetime.now().strftime("%H:%M:%S"),
                "level": _classify(text),
                "msg": text,
            })

        await proc.wait()
        task.status = "done" if proc.returncode == 0 else "error"
        await task.queue.put({"type": "done", "exit_code": proc.returncode})


def _classify(text: str) -> str:
    t = text.lower()
    if any(k in t for k in ("error", "traceback", "exception")):
        return "warn"
    if any(k in t for k in ("warning", "⚠")):
        return "warn"
    if any(k in t for k in ("完成", "done", "success", "✓", "finished")):
        return "success"
    if "%" in t:
        return "progress"
    return "default"


# Singleton used by API routes
task_manager = TaskManager()
```

- [ ] **Step 5: Run tests**

```bash
pytest tests/monitor/test_runner.py -v
```

Expected: all 4 tests PASSED.

- [ ] **Step 6: Commit**

```bash
git add monitor/runner.py tests/monitor/test_runner.py pytest.ini
git commit -m "feat: monitor runner with asyncio subprocess + SSE queue"
```

---

## Task 6: API routes + monitor.py entry

**Files:**
- Create: `monitor/api/status.py`
- Create: `monitor/api/run.py`
- Create: `monitor/api/stream.py`
- Create: `monitor.py`

- [ ] **Step 1: Implement monitor/api/status.py**

```python
# monitor/api/status.py
from fastapi import APIRouter
from monitor.readers import backtest, metrics, scan, watermark

router = APIRouter()


@router.get("/api/status")
async def get_status() -> dict:
    return {
        "watermarks": watermark.read_all(),
        "metrics": metrics.read_metrics(),
        "backtest": backtest.read_backtest(),
        "scan": scan.read_latest_scan(),
    }
```

- [ ] **Step 2: Implement monitor/api/run.py**

```python
# monitor/api/run.py
from fastapi import APIRouter, HTTPException
from monitor.runner import task_manager

router = APIRouter()


@router.post("/api/run/{cmd}")
async def run_command(cmd: str) -> dict:
    if task_manager.is_running():
        raise HTTPException(status_code=409, detail="A task is already running")
    try:
        task = await task_manager.run(cmd)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"task_id": task.task_id, "cmd": cmd}
```

- [ ] **Step 3: Implement monitor/api/stream.py**

```python
# monitor/api/stream.py
import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from monitor.runner import task_manager

router = APIRouter()


@router.get("/api/stream/{task_id}")
async def stream_logs(task_id: str) -> StreamingResponse:
    task = task_manager.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")

    async def _generate():
        while True:
            item = await task.queue.get()
            yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
            if item.get("type") == "done":
                break

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
```

- [ ] **Step 4: Implement monitor.py**

```python
# monitor.py
import argparse
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse

from monitor.api.run import router as run_router
from monitor.api.status import router as status_router
from monitor.api.stream import router as stream_router

app = FastAPI(title="量化交易监控台", docs_url=None, redoc_url=None)
app.include_router(status_router)
app.include_router(run_router)
app.include_router(stream_router)

_UI = Path(__file__).parent / "monitor_ui" / "index.html"


@app.get("/")
async def ui() -> FileResponse:
    return FileResponse(_UI)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="量化交易监控台")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
```

- [ ] **Step 5: Smoke-test the API (no frontend yet)**

```bash
# Terminal 1: start server
python monitor.py

# Terminal 2: verify status endpoint
curl -s http://127.0.0.1:8765/api/status | python -m json.tool | head -30
```

Expected: JSON with `watermarks`, `metrics`, `backtest`, `scan` keys.

```bash
# Test invalid command
curl -s -X POST http://127.0.0.1:8765/api/run/bogus
```

Expected: `{"detail":"Unknown command: 'bogus'"}`

- [ ] **Step 6: Commit**

```bash
git add monitor/api/status.py monitor/api/run.py monitor/api/stream.py monitor.py
git commit -m "feat: monitor API routes and entry point"
```

---

## Task 7: monitor_ui/index.html — full frontend

The HTML file must embed all CSS and JS inline (no external files, no build step).  
CSS reference: copy the full `<style>` block from `.superpowers/brainstorm/53101-1779761405/content/design-v3.html` — colors, card styles, table styles, log panel are all defined there.

Layout (top → bottom): topbar → watermark grid (6) → interface table (8 rows) → factor table (9 rows) → mid-row 3 cols → scan table → log panel (fixed bottom).

- [ ] **Step 1: Create the HTML skeleton with CSS**

Create `monitor_ui/index.html`. Copy the full `<style>` block from `design-v3.html` (lines 6–170), add the page structure (topbar, section containers, log panel) with empty data containers that JS will populate.

Key IDs JS will target:
- `#topbar-status` — running status text
- `#wm-grid` — watermark cards container
- `#if-tbody` — interface table body
- `#factor-tbody` — factor table body  
- `#metric-panel` — model metrics panel body
- `#chart-svg` — SVG equity curve
- `#chart-stats` — 4 stat cards
- `#scan-meta` — scan date/count text
- `#scan-tbody` — scan table body
- `#log-title` — log panel command name
- `#log-body` — log line container

```html
<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<title>量化交易监控台</title>
<style>
/* ── paste full <style> block from design-v3.html here ── */
</style>
</head>
<body>

<div class="topbar">
  <div>
    <div class="topbar-title">⚡ 量化交易监控台</div>
    <div class="topbar-sub">A股 LightGBM + Ridge · 5,641 只股票池</div>
  </div>
  <div class="topbar-right">
    <div id="status-dot" class="dot idle"></div>
    <span id="topbar-status" class="status-label">加载中…</span>
  </div>
</div>

<div class="main">
  <div>
    <div class="section-label">数据水位</div>
    <div id="wm-grid" class="watermark-grid"><!-- JS renders 6 cards --></div>
  </div>
  <div>
    <div class="section-label">数据接口 &amp; 采集状态</div>
    <div class="if-table-wrap">
      <table class="if-table">
        <thead><tr><th>接口/数据源</th><th>对应因子</th><th>采集命令</th><th>最新数据</th><th>覆盖率</th><th>状态</th><th>备注</th></tr></thead>
        <tbody id="if-tbody"><!-- static rows, filled in Step 4 --></tbody>
      </table>
    </div>
  </div>
  <div>
    <div class="section-label">特色因子 — 数据覆盖详情</div>
    <div class="factor-table-wrap">
      <table class="factor-table">
        <thead><tr><th>因子名</th><th>分类</th><th>数据接口</th><th>覆盖率</th><th>说明</th><th>状态</th></tr></thead>
        <tbody id="factor-tbody"><!-- static rows, filled in Step 4 --></tbody>
      </table>
    </div>
  </div>
  <div class="mid-row">
    <div class="panel">
      <div class="panel-header"><span>流程控制台</span><span style="color:#334155;font-size:10px">点击执行 · 底部查看日志</span></div>
      <div class="panel-body" id="cmd-panel"><!-- JS renders buttons --></div>
    </div>
    <div class="panel">
      <div class="panel-header">模型指标</div>
      <div class="panel-body" id="metric-panel"><!-- JS renders --></div>
    </div>
    <div class="panel">
      <div class="panel-header">回测权益曲线</div>
      <div class="panel-body" id="backtest-panel"><!-- JS renders --></div>
    </div>
  </div>
  <div>
    <div class="section-label">最新选股信号</div>
    <div class="scan-panel">
      <div class="scan-header">
        <span class="scan-title" id="scan-title">Top-50 持仓 · 缓冲池 25 只</span>
        <span class="scan-meta" id="scan-meta">—</span>
      </div>
      <table class="scan-table">
        <thead><tr><th>排名</th><th>代码</th><th>板块</th><th>收盘价</th><th>信号值</th><th>全市场分位</th><th>主力净流入</th><th>状态</th></tr></thead>
        <tbody id="scan-tbody"><!-- JS renders --></tbody>
      </table>
    </div>
  </div>
</div>

<div class="log-panel">
  <div class="log-header">
    <div class="log-title"><div id="log-dot" class="log-dot" style="background:#334155;animation:none"></div><span id="log-title">— 等待执行 —</span></div>
    <span style="color:#475569;font-size:11px;cursor:pointer" onclick="toggleLog()">▼ 收起</span>
  </div>
  <div class="log-body" id="log-body"></div>
</div>

<script>/* JS in Step 2–5 */</script>
</body>
</html>
```

- [ ] **Step 2: Add static table rows** (interface table + factor table)

Replace `<tbody id="if-tbody"><!-- static rows, filled in Step 4 --></tbody>` with the 8 rows from `design-v3.html` (lines 243–310 of design-v3.html). Replace `<tbody id="factor-tbody">` with the 9 factor rows (lines 326–429 of design-v3.html).

These rows are static HTML — they don't change at runtime because interface/factor metadata is fixed in the codebase.

- [ ] **Step 3: Add command console buttons** (static HTML in `#cmd-panel`)

Replace the `<div class="panel-body" id="cmd-panel">` content with the 4-group button layout from `design-v3.html` (lines 448–508), except change each `<button>` to have:

```html
<button class="cmd-btn" onclick="runCmd('phase1')"><span class="icon">▶</span> Phase 1</button>
```

Map of button onclick → cmd string:

| Label | `runCmd()` argument |
|-------|---------------------|
| update（日常增量一键） | `update` |
| ingest --zip | `ingest` |
| collect | `collect` |
| fetch-fund | `fetch-fund` |
| fetch-flow | `fetch-flow` |
| Phase 1 | `phase1` |
| Phase 2 --rolling | `phase2-rolling` |
| Phase 2 --final | `phase2-final` |
| Phase 3 | `phase3` |
| scan --top-k 50 | `scan` |

- [ ] **Step 4: Add JS — data rendering functions**

Add inside `<script>`:

```javascript
// ── helpers ──────────────────────────────────────
const $ = id => document.getElementById(id);
const pct = v => v != null ? (v * 100).toFixed(1) + '%' : '—';
const yuan = v => v != null ? (v / 1e4).toFixed(0) + '万' : '—';
const sign = v => v >= 0 ? '+' : '';
// A-share color: positive=red(up), negative=green(down)
const finColor = v => v == null ? '#94a3b8' : v > 0 ? '#f87171' : v < 0 ? '#4ade80' : '#94a3b8';

// ── watermark ────────────────────────────────────
function renderWatermarks(wm) {
  const CARDS = [
    { key: 'kline',        label: 'K线 · kline',             sub: d => d.detail },
    { key: 'features',     label: '特征 · features',          sub: d => d.detail },
    { key: 'northbound',   label: '北向资金 · northbound',    sub: d => d.detail },
    { key: 'fundamentals', label: '基本面 · fundamentals',    sub: d => d.detail },
    { key: 'fund_flow',    label: '资金流向 · fund_flow',     sub: d => d.detail },
    { key: 'models',       label: '模型 · models',            sub: d => d.detail },
  ];
  const STATUS_BADGE = { ok: '正常', warn: '落后', err: '异常' };
  $('wm-grid').innerHTML = CARDS.map(c => {
    const d = wm[c.key] || {};
    const st = d.status || 'err';
    const val = d.date || (d.count != null ? d.count + ' 只' : '—');
    const badge = STATUS_BADGE[st] || '—';
    const cov = d.coverage != null ? pct(d.coverage) : '';
    return `<div class="wm-card ${st}">
      <div>
        <div class="wm-name">${c.label}</div>
        <div class="wm-val">${val}</div>
        <div class="wm-sub">${c.sub(d)}</div>
      </div>
      <div class="wm-right">
        <span class="wm-badge">${badge}</span>
        ${cov ? `<div class="wm-sub" style="margin-top:4px">${cov}</div>` : ''}
      </div>
    </div>`;
  }).join('');
}

// ── metrics ──────────────────────────────────────
function renderMetrics(m) {
  if (m.error) { $('metric-panel').innerHTML = `<div style="color:#f87171">${m.error}</div>`; return; }
  const row = (label, val, color) =>
    `<div class="metric-row"><span class="ml">${label}</span><span class="mv" style="color:${color}">${val}</span></div>`;
  $('metric-panel').innerHTML = `
    <div class="metric-block">
      ${row('训练集 IC', m.train_ic.toFixed(4), finColor(m.train_ic))}
      ${row('验证集 IC', m.val_ic.toFixed(4), finColor(m.val_ic))}
      ${row('测试集 IC', m.test_ic.toFixed(4), finColor(m.test_ic))}
      ${row('验证集准确率', (m.val_accuracy * 100).toFixed(1) + '%', '#94a3b8')}
      ${row('验证集 F1', m.val_f1.toFixed(3), '#94a3b8')}
    </div>
    <div class="divider"></div>
    <div style="font-size:10px;color:#64748b;margin-bottom:5px">因子权重</div>
    <div style="font-size:11px;color:#94a3b8">
      lgbm ${(m.w_lgbm * 100).toFixed(1)}% · ridge ${(m.w_ridge * 100).toFixed(1)}% · ${m.n_features} 因子
    </div>`;
}

// ── backtest ─────────────────────────────────────
function renderBacktest(bt) {
  if (bt.error) { $('backtest-panel').innerHTML = `<div style="color:#f87171">${bt.error}</div>`; return; }
  const ec = bt.equity_curve;
  const n = ec.dates.length;
  const W = 240, H = 88;
  const strat = ec.strategy;
  const bench = ec.benchmark;
  const allVals = [...strat, ...bench.filter(v => v != null)];
  const minV = Math.min(...allVals), maxV = Math.max(...allVals);
  const toY = v => H - ((v - minV) / (maxV - minV + 0.0001)) * (H - 10) - 2;
  const toX = i => (i / (n - 1)) * W;
  const pts = s => s.map((v, i) => v != null ? `${toX(i).toFixed(1)},${toY(v).toFixed(1)}` : null).filter(Boolean).join(' ');
  const stratColor = '#ef4444';
  const benchColor = '#475569';

  const statCard = (label, val, color) =>
    `<div class="cstat"><div class="cstat-label">${label}</div><div class="cstat-val" style="color:${color}">${val}</div></div>`;

  $('backtest-panel').innerHTML = `
    <div class="chart-wrap">
      <svg class="chart-svg" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
        <line x1="0" y1="${H/2}" x2="${W}" y2="${H/2}" stroke="#1e293b" stroke-width="0.5"/>
        <polyline points="${pts(bench)}" fill="none" stroke="${benchColor}" stroke-width="1.5"/>
        <polyline points="${pts(strat)}" fill="none" stroke="${stratColor}" stroke-width="2"/>
        <text x="2" y="${H-4}" fill="#334155" font-size="7" font-family="monospace">${bt.period_start?.slice(0,7) || ''}</text>
        <text x="${W-50}" y="${H-4}" fill="#334155" font-size="7" font-family="monospace">${bt.period_end?.slice(0,7) || ''}</text>
      </svg>
    </div>
    <div class="chart-stats">
      ${statCard('超额收益', sign(bt.excess_return) + pct(bt.excess_return), finColor(bt.excess_return))}
      ${statCard('最大回撤', (bt.max_drawdown * 100).toFixed(1) + '%', finColor(bt.max_drawdown))}
      ${statCard('策略总收益', sign(bt.strategy_return) + pct(bt.strategy_return), finColor(bt.strategy_return))}
      ${statCard('回测区间', (bt.period_start || '').slice(0,7) + ' ~ ' + (bt.period_end || '').slice(0,7), '#94a3b8')}
    </div>
    <div class="chart-legend">
      <div class="legend-item"><div class="legend-line" style="background:${stratColor}"></div><span style="color:#f87171">策略</span></div>
      <div class="legend-item"><div class="legend-line" style="background:${benchColor}"></div><span style="color:#64748b">基准</span></div>
    </div>`;
}

// ── scan table ───────────────────────────────────
function renderScan(sc) {
  if (sc.error) { $('scan-tbody').innerHTML = `<tr><td colspan="8" style="color:#f87171">${sc.error}</td></tr>`; return; }
  $('scan-meta').textContent = `扫描日期：${sc.date} · 完整列表见 data/backtest/scan_${sc.date}.csv`;
  const RANK_CLS = ['', 'r1', 'r2', 'r3'];
  $('scan-tbody').innerHTML = sc.signals.map(s => {
    const rc = RANK_CLS[s.rank] || 'rn';
    const barW = Math.round(s.signal_pct * 95);
    const ff = s.fund_flow;
    const ffHtml = ff != null
      ? `<span style="color:${finColor(ff)}">${sign(ff)}${yuan(ff)}</span>`
      : `<span style="color:#64748b">— 无数据</span>`;
    const statusHtml = s.status === 'hold'
      ? `<span class="hold">◀ 持仓</span>`
      : `<span class="watch">缓冲区</span>`;
    return `<tr>
      <td><span class="rank ${rc}">${s.rank}</span></td>
      <td style="color:#f1f5f9;font-weight:600">${s.code}</td>
      <td><span class="tag">${s.segment}</span></td>
      <td style="color:#94a3b8">${s.close}</td>
      <td><div class="bar-wrap"><div class="bar" style="width:${barW}px"></div><span style="color:#f87171">${s.signal.toFixed(3)}</span></div></td>
      <td style="color:#94a3b8">${pct(s.signal_pct)}</td>
      <td>${ffHtml}</td>
      <td>${statusHtml}</td>
    </tr>`;
  }).join('');
}
```

- [ ] **Step 5: Add JS — command execution + SSE streaming**

```javascript
// ── command execution ─────────────────────────────
let currentEventSource = null;

async function runCmd(cmd) {
  // Disable all buttons while running
  document.querySelectorAll('.cmd-btn').forEach(b => b.disabled = true);
  $('log-body').innerHTML = '';
  $('log-title').textContent = `python main.py ${cmd} — 执行中`;
  $('log-dot').style.background = '#22c55e';
  $('log-dot').style.animation = 'pulse 1s infinite';
  $('status-dot').className = 'dot active';

  const res = await fetch(`/api/run/${cmd}`, { method: 'POST' });
  if (!res.ok) {
    const err = await res.json();
    appendLog('warn', err.detail || '启动失败');
    resetButtons();
    return;
  }
  const { task_id } = await res.json();

  if (currentEventSource) currentEventSource.close();
  currentEventSource = new EventSource(`/api/stream/${task_id}`);
  currentEventSource.onmessage = e => {
    const ev = JSON.parse(e.data);
    if (ev.type === 'done') {
      $('log-title').textContent = `python main.py ${cmd} — ${ev.exit_code === 0 ? '完成 ✓' : '失败 ✗'}`;
      $('log-dot').style.background = ev.exit_code === 0 ? '#4ade80' : '#f87171';
      $('log-dot').style.animation = 'none';
      $('status-dot').className = 'dot idle';
      currentEventSource.close();
      resetButtons();
      // Refresh status data after command completes
      loadStatus();
    } else {
      appendLog(ev.level || 'default', ev.msg || '');
    }
  };
  currentEventSource.onerror = () => {
    appendLog('warn', '连接中断');
    resetButtons();
  };
}

function appendLog(level, msg) {
  const ts = new Date().toLocaleTimeString('zh-CN', { hour12: false });
  const div = document.createElement('div');
  div.className = `log-line ${level}`;
  div.innerHTML = `<span class="ts">[${ts}]</span><span class="msg">${msg}</span>`;
  $('log-body').appendChild(div);
  $('log-body').scrollTop = $('log-body').scrollHeight;
}

function resetButtons() {
  document.querySelectorAll('.cmd-btn').forEach(b => b.disabled = false);
}

function toggleLog() {
  const body = $('log-body');
  body.style.display = body.style.display === 'none' ? '' : 'none';
}

// ── initial data load ─────────────────────────────
async function loadStatus() {
  try {
    const res = await fetch('/api/status');
    const data = await res.json();
    renderWatermarks(data.watermarks || {});
    renderMetrics(data.metrics || {});
    renderBacktest(data.backtest || {});
    renderScan(data.scan || {});
    $('topbar-status').textContent = `上次 scan：${(data.scan || {}).date || '—'}`;
    $('status-dot').className = 'dot idle';
  } catch (e) {
    $('topbar-status').textContent = '数据加载失败';
  }
}

document.addEventListener('DOMContentLoaded', loadStatus);
```

- [ ] **Step 6: Open browser and verify**

```bash
python monitor.py
```

Open http://127.0.0.1:8765. Verify:
- Page loads without JS errors (check browser console)
- 6 watermark cards show correct dates/counts
- Metrics panel shows IC values
- Backtest chart renders the equity curve
- Scan table shows at least 5 rows
- Click `scan --top-k 50` button → log panel shows real output → completion message appears

- [ ] **Step 7: Commit**

```bash
git add monitor_ui/index.html
git commit -m "feat: monitor UI single-page frontend"
```

---

## Task 8: Integration + final checks

- [ ] **Step 1: Run all monitor tests**

```bash
pytest tests/monitor/ -v
```

Expected: all tests PASSED, no warnings.

- [ ] **Step 2: Verify full flow end-to-end**

```bash
python monitor.py &
sleep 2

# status
curl -s http://127.0.0.1:8765/api/status | python -m json.tool | grep -E '"status"|"date"' | head -20

# invalid command
curl -s -X POST http://127.0.0.1:8765/api/run/bad_cmd
# Expected: {"detail":"Unknown command: 'bad_cmd'"}

# duplicate run (409)
curl -s -X POST http://127.0.0.1:8765/api/run/scan &
curl -s -X POST http://127.0.0.1:8765/api/run/scan
# Second call expected: {"detail":"A task is already running"}

kill %1
```

- [ ] **Step 3: Final commit**

```bash
git add -A
git commit -m "feat: quantitative trading monitor UI — complete implementation"
```

---

## Self-Review

**Spec coverage check:**

| Spec requirement | Covered in |
|-----------------|-----------|
| 流程控制台 4类按钮 | Task 7 Step 3 |
| 数据水位 6格 | Task 2 + Task 7 Step 4 |
| 数据接口状态表 8行 | Task 7 Step 2 (static HTML) |
| 特色因子表 9行 | Task 7 Step 2 (static HTML) |
| 模型指标 | Task 3 + Task 7 Step 4 |
| 回测权益曲线 | Task 4 + Task 7 Step 4 |
| 选股信号表 | Task 4 (scan.py) + Task 7 Step 4 |
| fund_flow 主力净流入列 | Task 4 scan.py _latest_fund_flow() |
| SSE 实时日志 | Task 5 + Task 6 + Task 7 Step 5 |
| A股涨跌色 | finColor() in Task 7 Step 4 |
| 同时只允许一个命令 | runner.py is_running() + api/run.py 409 |
| 命令白名单 | runner.py CMD_MAP |
| python monitor.py 启动 | monitor.py __main__ |

All requirements covered. No gaps found.
