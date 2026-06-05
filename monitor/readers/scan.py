"""Scan signals reader for the monitoring dashboard."""
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)


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
    streak: Optional[int]       # 连榜：截至当日连续在榜次数，缺则 None
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

    # 最新文件按文件名字典序最后（scan_YYYY-MM-DD 可正确排序）
    latest_file = scan_files[-1]
    date_str = latest_file.stem[len("scan_"):]  # "scan_2026-06-04" → "2026-06-04"

    df = pd.read_csv(latest_file)

    # 优雅降级：缺必需中文列（如最新文件恰为旧英文格式）→ 返回空，避免 /api/status 500
    required = ["排名", "代码", "收盘价", "信号值", "信号分位"]
    if not all(col in df.columns for col in required):
        logger.warning("scan CSV %s 缺必需列 %s，返回空信号", latest_file.name, required)
        return ScanData(date=date_str, top_n=top_n, buffer_n=buffer_n)

    df = df.sort_values("排名").head(top_n + buffer_n)

    signals: list[SignalRow] = []
    for _, row in df.iterrows():
        rank = int(row["排名"])
        code = str(int(float(row["代码"]))).zfill(6)  # 整数/字符串代码统一补零到 6 位
        status = "hold" if rank <= top_n else "buffer"
        fund_flow = _latest_fund_flow(data_dir, code)
        streak = int(row["连榜"]) if "连榜" in df.columns and pd.notna(row["连榜"]) else None
        segment = str(row["板块"]) if "板块" in df.columns and pd.notna(row["板块"]) else "—"

        signals.append(SignalRow(
            rank=rank,
            code=code,
            segment=segment,
            close=float(row["收盘价"]),
            signal=float(row["信号值"]),
            signal_pct=float(row["信号分位"]),
            north_5d=None,
            fund_flow=fund_flow,
            streak=streak,
            status=status,
        ))

    return ScanData(date=date_str, top_n=top_n, buffer_n=buffer_n, signals=signals)
