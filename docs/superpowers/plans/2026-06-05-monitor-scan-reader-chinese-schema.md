# 修复 monitor scan 读取器（中文列）+ 显示连榜 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `monitor/readers/scan.py` 读当前中文列 scan CSV（修 `/api/status` 500），并把「连榜」呈现到监控台信号表。

**Architecture:** `get_scan()` 改用中文列名映射 + 列缺失优雅降级；`SignalRow` 加 `streak` 字段；`index.html` 信号表加「连榜」列；`test_scan.py` 假数据改新中文 schema。

**Tech Stack:** Python 3.11, pandas, FastAPI(monitor), pytest, 原生 HTML/JS。

约定：项目根 `/Users/hanxuefei/7、AI 空间/7-3、GitHub/quant_trading`，用 `.venv/bin/python`。spec：`docs/superpowers/specs/2026-06-05-monitor-scan-reader-chinese-schema-design.md`。

---

## 文件结构

| 文件 | 改动 |
|------|------|
| `monitor/readers/scan.py` | `SignalRow` +`streak`；`get_scan()` 读中文列 + 优雅降级；加 logging |
| `tests/monitor/test_scan.py` | `_sample_rows`/内联行改中文列；加 streak 断言与缺列降级测试 |
| `monitor_ui/index.html` | 信号表加「连榜」列（th + td + colspan 8→9 两处）|

---

## Task 1: scan.py 读中文列 + streak + 优雅降级

**Files:**
- Modify: `monitor/readers/scan.py`
- Test: `tests/monitor/test_scan.py`

- [ ] **Step 1: 改测试假数据为新中文 schema + 加新断言/测试**

(a) 替换 `tests/monitor/test_scan.py` 的 `_sample_rows` 函数为：
```python
def _sample_rows(n: int, date: str = "2026-06-04") -> list[dict]:
    """生成 n 行新格式（中文列）scan 假数据，排名递增。"""
    return [
        {
            "排名": i + 1,
            "代码": f"{300500 + i:06d}",
            "名称": f"股票{i}",
            "行业": "元器件",
            "板块": "创业板",
            "收盘价": 10.0 + i,
            "信号值": 1.5 - i * 0.01,
            "信号分位": 1.0 - i * 0.01,
            "连榜": (i % 5) + 1,
        }
        for i in range(n)
    ]
```

(b) 在 `TestScanReadsSignals` 中替换 `test_signal_row_has_correct_fields` 为（加 streak 断言）：
```python
    def test_signal_row_has_correct_fields(self, tmp_path):
        """SignalRow should have expected fields populated from CSV."""
        _make_scan_csv(tmp_path, "2026-06-04", _sample_rows(3))
        result = get_scan(tmp_path, top_n=3, buffer_n=0)
        row = result.signals[0]
        assert row.rank == 1
        assert row.code == "300500"
        assert row.segment == "创业板"
        assert isinstance(row.close, float)
        assert isinstance(row.signal, float)
        assert isinstance(row.signal_pct, float)
        assert row.streak == 1
```

(c) 替换 `test_code_is_zero_padded_string` 与 `test_code_integer_in_csv_is_zero_padded` 的内联行为中文键：
```python
    def test_code_is_zero_padded_string(self, tmp_path):
        """code must be a zero-padded 6-digit string."""
        rows = [{
            "排名": 1, "代码": "000089", "名称": "x", "行业": "银行",
            "板块": "深市主板", "收盘价": 6.76, "信号值": 1.5,
            "信号分位": 1.0, "连榜": 1,
        }]
        _make_scan_csv(tmp_path, "2026-06-04", rows)
        result = get_scan(tmp_path, top_n=1, buffer_n=0)
        assert result.signals[0].code == "000089"

    def test_code_integer_in_csv_is_zero_padded(self, tmp_path):
        """If code is stored as integer in CSV (e.g., 89), it must be zero-padded to 6 digits."""
        rows = [{
            "排名": 1, "代码": 89, "名称": "x", "行业": "银行",
            "板块": "深市主板", "收盘价": 6.76, "信号值": 1.5,
            "信号分位": 1.0, "连榜": 1,
        }]
        _make_scan_csv(tmp_path, "2026-06-04", rows)
        result = get_scan(tmp_path, top_n=1, buffer_n=0)
        assert result.signals[0].code == "000089"
```

(d) 在 `TestScanFundFlow` 中替换两个内联行的英文键为中文键：
```python
    def test_fund_flow_returns_latest_major_net_inflow(self, tmp_path):
        """fund_flow should return the last row's major_net_inflow from parquet."""
        code = "300500"
        rows = [{
            "排名": 1, "代码": code, "名称": "x", "行业": "元器件",
            "板块": "创业板", "收盘价": 10.0, "信号值": 1.5,
            "信号分位": 1.0, "连榜": 1,
        }]
        _make_scan_csv(tmp_path, "2026-06-04", rows)
        _make_fund_flow(tmp_path, code, [100_000.0, 200_000.0, 999_000.0])

        result = get_scan(tmp_path, top_n=1, buffer_n=0)
        assert result.signals[0].fund_flow == pytest.approx(999_000.0)
```
（`test_fund_flow_some_stocks_have_parquet_some_dont` 与 `test_fund_flow_none_when_parquet_missing` 用 `_sample_rows`，随 (a) 自动适配，无需改。）

