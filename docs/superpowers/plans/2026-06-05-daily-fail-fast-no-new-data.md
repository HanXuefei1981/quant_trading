# daily 无新数据 fail-fast Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 `main.py daily` 在 `update` 未获取到新交易日数据（kline `ok==0`）时记录错误并以退出码 2 提前中止，跳过后续 fetch/phase1/scan。

**Architecture:** `update()` 末尾返回其 kline `CollectStats`；`daily()` 把 update 步从通用循环拆出，先跑并判定 `stats.ok==0` → `SystemExit(2)`，其余步保持现有异常 fail-fast（`SystemExit(1)`）。

**Tech Stack:** Python 3.11, argparse, pytest（monkeypatch）。

约定：项目根 `/Users/hanxuefei/7、AI 空间/7-3、GitHub/quant_trading`，用 `.venv/bin/python`。spec：`docs/superpowers/specs/2026-06-05-daily-fail-fast-no-new-data-design.md`。

---

## 文件结构

| 文件 | 改动 |
|------|------|
| `main.py` | `update()` 末尾 `return kline_stats`；重写 `daily()` 增加无新数据闸口 |
| `tests/test_main_commands.py` | 新增 2 测试 + 修改现有 2 个 daily 测试的 update mock |
| `docs/程序运行说明手册.md` | daily 段补退出码说明 |

---

## Task 1: update 返回 kline 统计 + daily 无新数据闸口

**Files:**
- Modify: `main.py`（`update()` 末尾；`daily()` 整体）
- Test: `tests/test_main_commands.py`

- [ ] **Step 1: 写失败测试 + 调整既有测试**

在 `tests/test_main_commands.py` 末尾追加两个新测试：

```python
def test_update_returns_kline_stats():
    """update() 必须返回 kline 统计（供 daily 判定无新数据）。"""
    import main as m
    src = inspect.getsource(m.update)
    assert "return kline_stats" in src, "update() 未返回 kline_stats"


def test_daily_aborts_when_no_new_data(monkeypatch):
    """update 无新交易日(ok==0) → SystemExit(2)，后续步骤不执行。"""
    import main as m
    from src.collectors.base import CollectStats
    calls = []
    def fake_update(args):
        calls.append("update")
        return CollectStats(ok=0)  # 无新交易日
    monkeypatch.setattr(m, "update", fake_update)
    monkeypatch.setattr(m, "fetch_fund", lambda args: calls.append("fetch_fund"))
    monkeypatch.setattr(m, "fetch_flow", lambda args: calls.append("fetch_flow"))
    monkeypatch.setattr(m, "phase1", lambda args: calls.append("phase1"))
    monkeypatch.setattr(m, "scan", lambda args: calls.append("scan"))

    class A:
        full = False
    with pytest.raises(SystemExit) as exc:
        m.daily(A())
    assert exc.value.code == 2
    assert calls == ["update"], "无新数据时不应执行后续步骤"
```

并修改现有两个 daily 测试，让 `update` mock 返回 `CollectStats(ok=1)`（否则新闸口会判定无数据而提前中止）。

把 `test_daily_runs_steps_in_order` 中这一行：
```python
    monkeypatch.setattr(m, "update", lambda args: calls.append("update"))
```
替换为：
```python
    from src.collectors.base import CollectStats
    def fake_update(args):
        calls.append("update")
        return CollectStats(ok=1)
    monkeypatch.setattr(m, "update", fake_update)
```

把 `test_daily_fail_fast` 中这一行：
```python
    monkeypatch.setattr(m, "update", lambda args: calls.append("update"))
```
替换为：
```python
    from src.collectors.base import CollectStats
    def fake_update(args):
        calls.append("update")
        return CollectStats(ok=1)
    monkeypatch.setattr(m, "update", fake_update)
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_main_commands.py -k daily -v`
Expected: FAIL（`test_daily_aborts_when_no_new_data` 因 daily 还没闸口而走完全部步骤；`test_update_returns_kline_stats` 因 update 还没 return）。

- [ ] **Step 3a: update() 末尾返回 kline_stats**

在 `main.py` 的 `update()` 函数末尾，找到最后一行：
```python
    logger.info("update 完成（特征工程请在所有数据采集完毕后运行 Phase 1）")
```
在其**后**新增一行（保持同级缩进）：
```python
    return kline_stats
```
（`kline_stats` 是该函数内已赋值的局部变量：`kline_stats = tdx.collect_tushare_daily_batch(since=kline_since)`。注意 `update()` 在 `kline_since is None` 时有一个更早的 `return`（无参，返回 None）——保持不动，daily 会把 None 当作无新数据处理。）

