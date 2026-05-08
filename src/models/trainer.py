"""Phase 2: 模型训练 —— 时序切分 + LightGBM 三分类"""
import logging
from pathlib import Path
from typing import Tuple

import joblib
import lightgbm as lgb
import numpy as np
import pandas as pd

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
    X = df[feature_cols].values.astype(np.float32)
    y = df["label"].map(LABEL_MAP).values.astype(np.int32)
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
        "learning_rate": 0.05,
        "num_leaves": 63,
        "max_depth": -1,
        "min_child_samples": 50,
        "feature_fraction": 0.8,
        "bagging_fraction": 0.8,
        "bagging_freq": 5,
        "is_unbalance": True,   # 自动处理三类不均衡
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


def save_model(model: lgb.Booster, feature_cols: list[str]) -> Path:
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    model_path = MODEL_DIR / "lgbm_model.joblib"
    meta_path = MODEL_DIR / "feature_cols.json"

    joblib.dump(model, model_path)

    import json
    with open(meta_path, "w") as f:
        json.dump(feature_cols, f, ensure_ascii=False, indent=2)

    logger.info(f"模型已保存至 {model_path}")
    return model_path


def load_model() -> Tuple[lgb.Booster, list[str]]:
    import json
    model_path = MODEL_DIR / "lgbm_model.joblib"
    meta_path = MODEL_DIR / "feature_cols.json"
    if not model_path.exists():
        raise FileNotFoundError(f"找不到模型文件 {model_path}，请先运行 phase2 训练")
    model = joblib.load(model_path)
    with open(meta_path) as f:
        feature_cols = json.load(f)
    return model, feature_cols


def run_training(df: pd.DataFrame) -> Tuple[lgb.Booster, list[str], pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """完整训练流程，返回 (model, feature_cols, train_df, val_df, test_df)"""
    feature_cols = [c for c in get_feature_columns(df) if c != "code"]
    logger.info(f"特征数量: {len(feature_cols)}")

    train_df, val_df, test_df = time_split(df)

    X_train, y_train = prepare_xy(train_df, feature_cols)
    X_val, y_val = prepare_xy(val_df, feature_cols)

    logger.info(
        f"训练集标签分布 | 跌(0): {(y_train==0).sum()} | "
        f"震荡(1): {(y_train==1).sum()} | 涨(2): {(y_train==2).sum()}"
    )

    model = train_lgbm(X_train, y_train, X_val, y_val, feature_cols)
    save_model(model, feature_cols)
    return model, feature_cols, train_df, val_df, test_df
