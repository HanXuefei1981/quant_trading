# Phase 1 增量日更 + 全量分离 + UI 重排 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `main.py 1` 默认走增量特征工程、全量重建改用 `1 --full`，新增 `daily` 一键日更链，并把 monitor UI 重排为「日常更新 / 全量重建与训练 / 其他采集」三区。

**Architecture:** CLI 层 `phase1()` 按 `--full` 分支选择 `assemble_incremental()`（默认）或 `assemble()`（全量），全量分支补回写 features 水位；新增 `daily()` 顺序跑 update→fetch-fund→fetch-flow→1(增量)→scan 并 fail-fast。UI 层只改 `CMD_MAP` 新增两键与 `index.html` 按钮分组，runner 核心不动。

**Tech Stack:** Python 3.11, argparse, DuckDB, pytest, FastAPI(monitor), 原生 HTML/JS。

**约定：** 所有命令在项目根 `/Users/hanxuefei/7、AI 空间/7-3、GitHub/quant_trading` 下用项目虚拟环境 `.venv/bin/python` 执行。spec 见 `docs/superpowers/specs/2026-06-04-phase1-incremental-cli-ui-design.md`。

---

## 文件结构

| 文件 | 职责 | 改动 |
|------|------|------|
| `src/features/assembler.py` | 特征组装 + 水位 | 新增 `_write_features_watermark()`；重构 `assemble_incremental` 末尾水位写入复用之 |
| `main.py` | CLI 入口 | 新增 `--full` 标志；`phase1()` 增量/全量分支；新增 `daily()` 子命令并注册 |
| `monitor/runner.py` | UI→CLI 命令映射 | `CMD_MAP` 新增 `phase1-full`、`daily` |
| `monitor_ui/index.html` | 监控台单页 | 流程控制台按钮三区重排 + 新增「一键日更」「Phase1 全量」按钮 |
| `tests/test_assembler_watermark.py` | 增量/水位测试 | 新增 helper 单测 |
| `tests/test_main_commands.py` | main 命令测试 | 新增 `--full`/`daily`/分支注册测试 |
| `tests/monitor/test_runner.py` | runner 测试 | 更新 `expected_keys`；新增映射断言 |
| `docs/程序运行说明手册.md` | 运行手册 | 新建 |
| `README.md` / `docs/执行手册.md` | 文档 | 更新工作流与子命令表 |

---

## Task 1: assembler — 抽出 `_write_features_watermark()` 并复用

**Files:**
- Modify: `src/features/assembler.py`（新增 helper；重构 `assemble_incremental` 末尾约 371-380 行）
- Test: `tests/test_assembler_watermark.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_assembler_watermark.py` 末尾追加：

```python
# ── _write_features_watermark helper ─────────────────────────────────────────

def test_write_features_watermark_uses_last_labeled_date(conn):
    """helper 应把水位写为 df 中最后【有标签】日，并返回该日期。"""
    from src.features.assembler import _write_features_watermark
    from src.dal.meta_repo import MetaRepo

    meta_repo = MetaRepo(conn)
    df = pd.DataFrame({
        "date": pd.to_datetime(["2026-06-01", "2026-06-02", "2026-06-03"]),
        "code": ["000001", "000001", "000001"],
        "label": [1.0, 0.0, float("nan")],  # 06-03 无标签
    })

    written = _write_features_watermark(df, meta_repo)

    assert written == date(2026, 6, 2)
    assert meta_repo.get_last_date("features", "__market__") == date(2026, 6, 2)


def test_write_features_watermark_empty_df_is_noop(conn):
    """空 df 不写水位、返回 None。"""
    from src.features.assembler import _write_features_watermark
    from src.dal.meta_repo import MetaRepo

    meta_repo = MetaRepo(conn)
    written = _write_features_watermark(pd.DataFrame(), meta_repo)

    assert written is None
    assert meta_repo.get_last_date("features", "__market__") is None
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_assembler_watermark.py -k write_features_watermark -v`
Expected: FAIL（`ImportError` / `cannot import name '_write_features_watermark'`）

- [ ] **Step 3: 实现 helper**

在 `src/features/assembler.py` 中、`assemble_incremental` 函数定义**之前**（约第 256 行后、`def assemble_incremental` 之上）新增：

