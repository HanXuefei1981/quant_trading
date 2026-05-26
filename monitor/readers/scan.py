"""Scan signals reader for the monitoring dashboard."""
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import pandas as pd


@dataclass
class SignalRow:
    """Single row from the scan signal file, enriched with fund-flow data."""

    rank: int
    code: str           # zero-padded 6-digit string e.g. "300511"
    segment: str        # market segment label e.g. "创业板"
    close: float
    signal: float
    signal_pct: float
    north_5d: Optional[float]   # always None — northbound is aggregate, not per-stock
    fund_flow: Optional[float]  # latest major_net_inflow, or None if unavailable
    status: str                 # "hold" for rank <= top_n, "buffer" for buffer rows


@dataclass
class ScanData:
    """Container for the latest scan results."""

    date: Optional[str]
    top_n: int
    buffer_n: int
    signals: list = field(default_factory=list)  # list of SignalRow


def _latest_fund_flow(data_dir: Path, code: str) -> Optional[float]:
    """Return the most recent major_net_inflow for a stock, or None if unavailable.

    Args:
        data_dir: Path to the data directory (contains ``fund_flow/`` sub-dir).
        code: Zero-padded 6-digit stock code string.

    Returns:
        Latest major_net_inflow as float, or None if the parquet file is absent
        or empty or lacks the required column.
    """
    path = data_dir / "fund_flow" / f"{code}.parquet"
    if not path.exists():
        return None
    try:
        df = pd.read_parquet(path)
    except Exception:
        return None
    if df.empty or "major_net_inflow" not in df.columns:
        return None
    return float(df["major_net_inflow"].iloc[-1])


def get_scan(data_dir: Path, top_n: int = 50, buffer_n: int = 5) -> ScanData:
    """Read the latest scan CSV and build enriched SignalRow objects.

    Args:
        data_dir: Path to the data directory (contains ``backtest/scan_*.csv``).
        top_n: Number of top-ranked signals to mark as "hold".
        buffer_n: Number of additional rows to include as "buffer".

    Returns:
        ScanData with signals sorted by rank ascending.
        Returns empty ScanData when no scan file is found.
    """
    backtest_dir = data_dir / "backtest"
    scan_files = sorted(backtest_dir.glob("scan_*.csv")) if backtest_dir.exists() else []

    if not scan_files:
        return ScanData(date=None, top_n=top_n, buffer_n=buffer_n)

    # Latest file is the last alphabetically (YYYY-MM-DD sorts correctly)
    latest_file = scan_files[-1]

    # Extract date from filename: scan_YYYY-MM-DD.csv
    stem = latest_file.stem  # e.g. "scan_2026-05-18"
    date_str = stem[len("scan_"):]  # "2026-05-18"

    df = pd.read_csv(latest_file, dtype={"code": str})

    # Zero-pad code to 6 digits (handles int-stored codes like 89 → "000089")
    df["code"] = df["code"].apply(lambda c: str(int(float(c))).zfill(6))

    # Sort by rank ascending and take top_n + buffer_n rows
    df = df.sort_values("rank").head(top_n + buffer_n)

    signals: list[SignalRow] = []
    for _, row in df.iterrows():
        rank = int(row["rank"])
        code = str(row["code"])
        status = "hold" if rank <= top_n else "buffer"
        fund_flow = _latest_fund_flow(data_dir, code)

        signals.append(SignalRow(
            rank=rank,
            code=code,
            segment=str(row["segment"]),
            close=float(row["close"]),
            signal=float(row["signal"]),
            signal_pct=float(row["signal_pct"]),
            north_5d=None,
            fund_flow=fund_flow,
            status=status,
        ))

    return ScanData(date=date_str, top_n=top_n, buffer_n=buffer_n, signals=signals)
