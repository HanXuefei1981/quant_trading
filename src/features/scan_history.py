"""scan 持续性确认与持仓分类：用历史 scan 结果过滤"一日游"，减少反复调仓。

核心思想：
  - 一日游股票只在单次 scan 冲上榜，连榜=1；持续高信号股连榜≥K。
  - 只把"连榜≥K 且在 Top-k"的股票标为可建仓，其余仅观察。
  - 配合 replace-only：持仓只要还在 Top-buffer 就持有，跌出才卖。
"""
from __future__ import annotations

import glob
import os
import re

import pandas as pd

_SCAN_RE = re.compile(r"scan_(\d{4}-\d{2}-\d{2})\.csv$")


def consecutive_streaks(history: list[set[str]]) -> dict[str, int]:
    """按时间升序的多次 scan 候选集（最后一个为当前），返回当前集合中每只
    股票"截至当前连续在榜次数"。中断即停止计数（一日游→1）。"""
    if not history:
        return {}
    current = history[-1]
    streaks: dict[str, int] = {}
    for code in current:
        n = 0
        for snapshot in reversed(history):  # 从最新往旧
            if code in snapshot:
                n += 1
            else:
                break
        streaks[code] = n
    return streaks


def update_streak(prev: dict[str, int], pool: set[str]) -> dict[str, int]:
    """逐日增量维护连榜：今日仍在候选池的连续次数 +1，跌出池的归零（移除）。

    回测引擎逐交易日调用，等价于 ``consecutive_streaks`` 的增量形式。
    """
    return {code: prev.get(code, 0) + 1 for code in pool}


def classify_actions(
    buffer_codes: set[str],
    topk_codes: set[str],
    streaks: dict[str, int],
    holdings: set[str],
    confirm_k: int,
) -> dict[str, set[str]]:
    """replace-only 持仓分类：
      - hold（继续持有）：现持仓且仍在 Top-buffer 内
      - sell（卖出）   ：现持仓但已跌出 Top-buffer
      - buy（新建仓）  ：在 Top-k、连榜≥confirm_k、且未持有
    """
    held = set(holdings)
    hold = held & buffer_codes
    sell = held - buffer_codes
    confirmed = {c for c in topk_codes if streaks.get(c, 0) >= confirm_k}
    buy = confirmed - held
    return {"hold": hold, "sell": sell, "buy": buy}


def load_scan_history(
    backtest_dir: str,
    current_date: str,
    lookback: int = 10,
) -> list[set[str]]:
    """读取 backtest_dir 下早于 current_date 的 scan_*.csv，按日期升序返回
    代码集列表（不含当前日，最多取最近 lookback 个）。"""
    current_date = str(current_date)
    items: list[tuple[str, set[str]]] = []
    for path in glob.glob(os.path.join(backtest_dir, "scan_*.csv")):
        m = _SCAN_RE.search(os.path.basename(path))
        if not m:
            continue
        d = m.group(1)
        if d >= current_date:  # 仅取早于当前日的历史（字符串比较即时序）
            continue
        try:
            df = pd.read_csv(path, dtype={"代码": str})
        except Exception:
            continue
        col = "代码" if "代码" in df.columns else ("code" if "code" in df.columns else None)
        if col is None:
            continue
        codes = set(df[col].astype(str).str.zfill(6))
        items.append((d, codes))
    items.sort(key=lambda x: x[0])
    return [codes for _, codes in items[-lookback:]]
