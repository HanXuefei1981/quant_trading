"""Tests for monitor.readers.backtest module (TDD - Task 4)"""
import sys
from pathlib import Path
from math import sqrt

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from monitor.readers.backtest import BacktestData, get_backtest


def _make_equity_csv(tmp_path: Path, strategy: list, benchmark: list, dates: list | None = None) -> Path:
    """Create a fake equity_curve.csv in tmp_path/backtest/."""
    backtest_dir = tmp_path / "backtest"
    backtest_dir.mkdir(parents=True, exist_ok=True)
    if dates is None:
        dates = [f"2024-01-{i+1:02d}" for i in range(len(strategy))]
    df = pd.DataFrame({"strategy": strategy, "benchmark": benchmark}, index=dates)
    df.index.name = "date"
    path = backtest_dir / "equity_curve.csv"
    df.to_csv(path)
    return path


class TestBacktestMissingFile:
    def test_returns_all_none_when_equity_curve_missing(self, tmp_path):
        """When equity_curve.csv is absent, all fields must be None."""
        result = get_backtest(tmp_path)
        assert isinstance(result, BacktestData)
        assert result.strategy_return is None
        assert result.benchmark_return is None
        assert result.excess_return is None
        assert result.max_drawdown is None
        assert result.sharpe is None
        assert result.period_start is None
        assert result.period_end is None
        assert result.dates == []
        assert result.strategy == []
        assert result.benchmark == []


class TestBacktestReturns:
    def test_strategy_return_is_final_minus_one(self, tmp_path):
        """strategy_return = (final_strategy / initial_strategy) - 1."""
        # Values as multipliers starting at 1.0 — reader must normalize
        _make_equity_csv(tmp_path, strategy=[1.0, 1.05, 1.10], benchmark=[1.0, 1.02, 1.04])
        result = get_backtest(tmp_path)
        assert result.strategy_return == pytest.approx(0.10, abs=1e-3)

    def test_benchmark_return_is_final_minus_one(self, tmp_path):
        """benchmark_return = (final_benchmark / initial_benchmark) - 1."""
        _make_equity_csv(tmp_path, strategy=[1.0, 1.05, 1.10], benchmark=[1.0, 1.02, 1.04])
        result = get_backtest(tmp_path)
        assert result.benchmark_return == pytest.approx(0.04, abs=1e-3)

    def test_excess_return_equals_strategy_minus_benchmark(self, tmp_path):
        """excess_return = strategy_return - benchmark_return."""
        _make_equity_csv(tmp_path, strategy=[1.0, 1.05, 1.10], benchmark=[1.0, 1.02, 1.04])
        result = get_backtest(tmp_path)
        assert result.excess_return == pytest.approx(
            result.strategy_return - result.benchmark_return, abs=1e-6
        )

    def test_strategy_return_with_absolute_values(self, tmp_path):
        """Reader must normalize absolute values like 1_000_000 start."""
        # Real data starts at 1,000,000
        _make_equity_csv(tmp_path,
                         strategy=[1_000_000, 1_050_000, 1_100_000],
                         benchmark=[1_000_000, 1_020_000, 1_040_000])
        result = get_backtest(tmp_path)
        assert result.strategy_return == pytest.approx(0.10, abs=1e-3)
        assert result.benchmark_return == pytest.approx(0.04, abs=1e-3)


class TestBacktestFirstRowNaNBenchmark:
    def test_handles_first_benchmark_nan(self, tmp_path):
        """First benchmark row may be NaN; reader must fill it and still compute returns."""
        _make_equity_csv(tmp_path,
                         strategy=[1.0, 1.05, 1.10],
                         benchmark=[None, 1.02, 1.04])
        result = get_backtest(tmp_path)
        # Should not raise; benchmark_return should be computed
        assert result.benchmark_return is not None


