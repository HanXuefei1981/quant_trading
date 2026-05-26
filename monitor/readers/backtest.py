"""Backtest equity-curve reader for the monitoring dashboard."""
from dataclasses import dataclass, field
from math import sqrt
from pathlib import Path
from typing import Optional

import pandas as pd


@dataclass
class BacktestData:
    """Container for backtest performance metrics and downsampled equity curves."""

    strategy_return: Optional[float]
    benchmark_return: Optional[float]
    excess_return: Optional[float]
    max_drawdown: Optional[float]   # negative float
    sharpe: Optional[float]
    period_start: Optional[str]
    period_end: Optional[str]
    dates: list = field(default_factory=list)      # downsampled date strings
    strategy: list = field(default_factory=list)   # downsampled strategy values
    benchmark: list = field(default_factory=list)  # downsampled benchmark values


def _empty() -> BacktestData:
    return BacktestData(
        strategy_return=None,
        benchmark_return=None,
        excess_return=None,
        max_drawdown=None,
        sharpe=None,
        period_start=None,
        period_end=None,
    )


def get_backtest(data_dir: Path) -> BacktestData:
    """Read equity_curve.csv and compute backtest summary metrics.

    Args:
        data_dir: Path to the data directory (contains ``backtest/`` sub-dir).

    Returns:
        BacktestData with computed metrics and downsampled curve lists.
        All fields are None / empty lists when the file is missing.
    """
    csv_path = data_dir / "backtest" / "equity_curve.csv"
    if not csv_path.exists():
        return _empty()

    df = pd.read_csv(csv_path, index_col=0)

    # Drop rows where both columns are NaN
    df = df.dropna(how="all")

    # Fill remaining NaN benchmark with forward-fill then back-fill
    df["benchmark"] = df["benchmark"].ffill().bfill()

    # Normalize to multipliers starting at 1.0 (handles absolute values like 1_000_000)
    strategy_init = df["strategy"].iloc[0]
    benchmark_init = df["benchmark"].iloc[0]

    if strategy_init == 0 or benchmark_init == 0:
        return _empty()

    df["strategy"] = df["strategy"] / strategy_init
    df["benchmark"] = df["benchmark"] / benchmark_init

    # --- Scalar metrics ---
    strategy_return = round(float(df["strategy"].iloc[-1]) - 1, 4)
    benchmark_return = round(float(df["benchmark"].iloc[-1]) - 1, 4)
    excess_return = round(strategy_return - benchmark_return, 4)

    # Max drawdown from strategy curve
    rolling_max = df["strategy"].cummax()
    drawdown_series = df["strategy"] / rolling_max - 1
    max_drawdown = round(float(drawdown_series.min()), 4)

    # Annualized Sharpe from daily returns
    daily_returns = df["strategy"].pct_change().dropna()
    if len(daily_returns) > 1 and daily_returns.std() != 0:
        sharpe = round(float(daily_returns.mean() / daily_returns.std() * sqrt(252)), 4)
    else:
        sharpe = None

    # Period start / end
    period_start = str(df.index[0])
    period_end = str(df.index[-1])

    # --- Downsample to ≤200 points ---
    step = max(1, len(df) // 200)
    downsampled = df.iloc[::step]

    dates = list(downsampled.index)
    strategy_vals = [round(float(v), 4) for v in downsampled["strategy"]]
    benchmark_vals = [round(float(v), 4) for v in downsampled["benchmark"]]

    return BacktestData(
        strategy_return=strategy_return,
        benchmark_return=benchmark_return,
        excess_return=excess_return,
        max_drawdown=max_drawdown,
        sharpe=sharpe,
        period_start=period_start,
        period_end=period_end,
        dates=dates,
        strategy=strategy_vals,
        benchmark=benchmark_vals,
    )
