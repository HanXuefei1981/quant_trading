"""测试模型评估函数（evaluator.py）"""
import numpy as np
import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.models.evaluator import direction_accuracy, ic_score


# ── direction_accuracy ────────────────────────────────────────────────────────

def test_direction_accuracy_perfect():
    y_true = np.array([2, 0, 2, 0])
    y_pred = np.array([2, 0, 2, 0])
    assert direction_accuracy(y_true, y_pred) == pytest.approx(1.0)


def test_direction_accuracy_zero():
    y_true = np.array([2, 0, 2, 0])
    y_pred = np.array([0, 2, 0, 2])
    assert direction_accuracy(y_true, y_pred) == pytest.approx(0.0)


def test_direction_accuracy_ignores_neutral():
    # 预测为震荡(1)的样本不参与计算
    y_true = np.array([2, 0, 2, 0, 1])
    y_pred = np.array([2, 0, 1, 1, 1])  # 后三个预测为震荡，只有前两个参与
    assert direction_accuracy(y_true, y_pred) == pytest.approx(1.0)


def test_direction_accuracy_all_neutral_returns_nan():
    y_true = np.array([2, 0, 2])
    y_pred = np.array([1, 1, 1])  # 全部预测为震荡
    result = direction_accuracy(y_true, y_pred)
    assert np.isnan(result)


def test_direction_accuracy_half():
    y_true = np.array([2, 2, 0, 0])
    y_pred = np.array([2, 0, 0, 2])
    assert direction_accuracy(y_true, y_pred) == pytest.approx(0.5)


# ── ic_score ──────────────────────────────────────────────────────────────────

def test_ic_score_perfect_positive():
    # 得分与收益率完全正相关
    proba = np.zeros((10, 3))
    proba[:, 2] = np.linspace(0.1, 1.0, 10)  # P_涨 递增
    proba[:, 0] = 0.0
    future_ret = np.linspace(0.01, 0.10, 10)
    ic = ic_score(proba, future_ret)
    assert ic == pytest.approx(1.0, abs=1e-6)


def test_ic_score_perfect_negative():
    proba = np.zeros((10, 3))
    proba[:, 2] = np.linspace(1.0, 0.1, 10)  # P_涨 递减
    proba[:, 0] = 0.0
    future_ret = np.linspace(0.01, 0.10, 10)
    ic = ic_score(proba, future_ret)
    assert ic == pytest.approx(-1.0, abs=1e-6)


def test_ic_score_range():
    rng = np.random.default_rng(99)
    proba = rng.dirichlet([1, 1, 1], size=200)
    future_ret = rng.normal(0, 0.02, 200)
    ic = ic_score(proba, future_ret)
    assert -1.0 <= ic <= 1.0


def test_ic_score_ignores_nan_returns():
    proba = np.zeros((5, 3))
    proba[:, 2] = [0.8, 0.6, 0.4, 0.2, np.nan]
    future_ret = np.array([0.05, 0.03, -0.01, -0.04, np.nan])
    ic = ic_score(proba, future_ret)
    assert np.isfinite(ic)


def test_ic_score_formula():
    # 验证 IC = Pearson(P_涨 - P_跌, future_ret)
    rng = np.random.default_rng(7)
    proba = rng.dirichlet([1, 1, 1], size=100)
    future_ret = rng.normal(0, 0.02, 100)
    ic = ic_score(proba, future_ret)
    score = proba[:, 2] - proba[:, 0]
    expected = float(np.corrcoef(score, future_ret)[0, 1])
    assert ic == pytest.approx(expected, abs=1e-10)