(e) 在文件末尾新增一个类与两个测试（streak + 优雅降级）：
```python
class TestScanStreakAndDegrade:
    def test_streak_populated_from_csv(self, tmp_path):
        """连榜 列应映射到 SignalRow.streak。"""
        _make_scan_csv(tmp_path, "2026-06-04", _sample_rows(6))
        result = get_scan(tmp_path, top_n=6, buffer_n=0)
        # _sample_rows: 连榜 = (i % 5) + 1 → 第6行(i=5) streak=1
        assert [s.streak for s in result.signals] == [1, 2, 3, 4, 5, 1]

    def test_missing_required_columns_returns_empty(self, tmp_path):
        """CSV 缺必需中文列时返回空 ScanData（不抛异常，防 /api/status 500）。"""
        # 旧英文格式（无中文列）
        bad_rows = [{"date": "2026-06-04", "code": "300500", "rank": 1, "signal": 1.5}]
        _make_scan_csv(tmp_path, "2026-06-04", bad_rows)
        result = get_scan(tmp_path)
        assert isinstance(result, ScanData)
        assert result.date == "2026-06-04"
        assert result.signals == []
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/monitor/test_scan.py -v`
Expected: 多个 FAIL（现 reader 读英文列 `df["code"]`，遇中文列 KeyError；streak/降级测试也失败）。

- [ ] **Step 3: 重写 scan.py 的 get_scan + SignalRow**

在 `monitor/readers/scan.py` 顶部 import 区加 logging（紧跟现有 import）：
```python
import logging

logger = logging.getLogger(__name__)
```

在 `SignalRow` dataclass 中，于 `fund_flow` 与 `status` 之间插入一行字段：
```python
    fund_flow: Optional[float]  # latest major_net_inflow, or None if unavailable
    streak: Optional[int]       # 连榜：截至当日连续在榜次数，缺则 None
    status: str                 # "hold" for rank <= top_n, "buffer" for buffer rows
```

把 `get_scan()` 整个函数体（从 `backtest_dir = ...` 到 `return ScanData(...)`）替换为：
```python
    backtest_dir = data_dir / "backtest"
    scan_files = sorted(backtest_dir.glob("scan_*.csv")) if backtest_dir.exists() else []

    if not scan_files:
        return ScanData(date=None, top_n=top_n, buffer_n=buffer_n)

    # 最新文件按文件名字典序最后（scan_YYYY-MM-DD 可正确排序）
    latest_file = scan_files[-1]
    date_str = latest_file.stem[len("scan_"):]  # "scan_2026-06-04" → "2026-06-04"

    df = pd.read_csv(latest_file)

    # 优雅降级：缺必需中文列（如最新文件恰为旧英文格式）→ 返回空，避免 /api/status 500
    required = ["排名", "代码", "收盘价", "信号值", "信号分位"]
    if not all(col in df.columns for col in required):
        logger.warning("scan CSV %s 缺必需列 %s，返回空信号", latest_file.name, required)
        return ScanData(date=date_str, top_n=top_n, buffer_n=buffer_n)

    df = df.sort_values("排名").head(top_n + buffer_n)

    signals: list[SignalRow] = []
    for _, row in df.iterrows():
        rank = int(row["排名"])
        code = str(int(float(row["代码"]))).zfill(6)  # 整数/字符串代码统一补零到 6 位
        status = "hold" if rank <= top_n else "buffer"
        fund_flow = _latest_fund_flow(data_dir, code)
        streak = int(row["连榜"]) if "连榜" in df.columns and pd.notna(row["连榜"]) else None
        segment = str(row["板块"]) if "板块" in df.columns and pd.notna(row["板块"]) else "—"

        signals.append(SignalRow(
            rank=rank,
            code=code,
            segment=segment,
            close=float(row["收盘价"]),
            signal=float(row["信号值"]),
            signal_pct=float(row["信号分位"]),
            north_5d=None,
            fund_flow=fund_flow,
            streak=streak,
            status=status,
        ))

    return ScanData(date=date_str, top_n=top_n, buffer_n=buffer_n, signals=signals)
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/monitor/test_scan.py -v`
Expected: 全部 PASS。

- [ ] **Step 5: 真实 CSV 冒烟（确认读现网 06-04 文件）**

Run:
```bash
.venv/bin/python -c "
from pathlib import Path
from monitor.readers.scan import get_scan
sc = get_scan(Path('data'))
print('date:', sc.date, 'n signals:', len(sc.signals))
s = sc.signals[0]
print('top1:', s.rank, s.code, s.segment, s.close, s.signal_pct, 'streak=', s.streak, s.status)
"
```
Expected: 打印 `date: 2026-06-04`、若干信号、top1 含 streak 值，无异常。

- [ ] **Step 6: Commit**