```python
def _write_features_watermark(df: "pd.DataFrame", meta_repo) -> "date | None":
    """把 features 水位写为 df 中最后【有标签】日（无标签则用最大日）。

    增量与全量两条路径共用，保证水位语义一致：下次增量从此日向前重算，
    给之前无标签的最近行补标签并延伸新尾部。

    Args:
        df: 含 date / label 列的特征 DataFrame。
        meta_repo: MetaRepo 实例。
    Returns:
        实际写入的日期；df 为空时返回 None（不写）。
    """
    if df is None or df.empty:
        return None
    labeled = df[df["label"].notna()]
    src = labeled if not labeled.empty else df
    new_max = src["date"].max()
    if hasattr(new_max, "date"):
        new_max = new_max.date()
    meta_repo.set_last_date("features", "__market__", new_max, row_count=len(df))
    return new_max
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_assembler_watermark.py -k write_features_watermark -v`
Expected: PASS（2 passed）

- [ ] **Step 5: 重构 `assemble_incremental` 末尾复用 helper**

在 `src/features/assembler.py` 的 `assemble_incremental` 中，找到当前的水位写入段（约 371-380 行）：

```python
    # 水位设为最后**有标签**日：下次增量从此向前重算，给之前无标签的最近行补上标签，
    # 并延伸新的无标签尾部，保证标签最终都被填充。
    labeled = combined[combined["label"].notna()]
    watermark_src = labeled if not labeled.empty else combined
    new_max_date = watermark_src["date"].max().date()
    meta_repo.set_last_date("features", "__market__", new_max_date, row_count=len(combined))
    logger.info(
        f"增量完成：新增 {len(combined)} 行（含无标签最近行），已写入 FeatureRepo，"
        f"水位（最后有标签日）更新至 {new_max_date}"
    )
    return combined
```

替换为：

```python
    # 水位设为最后**有标签**日：下次增量从此向前重算，给之前无标签的最近行补上标签，
    # 并延伸新的无标签尾部，保证标签最终都被填充。
    new_max_date = _write_features_watermark(combined, meta_repo)
    logger.info(
        f"增量完成：新增 {len(combined)} 行（含无标签最近行），已写入 FeatureRepo，"
        f"水位（最后有标签日）更新至 {new_max_date}"
    )
    return combined
```

- [ ] **Step 6: 运行整组 assembler 水位测试确认无回归**

Run: `.venv/bin/python -m pytest tests/test_assembler_watermark.py -v`
Expected: PASS（含既有 + 2 新测试全过）

- [ ] **Step 7: Commit**

```bash
git add src/features/assembler.py tests/test_assembler_watermark.py
git commit -m "refactor: 抽出 _write_features_watermark 供增量/全量共用"
```

---

## Task 2: main.py — `--full` 标志 + phase1 增量/全量分支

**Files:**
- Modify: `main.py`（argparse 约 728-729 行后加 `--full`；`phase1()` 约 446-453 行改分支）
- Test: `tests/test_main_commands.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_main_commands.py` 末尾追加：

```python
def test_full_flag_registered():
    """argparse 必须注册 --full 标志（dest=full）。"""
    import main as m
    src = inspect.getsource(m.main)
    assert '"--full"' in src or "'--full'" in src, "main() 未注册 --full"


def test_phase1_branches_on_full():
    """phase1 必须依据 args.full 在 assemble_incremental 与 assemble 间分支。"""
    import main as m
    src = inspect.getsource(m.phase1)
    assert "assemble_incremental" in src, "phase1 未走增量 assemble_incremental"
    assert "assemble(" in src, "phase1 未保留全量 assemble()"
    assert "full" in src, "phase1 未根据 args.full 分支"


def test_phase1_full_writes_watermark():
    """phase1 全量分支必须回写水位（调用 _write_features_watermark）。"""
    import main as m
    src = inspect.getsource(m.phase1)
    assert "_write_features_watermark" in src, "phase1 全量分支未回写 features 水位"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_main_commands.py -k "full or branches" -v`
Expected: FAIL（断言失败：未找到 `--full` / `_write_features_watermark`）

- [ ] **Step 3a: 注册 `--full` 标志**

在 `main.py` 的 `--refresh` 参数定义之后（约 729 行后）插入：

```python
    parser.add_argument("--full", action="store_true", dest="full",
                        help="phase1(1): 全量重建特征表（默认增量日更，仅处理新交易日）")
```

- [ ] **Step 3b: 改写 phase1 的 assemble 调用为分支**

在 `main.py` `phase1()` 中，找到：

