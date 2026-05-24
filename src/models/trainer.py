"""Phase 2: 模型训练 —— LightGBM 三分类 + Ridge 回归 + IC 加权集成"""
import json
import logging
from pathlib import Path
from typing import Tuple

import joblib
import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge

from config.settings import TRAIN_RATIO, VAL_RATIO, RANDOM_SEED, PROCESSED_DIR
from src.features.indicators import get_feature_columns

logger = logging.getLogger(__name__)

MODEL_DIR = PROCESSED_DIR.parent / "models"
LABEL_MAP = {-1: 0, 0: 1, 1: 2}      # LightGBM 要求标签从 0 开始
LABEL_MAP_INV = {0: -1, 1: 0, 2: 1}  # 预测结果还原


def time_split(
    df: pd.DataFrame,
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """按日期严格切分，无未来信息泄漏"""
    dates = np.sort(df["date"].unique())
    n = len(dates)
    train_end_idx = int(n * TRAIN_RATIO) - 1
    val_end_idx = int(n * (TRAIN_RATIO + VAL_RATIO)) - 1

    train_end = dates[train_end_idx]
    val_end = dates[val_end_idx]

    train = df[df["date"] <= train_end].reset_index(drop=True)
    val = df[(df["date"] > train_end) & (df["date"] <= val_end)].reset_index(drop=True)
    test = df[df["date"] > val_end].reset_index(drop=True)

    logger.info(
        f"切分完成 | 训练: {train['date'].min().date()} ~ {train['date'].max().date()} "
        f"({train['date'].nunique()} 天, {len(train)} 行) | "
        f"验证: {val['date'].min().date()} ~ {val['date'].max().date()} "
        f"({val['date'].nunique()} 天, {len(val)} 行) | "
        f"测试: {test['date'].min().date()} ~ {test['date'].max().date()} "
        f"({test['date'].nunique()} 天, {len(test)} 行)"
    )
    return train, val, test


def prepare_xy(
    df: pd.DataFrame,
    feature_cols: list[str],
) -> Tuple[np.ndarray, np.ndarray]:
    """LightGBM 分类目标：标签映射为 0/1/2"""
    X = df[feature_cols].values.astype(np.float32)
    y = df["label"].map(LABEL_MAP).values.astype(np.int32)
    return X, y


def prepare_xy_regression(
    df: pd.DataFrame,
    feature_cols: list[str],
) -> Tuple[np.ndarray, np.ndarray]:
    """Ridge 回归目标：截面收益率排名分位数（0~1）"""
    X = df[feature_cols].values.astype(np.float32)
    y = df.groupby("date")["future_ret"].rank(pct=True).values.astype(np.float32)
    return X, y


# 温和类别权重：轻度提升"涨"召回，保持"震荡"预测能力
# 0=跌, 1=震荡, 2=涨；cross-sectional标签各约30/40/30%
_CLASS_WEIGHTS: dict[int, float] = {0: 1.0, 1: 0.8, 2: 1.5}

_LGBM_PARAMS: dict = {
    "objective": "multiclass",
    "num_class": 3,
    "metric": "multi_logloss",
    "learning_rate": 0.03,
    "num_leaves": 31,
    "max_depth": 6,
    "min_child_samples": 200,
    "feature_fraction": 0.6,
    "bagging_fraction": 0.7,
    "bagging_freq": 5,
    "reg_alpha": 0.1,
    "reg_lambda": 0.1,
    "verbose": -1,
    "n_jobs": -1,
    "seed": RANDOM_SEED,
}


def train_lgbm(
    X_train: np.ndarray,
    y_train: np.ndarray,
    X_val: np.ndarray,
    y_val: np.ndarray,
    feature_names: list[str],
    log_period: int = 100,
) -> lgb.Booster:
    sample_weights = np.array([_CLASS_WEIGHTS[yi] for yi in y_train], dtype=np.float32)
    train_set = lgb.Dataset(X_train, label=y_train, weight=sample_weights, feature_name=feature_names)
    val_set = lgb.Dataset(X_val, label=y_val, feature_name=feature_names, reference=train_set)

    callbacks = [
        lgb.early_stopping(stopping_rounds=50, verbose=False),
        lgb.log_evaluation(period=log_period),
    ]

    model = lgb.train(
        _LGBM_PARAMS,
        train_set,
        num_boost_round=2000,
        valid_sets=[val_set],
        callbacks=callbacks,
    )
    logger.info(f"最优迭代轮数: {model.best_iteration}")
    return model


def train_lgbm_fixed(
    X_train: np.ndarray,
    y_train: np.ndarray,
    feature_names: list[str],
    num_rounds: int,
) -> lgb.Booster:
    """全量训练：固定迭代轮数，无早停，无验证集。"""
    sample_weights = np.array([_CLASS_WEIGHTS[yi] for yi in y_train], dtype=np.float32)
    train_set = lgb.Dataset(X_train, label=y_train, weight=sample_weights, feature_name=feature_names)
    model = lgb.train(
        _LGBM_PARAMS,
        train_set,
        num_boost_round=num_rounds,
        callbacks=[lgb.log_evaluation(period=200)],
    )
    return model


def _fill_nan(X: np.ndarray) -> np.ndarray:
    """用列均值填充 NaN（数据已 Z-score，0 即截面均值）"""
    col_means = np.where(np.isfinite(X).any(axis=0), np.nanmean(X, axis=0), 0.0)
    result = X.copy()
    nan_mask = ~np.isfinite(result)
    result[nan_mask] = np.take(col_means, np.where(nan_mask)[1])
    return result


def train_ridge(
    X_train: np.ndarray,
    y_train: np.ndarray,
    alpha: float = 1.0,
) -> Ridge:
    """训练 Ridge 回归（目标：截面排名分位数）"""
    model = Ridge(alpha=alpha, fit_intercept=True)
    model.fit(_fill_nan(X_train), y_train)
    return model


def _calc_weights_from_ics(ic_lgbm: float, ic_ridge: float) -> Tuple[float, float]:
    """ReLU + 归一化：负 IC 的模型权重置 0，再按比例分配"""
    w_l = max(0.0, ic_lgbm)
    w_r = max(0.0, ic_ridge)
    total = w_l + w_r
    if total < 1e-8:
        return 0.5, 0.5   # fallback: 两模型 IC 均为负，等权
    return w_l / total, w_r / total


def compute_ensemble_weights(
    lgbm_model: lgb.Booster,
    ridge_model: Ridge,
    val_df: pd.DataFrame,
    feature_cols: list[str],
) -> Tuple[float, float]:
    """在验证集上逐日计算 IC，取均值后用 ReLU+归一化得到集成权重"""
    X_val = val_df[feature_cols].values.astype(np.float32)
    proba = lgbm_model.predict(X_val)
    lgbm_signal = proba[:, 2] - proba[:, 0]
    ridge_signal = ridge_model.predict(_fill_nan(X_val)).astype(np.float64)
    future_ret = val_df["future_ret"].values
    dates = val_df["date"].values

    ic_lgbm_list: list[float] = []
    ic_ridge_list: list[float] = []

    for d in np.unique(dates):
        mask = dates == d
        fr = future_ret[mask]
        ls = lgbm_signal[mask]
        rs = ridge_signal[mask]
        valid = np.isfinite(fr) & np.isfinite(ls) & np.isfinite(rs)
        if valid.sum() < 5:
            continue
        ic_lgbm_list.append(float(np.corrcoef(ls[valid], fr[valid])[0, 1]))
        ic_ridge_list.append(float(np.corrcoef(rs[valid], fr[valid])[0, 1]))

    mean_ic_lgbm = float(np.nanmean(ic_lgbm_list)) if ic_lgbm_list else 0.0
    mean_ic_ridge = float(np.nanmean(ic_ridge_list)) if ic_ridge_list else 0.0

    logger.info(f"验证集平均 IC | LightGBM: {mean_ic_lgbm:.4f} | Ridge: {mean_ic_ridge:.4f}")

    w_lgbm, w_ridge = _calc_weights_from_ics(mean_ic_lgbm, mean_ic_ridge)
    logger.info(f"集成权重 | LightGBM: {w_lgbm:.3f} | Ridge: {w_ridge:.3f}")
    return w_lgbm, w_ridge


def save_ensemble(
    lgbm_model: lgb.Booster,
    ridge_model: Ridge,
    w_lgbm: float,
    w_ridge: float,
    feature_cols: list[str],
) -> Path:
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    joblib.dump(lgbm_model, MODEL_DIR / "lgbm_model.joblib")
    joblib.dump(ridge_model, MODEL_DIR / "ridge_model.joblib")

    meta = {"feature_cols": feature_cols, "w_lgbm": w_lgbm, "w_ridge": w_ridge}
    with open(MODEL_DIR / "ensemble_meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    # 保持 feature_cols.json 向后兼容
    with open(MODEL_DIR / "feature_cols.json", "w", encoding="utf-8") as f:
        json.dump(feature_cols, f, ensure_ascii=False, indent=2)

    logger.info(f"集成模型已保存至 {MODEL_DIR}")
    return MODEL_DIR


def load_ensemble() -> Tuple[lgb.Booster, "Ridge | None", float, float, list[str]]:
    """加载集成模型；若 Ridge 文件不存在则降级为纯 LightGBM（w_lgbm=1.0）"""
    lgbm_path = MODEL_DIR / "lgbm_model.joblib"
    ridge_path = MODEL_DIR / "ridge_model.joblib"
    meta_path = MODEL_DIR / "ensemble_meta.json"
    feat_path = MODEL_DIR / "feature_cols.json"

    if not lgbm_path.exists():
        raise FileNotFoundError(f"找不到模型文件 {lgbm_path}，请先运行 phase2 训练")

    lgbm_model = joblib.load(lgbm_path)

    if ridge_path.exists() and meta_path.exists():
        ridge_model = joblib.load(ridge_path)
        with open(meta_path, encoding="utf-8") as f:
            meta = json.load(f)
        return lgbm_model, ridge_model, meta["w_lgbm"], meta["w_ridge"], meta["feature_cols"]

    # 降级：无 Ridge 模型
    ridge_model = None
    with open(feat_path, encoding="utf-8") as f:
        feature_cols = json.load(f)
    logger.warning("未找到 Ridge 模型，降级为纯 LightGBM 推理")
    return lgbm_model, ridge_model, 1.0, 0.0, feature_cols


# 保留旧接口供 evaluator.py 使用
def save_model(model: lgb.Booster, feature_cols: list[str]) -> Path:
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, MODEL_DIR / "lgbm_model.joblib")
    with open(MODEL_DIR / "feature_cols.json", "w", encoding="utf-8") as f:
        json.dump(feature_cols, f, ensure_ascii=False, indent=2)
    logger.info(f"模型已保存至 {MODEL_DIR / 'lgbm_model.joblib'}")
    return MODEL_DIR / "lgbm_model.joblib"


def load_model() -> Tuple[lgb.Booster, list[str]]:
    lgbm_path = MODEL_DIR / "lgbm_model.joblib"
    feat_path = MODEL_DIR / "feature_cols.json"
    if not lgbm_path.exists():
        raise FileNotFoundError(f"找不到模型文件 {lgbm_path}，请先运行 phase2 训练")
    model = joblib.load(lgbm_path)
    with open(feat_path, encoding="utf-8") as f:
        feature_cols = json.load(f)
    return model, feature_cols


def run_training(
    df: pd.DataFrame,
) -> Tuple[lgb.Booster, Ridge, float, float, list[str], pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """完整训练流程，返回 (lgbm, ridge, w_lgbm, w_ridge, feature_cols, train_df, val_df, test_df)"""
    feature_cols = [c for c in get_feature_columns(df) if c != "code"]
    logger.info(f"特征数量: {len(feature_cols)}")

    train_df, val_df, test_df = time_split(df)

    X_train, y_train = prepare_xy(train_df, feature_cols)
    X_val, y_val = prepare_xy(val_df, feature_cols)

    logger.info(
        f"训练集标签分布 | 跌(0): {(y_train==0).sum()} | "
        f"震荡(1): {(y_train==1).sum()} | 涨(2): {(y_train==2).sum()}"
    )

    lgbm_model = train_lgbm(X_train, y_train, X_val, y_val, feature_cols)

    X_train_r, y_train_r = prepare_xy_regression(train_df, feature_cols)
    ridge_model = train_ridge(X_train_r, y_train_r)
    logger.info("Ridge 回归训练完成")

    w_lgbm, w_ridge = compute_ensemble_weights(lgbm_model, ridge_model, val_df, feature_cols)
    save_ensemble(lgbm_model, ridge_model, w_lgbm, w_ridge, feature_cols)

    return lgbm_model, ridge_model, w_lgbm, w_ridge, feature_cols, train_df, val_df, test_df


def run_training_final(
    df: pd.DataFrame,
    best_iteration: int,
    w_lgbm: float,
    w_ridge: float,
    feature_cols: list[str],
) -> Tuple[lgb.Booster, Ridge]:
    """
    Final 生产模型：在全部有标签数据上训练，固定迭代轮数（来自评估阶段）。

    评估阶段的 70/15/15 切分不变，保证指标诚实。
    此步骤仅替换模型文件，供 scan 使用。
    集成权重复用评估阶段结果（IC 比例稳定，无需重算）。
    """
    logger.info(
        f"=== Final 生产模型：全量 {len(df)} 行，固定 {best_iteration} 轮 ==="
    )

    X_all, y_all = prepare_xy(df, feature_cols)
    lgbm_final = train_lgbm_fixed(X_all, y_all, feature_cols, num_rounds=best_iteration)
    logger.info("Final LightGBM 训练完成")

    X_all_r, y_all_r = prepare_xy_regression(df, feature_cols)
    ridge_final = train_ridge(X_all_r, y_all_r)
    logger.info("Final Ridge 训练完成")

    save_ensemble(lgbm_final, ridge_final, w_lgbm, w_ridge, feature_cols)
    logger.info(
        f"Final 生产模型已保存"
        f"（全量数据 {len(df)} 行，LGB={w_lgbm:.3f} / Ridge={w_ridge:.3f}）"
    )
    return lgbm_final, ridge_final


def walk_forward_cv(
    df: pd.DataFrame,
    min_train_months: int = 18,
    pred_months: int = 6,
) -> dict:
    """
    扩展窗口 Walk-Forward 交叉验证。

    每个折叠：
      - 训练集：从数据起点到折叠截止日（扩展窗口）
      - 内部验证集：训练集最后 15% 日期（供 LightGBM early stopping）
      - OOS 预测：截止日后的 pred_months 个月

    返回逐折 OOS IC 和整体汇总。
    """
    feature_cols = [c for c in get_feature_columns(df) if c != "code"]
    all_dates = np.sort(df["date"].unique())
    n_dates = len(all_dates)

    min_train_days = min_train_months * 21
    pred_days = pred_months * 21

    # 构建折叠边界
    folds_meta: list[dict] = []
    pred_start_idx = min_train_days
    while pred_start_idx < n_dates:
        pred_end_idx = min(pred_start_idx + pred_days, n_dates)
        if pred_end_idx - pred_start_idx < 10:
            break
        internal_val_start_idx = int(pred_start_idx * 0.85)
        folds_meta.append({
            "train_end_idx": pred_start_idx,
            "val_start_idx": internal_val_start_idx,
            "oos_start_idx": pred_start_idx,
            "oos_end_idx": pred_end_idx,
        })
        pred_start_idx += pred_days

    logger.info(f"Walk-Forward CV: {len(folds_meta)} 折，OOS 窗口 {pred_months} 个月/折")

    fold_results: list[dict] = []

    for i, meta in enumerate(folds_meta):
        train_cut = all_dates[meta["train_end_idx"] - 1]
        val_cut = all_dates[meta["val_start_idx"]]
        oos_start = all_dates[meta["oos_start_idx"]]
        oos_end = all_dates[meta["oos_end_idx"] - 1]

        train_df = df[df["date"] < val_cut].reset_index(drop=True)
        val_df_fold = df[(df["date"] >= val_cut) & (df["date"] <= train_cut)].reset_index(drop=True)
        oos_df = df[(df["date"] > train_cut) & (df["date"] <= oos_end)].reset_index(drop=True)

        if len(train_df) < 1000 or len(oos_df) < 100:
            continue

        logger.info(
            f"  Fold {i+1}/{len(folds_meta)}: 训练截至 {pd.Timestamp(train_cut).date()} "
            f"({len(train_df)} 行) → OOS {pd.Timestamp(oos_start).date()}~{pd.Timestamp(oos_end).date()} "
            f"({len(oos_df)} 行)"
        )

        X_train, y_train = prepare_xy(train_df, feature_cols)
        X_val, y_val = prepare_xy(val_df_fold, feature_cols)

        fold_lgbm = train_lgbm(X_train, y_train, X_val, y_val, feature_cols, log_period=500)

        X_oos = oos_df[feature_cols].values.astype(np.float32)
        proba = fold_lgbm.predict(X_oos)
        signal = proba[:, 2] - proba[:, 0]
        future_ret = oos_df["future_ret"].values
        oos_date_vals = oos_df["date"].values

        ic_per_date: list[float] = []
        for d in np.unique(oos_date_vals):
            mask = oos_date_vals == d
            fr, sig = future_ret[mask], signal[mask]
            valid = np.isfinite(fr) & np.isfinite(sig)
            if valid.sum() >= 5:
                ic_per_date.append(float(np.corrcoef(sig[valid], fr[valid])[0, 1]))

        oos_ic = float(np.nanmean(ic_per_date)) if ic_per_date else float("nan")

        fold_results.append({
            "fold": i + 1,
            "train_end": str(pd.Timestamp(train_cut).date()),
            "oos_start": str(pd.Timestamp(oos_start).date()),
            "oos_end": str(pd.Timestamp(oos_end).date()),
            "train_rows": len(train_df),
            "oos_rows": len(oos_df),
            "oos_ic": round(oos_ic, 6),
            "best_iteration": fold_lgbm.best_iteration,
        })
        logger.info(f"    → OOS IC: {oos_ic:.4f}")

    overall_oos_ic = float(np.nanmean([r["oos_ic"] for r in fold_results]))
    logger.info(
        f"Walk-Forward 汇总: {len(fold_results)} 折完成，"
        f"整体 OOS IC = {overall_oos_ic:.4f}"
    )
    return {
        "folds": fold_results,
        "overall_oos_ic": round(overall_oos_ic, 6),
        "n_folds": len(fold_results),
    }


def run_training_rolling(
    df: pd.DataFrame,
    train_years: int = 2,
) -> Tuple[lgb.Booster, Ridge, float, float, list[str], pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    滚动窗口训练：使用最近 train_years 年数据作为训练集，
    其余切分逻辑与 run_training() 相同。
    生产模型文件仍写入 data/models/。
    """
    feature_cols = [c for c in get_feature_columns(df) if c != "code"]
    logger.info(f"特征数量: {len(feature_cols)}（滚动窗口 {train_years} 年）")

    all_dates = np.sort(df["date"].unique())
    n_dates = len(all_dates)
    train_days = train_years * 252

    # 测试集 = 最后 15% 日期
    test_start_idx = int(n_dates * (1 - VAL_RATIO))
    test_start = all_dates[test_start_idx]

    # 验证集 = 测试集前的 VAL_RATIO 日期
    val_start_idx = int(n_dates * (1 - 2 * VAL_RATIO))
    val_start = all_dates[val_start_idx]

    # 训练集 = val_start 前最多 train_days 个交易日
    train_end_idx = val_start_idx - 1
    train_start_idx = max(0, train_end_idx - train_days + 1)
    train_start = all_dates[train_start_idx]

    train_df = df[(df["date"] >= train_start) & (df["date"] < val_start)].reset_index(drop=True)
    val_df = df[(df["date"] >= val_start) & (df["date"] < test_start)].reset_index(drop=True)
    test_df = df[df["date"] >= test_start].reset_index(drop=True)

    logger.info(
        f"滚动切分 | 训练: {train_df['date'].min().date()} ~ {train_df['date'].max().date()} "
        f"({train_df['date'].nunique()} 天, {len(train_df)} 行) | "
        f"验证: {val_df['date'].min().date()} ~ {val_df['date'].max().date()} "
        f"({val_df['date'].nunique()} 天, {len(val_df)} 行) | "
        f"测试: {test_df['date'].min().date()} ~ {test_df['date'].max().date()} "
        f"({test_df['date'].nunique()} 天, {len(test_df)} 行)"
    )

    X_train, y_train = prepare_xy(train_df, feature_cols)
    X_val, y_val = prepare_xy(val_df, feature_cols)

    logger.info(
        f"训练集标签分布 | 跌(0): {(y_train==0).sum()} | "
        f"震荡(1): {(y_train==1).sum()} | 涨(2): {(y_train==2).sum()}"
    )

    lgbm_model = train_lgbm(X_train, y_train, X_val, y_val, feature_cols)

    X_train_r, y_train_r = prepare_xy_regression(train_df, feature_cols)
    ridge_model = train_ridge(X_train_r, y_train_r)
    logger.info("Ridge 回归训练完成（滚动窗口）")

    w_lgbm, w_ridge = compute_ensemble_weights(lgbm_model, ridge_model, val_df, feature_cols)
    save_ensemble(lgbm_model, ridge_model, w_lgbm, w_ridge, feature_cols)

    return lgbm_model, ridge_model, w_lgbm, w_ridge, feature_cols, train_df, val_df, test_df
