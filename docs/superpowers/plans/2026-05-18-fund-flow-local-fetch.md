# 个股资金流向本地下载脚本 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现 `scripts/fetch_fund_flow_local.py`，让用户在关闭 VPN 后用国内 IP 一次性完成全量 5641 只股票资金流向历史下载，覆盖率目标 ≥ 80%。

**Architecture:** 独立脚本，通过 `sys.path` 复用 `src.data.fund_flow.fetch_fund_flow()` 完成单只股票拉取与缓存写入；脚本本身负责批量调度、进度显示、错误统计和结束报告。北交所（8x 开头）股票由 `fetch_fund_flow` 内部返回 None 自动跳过。

**Tech Stack:** Python 3.11、akshare、tqdm、pytest（测试）

---

## 文件结构

| 路径 | 操作 | 说明 |
|------|------|------|
| `scripts/fetch_fund_flow_local.py` | CREATE | 主脚本 |
| `tests/test_fetch_fund_flow_local.py` | CREATE | 单元测试 |
| `data/fund_flow/_failed.txt` | 运行时生成 | 失败股票列表 |

---

### Task 1: 代码索引与待下载过滤函数

**Files:**
- Create: `scripts/fetch_fund_flow_local.py`（骨架 + 两个函数）
- Create: `tests/test_fetch_fund_flow_local.py`

- [ ] **Step 1: 写失败测试**

新建 `tests/test_fetch_fund_flow_local.py`：

```python
"""测试 fetch_fund_flow_local 核心工具函数"""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.fetch_fund_flow_local import get_all_codes, get_pending_codes


def test_get_all_codes_returns_stems(tmp_path):
    """应返回目录下所有 parquet 文件的 stem，按字母排序"""
    (tmp_path / "000001.parquet").touch()
    (tmp_path / "600036.parquet").touch()
    (tmp_path / "readme.txt").touch()  # 非 parquet 应被忽略

    result = get_all_codes(tmp_path)

    assert result == ["000001", "600036"]


def test_get_all_codes_empty_dir(tmp_path):
    assert get_all_codes(tmp_path) == []


def test_get_pending_codes_filters_cached(tmp_path):
    """已有 parquet 的股票应被过滤掉"""
    (tmp_path / "000001.parquet").touch()
    all_codes = ["000001", "600036", "300001"]

    result = get_pending_codes(all_codes, tmp_path)

    assert result == ["600036", "300001"]


def test_get_pending_codes_all_cached(tmp_path):
    (tmp_path / "000001.parquet").touch()
    result = get_pending_codes(["000001"], tmp_path)
    assert result == []


def test_get_pending_codes_none_cached(tmp_path):
    result = get_pending_codes(["000001", "600036"], tmp_path)
    assert result == ["000001", "600036"]
```

- [ ] **Step 2: 运行测试，确认失败**

```bash
cd "/Users/hanxuefei/7、AI 空间/7-3、GitHub/quant_trading"
.venv/bin/pytest tests/test_fetch_fund_flow_local.py -v 2>&1 | head -20
```

预期：`ModuleNotFoundError: No module named 'scripts.fetch_fund_flow_local'`

- [ ] **Step 3: 实现骨架 + 两个函数**

新建 `scripts/fetch_fund_flow_local.py`：

```python
"""
个股资金流向本地下载脚本（关 VPN 后在终端运行）

使用方式：
  .venv/bin/python scripts/fetch_fund_flow_local.py           # 全量
  .venv/bin/python scripts/fetch_fund_flow_local.py --delay 0.5
  .venv/bin/python scripts/fetch_fund_flow_local.py --codes 000001 600036
"""
import argparse
import sys
import time
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tqdm import tqdm

from src.data.fund_flow import FUND_FLOW_DIR, fetch_fund_flow

KLINE_DIR = ROOT / "data" / "raw" / "kline"


def get_all_codes(kline_dir: Path = KLINE_DIR) -> list[str]:
    """返回 kline 目录下所有股票代码（parquet stem），按字母排序。"""
    return sorted(p.stem for p in kline_dir.glob("*.parquet"))


def get_pending_codes(all_codes: list[str], fund_flow_dir: Path = FUND_FLOW_DIR) -> list[str]:
    """过滤掉已有本地缓存的股票，返回待下载列表。"""
    return [c for c in all_codes if not (fund_flow_dir / f"{c}.parquet").exists()]
```