- [ ] **Step 3b: 重写 daily() 增加无新数据闸口**

把 `main.py` 中整个 `daily()` 函数替换为：
```python
def daily(args):
    """一键日更：update → fetch-fund → fetch-flow → Phase1(增量) → scan。

    fail-fast：
      - update 未获取到新交易日数据（kline ok==0）→ 退出码 2 提前中止；
      - 任一步抛异常 → 退出码 1 中止。
    后续步骤不执行。链中 Phase1 强制走增量（在 args 的副本上置 full=False，不改动调用方 args）。
    """
    import copy
    inner_args = copy.copy(args)
    inner_args.full = False  # 仅作用于副本，链中 Phase1 始终增量

    # 步骤 1：update —— 无新交易日数据则提前中止，避免后续空转
    logger.info("===== 一键日更 ▶ update =====")
    try:
        stats = update(inner_args)
    except Exception:
        logger.exception("一键日更在步骤 [update] 失败，已中止")
        raise SystemExit(1)
    if stats is None or stats.ok == 0:
        logger.error("一键日更：update 未获取到新交易日数据（非交易日 / 数据未就绪 / token 失效？），已中止，后续步骤跳过")
        raise SystemExit(2)

    # 步骤 2+：基本面 / 资金流 / 特征增量 / 选股
    steps = [
        ("fetch-fund", fetch_fund),
        ("fetch-flow", fetch_flow),
        ("1(增量)", phase1),
        ("scan", scan),
    ]
    for name, fn in steps:
        logger.info(f"===== 一键日更 ▶ {name} =====")
        try:
            fn(inner_args)
        except Exception:
            logger.exception(f"一键日更在步骤 [{name}] 失败，已中止")
            raise SystemExit(1)
    logger.info("===== 一键日更完成 =====")
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/test_main_commands.py -k daily -v`
Expected: PASS（含 `test_daily_aborts_when_no_new_data`、`test_daily_runs_steps_in_order`、`test_daily_fail_fast`）。

- [ ] **Step 5: 跑整个 main-commands 文件**

Run: `.venv/bin/python -m pytest tests/test_main_commands.py -v`
Expected: 全部 PASS。

- [ ] **Step 6: 烟雾验证 argparse 未破坏**

Run: `.venv/bin/python main.py --help`
Expected: 退出 0，正常打印帮助。（不要真跑 `main.py daily`，会打外部接口/库。）

- [ ] **Step 7: Commit**

```bash
git add main.py tests/test_main_commands.py
git commit -m "feat: daily 在 update 无新交易日(ok==0)时 fail-fast 退出码2"
```

---

## Task 2: 文档 + 全量回归

**Files:**
- Modify: `docs/程序运行说明手册.md`

- [ ] **Step 1: 手册 daily 段补退出码说明**

在 `docs/程序运行说明手册.md` 的「### 2.1 每日选股（最常用）」小节，找到这段：
```
等价于依次：`update → fetch-fund → fetch-flow → 1 → scan`。任一步失败即停并报错。
```
替换为：
```
等价于依次：`update → fetch-fund → fetch-flow → 1 → scan`。任一步抛异常即停（退出码 1）。
**若 update 未取到新交易日数据**（非交易日 / 数据未就绪 / tushare token 失效），daily 会**提前中止**（退出码 2），不再空跑后续步骤——此时请检查是否交易日、数据是否就绪、或刷新 `.env` 的 `TUSHARE_TOKEN`。
```

- [ ] **Step 2: Commit 文档**

```bash
git add docs/程序运行说明手册.md
git commit -m "docs: 手册说明 daily 无新数据提前中止(退出码2)"
```

- [ ] **Step 3: 全量回归**

Run: `.venv/bin/python -m pytest -q -p no:cacheprovider`
Expected: 全部 PASS（应为 338 passed：原 336 + 2 新增；现有 2 个 daily 测试被修改但数量不变）。

---

## Self-Review 记录

- **Spec 覆盖**：①update 返回 kline_stats → Task1 Step3a；②daily ok==0 闸口 + 退出码2 → Task1 Step3b；③退出码语义(0/1/2) → daily 实现；④改 2 既有测试 + 新增 abort 测试 + update 返回测试 → Task1 Step1；⑤文档 → Task2。无遗漏。
- **占位扫描**：无 TBD/TODO；每个改代码步骤含完整代码与命令。
- **类型/命名一致**：`CollectStats(ok=...)` 字段名与 `src/collectors/base.py` 一致；`update`/`daily`/`fetch_fund`/`fetch_flow`/`phase1`/`scan` 均为 main.py 现有函数名；`stats.ok` 与 CollectStats.ok 一致。
