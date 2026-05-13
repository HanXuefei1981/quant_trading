"""组合权重构建：板块中性选股 + 信号加权 + 集中度约束 + 换手率限制"""
import numpy as np
import pandas as pd


def _allocate_slots(sector_sizes: dict[str, int], top_k: int) -> dict[str, int]:
    """按板块规模比例分配选股名额，总和精确等于 top_k，每板块至少 1"""
    total = sum(sector_sizes.values())
    if total == 0:
        return {}
    exact = {seg: top_k * count / total for seg, count in sector_sizes.items()}
    slots = {seg: max(1, int(v)) for seg, v in exact.items()}

    diff = top_k - sum(slots.values())
    if diff > 0:
        fracs = sorted(exact, key=lambda s: exact[s] - int(exact[s]), reverse=True)
        for seg in fracs[:diff]:
            slots[seg] += 1
    elif diff < 0:
        for seg in sorted(slots, key=lambda s: slots[s], reverse=True):
            if diff == 0:
                break
            if slots[seg] > 1:
                slots[seg] -= 1
                diff += 1
    return slots


def _cap_weights(weights: np.ndarray, cap: float) -> np.ndarray:
    """迭代截断 + 再分配，使单股权重不超过 cap"""
    w = weights.copy()
    for _ in range(50):
        total = w.sum()
        if total < 1e-10:
            break
        w_norm = w / total
        over = w_norm > cap
        if not over.any():
            break
        excess = (w_norm[over] - cap).sum()
        w_norm[over] = cap
        free = ~over
        if free.any() and w_norm[free].sum() > 1e-10:
            w_norm[free] += excess * w_norm[free] / w_norm[free].sum()
        w = w_norm
    return w


def _cap_sector_weights(
    weights: np.ndarray,
    segments: np.ndarray,
    cap: float,
) -> np.ndarray:
    """超限板块截断 + 余量再分配（迭代至收敛）"""
    w = weights.copy()
    unique_segs = np.unique(segments)
    for _ in range(50):
        total = w.sum()
        if total < 1e-10:
            break
        w /= total
        over = False
        for seg in unique_segs:
            mask = segments == seg
            sec_w = float(w[mask].sum())
            if sec_w > cap + 1e-9:
                excess = sec_w - cap
                w[mask] *= cap / sec_w
                free = ~mask
                free_total = float(w[free].sum())
                if free_total > 1e-10:
                    w[free] += excess * w[free] / free_total
                over = True
        if not over:
            break
    return w


def build_target_weights(
    signal_series: pd.Series,
    segment_map: dict[str, str],
    top_k: int = 50,
    max_stock_weight: float = 0.05,
    max_sector_weight: float = 0.40,
    signal_weighted: bool = True,
) -> dict[str, float]:
    """
    板块中性目标权重构建。

    流程：
    1. 按板块规模比例分配 top_k 选股名额
    2. 板块内按信号降序选股
    3. 截面 Z-score → ReLU 加权（负信号贡献零权重）
    4. 单股权重上限约束 max_stock_weight
    5. 板块权重上限约束 max_sector_weight
    6. 归一化至总权重 = 1
    """
    if len(signal_series) == 0:
        return {}

    codes = list(signal_series.index)
    signals = signal_series.values.astype(np.float64)
    segments = np.array([segment_map.get(c, "其他") for c in codes])

    unique_segs, seg_counts = np.unique(segments, return_counts=True)
    sector_sizes = dict(zip(unique_segs.tolist(), seg_counts.tolist()))
    slots = _allocate_slots(sector_sizes, top_k)

    selected_codes: list[str] = []
    selected_signals: list[float] = []

    for seg in unique_segs:
        mask = segments == seg
        seg_codes = [codes[i] for i, m in enumerate(mask) if m]
        seg_sigs = signals[mask]
        n_select = min(slots.get(seg, 0), len(seg_codes))
        if n_select <= 0:
            continue
        top_idx = np.argsort(seg_sigs)[::-1][:n_select]
        for idx in top_idx:
            selected_codes.append(seg_codes[idx])
            selected_signals.append(float(seg_sigs[idx]))

    if not selected_codes:
        return {}

    sel_sigs = np.array(selected_signals)

    if signal_weighted:
        std = sel_sigs.std()
        if std < 1e-8:
            raw_w = np.ones(len(sel_sigs))
        else:
            z = (sel_sigs - sel_sigs.mean()) / std
            raw_w = np.maximum(z, 0.0)
        if raw_w.sum() < 1e-8:
            raw_w = np.ones(len(sel_sigs))
    else:
        raw_w = np.ones(len(sel_sigs))

    total = raw_w.sum()
    if total < 1e-10:
        return {}
    raw_w = raw_w / total

    sel_segs = np.array([segment_map.get(c, "其他") for c in selected_codes])

    # 迭代收敛：单股上限 → 板块上限，两个约束相互影响，需多轮直到同时满足
    for _ in range(30):
        raw_w = _cap_weights(raw_w, max_stock_weight)   # 内部自归一化
        raw_w = _cap_sector_weights(raw_w, sel_segs, max_sector_weight)  # 内部自归一化
        t = raw_w.sum()
        if t < 1e-10:
            break
        raw_w /= t
        if (raw_w.max() <= max_stock_weight + 1e-8 and
                max(raw_w[sel_segs == s].sum() for s in np.unique(sel_segs)) <= max_sector_weight + 1e-8):
            break

    return {c: float(w) for c, w in zip(selected_codes, raw_w) if w > 1e-8}


def apply_turnover_limit(
    target_weights: dict[str, float],
    current_weights: dict[str, float],
    max_one_way_turnover: float = 0.5,
) -> dict[str, float]:
    """
    限制单向换手率至 max_one_way_turnover。

    单向换手率 = 0.5 × Σ|w_target − w_current|

    超限时在 target 和 current 之间线性插值，恰好满足换手率上限后归一化。
    """
    all_codes = sorted(set(target_weights) | set(current_weights))
    if not all_codes:
        return {}

    t = np.array([target_weights.get(c, 0.0) for c in all_codes])
    cur = np.array([current_weights.get(c, 0.0) for c in all_codes])

    one_way = 0.5 * np.abs(t - cur).sum()
    if one_way <= max_one_way_turnover + 1e-8:
        return {c: float(w) for c, w in target_weights.items()}

    alpha = max_one_way_turnover / one_way
    blended = alpha * t + (1 - alpha) * cur
    total = blended.sum()
    if total < 1e-8:
        return {c: float(w) for c, w in target_weights.items()}
    blended /= total

    return {c: float(w) for c, w in zip(all_codes, blended) if w > 1e-8}
