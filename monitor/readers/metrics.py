"""Model metrics reader for monitoring dashboard"""
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
import json


@dataclass
class ModelMetrics:
    """Container for model performance metrics"""

    train_ic: Optional[float]
    val_ic: Optional[float]
    test_ic: Optional[float]
    val_accuracy: Optional[float]
    val_f1: Optional[float]
    w_lgbm: Optional[float]
    w_ridge: Optional[float]
    n_features: Optional[int]
    last_train: Optional[str]


def get_metrics(data_dir: Path) -> ModelMetrics:
    """Read model metrics from data directory.

    Reads from:
    - data_dir / "models" / "eval_results.json" for train/val/test metrics
    - data_dir / "models" / "ensemble_meta.json" for ensemble weights and feature count
    - data_dir / "watermark.json" for last training date

    Args:
        data_dir: Path to the data directory

    Returns:
        ModelMetrics with all available metrics, None for missing data
    """
    # Initialize all values as None
    train_ic: Optional[float] = None
    val_ic: Optional[float] = None
    test_ic: Optional[float] = None
    val_accuracy: Optional[float] = None
    val_f1: Optional[float] = None
    w_lgbm: Optional[float] = None
    w_ridge: Optional[float] = None
    n_features: Optional[int] = None
    last_train: Optional[str] = None

    # Read eval_results.json
    eval_results_path = data_dir / "models" / "eval_results.json"
    if eval_results_path.exists():
        with open(eval_results_path) as f:
            eval_data = json.load(f)

        # Find metrics by split name
        for entry in eval_data:
            split = entry.get("split")
            if split == "训练集":
                train_ic = entry.get("ic")
            elif split == "验证集":
                val_ic = entry.get("ic")
                val_accuracy = entry.get("accuracy")
                val_f1 = entry.get("f1_weighted")
            elif split == "测试集":
                test_ic = entry.get("ic")

    # Read ensemble_meta.json
    ensemble_meta_path = data_dir / "models" / "ensemble_meta.json"
    if ensemble_meta_path.exists():
        with open(ensemble_meta_path) as f:
            meta_data = json.load(f)

        w_lgbm = meta_data.get("w_lgbm")
        w_ridge = meta_data.get("w_ridge")
        feature_cols = meta_data.get("feature_cols")
        if feature_cols is not None:
            n_features = len(feature_cols)

    # Read watermark.json
    watermark_path = data_dir / "watermark.json"
    if watermark_path.exists():
        with open(watermark_path) as f:
            watermark_data = json.load(f)

        last_train = watermark_data.get("features")

    return ModelMetrics(
        train_ic=train_ic,
        val_ic=val_ic,
        test_ic=test_ic,
        val_accuracy=val_accuracy,
        val_f1=val_f1,
        w_lgbm=w_lgbm,
        w_ridge=w_ridge,
        n_features=n_features,
        last_train=last_train,
    )
