"""Phase 2: 模型评估 —— 分类指标 + 方向准确率 + IC"""
import logging
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.metrics import classification_report, confusion_matrix

from src.models.trainer import LABEL_MAP, LABEL_MAP_INV, prepare_xy

logger = logging.getLogger(__name__)


def predict(
    model: lgb.Booster,
    X: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """返回 (预测类别, 各类概率矩阵 shape=[n, 3])"""
    proba = model.predict(X)          # shape (n, 3): [p_down, p_neutral, p_up]
    pred_cls = np.argmax(proba, axis=1)
    return pred_cls, proba


def direction_accuracy(
    y_true: np.ndarray,
    y_pred: np.ndarray,
) -> float:
    """仅在预测为涨(2)或跌(0)的样本上计算方向准确率，过滤掉预测震荡(1)的噪音"""
    mask = y_pred != 1
    if mask.sum() == 0:
        return float("nan")
    return float((y_true[mask] == y_pred[mask]).mean())


def ic_score(
    proba: np.ndarray,
    future_ret: np.ndarray,
) -> float:
    """
    信息系数 IC：预测分数（涨概率-跌概率）与实际收益率的 Pearson 相关。
    IC > 0.05 视为有效因子。
    """
    score = proba[:, 2] - proba[:, 0]   # 多空得分
    valid = np.isfinite(future_ret)
    if valid.sum() < 2:
        return float("nan")
    return float(np.corrcoef(score[valid], future_ret[valid])[0, 1])


def evaluate_split(
    model: lgb.Booster,
    df: pd.DataFrame,
    feature_cols: list[str],
    split_name: str,
) -> dict:
    X, y_true = prepare_xy(df, feature_cols)
    y_pred, proba = predict(model, X)

    report = classification_report(
        y_true, y_pred,
        target_names=["跌(-1)", "震荡(0)", "涨(1)"],
        output_dict=True,
        zero_division=0,
    )
    cm = confusion_matrix(y_true, y_pred)
    dir_acc = direction_accuracy(y_true, y_pred)
    ic = ic_score(proba, df["future_ret"].values)

    acc = float(report["accuracy"])
    f1_weighted = float(report["weighted avg"]["f1-score"])

    logger.info(
        f"\n{'='*60}\n"
        f"[{split_name}] 评估结果\n"
        f"  样本数:       {len(y_true)}\n"
        f"  总体准确率:   {acc:.4f}\n"
        f"  加权 F1:      {f1_weighted:.4f}\n"
        f"  方向准确率:   {dir_acc:.4f}  (仅统计预测涨/跌的样本)\n"
        f"  IC:           {ic:.4f}\n"
        f"\n  各类指标:\n"
        f"    跌 precision={report['跌(-1)']['precision']:.3f} recall={report['跌(-1)']['recall']:.3f} f1={report['跌(-1)']['f1-score']:.3f}\n"
        f"    震荡 precision={report['震荡(0)']['precision']:.3f} recall={report['震荡(0)']['recall']:.3f} f1={report['震荡(0)']['f1-score']:.3f}\n"
        f"    涨 precision={report['涨(1)']['precision']:.3f} recall={report['涨(1)']['recall']:.3f} f1={report['涨(1)']['f1-score']:.3f}\n"
        f"\n  混淆矩阵 (行=真实, 列=预测):\n"
        f"    跌/震/涨    跌      震荡     涨\n"
        f"    跌        {cm[0,0]:6d}  {cm[0,1]:6d}  {cm[0,2]:6d}\n"
        f"    震荡      {cm[1,0]:6d}  {cm[1,1]:6d}  {cm[1,2]:6d}\n"
        f"    涨        {cm[2,0]:6d}  {cm[2,1]:6d}  {cm[2,2]:6d}\n"
        f"{'='*60}"
    )

    return {
        "split": split_name,
        "n_samples": len(y_true),
        "accuracy": acc,
        "f1_weighted": f1_weighted,
        "direction_accuracy": dir_acc,
        "ic": ic,
    }


def save_feature_importance(model: lgb.Booster, feature_cols: list[str], out_dir: Path) -> None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        importance = model.feature_importance(importance_type="gain")
        pairs = sorted(zip(feature_cols, importance), key=lambda x: x[1], reverse=True)[:25]
        names, values = zip(*pairs)

        fig, ax = plt.subplots(figsize=(10, 8))
        ax.barh(range(len(names)), values, align="center", color="steelblue")
        ax.set_yticks(range(len(names)))
        ax.set_yticklabels(names, fontsize=9)
        ax.invert_yaxis()
        ax.set_xlabel("Gain Importance")
        ax.set_title("Top 25 Feature Importance (Gain)")
        ax.grid(axis="x", alpha=0.3)
        plt.tight_layout()
        out_dir.mkdir(parents=True, exist_ok=True)
        fig.savefig(out_dir / "feature_importance.png", dpi=120)
        plt.close(fig)
        logger.info(f"特征重要性图已保存至 {out_dir / 'feature_importance.png'}")
    except Exception as e:
        logger.warning(f"特征重要性图保存失败: {e}")


def run_evaluation(
    model: lgb.Booster,
    feature_cols: list[str],
    train_df: pd.DataFrame,
    val_df: pd.DataFrame,
    test_df: pd.DataFrame,
) -> list[dict]:
    results = []
    for df, name in [(train_df, "训练集"), (val_df, "验证集"), (test_df, "测试集")]:
        results.append(evaluate_split(model, df, feature_cols, name))

    from config.settings import PROCESSED_DIR
    save_feature_importance(model, feature_cols, PROCESSED_DIR.parent / "models")

    import json
    out_path = PROCESSED_DIR.parent / "models" / "eval_results.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    logger.info(f"评估结果已保存至 {out_path}")
    return results
