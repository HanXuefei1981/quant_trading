"""Watermark reader — reports freshness and coverage of each data source."""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional
import json

UNIVERSE_SIZE = 5641


@dataclass
class SourceStatus:
    name: str
    date: Optional[str]       # latest date (None if unknown)
    count: Optional[int]      # stock count (None if N/A)
    coverage: Optional[float]  # 0.0–1.0 (None if N/A)
    status: str               # "ok" | "warn" | "err"


@dataclass
class WatermarkData:
    kline: SourceStatus
    features: SourceStatus
    northbound: SourceStatus
    fundamentals: SourceStatus
    fund_flow: SourceStatus
    models: SourceStatus


def _count_parquet(directory: Path) -> int:
    """Count .parquet files in *directory*, excluding names that start with '_'."""
    if not directory.exists():
        return 0
    return sum(1 for f in directory.glob("*.parquet") if not f.name.startswith("_"))


def _load_watermark_json(data_dir: Path) -> dict:
    wm_path = data_dir / "watermark.json"
    if not wm_path.exists():
        return {}
    try:
        return json.loads(wm_path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _status_by_coverage(coverage: float, ok_threshold: float, warn_threshold: float) -> str:
    if coverage >= ok_threshold:
        return "ok"
    if coverage >= warn_threshold:
        return "warn"
    return "err"


def get_watermarks(data_dir: Path) -> WatermarkData:
    """Read all data-source watermarks and return a WatermarkData summary.

    Args:
        data_dir: Path to the ``data/`` directory.

    Returns:
        WatermarkData with a SourceStatus for each data source.
    """
    wm = _load_watermark_json(data_dir)

    # ------------------------------------------------------------------ kline
    kline_date: Optional[str] = wm.get("kline")
    kline_status = SourceStatus(
        name="kline",
        date=kline_date,
        count=None,
        coverage=None,
        status="ok" if kline_date else "err",
    )

    # --------------------------------------------------------------- features
    features_date: Optional[str] = wm.get("features")
    if features_date and kline_date:
        features_ok = features_date == kline_date
    else:
        features_ok = bool(features_date)
    features_status = SourceStatus(
        name="features",
        date=features_date,
        count=None,
        coverage=None,
        status="ok" if features_ok else "warn",
    )

    # ------------------------------------------------------------- northbound
    northbound_date: Optional[str] = wm.get("northbound")
    northbound_status = SourceStatus(
        name="northbound",
        date=northbound_date,
        count=None,
        coverage=None,  # aggregate data, not per-stock
        status="ok" if northbound_date else "err",
    )

    # --------------------------------------------------------- fundamentals
    fund_dir = data_dir / "fundamentals"
    fund_count = _count_parquet(fund_dir)
    fund_coverage = fund_count / UNIVERSE_SIZE
    fundamentals_status = SourceStatus(
        name="fundamentals",
        date=None,
        count=fund_count,
        coverage=fund_coverage,
        status=_status_by_coverage(fund_coverage, ok_threshold=0.90, warn_threshold=0.70),
    )

    # ---------------------------------------------------------------- fund_flow
    flow_dir = data_dir / "fund_flow"
    flow_count = _count_parquet(flow_dir)
    flow_coverage = flow_count / UNIVERSE_SIZE
    fund_flow_status = SourceStatus(
        name="fund_flow",
        date=None,
        count=flow_count,
        coverage=flow_coverage,
        status=_status_by_coverage(flow_coverage, ok_threshold=0.50, warn_threshold=0.20),
    )

    # ------------------------------------------------------------------ models
    eval_results = data_dir / "models" / "eval_results.json"
    models_date: Optional[str] = wm.get("features")  # proxy for last training date
    models_status = SourceStatus(
        name="models",
        date=models_date,
        count=None,
        coverage=None,
        status="ok" if eval_results.exists() else "err",
    )

    return WatermarkData(
        kline=kline_status,
        features=features_status,
        northbound=northbound_status,
        fundamentals=fundamentals_status,
        fund_flow=fund_flow_status,
        models=models_status,
    )
