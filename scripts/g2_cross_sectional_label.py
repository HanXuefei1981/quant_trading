"""G2 实验：截面排名标签（去 beta）

思路：每个交易日按 future_ret 截面排名
  - 前 30% 分位 → label=1（涨）
  - 后 30% 分位 → label=-1（跌）
  - 中间 40%   → label=0（震荡）

不重跑 Phase 1 —— 直接在 market_features.parquet 上重写 label 列。
模型保存到 data/models/g2_rank/，不覆盖原模型。
"""
import sys, json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import joblib
import numpy as np
import pandas as pd

from config.settings import PROCESSED_DIR
from src.features.indicators import get_feature_columns
from src.models.trainer import time_split, prepare_xy, train_lgbm
from src.models.evaluator import evaluate_split

UP_PCT = 0.70
DOWN_PCT = 0.30
OUT_DIR = PROCESSED_DIR.parent / "models" / "g2_rank"


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print("[G2] 加载数据 ...")
    df = pd.read_parquet(PROCESSED_DIR / "market_features.parquet")
    print(f"  数据形状: {df.shape}")
    print(f"  原 label 分布: {df['label'].value_counts(normalize=True).round(4).to_dict()}")

    print("[G2] 计算每日截面 future_ret 分位 ...")
    df["future_ret_rank"] = df.groupby("date")["future_ret"].rank(pct=True)

    print(f"[G2] 重写 label：>={UP_PCT} → 涨, <={DOWN_PCT} → 跌, 其它 → 震荡 ...")
    df["label"] = np.where(
        df["future_ret_rank"] >= UP_PCT, 1,
        np.where(df["future_ret_rank"] <= DOWN_PCT, -1, 0)
    ).astype(np.int64)
    print(f"  新 label 分布: {df['label'].value_counts(normalize=True).round(4).to_dict()}")

    # 关键检查：每日分位标签是否真的在每天都有 30/40/30 样本
    daily_dist = df.groupby("date")["label"].value_counts(normalize=True).unstack(fill_value=0)
    print(f"  每日 涨 比例 中位数: {daily_dist[1].median():.4f}")
    print(f"  每日 跌 比例 中位数: {daily_dist[-1].median():.4f}")
    print(f"  每日 震荡 比例 中位数: {daily_dist[0].median():.4f}")

    # 用 get_feature_columns（去掉 future_ret_rank 这个新列以免泄漏）
    all_feats = [c for c in get_feature_columns(df) if c not in ("code", "future_ret_rank")]
    print(f"  特征数: {len(all_feats)}")

    print("[G2] 时序切分 ...")
    train_df, val_df, test_df = time_split(df)
    X_train, y_train = prepare_xy(train_df, all_feats)
    X_val,   y_val   = prepare_xy(val_df,   all_feats)

    print(f"[G2] 训练集标签分布 | 跌(0): {(y_train==0).sum()} | "
          f"震荡(1): {(y_train==1).sum()} | 涨(2): {(y_train==2).sum()}")

    print("[G2] 训练 LightGBM ...")
    model = train_lgbm(X_train, y_train, X_val, y_val, all_feats)
    print(f"[G2] 最优迭代轮数: {model.best_iteration}")

    joblib.dump(model, OUT_DIR / "lgbm_model.joblib")
    with open(OUT_DIR / "feature_cols.json", "w") as f:
        json.dump(all_feats, f, ensure_ascii=False, indent=2)
    print(f"[G2] 模型保存到 {OUT_DIR}")

    print("[G2] 评估三个集合 ...")
    results = []
    for d, name in [(train_df, "训练集"), (val_df, "验证集"), (test_df, "测试集")]:
        results.append(evaluate_split(model, d, all_feats, name))

    with open(OUT_DIR / "eval_results.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print("\n[G2] 关键指标汇总:")
    print(f"  {'split':<8}{'acc':>8}{'f1':>8}{'dir_acc':>10}{'IC':>10}")
    for r in results:
        print(f"  {r['split']:<8}{r['accuracy']:>8.4f}{r['f1_weighted']:>8.4f}"
              f"{r['direction_accuracy']:>10.4f}{r['ic']:>10.4f}")


if __name__ == "__main__":
    main()