```python
    from src.features.assembler import assemble
    df = assemble(
        sample_size=args.sample,
    )
    logger.info(f"Phase 1 完成，数据集形状: {df.shape}")
    logger.info(f"特征列数: {df.shape[1]}")
    logger.info(f"时间范围: {df['date'].min()} ~ {df['date'].max()}")
    logger.info(f"股票数量: {df['code'].nunique()}")
```

替换为：

```python
    if getattr(args, "full", False):
        from src.features.assembler import assemble, _write_features_watermark
        from src.dal.meta_repo import MetaRepo
        logger.info("Phase 1【全量重建】：assemble() 重算全表（耗时较长、写盘量大）")
        df = assemble(sample_size=args.sample)
        _write_features_watermark(df, MetaRepo(conn))
    else:
        from src.features.assembler import assemble_incremental
        logger.info("Phase 1【增量日更】：assemble_incremental() 仅处理新交易日")
        df = assemble_incremental()

    if df is None or df.empty:
        logger.info("Phase 1 完成：无新交易日数据")
        return

    logger.info(f"Phase 1 完成，数据集形状: {df.shape}")
    logger.info(f"特征列数: {df.shape[1]}")
    logger.info(f"时间范围: {df['date'].min()} ~ {df['date'].max()}")
    logger.info(f"股票数量: {df['code'].nunique()}")
```

> 说明：`conn` 在 phase1 前半段已由 `conn = get_db()` 取得，可直接复用构造 `MetaRepo(conn)`。`assemble_incremental()` 内部用同一 `get_db()` 单例连接。

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_main_commands.py -k "full or branches" -v`
Expected: PASS（3 passed）

- [ ] **Step 5: 烟雾验证 argparse 不报错**

Run: `.venv/bin/python main.py --help`
Expected: 正常打印帮助，含 `--full`，无异常退出。

- [ ] **Step 6: Commit**

```bash
git add main.py tests/test_main_commands.py
git commit -m "feat: main.py 1 默认增量, 1 --full 全量重建并回写水位"
```

---

## Task 3: main.py — `daily` 一键日更子命令

**Files:**
- Modify: `main.py`（新增 `daily()` 函数；`phase` choices 约 724 行加 `daily`；`phases` 字典约 771-785 行加 `daily`）
- Test: `tests/test_main_commands.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_main_commands.py` 末尾追加：

```python
def test_daily_registered_in_phases():
    """daily 必须在 choices 与 phases 字典中注册。"""
    import main as m
    src = inspect.getsource(m.main)
    assert '"daily"' in src or "'daily'" in src, "daily 未注册到 main()"


def test_daily_runs_steps_in_order(monkeypatch):
    """daily 顺序调用 update→fetch_fund→fetch_flow→phase1→scan，且 phase1 走增量。"""
    import main as m
    calls = []
    for name in ["update", "fetch_fund", "fetch_flow", "phase1", "scan"]:
        monkeypatch.setattr(m, name, (lambda n: (lambda args: calls.append(n)))(name))

    class A:  # 简易 args 容器
        full = True  # 故意置 True，daily 应在链中强制改为增量
    a = A()
    m.daily(a)

    assert calls == ["update", "fetch_fund", "fetch_flow", "phase1", "scan"]
    assert a.full is False, "daily 应把 args.full 置 False 让 phase1 走增量"


def test_daily_fail_fast(monkeypatch):
    """某步抛异常时，daily 应中止后续并以非零码退出 (SystemExit)。"""
    import main as m
    calls = []
    monkeypatch.setattr(m, "update", lambda args: calls.append("update"))
    def boom(args):
        calls.append("fetch_fund")
        raise RuntimeError("boom")
    monkeypatch.setattr(m, "fetch_fund", boom)
    monkeypatch.setattr(m, "fetch_flow", lambda args: calls.append("fetch_flow"))
    monkeypatch.setattr(m, "phase1", lambda args: calls.append("phase1"))
    monkeypatch.setattr(m, "scan", lambda args: calls.append("scan"))

    class A:
        full = False
    with pytest.raises(SystemExit) as exc:
        m.daily(A())
    assert exc.value.code == 1
    assert calls == ["update", "fetch_fund"]  # 在 fetch_fund 失败后中止
```

确保该文件顶部已 `import pytest`（若无则加）。

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_main_commands.py -k daily -v`
Expected: FAIL（`AttributeError: module 'main' has no attribute 'daily'`）

