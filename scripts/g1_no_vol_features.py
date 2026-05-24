"""G1 实验：屏蔽波动率特征再训，看 IC 是否更稳

屏蔽列表：atr_ratio, atr, volatility5, volatility20, high_low_ratio
不改业务代码，模型保存到 data/models/g1_no_vol/，不覆盖原模型。
"""
import sys, json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import joblib
import numpy as np
import pandas as pd

from config.settings import DATA_DIR
from src.dal.feature_repo import FeatureRepo
from src.features.indicators import get_feature_columns
from src.models.trainer import time_split, prepare_xy, train_lgbm
from src.models.evaluator import evaluate_split

VOL_FEATURES = {"atr_ratio", "atr", "volatility5", "volatility20", "high_low_ratio"}
OUT_DIR = DATA_DIR / "models" / "g1_no_vol"


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"[G1] 加载数据 ...")
    repo = FeatureRepo()
    date_range = repo.get_feature_date_range()
    if date_range is None:
        raise FileNotFoundError("features 表为空，请先运行: python main.py 1")
    df = repo.load_features(date_range[0], date_range[1])
    print(f"  数据形状: {df.shape}")

    all_feats = [c for c in get_feature_columns(df) if c != "code"]
    feature_cols = [c for c in all_feats if c not in VOL_FEATURES]
    dropped = [c for c in all_feats if c in VOL_FEATURES]
    print(f"  原特征数: {len(all_feats)}, 屏蔽: {dropped}, 实际使用: {len(feature_cols)}")

    print("[G1] 时序切分 ...")
    train_df, val_df, test_df = time_split(df)

    X_train, y_train = prepare_xy(train_df, feature_cols)
    X_val,   y_val   = prepare_xy(val_df,   feature_cols)

    print(f"[G1] 训练集标签分布 | 跌(0): {(y_train==0).sum()} | "
          f"震荡(1): {(y_train==1).sum()} | 涨(2): {(y_train==2).sum()}")

    print("[G1] 训练 LightGBM ...")
    model = train_lgbm(X_train, y_train, X_val, y_val, feature_cols)
    print(f"[G1] 最优迭代轮数: {model.best_iteration}")

    # 保存到独立目录
    joblib.dump(model, OUT_DIR / "lgbm_model.joblib")
    with open(OUT_DIR / "feature_cols.json", "w") as f:
        json.dump(feature_cols, f, ensure_ascii=False, indent=2)
    print(f"[G1] 模型已保存到 {OUT_DIR}")

    print("[G1] 评估三个集合 ...")
    results = []
    for d, name in [(train_df, "训练集"), (val_df, "验证集"), (test_df, "测试集")]:
        results.append(evaluate_split(model, d, feature_cols, name))

    with open(OUT_DIR / "eval_results.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    # 简要对比表
    print("\n[G1] 关键指标汇总:")
    print(f"  {'split':<8}{'acc':>8}{'f1':>8}{'dir_acc':>10}{'IC':>10}")
    for r in results:
        print(f"  {r['split']:<8}{r['accuracy']:>8.4f}{r['f1_weighted']:>8.4f}"
              f"{r['direction_accuracy']:>10.4f}{r['ic']:>10.4f}")


if __name__ == "__main__":
    main()
