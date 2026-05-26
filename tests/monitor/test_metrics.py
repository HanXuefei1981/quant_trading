"""Test monitor.readers.metrics module"""
import sys
from pathlib import Path
import json

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from monitor.readers.metrics import ModelMetrics, get_metrics


class TestGetMetrics:
    """Test get_metrics function with various data conditions"""

    def test_returns_all_none_when_eval_results_missing(self, tmp_path):
        """When eval_results.json is missing, all fields should be None"""
        metrics = get_metrics(tmp_path)
        assert metrics.train_ic is None
        assert metrics.val_ic is None
        assert metrics.test_ic is None
        assert metrics.val_accuracy is None
        assert metrics.val_f1 is None
        assert metrics.w_lgbm is None
        assert metrics.w_ridge is None
        assert metrics.n_features is None
        assert metrics.last_train is None

    def test_reads_ic_from_eval_results(self, tmp_path):
        """Should read train, val, test IC from eval_results.json"""
        # Create models directory
        models_dir = tmp_path / "models"
        models_dir.mkdir()

        # Create eval_results.json with proper structure
        eval_data = [
            {
                "split": "训练集",
                "accuracy": 0.39,
                "f1_weighted": 0.36,
                "ic": -0.0129,
            },
            {
                "split": "验证集",
                "accuracy": 0.385,
                "f1_weighted": 0.35,
                "ic": -0.0209,
            },
            {
                "split": "测试集",
                "accuracy": 0.40,
                "f1_weighted": 0.36,
                "ic": -0.023,
            },
        ]
        eval_file = models_dir / "eval_results.json"
        eval_file.write_text(json.dumps(eval_data))

        metrics = get_metrics(tmp_path)
        assert metrics.train_ic == pytest.approx(-0.0129)
        assert metrics.val_ic == pytest.approx(-0.0209)
        assert metrics.test_ic == pytest.approx(-0.023)

    def test_reads_val_accuracy_and_f1_from_eval_results(self, tmp_path):
        """Should read val_accuracy and val_f1 from 验证集 entry"""
        models_dir = tmp_path / "models"
        models_dir.mkdir()

        eval_data = [
            {
                "split": "训练集",
                "accuracy": 0.39,
                "f1_weighted": 0.36,
                "ic": -0.0129,
            },
            {
                "split": "验证集",
                "accuracy": 0.385,
                "f1_weighted": 0.35,
                "ic": -0.0209,
            },
            {
                "split": "测试集",
                "accuracy": 0.40,
                "f1_weighted": 0.36,
                "ic": -0.023,
            },
        ]
        eval_file = models_dir / "eval_results.json"
        eval_file.write_text(json.dumps(eval_data))

        metrics = get_metrics(tmp_path)
        assert metrics.val_accuracy == pytest.approx(0.385)
        assert metrics.val_f1 == pytest.approx(0.35)

    def test_reads_ensemble_weights_from_meta(self, tmp_path):
        """Should read w_lgbm and w_ridge from ensemble_meta.json"""
        models_dir = tmp_path / "models"
        models_dir.mkdir()

        meta_data = {
            "w_lgbm": 0.54,
            "w_ridge": 0.46,
            "feature_cols": ["feat1", "feat2", "feat3"],
        }
        meta_file = models_dir / "ensemble_meta.json"
        meta_file.write_text(json.dumps(meta_data))

        metrics = get_metrics(tmp_path)
        assert metrics.w_lgbm == pytest.approx(0.54)
        assert metrics.w_ridge == pytest.approx(0.46)

    def test_reads_n_features_from_ensemble_meta(self, tmp_path):
        """Should count feature_cols from ensemble_meta.json"""
        models_dir = tmp_path / "models"
        models_dir.mkdir()

        meta_data = {
            "w_lgbm": 0.54,
            "w_ridge": 0.46,
            "feature_cols": ["feat1", "feat2", "feat3", "feat4", "feat5"],
        }
        meta_file = models_dir / "ensemble_meta.json"
        meta_file.write_text(json.dumps(meta_data))

        metrics = get_metrics(tmp_path)
        assert metrics.n_features == 5

    def test_w_lgbm_w_ridge_n_features_none_when_ensemble_meta_missing(
        self, tmp_path
    ):
        """When ensemble_meta.json is missing, weights and n_features should be None"""
        metrics = get_metrics(tmp_path)
        assert metrics.w_lgbm is None
        assert metrics.w_ridge is None
        assert metrics.n_features is None

    def test_reads_last_train_from_watermark(self, tmp_path):
        """Should read last_train from watermark.json['features']"""
        watermark_data = {
            "features": "2026-05-08",
            "kline": "2026-05-15",
            "northbound": "2026-05-18",
        }
        watermark_file = tmp_path / "watermark.json"
        watermark_file.write_text(json.dumps(watermark_data))

        metrics = get_metrics(tmp_path)
        assert metrics.last_train == "2026-05-08"

    def test_last_train_none_when_watermark_missing(self, tmp_path):
        """When watermark.json is missing, last_train should be None"""
        metrics = get_metrics(tmp_path)
        assert metrics.last_train is None

    def test_full_integration_all_files_present(self, tmp_path):
        """Full integration test with all files present"""
        # Create models directory
        models_dir = tmp_path / "models"
        models_dir.mkdir()

        # Create eval_results.json
        eval_data = [
            {
                "split": "训练集",
                "accuracy": 0.39,
                "f1_weighted": 0.36,
                "ic": -0.0129,
            },
            {
                "split": "验证集",
                "accuracy": 0.385,
                "f1_weighted": 0.35,
                "ic": -0.0209,
            },
            {
                "split": "测试集",
                "accuracy": 0.40,
                "f1_weighted": 0.36,
                "ic": -0.023,
            },
        ]
        eval_file = models_dir / "eval_results.json"
        eval_file.write_text(json.dumps(eval_data))

        # Create ensemble_meta.json
        meta_data = {
            "w_lgbm": 0.54,
            "w_ridge": 0.46,
            "feature_cols": ["feat1", "feat2", "feat3"],
        }
        meta_file = models_dir / "ensemble_meta.json"
        meta_file.write_text(json.dumps(meta_data))

        # Create watermark.json
        watermark_data = {
            "features": "2026-05-08",
            "kline": "2026-05-15",
            "northbound": "2026-05-18",
        }
        watermark_file = tmp_path / "watermark.json"
        watermark_file.write_text(json.dumps(watermark_data))

        metrics = get_metrics(tmp_path)

        # Check all fields
        assert metrics.train_ic == pytest.approx(-0.0129)
        assert metrics.val_ic == pytest.approx(-0.0209)
        assert metrics.test_ic == pytest.approx(-0.023)
        assert metrics.val_accuracy == pytest.approx(0.385)
        assert metrics.val_f1 == pytest.approx(0.35)
        assert metrics.w_lgbm == pytest.approx(0.54)
        assert metrics.w_ridge == pytest.approx(0.46)
        assert metrics.n_features == 3
        assert metrics.last_train == "2026-05-08"

    def test_model_metrics_is_dataclass(self):
        """ModelMetrics should be a dataclass"""
        metrics = ModelMetrics(
            train_ic=-0.01,
            val_ic=-0.02,
            test_ic=-0.023,
            val_accuracy=0.385,
            val_f1=0.35,
            w_lgbm=0.54,
            w_ridge=0.46,
            n_features=3,
            last_train="2026-05-08",
        )
        assert metrics.train_ic == -0.01
        assert metrics.val_ic == -0.02