- [ ] **Step 3a: 新增 `daily()` 函数**

在 `main.py` 的 `scan()` 函数定义**之后**、`def main():` **之前**插入：

```python
def daily(args):
    """一键日更：update → fetch-fund → fetch-flow → Phase1(增量) → scan。

    fail-fast：任一步抛异常即记录失败步骤并以退出码 1 结束，后续步骤不执行。
    链中 Phase1 强制走增量（args.full=False）。
    """
    args.full = False  # 链中 Phase1 始终增量
    steps = [
        ("update", update),
        ("fetch-fund", fetch_fund),
        ("fetch-flow", fetch_flow),
        ("1(增量)", phase1),
        ("scan", scan),
    ]
    for name, fn in steps:
        logger.info(f"===== 一键日更 ▶ {name} =====")
        try:
            fn(args)
        except Exception:
            logger.exception(f"一键日更在步骤 [{name}] 失败，已中止")
            raise SystemExit(1)
    logger.info("===== 一键日更完成 =====")
```

- [ ] **Step 3b: 注册到 choices 与 phases**

把 `main()` 中的 `phase` 位置参数 choices 行（约 724 行）：

```python
    parser.add_argument("phase", choices=["ingest", "sync", "collect", "fetch-fund", "fetch-flow", "fetch-financial", "fetch-reports", "fetch-basic", "update", "1", "2", "3", "scan"],
```

改为（结尾加 `"daily"`）：

```python
    parser.add_argument("phase", choices=["ingest", "sync", "collect", "fetch-fund", "fetch-flow", "fetch-financial", "fetch-reports", "fetch-basic", "update", "daily", "1", "2", "3", "scan"],
```

并在 `phases` 字典（约 771-785 行）的 `"scan": scan,` 后加一行：

```python
        "daily": daily,
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_main_commands.py -k daily -v`
Expected: PASS（3 passed）

- [ ] **Step 5: 全量跑 main 命令测试**

Run: `.venv/bin/python -m pytest tests/test_main_commands.py -v`
Expected: PASS（全部）

- [ ] **Step 6: Commit**

```bash
git add main.py tests/test_main_commands.py
git commit -m "feat: 新增 daily 一键日更子命令 (update→fetch→phase1增量→scan, fail-fast)"
```

---

## Task 4: monitor/runner.py — CMD_MAP 新增 phase1-full / daily

**Files:**
- Modify: `monitor/runner.py`（`CMD_MAP` 约 36 行）
- Test: `tests/monitor/test_runner.py`（`test_cmd_map_has_all_keys` 约 49-64 行）

- [ ] **Step 1: 更新失败测试 + 新增映射断言**

把 `tests/monitor/test_runner.py` 的 `test_cmd_map_has_all_keys` 中 `expected_keys` 集合替换为（加入两键）：

```python
    expected_keys = {
        "update",
        "ingest",
        "collect",
        "fetch-fund",
        "fetch-flow",
        "fetch-financial",
        "fetch-reports",
        "phase1",
        "phase1-full",
        "daily",
        "phase2-rolling",
        "phase2-final",
        "phase3",
        "scan",
    }
    assert set(CMD_MAP.keys()) == expected_keys
```

并在该测试函数下方新增：

```python
def test_cmd_map_phase1_is_incremental_and_full_separate():
    """phase1 映射到 main.py 1（增量）；phase1-full 映射到 1 --full；daily 映射到 daily。"""
    assert CMD_MAP["phase1"][-1:] == ["1"]
    assert CMD_MAP["phase1-full"][-2:] == ["1", "--full"]
    assert CMD_MAP["daily"][-1:] == ["daily"]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/monitor/test_runner.py -k "cmd_map" -v`
Expected: FAIL（`expected_keys` 不匹配 / `KeyError: 'phase1-full'`）

- [ ] **Step 3: 更新 CMD_MAP**

在 `monitor/runner.py` 的 `CMD_MAP` 中，把 `"phase1"` 行（约 36 行）所在处替换为下面三行（`phase1` 保持不变，**新增** `phase1-full`、`daily`）：

```python
    "phase1":           [_PYTHON, "main.py", "1"],
    "phase1-full":      [_PYTHON, "main.py", "1", "--full"],
    "daily":            [_PYTHON, "main.py", "daily"],
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/monitor/test_runner.py -v`
Expected: PASS（全部）

- [ ] **Step 5: Commit**

