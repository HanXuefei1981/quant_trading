"""测试组合权重构建（portfolio.py）"""
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.backtest.portfolio import (
    _allocate_slots,
    _cap_weights,
    build_target_weights,
    apply_turnover_limit,
)


# ── 辅助函数 ──────────────────────────────────────────────────────────────────

def make_signal_series(n_per_seg: int = 20, seed: int = 0) -> pd.Series:
    """生成四个板块各 n_per_seg 只股票的模拟信号"""
    rng = np.random.default_rng(seed)
    codes = (
        [f"6{i:05d}" for i in range(n_per_seg)] +        # 沪市主板
        [f"00{i:04d}" for i in range(n_per_seg)] +       # 深市主板
        [f"3001{i:02d}" for i in range(n_per_seg)] +     # 创业板
        [f"6881{i:02d}" for i in range(n_per_seg)]       # 科创板
    )
    return pd.Series(rng.normal(0, 1, len(codes)), index=codes)


def make_segment_map(n_per_seg: int = 20) -> dict[str, str]:
    m: dict[str, str] = {}
    for i in range(n_per_seg):
        m[f"6{i:05d}"] = "沪市主板"
        m[f"00{i:04d}"] = "深市主板"
        m[f"3001{i:02d}"] = "创业板"
        m[f"6881{i:02d}"] = "科创板"
    return m


SEG_MAP = make_segment_map()


# ── _allocate_slots ───────────────────────────────────────────────────────────

def test_allocate_slots_sum_equals_top_k():
    sizes = {"A": 40, "B": 30, "C": 20, "D": 10}
    slots = _allocate_slots(sizes, top_k=50)
    assert sum(slots.values()) == 50


def test_allocate_slots_each_segment_at_least_one():
    sizes = {"A": 100, "B": 1}   # 极端：B 只有 1 只
    slots = _allocate_slots(sizes, top_k=10)
    assert slots["B"] >= 1


def test_allocate_slots_empty_returns_empty():
    assert _allocate_slots({}, top_k=10) == {}


# ── _cap_weights ──────────────────────────────────────────────────────────────

def test_cap_weights_no_exceed_cap():
    w = np.array([0.3, 0.4, 0.2, 0.1])
    cap = 0.25
    result = _cap_weights(w, cap)
    total = result.sum()
    w_norm = result / total if total > 1e-10 else result
    assert np.all(w_norm <= cap + 1e-6)


def test_cap_weights_sums_preserved():
    w = np.array([0.5, 0.3, 0.2])
    result = _cap_weights(w, cap=0.4)
    assert abs(result.sum() - 1.0) < 1e-6


# ── build_target_weights ──────────────────────────────────────────────────────

def test_weights_sum_to_one():
    signal = make_signal_series()
    weights = build_target_weights(signal, SEG_MAP, top_k=40)
    assert abs(sum(weights.values()) - 1.0) < 1e-6


def test_weights_non_negative():
    signal = make_signal_series()
    weights = build_target_weights(signal, SEG_MAP, top_k=40)
    assert all(w >= 0 for w in weights.values())


def test_stock_weight_cap():
    signal = make_signal_series()
    cap = 0.08
    weights = build_target_weights(signal, SEG_MAP, top_k=40, max_stock_weight=cap)
    assert all(w <= cap + 1e-6 for w in weights.values()), \
        f"最大权重 {max(weights.values()):.4f} 超过上限 {cap}"


def test_sector_weight_cap():
    signal = make_signal_series()
    cap = 0.35
    weights = build_target_weights(signal, SEG_MAP, top_k=40, max_sector_weight=cap)
    sector_w: dict[str, float] = defaultdict(float)
    for code, w in weights.items():
        sector_w[SEG_MAP.get(code, "其他")] += w
    for seg, sw in sector_w.items():
        assert sw <= cap + 1e-5, f"板块 {seg} 权重 {sw:.4f} 超过上限 {cap}"


def test_top_k_count():
    signal = make_signal_series()
    top_k = 20
    weights = build_target_weights(signal, SEG_MAP, top_k=top_k)
    assert len(weights) <= top_k


def test_multi_sector_represented():
    """板块中性：各板块都应有持仓"""
    signal = make_signal_series(n_per_seg=20)
    weights = build_target_weights(signal, SEG_MAP, top_k=20)
    held_segs = {SEG_MAP.get(code, "其他") for code in weights}
    assert len(held_segs) >= 3, f"只有 {len(held_segs)} 个板块有持仓，应 >= 3"


def test_empty_signal_returns_empty():
    weights = build_target_weights(pd.Series([], dtype=float), SEG_MAP, top_k=10)
    assert weights == {}


def test_signal_weighted_vs_equal_weight():
    """信号加权时，高信号股票权重应 >= 低信号股票"""
    signal = make_signal_series()
    wts_sig = build_target_weights(signal, SEG_MAP, top_k=40, signal_weighted=True)
    wts_eq = build_target_weights(signal, SEG_MAP, top_k=40, signal_weighted=False)
    # 等权时各股权重差异应更小
    vals_sig = list(wts_sig.values())
    vals_eq = list(wts_eq.values())
    if len(vals_sig) > 1 and len(vals_eq) > 1:
        assert np.std(vals_eq) <= np.std(vals_sig) + 1e-4


def test_single_stock_universe():
    """只有一只股票时应返回权重 = 1"""
    signal = pd.Series([0.5], index=["600001"])
    weights = build_target_weights(signal, {"600001": "沪市主板"}, top_k=5)
    assert abs(sum(weights.values()) - 1.0) < 1e-6


# ── apply_turnover_limit ──────────────────────────────────────────────────────

def test_no_change_when_below_threshold():
    target = {"A": 0.5, "B": 0.3, "C": 0.2}
    current = {"A": 0.48, "B": 0.32, "C": 0.20}
    # one_way = 0.5 * (0.02 + 0.02 + 0) = 0.02 < 0.1
    result = apply_turnover_limit(target, current, max_one_way_turnover=0.1)
    assert abs(result.get("A", 0) - 0.5) < 1e-6
    assert abs(result.get("B", 0) - 0.3) < 1e-6


def test_turnover_capped_to_limit():
    target = {"A": 1.0, "B": 0.0}
    current = {"A": 0.0, "B": 1.0}
    # one_way = 0.5 * (1 + 1) = 1.0
    result = apply_turnover_limit(target, current, max_one_way_turnover=0.2)
    all_codes = sorted(set(target) | set(current))
    cur_arr = np.array([current.get(c, 0.0) for c in all_codes])
    res_arr = np.array([result.get(c, 0.0) for c in all_codes])
    actual_turnover = 0.5 * np.abs(res_arr - cur_arr).sum()
    assert actual_turnover <= 0.2 + 1e-6


def test_turnover_result_sums_to_one():
    target = {"A": 0.6, "B": 0.4}
    current = {"C": 0.5, "D": 0.5}
    result = apply_turnover_limit(target, current, max_one_way_turnover=0.3)
    assert abs(sum(result.values()) - 1.0) < 1e-6


def test_empty_inputs_return_empty():
    result = apply_turnover_limit({}, {}, max_one_way_turnover=0.5)
    assert result == {}


def test_no_current_positions_full_buy():
    """无持仓时且换手率足够大，应等于 target"""
    target = {"A": 0.5, "B": 0.5}
    result = apply_turnover_limit(target, {}, max_one_way_turnover=1.0)
    assert abs(result.get("A", 0) - 0.5) < 1e-6
    assert abs(result.get("B", 0) - 0.5) < 1e-6