class TestBacktestMaxDrawdown:
    def test_max_drawdown_is_negative(self, tmp_path):
        """max_drawdown must be a negative float for any realistic series."""
        # A series that goes up then drops
        _make_equity_csv(tmp_path,
                         strategy=[1.0, 1.2, 1.1, 1.3, 1.0],
                         benchmark=[1.0, 1.1, 1.05, 1.2, 1.15])
        result = get_backtest(tmp_path)
        assert result.max_drawdown is not None
        assert result.max_drawdown < 0

    def test_max_drawdown_is_float(self, tmp_path):
        """max_drawdown should be a float."""
        _make_equity_csv(tmp_path,
                         strategy=[1.0, 1.2, 0.9, 1.1],
                         benchmark=[1.0, 1.1, 1.05, 1.2])
        result = get_backtest(tmp_path)
        assert isinstance(result.max_drawdown, float)


class TestBacktestSharpe:
    def test_sharpe_is_float(self, tmp_path):
        """sharpe ratio should be a float."""
        _make_equity_csv(tmp_path,
                         strategy=[1.0, 1.01, 1.02, 1.03, 1.04],
                         benchmark=[1.0, 1.005, 1.01, 1.015, 1.02])
        result = get_backtest(tmp_path)
        assert isinstance(result.sharpe, float)


class TestBacktestDownsampling:
    def test_dates_strategy_benchmark_have_at_most_200_items(self, tmp_path):
        """Downsampled lists should have ≤200 items."""
        # Create a series with 400 rows
        n = 400
        strategy = [1.0 + i * 0.001 for i in range(n)]
        benchmark = [1.0 + i * 0.0008 for i in range(n)]
        dates = [f"2020-01-01" for _ in range(n)]  # dates don't need to be unique for this test
        # Actually use a helper that doesn't mind repeated index...
        backtest_dir = tmp_path / "backtest"
        backtest_dir.mkdir(parents=True, exist_ok=True)
        df = pd.DataFrame({"strategy": strategy, "benchmark": benchmark})
        df.index = pd.date_range("2020-01-01", periods=n, freq="B").strftime("%Y-%m-%d")
        df.index.name = "date"
        df.to_csv(backtest_dir / "equity_curve.csv")

        result = get_backtest(tmp_path)
        assert len(result.dates) <= 200
        assert len(result.strategy) <= 200
        assert len(result.benchmark) <= 200

    def test_dates_strategy_benchmark_lengths_match(self, tmp_path):
        """dates, strategy, and benchmark lists must have the same length."""
        _make_equity_csv(tmp_path,
                         strategy=[1.0, 1.05, 1.10, 1.08, 1.12],
                         benchmark=[1.0, 1.02, 1.04, 1.03, 1.05])
        result = get_backtest(tmp_path)
        assert len(result.dates) == len(result.strategy) == len(result.benchmark)

    def test_short_series_not_downsampled(self, tmp_path):
        """A series with ≤200 rows should return all rows unchanged."""
        n = 10
        strategy = [1.0 + i * 0.01 for i in range(n)]
        benchmark = [1.0 + i * 0.008 for i in range(n)]
        _make_equity_csv(tmp_path, strategy=strategy, benchmark=benchmark)
        result = get_backtest(tmp_path)
        assert len(result.dates) == n


class TestBacktestPeriod:
    def test_period_start_and_end_are_strings(self, tmp_path):
        """period_start and period_end must be strings."""
        _make_equity_csv(tmp_path,
                         strategy=[1.0, 1.05],
                         benchmark=[1.0, 1.02],
                         dates=["2024-01-02", "2024-01-03"])
        result = get_backtest(tmp_path)
        assert isinstance(result.period_start, str)
        assert isinstance(result.period_end, str)

    def test_period_start_is_first_date(self, tmp_path):
        """period_start should match the first index date."""
        _make_equity_csv(tmp_path,
                         strategy=[1.0, 1.05],
                         benchmark=[1.0, 1.02],
                         dates=["2024-01-02", "2024-12-31"])
        result = get_backtest(tmp_path)
        assert result.period_start == "2024-01-02"

    def test_period_end_is_last_date(self, tmp_path):
        """period_end should match the last index date."""
        _make_equity_csv(tmp_path,
                         strategy=[1.0, 1.05],
                         benchmark=[1.0, 1.02],
                         dates=["2024-01-02", "2024-12-31"])
        result = get_backtest(tmp_path)
        assert result.period_end == "2024-12-31"