```bash
git add monitor/runner.py tests/monitor/test_runner.py
git commit -m "feat(ui): CMD_MAP 新增 phase1-full 与 daily"
```

---

## Task 5: monitor_ui/index.html — 控制台三区重排 + 新按钮

**Files:**
- Modify: `monitor_ui/index.html`（流程控制台 206-244 行）
- Test: `tests/monitor/test_ui_buttons.py`（新建，轻量内容断言）

> `runCmd(cmd)` 用 `$('btn-'+cmd)` 按 id 定位按钮、用 `/api/run/<cmd>` 发起，逻辑通用——新按钮只需 `id="btn-<cmd>"` 与 `onclick="runCmd('<cmd>')"`，无需改 JS。

- [ ] **Step 1: 写失败测试**

新建 `tests/monitor/test_ui_buttons.py`：

```python
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
HTML = (ROOT / "monitor_ui" / "index.html").read_text(encoding="utf-8")


def test_daily_button_present():
    assert "runCmd('daily')" in HTML
    assert 'id="btn-daily"' in HTML


def test_phase1_incremental_and_full_buttons_present():
    assert "runCmd('phase1')" in HTML          # 增量(日更)
    assert "runCmd('phase1-full')" in HTML      # 全量
    assert 'id="btn-phase1-full"' in HTML


def test_section_labels_present():
    assert "日常更新" in HTML
    assert "全量重建与训练" in HTML


def test_no_orphan_old_phase1_label():
    # 旧的单一“Phase 1 特征计算”按钮文案应已被拆分替换
    assert "Phase 1 特征计算" not in HTML
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/monitor/test_ui_buttons.py -v`
Expected: FAIL（找不到 `runCmd('daily')` 等）

- [ ] **Step 3: 重排控制台 HTML**

把 `monitor_ui/index.html` 中 `<!-- Col 1: 流程控制台 -->` 到其 `</div>`（约 206-244 行）整段替换为：

```html
  <!-- Col 1: 流程控制台 -->
  <div class="ctrl-panel">
    <div class="section-title">流程控制台</div>

    <div class="ctrl-group">
      <div class="ctrl-group-label">日常更新 <span style="color:#92400e;font-size:9px">▶ 执行前确认已关闭 VPN</span></div>
      <div class="btn-grid">
        <button id="btn-daily" class="btn primary" onclick="runCmd('daily')">✨ 一键日更（update→特征增量→scan）</button>
        <button id="btn-update" class="btn" onclick="runCmd('update')">日 Kline / 北向</button>
        <button id="btn-fetch-fund" class="btn" onclick="runCmd('fetch-fund')">基本面（批量）</button>
        <button id="btn-fetch-flow" class="btn" onclick="runCmd('fetch-flow')">主力资金流向（批量）</button>
        <button id="btn-phase1" class="btn" onclick="runCmd('phase1')">Phase 1 增量（日更）</button>
        <button id="btn-scan" class="btn" onclick="runCmd('scan')">Scan Top-50</button>
      </div>
    </div>

    <div class="ctrl-group">
      <div class="ctrl-group-label">全量重建与训练</div>
      <div class="btn-grid">
        <button id="btn-phase1-full" class="btn warn-btn full" onclick="runCmd('phase1-full')">⚠ Phase 1 全量重建（耗时/大写盘）</button>
        <button id="btn-phase2-rolling" class="btn" onclick="runCmd('phase2-rolling')">Phase 2 滚动</button>
        <button id="btn-phase2-final" class="btn" onclick="runCmd('phase2-final')">Phase 2 全量</button>
        <button id="btn-phase3" class="btn full" onclick="runCmd('phase3')">Phase 3 回测</button>
      </div>
    </div>

    <div class="ctrl-group">
      <div class="ctrl-group-label">其他数据采集 <span style="color:#92400e;font-size:9px">▶ 关闭 VPN</span></div>
      <div class="btn-grid">
        <button id="btn-collect" class="btn" onclick="runCmd('collect')">龙虎榜</button>
        <button id="btn-fetch-financial" class="btn" onclick="runCmd('fetch-financial')">财务指标</button>
        <button id="btn-fetch-reports" class="btn" onclick="runCmd('fetch-reports')">研报 / EPS</button>
      </div>
    </div>
  </div>
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/monitor/test_ui_buttons.py -v`
Expected: PASS（4 passed）

- [ ] **Step 5: 人工核对（启动 monitor，肉眼看三区与按钮）**

