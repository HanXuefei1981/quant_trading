"""因子预处理：截面 MAD 去极值 → 板块中性化 → 截面 Z-score 标准化"""
import numpy as np
import pandas as pd

from config.settings import MAD_SIGMA


def get_segment(code: str) -> str:
    """按股票代码识别市场板块（粗分，用于中性化）"""
    if code.startswith("688"):
        return "科创板"
    if code.startswith("6"):
        return "沪市主板"
    if code.startswith(("300", "301")):
        return "创业板"
    if code.startswith(("000", "001", "002", "003")):
        return "深市主板"
    if code.startswith("8"):
        return "北交所"
    return "其他"


# ── 截面级别的纯 numpy 操作（每次处理单日所有股票 × 所有因子的矩阵）────────

def _mad_winsorize_block(X: np.ndarray, n_sigma: float) -> np.ndarray:
    """MAD 去极值：将超出 median ± n_sigma×MAD 的值截断"""
    med = np.nanmedian(X, axis=0)
    mad = np.nanmedian(np.abs(X - med), axis=0)
    return np.clip(X, med - n_sigma * mad, med + n_sigma * mad)


def _neutralize_block(X: np.ndarray, segments: np.ndarray) -> np.ndarray:
    """板块中性化：对每个板块做组内去均值，消除板块系统性暴露"""
    result = X.copy()
    for seg in np.unique(segments):
        mask = segments == seg
        group = X[mask]
        result[mask] = group - np.nanmean(group, axis=0)
    return result


def _zscore_block(X: np.ndarray) -> np.ndarray:
    """Z-score 标准化：使截面均值→0，标准差→1"""
    mean = np.nanmean(X, axis=0)
    std = np.nanstd(X, axis=0)
    return (X - mean) / (std + 1e-8)


# ── 对外接口 ─────────────────────────────────────────────────────────────────

def preprocess_features(
    df: pd.DataFrame,
    feature_cols: list[str],
    use_neutralization: bool = True,
    mad_sigma: float = MAD_SIGMA,
) -> pd.DataFrame:
    """
    完整因子预处理流水线：
      1. 截面 MAD 去极值（n_sigma 倍）
      2. 板块中性化（可选，组内去均值消除板块 Beta）
      3. 截面 Z-score 标准化

    仅对 feature_cols 中的列操作，'date'/'code'/'label'/'future_ret' 等保持原值。
    结果 df 新增 'segment' 列（板块分类，不是特征）。
    """
    result = df.copy()
    result["segment"] = df["code"].apply(get_segment)

    dates = result["date"].values
    segs = result["segment"].values
    feat = result[feature_cols].values.astype(np.float64)

    for d in np.unique(dates):
        mask = dates == d
        X = feat[mask]
        X = _mad_winsorize_block(X, mad_sigma)
        if use_neutralization:
            X = _neutralize_block(X, segs[mask])
        X = _zscore_block(X)
        feat[mask] = X

    result[feature_cols] = feat
    return result