- [ ] **Step 4: 运行测试，确认通过**

```bash
.venv/bin/pytest tests/test_fetch_fund_flow_local.py::test_get_all_codes_returns_stems \
    tests/test_fetch_fund_flow_local.py::test_get_all_codes_empty_dir \
    tests/test_fetch_fund_flow_local.py::test_get_pending_codes_filters_cached \
    tests/test_fetch_fund_flow_local.py::test_get_pending_codes_all_cached \
    tests/test_fetch_fund_flow_local.py::test_get_pending_codes_none_cached -v
```

预期：5 个 PASSED

- [ ] **Step 5: 提交**

```bash
git add scripts/fetch_fund_flow_local.py tests/test_fetch_fund_flow_local.py
git commit -m "feat: 添加 get_all_codes / get_pending_codes 及测试"
```

---

### Task 2: 下载主循环 `download_all`

**Files:**
- Modify: `scripts/fetch_fund_flow_local.py`（追加 `download_all`）
- Modify: `tests/test_fetch_fund_flow_local.py`（追加测试）

- [ ] **Step 1: 写失败测试**

在 `tests/test_fetch_fund_flow_local.py` 末尾追加：

```python
from unittest.mock import patch, MagicMock
from scripts.fetch_fund_flow_local import download_all


def test_download_all_counts_ok_and_fail(tmp_path):
    """成功和失败分别计入统计，failed_codes 收集失败股票"""
    codes = ["000001", "600036", "300001"]

    def fake_fetch(code, use_cache):
        if code == "600036":
            return None  # 模拟失败
        import pandas as pd
        return pd.DataFrame({"date": ["2026-01-01"], "major_net_inflow": [1.0]})

    with patch("scripts.fetch_fund_flow_local.fetch_fund_flow", side_effect=fake_fetch):
        result = download_all(codes, delay=0.0, max_errors=200)

    assert result["ok"] == 2
    assert result["fail"] == 1
    assert "600036" in result["failed_codes"]


def test_download_all_stops_on_max_consecutive_errors(tmp_path):
    """连续失败达到 max_errors 时提前终止"""
    codes = ["000001", "000002", "000003", "000004", "000005"]

    with patch("scripts.fetch_fund_flow_local.fetch_fund_flow", return_value=None):
        result = download_all(codes, delay=0.0, max_errors=3)

    # 应在第 3 次连续失败后停止，不处理全部 5 只
    assert result["fail"] == 3
    assert result["ok"] == 0


def test_download_all_resets_consecutive_on_success():
    """成功后连续失败计数应重置，不提前终止"""
    # 失败-失败-成功-失败-失败 → 连续最多 2，不触发 max_errors=3
    import pandas as pd
    dummy_df = pd.DataFrame({"date": ["2026-01-01"], "major_net_inflow": [1.0]})
    side_effects = [None, None, dummy_df, None, None]
    codes = ["a", "b", "c", "d", "e"]

    with patch("scripts.fetch_fund_flow_local.fetch_fund_flow", side_effect=side_effects):
        result = download_all(codes, delay=0.0, max_errors=3)

    assert result["ok"] == 1
    assert result["fail"] == 4
    assert len(result["failed_codes"]) == 4
```

- [ ] **Step 2: 运行测试，确认失败**

```bash
.venv/bin/pytest tests/test_fetch_fund_flow_local.py::test_download_all_counts_ok_and_fail -v
```

预期：`ImportError: cannot import name 'download_all'`

