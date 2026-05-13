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


def train_lgbm(
    X_train: np.ndarray,
    y_train: np.ndarray,
    X_val: np.ndarray,
    y_val: np.ndarray,
    feature_names: list[str],
) -> lgb.Booster:
    params = {
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
        "is_unbalance": True,
        "verbose": -1,
        "n_jobs": -1,
        "seed": RANDOM_SEED,
    }

    train_set = lgb.Dataset(X_train, label=y_train, feature_name=feature_names)
    val_set = lgb.Dataset(X_val, label=y_val, feature_name=feature_names, reference=train_set)

    callbacks = [
        lgb.early_stopping(stopping_rounds=50, verbose=True),
        lgb.log_evaluation(period=100),
    ]

    model = lgb.train(
        params,
        train_set,
        num_boost_round=2000,
        valid_sets=[val_set],
        callbacks=callbacks,
    )
    logger.info(f"最优迭代轮数: {model.best_iteration}")
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