Run: `.venv/bin/python monitor.py --port 8848`（另开浏览器访问 http://127.0.0.1:8848，确认三区布局、按钮可点、点击「一键日更」日志面板有流式输出后，Ctrl+C 停止）
Expected: 三分区显示正常；「✨ 一键日更」「⚠ Phase 1 全量重建」按钮存在且样式区分。

- [ ] **Step 6: Commit**

```bash
git add monitor_ui/index.html tests/monitor/test_ui_buttons.py
git commit -m "feat(ui): 流程控制台三区重排 + 一键日更/全量重建按钮"
```

---

## Task 6: 文档 — 运行说明手册 + README + 执行手册

**Files:**
- Create: `docs/程序运行说明手册.md`
- Modify: `README.md`（工作流段落 + 子命令表）
- Modify: `docs/执行手册.md`（命令说明）

- [ ] **Step 1: 新建 `docs/程序运行说明手册.md`**

写入以下完整内容：

````markdown
# 程序运行说明手册

> 本手册覆盖 A 股量化交易系统的全部运行方式：命令行（CLI）与监控台（UI）。
> 数据库默认在外置盘 `/Volumes/Elements/.../quant.duckdb`（exFAT），所有 tushare 采集命令须**关闭 VPN**。

## 0. 环境

```bash
cd "/Users/hanxuefei/7、AI 空间/7-3、GitHub/quant_trading"
# 统一用项目虚拟环境
.venv/bin/python main.py <命令>
```

## 1. 命令总览

| 命令 | 用途 | 备注 |
|------|------|------|
| `main.py daily` | **一键日更**：update→fetch-fund→fetch-flow→特征增量→scan | 推荐每日用；fail-fast |
| `main.py update` | 拉当日全市场 K 线 + 北向（T+1） | 关 VPN |
| `main.py fetch-fund` | 拉当日基本面（PE/PB/市值） | 关 VPN |
| `main.py fetch-flow` | 拉资金流向 + 北向 | 关 VPN |
| `main.py 1` | **特征工程（增量）**：仅算新交易日 | 日常默认；写盘量小 |
| `main.py 1 --full` | **特征工程（全量重建）**：重算全表 | 耗时长、写盘量大，应急/换数据源用 |
| `main.py 2 [--rolling|--final]` | 模型训练 | 全量批训，非增量 |
| `main.py 3` | 组合回测 | 一次性模拟，非增量 |
| `main.py scan --top-k N` | 最新截面 Top-N 选股 | 默认 --confirm 2 |
| `main.py sync --zip <zip>` | 月初换通达信全量数据 | 之后跑 `1 --full` |
| `main.py fetch-basic` | 拉股票名称/行业（scan 富信息用） | 偶尔刷新 |

## 2. 日常场景

### 2.1 每日选股（最常用）
```bash
.venv/bin/python main.py daily        # 一条命令跑完日更链
```
等价于依次：`update → fetch-fund → fetch-flow → 1 → scan`。任一步失败即停并报错。

### 2.2 只想重算特征 / 只想选股
```bash
.venv/bin/python main.py 1            # 增量补最新交易日特征
.venv/bin/python main.py scan --top-k 20
```

## 3. 全量重建场景（应急 / 月初换数据源）

```bash
# 月初：导入新的通达信全量包后重建
.venv/bin/python main.py sync --zip /path/to/hsjday-YYYY-MM-DD.zip
.venv/bin/python main.py 1 --full     # 全量重建特征表 + 回写水位
.venv/bin/python main.py 2 --rolling  # 重训
```
> `1 --full` 会重算 2021→今全表并对外置盘做数 GB 写盘，耗时约 1.5–2.5h。完成后会回写 features 水位，确保后续 `main.py 1` 增量从正确日期续算。

## 4. 监控台（UI）

```bash
.venv/bin/python monitor.py --port 8848   # 浏览器开 http://127.0.0.1:8848
```
控制台分三区：
- **日常更新**：✨ 一键日更 / 日 Kline / 基本面 / 资金流 / Phase1 增量 / Scan
- **全量重建与训练**：⚠ Phase1 全量重建 / Phase2 滚动 / Phase2 全量 / Phase3 回测
- **其他数据采集**：龙虎榜 / 财务指标 / 研报·EPS

一次只能跑一个任务；日志面板实时流式输出，可「■ 终止进程」。

## 5. 故障排查