- [ ] **Step 3: 实现 `download_all`**

在 `scripts/fetch_fund_flow_local.py` 的函数区追加：

```python
def download_all(
    codes: list[str],
    delay: float,
    max_errors: int,
) -> dict:
    """
    批量下载资金流向，返回统计字典。

    Returns:
        {"ok": int, "fail": int, "failed_codes": list[str]}
    """
    ok = 0
    fail = 0
    failed_codes: list[str] = []
    consecutive_errors = 0

    for i, code in enumerate(tqdm(codes, desc="资金流向下载", unit="只")):
        df = fetch_fund_flow(code, use_cache=False)
        if df is not None and not df.empty:
            ok += 1
            consecutive_errors = 0
        else:
            fail += 1
            failed_codes.append(code)
            consecutive_errors += 1
            if consecutive_errors >= max_errors:
                print(
                    f"\n⚠ 连续 {max_errors} 次失败，可能被限频。"
                    f"建议稍后重试剩余股票。"
                )
                break

        if (i + 1) % 200 == 0:
            print(f"\n  进度 {i+1}/{len(codes)}  成功 {ok}  失败 {fail}")

        if delay > 0 and i < len(codes) - 1:
            time.sleep(delay)

    return {"ok": ok, "fail": fail, "failed_codes": failed_codes}
```

- [ ] **Step 4: 运行测试，确认通过**

```bash
.venv/bin/pytest tests/test_fetch_fund_flow_local.py::test_download_all_counts_ok_and_fail \
    tests/test_fetch_fund_flow_local.py::test_download_all_stops_on_max_consecutive_errors \
    tests/test_fetch_fund_flow_local.py::test_download_all_resets_consecutive_on_success -v
```

预期：3 个 PASSED

- [ ] **Step 5: 提交**

```bash
git add scripts/fetch_fund_flow_local.py tests/test_fetch_fund_flow_local.py
git commit -m "feat: 实现 download_all 主循环及测试"
```

---

### Task 3: 启动摘要与结束报告

**Files:**
- Modify: `scripts/fetch_fund_flow_local.py`（追加 `print_header`、`print_report`）
- Modify: `tests/test_fetch_fund_flow_local.py`（追加测试）

- [ ] **Step 1: 写失败测试**

在 `tests/test_fetch_fund_flow_local.py` 末尾追加：

```python
from scripts.fetch_fund_flow_local import print_header, print_report


def test_print_header_shows_eta(capsys):
    """启动摘要应包含总量、缓存数、待下载数、预计时长"""
    print_header(total=5641, cached_count=531, pending_count=5110, delay=0.3)
    out = capsys.readouterr().out
    assert "5641" in out
    assert "531" in out
    assert "5110" in out
    assert "分钟" in out


def test_print_report_shows_coverage(capsys, tmp_path):
    """结束报告应包含覆盖率百分比"""
    print_report(ok=4000, fail=200, cached_count=531, total=5641,
                 failed_codes=[], fund_flow_dir=tmp_path)
    out = capsys.readouterr().out
    assert "4531" in out   # covered = 4000 + 531
    assert "5641" in out
    assert "%" in out


def test_print_report_writes_failed_txt(tmp_path, capsys):
    """有失败股票时应写入 _failed.txt"""
    failed = ["000999", "600999"]
    print_report(ok=100, fail=2, cached_count=0, total=102,
                 failed_codes=failed, fund_flow_dir=tmp_path)
    failed_file = tmp_path / "_failed.txt"
    assert failed_file.exists()
    content = failed_file.read_text().splitlines()
    assert content == ["000999", "600999"]


def test_print_report_no_failed_txt_when_empty(tmp_path, capsys):
    """无失败时不应创建 _failed.txt"""
    print_report(ok=100, fail=0, cached_count=0, total=100,
                 failed_codes=[], fund_flow_dir=tmp_path)
    assert not (tmp_path / "_failed.txt").exists()
```

