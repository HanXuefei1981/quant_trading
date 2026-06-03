"""测试 scan 持续性确认 + 持仓分类逻辑"""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.features.scan_history import (
    consecutive_streaks,
    classify_actions,
    load_scan_history,
    update_streak,
)


# ── 增量连榜更新（回测逐日维护用）─────────────────────────────────────────

def test_update_streak_increments_and_resets():
    s1 = update_streak({}, {"A", "B"})        # 首日
    s2 = update_streak(s1, {"A", "C"})        # B 跌出、C 进入
    s3 = update_streak(s2, {"A", "B"})        # B 重新进入（从 1 重新计）
    assert s1 == {"A": 1, "B": 1}
    assert s2 == {"A": 2, "C": 1}             # A 连续=2，B 已归零移除
    assert s3 == {"A": 3, "B": 1}


# ── 连榜（截至当前连续在榜次数）─────────────────────────────────────────────

def test_consecutive_streaks_counts_from_latest():
    history = [
        {"A", "B"},        # 最旧
        {"A", "C"},
        {"A", "B", "C"},   # 当前（最新）
    ]
    s = consecutive_streaks(history)
    assert s["A"] == 3   # 三次都在
    assert s["B"] == 1   # 当前在、上一次不在 → 连续=1（一日游特征）
    assert s["C"] == 2   # 最近两次在 → 连续=2


def test_consecutive_streaks_single_history():
    assert consecutive_streaks([{"A", "B"}]) == {"A": 1, "B": 1}


def test_consecutive_streaks_empty():
    assert consecutive_streaks([]) == {}


# ── 持仓分类（继续持有 / 卖出 / 新建仓）──────────────────────────────────────

def test_classify_actions_buckets():
    buffer = {"A", "B", "C", "D"}     # 当前 Top-buffer
    topk = {"A", "B", "C"}            # 当前 Top-k 建仓档
    streaks = {"A": 3, "B": 1, "C": 2, "D": 1}
    holdings = {"A", "E"}             # 现持仓：A 仍在 buffer，E 已跌出
    res = classify_actions(buffer, topk, streaks, holdings, confirm_k=2)
    assert res["hold"] == {"A"}        # 持仓且仍在 buffer → 继续持有
    assert res["sell"] == {"E"}        # 持仓但跌出 buffer → 卖出
    assert res["buy"] == {"C"}         # Top-k 且连榜≥2 且未持有；B 连榜1 被过滤


def test_classify_actions_confirm_zero_buys_all_topk():
    res = classify_actions({"A", "B"}, {"A", "B"}, {"A": 1, "B": 1}, set(), confirm_k=0)
    assert res["buy"] == {"A", "B"}    # confirm_k=0 不做持续性过滤


# ── 历史加载（读 scan_*.csv，只取早于当前日的）─────────────────────────────

def test_load_scan_history_filters_and_orders(tmp_path):
    def _csv(d, codes):
        pd.DataFrame({"代码": codes}).to_csv(tmp_path / f"scan_{d}.csv", index=False)
    _csv("2026-05-29", ["000001", "600000"])
    _csv("2026-06-01", ["000001", "300750"])
    _csv("2026-06-02", ["999999"])  # = 当前日，应被排除
    hist = load_scan_history(str(tmp_path), "2026-06-02", lookback=10)
    assert hist == [{"000001", "600000"}, {"000001", "300750"}]  # 升序、不含当前