```bash
git add monitor/readers/scan.py tests/monitor/test_scan.py
git commit -m "fix(monitor): scan 读取器改读中文列 + 映射连榜(streak), 缺列优雅降级"
```

---

## Task 2: index.html 信号表加「连榜」列

**Files:**
- Modify: `monitor_ui/index.html`
- Test: `tests/monitor/test_ui_buttons.py`（沿用现有文件，加一条内容断言）

- [ ] **Step 1: 写失败测试**

在 `tests/monitor/test_ui_buttons.py` 末尾追加：
```python
def test_scan_table_has_streak_column():
    assert "<th>连榜</th>" in HTML
    assert "s.streak" in HTML  # 行模板引用 streak
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/monitor/test_ui_buttons.py -k streak -v`
Expected: FAIL（找不到 连榜 列）。

- [ ] **Step 3: 改 index.html**

(a) 表头（第 277 行附近）：把
```html
      <thead><tr><th>#</th><th>代码</th><th>板块</th><th>收盘价</th><th>信号值</th><th>分位</th><th>主力流入</th><th>状态</th></tr></thead>
```
改为（在 `<th>状态</th>` 前插 `<th>连榜</th>`）：
```html
      <thead><tr><th>#</th><th>代码</th><th>板块</th><th>收盘价</th><th>信号值</th><th>分位</th><th>主力流入</th><th>连榜</th><th>状态</th></tr></thead>
```

(b) 静态空态行（第 279 行附近）：把 `<td colspan="8"` 改为 `<td colspan="9"`：
```html
        <tr><td colspan="9" style="text-align:center;color:#334155;padding:20px">暂无信号数据</td></tr>
```

(c) `renderScan` 空态（约第 397 行）：把
```javascript
  if (!sigs.length) { $('scan-body').innerHTML='<tr><td colspan="8" style="text-align:center;color:#334155;padding:20px">暂无信号</td></tr>'; return; }
```
改为 `colspan="9"`：
```javascript
  if (!sigs.length) { $('scan-body').innerHTML='<tr><td colspan="9" style="text-align:center;color:#334155;padding:20px">暂无信号</td></tr>'; return; }
```

(d) `renderScan` 行模板：在主力流入 `<td>` 与状态 `<td>`（`${stTag}`）之间插入连榜 `<td>`。把：
```javascript
      <td style="color:${ffColor}">${s.fund_flow!=null?(s.fund_flow>0?'+':'')+s.fund_flow.toFixed(0)+'万':'—'}</td>
      <td>${stTag}</td>
```
改为：
```javascript
      <td style="color:${ffColor}">${s.fund_flow!=null?(s.fund_flow>0?'+':'')+s.fund_flow.toFixed(0)+'万':'—'}</td>
      <td style="color:#94a3b8">${s.streak!=null?s.streak:'—'}</td>
      <td>${stTag}</td>
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/monitor/test_ui_buttons.py -v`
Expected: 全部 PASS。

- [ ] **Step 5: Commit**

```bash
git add monitor_ui/index.html tests/monitor/test_ui_buttons.py
git commit -m "feat(monitor): 信号表新增连榜列"
```

---

## Task 3: 全量回归 + 服务端到端验证

**Files:** 无（验证）

- [ ] **Step 1: 全量回归**

Run: `.venv/bin/python -m pytest -q -p no:cacheprovider`
Expected: 全部 PASS（341 + 新增测试）。

- [ ] **Step 2: 重启 monitor 并验证 /api/status 200**

先停旧服务（若在跑）：`pkill -f "monitor.py" 2>/dev/null; sleep 1`
启动（后台）：`.venv/bin/python monitor.py --port 8765 &`，待 ~3 秒，然后：
```bash
curl -s -o /dev/null -w "status HTTP %{http_code}\n" http://127.0.0.1:8765/api/status
curl -s http://127.0.0.1:8765/api/status | python3 -c "import sys,json; d=json.load(sys.stdin); s=d['scan']; print('scan date:', s['date'], 'n:', len(s['signals'])); print('top1 streak:', s['signals'][0]['streak'] if s['signals'] else None)"
```
Expected: `status HTTP 200`；scan date=2026-06-04、信号非空、top1 含 streak 值。

> 验证完成后此后台 monitor 即可作为运行中的监控服务（或按需 `pkill -f monitor.py` 停止）。

---

## Self-Review 记录

- **Spec 覆盖**：①SignalRow.streak → Task1 Step3；②get_scan 中文列映射 → Task1 Step3；③优雅降级 → Task1 Step3 + test；④index.html 连榜列 → Task2；⑤测试改新 schema + streak + 降级 → Task1 Step1；⑥status.py 不改（asdict 自动）→ 非目标，Task3 Step2 验证 streak 已进 JSON。无遗漏。
- **占位扫描**：无 TBD/TODO；每步完整代码与命令。
- **类型/命名一致**：`SignalRow.streak`、CSV 列 `排名/代码/板块/收盘价/信号值/信号分位/连榜`、`required` 列表、`s.streak`(JS) 跨任务一致；`_sample_rows` 中文键与 get_scan 读取列一致。