- [ ] **Step 2: 运行测试，确认失败**

```bash
.venv/bin/pytest tests/test_fetch_fund_flow_local.py::test_print_header_shows_eta -v
```

预期：`ImportError: cannot import name 'print_header'`

- [ ] **Step 3: 实现 `print_header` 和 `print_report`**

在 `scripts/fetch_fund_flow_local.py` 追加：

```python
def print_header(total: int, cached_count: int, pending_count: int, delay: float) -> None:
    """打印下载开始前的摘要信息。"""
    est_minutes = pending_count * delay / 60
    print(f"\n{'='*50}")
    print(f"  股票总量：{total} 只")
    print(f"  已缓存：  {cached_count} 只（跳过）")
    print(f"  待下载：  {pending_count} 只")
    print(f"  预计时长：~{est_minutes:.0f} 分钟（按 {delay}s/只估算）")
    print(f"{'='*50}\n")


def print_report(
    ok: int,
    fail: int,
    cached_count: int,
    total: int,
    failed_codes: list[str],
    fund_flow_dir: Path = FUND_FLOW_DIR,
) -> None:
    """打印下载结束报告，并将失败列表写入 _failed.txt。"""
    covered = ok + cached_count
    pct = covered / total * 100 if total > 0 else 0.0

    print(f"\n{'='*50}")
    print(f"  资金流向下载完成")
    print(f"  成功：{ok} 只   缓存复用：{cached_count} 只   失败：{fail} 只")
    print(f"  覆盖率：{pct:.1f}%（{covered} / {total}）")

    if failed_codes:
        fund_flow_dir.mkdir(parents=True, exist_ok=True)
        failed_path = fund_flow_dir / "_failed.txt"
        failed_path.write_text("\n".join(failed_codes))
        print(f"  失败列表已写入 {failed_path}")

    print(f"{'='*50}")
    print(f"\n下一步：开启 VPN → 在 Claude Code 中运行 python main.py 1\n")
```

- [ ] **Step 4: 运行测试，确认通过**

```bash
.venv/bin/pytest tests/test_fetch_fund_flow_local.py::test_print_header_shows_eta \
    tests/test_fetch_fund_flow_local.py::test_print_report_shows_coverage \
    tests/test_fetch_fund_flow_local.py::test_print_report_writes_failed_txt \
    tests/test_fetch_fund_flow_local.py::test_print_report_no_failed_txt_when_empty -v
```

预期：4 个 PASSED

- [ ] **Step 5: 提交**

```bash
git add scripts/fetch_fund_flow_local.py tests/test_fetch_fund_flow_local.py
git commit -m "feat: 实现 print_header / print_report 及测试"
```

---

### Task 4: CLI 入口 `main()` 与全量测试

**Files:**
- Modify: `scripts/fetch_fund_flow_local.py`（追加 `main()`）
- Modify: `tests/test_fetch_fund_flow_local.py`（追加 smoke test）

- [ ] **Step 1: 写失败测试**

在 `tests/test_fetch_fund_flow_local.py` 末尾追加：

```python
from scripts.fetch_fund_flow_local import main


def test_main_no_pending_exits_early(tmp_path, capsys, monkeypatch):
    """无待下载股票时应打印提示后退出，不调用 download_all"""
    monkeypatch.setattr(
        "scripts.fetch_fund_flow_local.get_all_codes",
        lambda kline_dir=None: ["000001"],
    )
    monkeypatch.setattr(
        "scripts.fetch_fund_flow_local.get_pending_codes",
        lambda codes, fund_flow_dir=None: [],
    )
    main([])  # 空 argv
    out = capsys.readouterr().out
    assert "无需重新下载" in out


def test_main_custom_codes_bypass_pending_filter(monkeypatch, capsys):
    """指定 --codes 时直接使用指定列表，不经过 get_pending_codes 过滤"""
    import pandas as pd
    dummy = pd.DataFrame({"date": ["2026-01-01"], "major_net_inflow": [1.0]})

    monkeypatch.setattr(
        "scripts.fetch_fund_flow_local.get_all_codes",
        lambda kline_dir=None: ["000001", "600036"],
    )
    monkeypatch.setattr(
        "scripts.fetch_fund_flow_local.fetch_fund_flow",
        lambda code, use_cache: dummy,
    )
    main(["--codes", "600036", "--delay", "0"])
    out = capsys.readouterr().out
    assert "下载完成" in out
```