### 5.1 外置盘 I/O 挂起（exFAT USB）
症状：`main.py 1 --full` 长时间无进度、进程 `STAT=U`（不可中断等待）、外置盘 `ls` 超时。
- 这是存储层问题，**不在代码层绕过**。
- 处理：重新插拔 USB；无效则重启 Mac。恢复后先验库可读：
  ```bash
  .venv/bin/python -c "import duckdb; duckdb.connect('<db路径>', read_only=True).execute('select max(date) from features').fetchall()"
  ```
- 预防：日常用增量（`main.py 1` / `daily`），全量重建只在必要时跑。

### 5.2 tushare「无效的 token」
私有代理 token 有时效，过期需更新 `.env` 的 `TUSHARE_TOKEN`。

### 5.3 VPN 开启导致采集失败
所有 tushare 命令须关 VPN（走国内私有代理）。
````

- [ ] **Step 2: 更新 `README.md` 日常增量段落**

在 `README.md` 的「日常增量更新」代码块（含 `python main.py update` 那段）中，把：

```
   python main.py 1               # 增量重建特征到最新交易日
   python main.py scan            # 最新推荐
```

替换为：

```
   python main.py 1               # 特征工程（增量，仅算新交易日）
   python main.py scan            # 最新推荐
   # 或一条命令跑完整条链：
   python main.py daily           # update→fetch-fund→fetch-flow→1(增量)→scan
```

- [ ] **Step 3: 更新 `README.md` 子命令表**

在「## 子命令一览」表格中，把 `python main.py 1 [--sample N]` 那一行替换为下面两行，并在 `update` 行后补 `daily` 行：

```
| `python main.py 1 [--sample N]` | Phase 1：特征工程（**增量**） | 仅处理 features 水位之后的新交易日，写盘量小（日常默认）|
| `python main.py 1 --full` | Phase 1：特征工程（**全量重建**） | 重算全表 + 回写水位；耗时长、写盘量大，应急/换数据源用 |
| `python main.py daily` | 一键日更链 | update→fetch-fund→fetch-flow→1(增量)→scan，fail-fast |
```

- [ ] **Step 4: 更新 `docs/执行手册.md`**

在 `docs/执行手册.md` 中检索 `main.py 1` 的说明处，补充一句区分（用 Edit 在该处追加）：

```
> `main.py 1` 现为**增量**（仅算新交易日）；需整表重算请用 `main.py 1 --full`；日常推荐 `main.py daily` 一键链。
```

- [ ] **Step 5: Commit**

```bash
git add docs/程序运行说明手册.md README.md docs/执行手册.md
git commit -m "docs: 新增运行说明手册, README/执行手册区分增量与全量"
```

---

## Task 7: 全量回归 + 收尾

- [ ] **Step 1: 跑受影响测试**

Run: `.venv/bin/python -m pytest tests/test_assembler_watermark.py tests/test_main_commands.py tests/monitor/test_runner.py tests/monitor/test_ui_buttons.py -v`
Expected: 全部 PASS。

- [ ] **Step 2: 跑全量测试套件确认无回归**

Run: `.venv/bin/python -m pytest -q`
Expected: 全部 PASS（如有与本次无关的既有失败，记录但不在本计划范围修复）。

- [ ] **Step 3: 端到端烟雾验证（真实库，增量路径，不动模型）**

Run: `.venv/bin/python main.py 1`（增量；当日无新数据则应打印「无新交易日数据」并正常退出）
Expected: 退出码 0，无异常；features 水位被更新或维持。

- [ ] **Step 4: 最终 commit（若有零散改动）**

```bash
git add -A
git commit -m "chore: phase1 增量化改造收尾"
```

---

## Self-Review 记录

- **Spec 覆盖**：①`1`=增量 → Task2；②`1 --full`=全量 → Task2；③全量回写水位 → Task1+Task2；④daily 一键 → Task3；⑤CMD_MAP → Task4；⑥UI 三区 → Task5；⑦文档 → Task6；⑧测试 → 各 Task 内。无遗漏。
- **占位扫描**：无 TBD/TODO；每个改代码步骤均含完整代码与精确命令。
- **类型/命名一致**：`_write_features_watermark(df, meta_repo)` 在 Task1 定义、Task2 调用，签名一致；CMD_MAP 键 `phase1-full`/`daily` 在 Task4 与 Task5/test 一致；`daily` 函数名在 Task3 定义、choices/phases/CMD_MAP 引用一致。