- [ ] **Step 2: 运行测试，确认失败**

```bash
.venv/bin/pytest tests/test_fetch_fund_flow_local.py::test_main_no_pending_exits_early -v
```

预期：`TypeError: main() takes 0 positional arguments but 1 was given`（`main` 尚未实现）

- [ ] **Step 3: 实现 `main()`**

在 `scripts/fetch_fund_flow_local.py` 末尾追加：

```python
def main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(
        description="个股资金流向本地下载（关 VPN 后运行）"
    )
    parser.add_argument(
        "--delay", type=float, default=0.3,
        help="每只股票请求间隔（秒），默认 0.3",
    )
    parser.add_argument(
        "--max-errors", type=int, default=200,
        help="连续失败上限，默认 200",
    )
    parser.add_argument(
        "--codes", nargs="+",
        help="指定股票代码（空格分隔），默认全量",
    )
    args = parser.parse_args(argv)

    all_codes = get_all_codes()

    if args.codes:
        pending = args.codes
    else:
        pending = get_pending_codes(all_codes)

    if not pending:
        print("所有股票已有缓存，无需重新下载。")
        return

    cached_count = len(all_codes) - len(pending)
    print_header(
        total=len(all_codes),
        cached_count=cached_count,
        pending_count=len(pending),
        delay=args.delay,
    )

    stats = download_all(pending, delay=args.delay, max_errors=args.max_errors)

    print_report(
        ok=stats["ok"],
        fail=stats["fail"],
        cached_count=cached_count,
        total=len(all_codes),
        failed_codes=stats["failed_codes"],
    )


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 运行全部测试，确认通过**

```bash
.venv/bin/pytest tests/test_fetch_fund_flow_local.py -v
```

预期：全部 PASSED（共 13 个测试）

- [ ] **Step 5: 冒烟测试脚本可执行**

```bash
.venv/bin/python scripts/fetch_fund_flow_local.py --help
```

预期：打印 usage 信息，无报错。

- [ ] **Step 6: 提交**

```bash
git add scripts/fetch_fund_flow_local.py tests/test_fetch_fund_flow_local.py
git commit -m "feat: 实现 main() CLI 入口，完成完整脚本"
```

---

### Task 5: 验收

- [ ] **Step 1: 跑完整测试套件，确认无回归**

```bash
.venv/bin/pytest tests/ -v --tb=short 2>&1 | tail -20
```

预期：所有已有测试 + 新增 13 个测试全部 PASSED

- [ ] **Step 2: 确认脚本在 VPN 开启状态下也能正确列出待下载股票（不实际下载）**

```bash
.venv/bin/python scripts/fetch_fund_flow_local.py --max-errors 0 --delay 0 2>&1 | head -15
```

预期：打印摘要后因 max_errors=0 立即终止，不挂起。

- [ ] **Step 3: 最终提交**

```bash
git add .
git commit -m "chore: 验收通过，个股资金流向本地下载脚本完成"
```

---

## 实际下载流程（脚本完成后）

```
1. 关闭 VPN（切换至国内网络）
2. 在项目根目录：
   .venv/bin/python scripts/fetch_fund_flow_local.py
3. 等待完成（预计 ~26 分钟 @ 0.3s/只，若限频可加 --delay 0.5）
4. 开启 VPN → 回到 Claude Code
5. python main.py 1    （Phase 1 特征重建）
6. python main.py 2    （Phase 2 模型重训）
7. python main.py scan （选股扫描）
```
