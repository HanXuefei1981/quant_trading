"""Tests for monitor/readers/watermark.py — TDD (RED → GREEN)."""

import json
from pathlib import Path

import pytest

from monitor.readers.watermark import (
    UNIVERSE_SIZE,
    SourceStatus,
    WatermarkData,
    _count_parquet,
    get_watermarks,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_watermark(data_dir: Path, payload: dict) -> None:
    (data_dir / "watermark.json").write_text(json.dumps(payload))


def _make_parquet_files(directory: Path, count: int, prefix: str = "stock_") -> None:
    """Create `count` dummy parquet files (not real parquet, just for counting)."""
    directory.mkdir(parents=True, exist_ok=True)
    for i in range(count):
        (directory / f"{prefix}{i:05d}.parquet").write_bytes(b"")


# ---------------------------------------------------------------------------
# Test 1: get_watermarks returns WatermarkData with all 6 fields
# ---------------------------------------------------------------------------


def test_returns_watermark_data_with_all_fields(tmp_path: Path) -> None:
    """get_watermarks returns a WatermarkData with all 6 SourceStatus fields."""
    _write_watermark(tmp_path, {"kline": "2026-05-15", "features": "2026-05-15", "northbound": "2026-05-18"})
    (tmp_path / "fundamentals").mkdir()
    (tmp_path / "fund_flow").mkdir()
    (tmp_path / "models").mkdir()

    result = get_watermarks(tmp_path)

    assert isinstance(result, WatermarkData)
    assert isinstance(result.kline, SourceStatus)
    assert isinstance(result.features, SourceStatus)
    assert isinstance(result.northbound, SourceStatus)
    assert isinstance(result.fundamentals, SourceStatus)
    assert isinstance(result.fund_flow, SourceStatus)
    assert isinstance(result.models, SourceStatus)


# ---------------------------------------------------------------------------
# Test 2: kline status is "ok" when watermark.json has kline date
# ---------------------------------------------------------------------------


def test_kline_status_ok_when_date_exists(tmp_path: Path) -> None:
    _write_watermark(tmp_path, {"kline": "2026-05-15", "features": "2026-05-15", "northbound": "2026-05-18"})
    (tmp_path / "fundamentals").mkdir()
    (tmp_path / "fund_flow").mkdir()
    (tmp_path / "models").mkdir()

    result = get_watermarks(tmp_path)

    assert result.kline.status == "ok"
    assert result.kline.date == "2026-05-15"


# ---------------------------------------------------------------------------
# Test 3: features status is "warn" when features date < kline date
# ---------------------------------------------------------------------------


def test_features_status_warn_when_features_date_behind_kline(tmp_path: Path) -> None:
    _write_watermark(tmp_path, {"kline": "2026-05-15", "features": "2026-05-08", "northbound": "2026-05-18"})
    (tmp_path / "fundamentals").mkdir()
    (tmp_path / "fund_flow").mkdir()
    (tmp_path / "models").mkdir()

    result = get_watermarks(tmp_path)

    assert result.features.status == "warn"
    assert result.features.date == "2026-05-08"


# ---------------------------------------------------------------------------
# Test 4: features status is "ok" when features date == kline date
# ---------------------------------------------------------------------------


def test_features_status_ok_when_dates_match(tmp_path: Path) -> None:
    _write_watermark(tmp_path, {"kline": "2026-05-15", "features": "2026-05-15", "northbound": "2026-05-18"})
    (tmp_path / "fundamentals").mkdir()
    (tmp_path / "fund_flow").mkdir()
    (tmp_path / "models").mkdir()

    result = get_watermarks(tmp_path)

    assert result.features.status == "ok"


# ---------------------------------------------------------------------------
# Test 5: fundamentals coverage computed correctly from parquet count
# ---------------------------------------------------------------------------


def test_fundamentals_coverage_computed_correctly(tmp_path: Path) -> None:
    _write_watermark(tmp_path, {"kline": "2026-05-15", "features": "2026-05-15", "northbound": "2026-05-18"})
    fund_dir = tmp_path / "fundamentals"
    _make_parquet_files(fund_dir, count=5641)
    (tmp_path / "fund_flow").mkdir()
    (tmp_path / "models").mkdir()

    result = get_watermarks(tmp_path)

    assert result.fundamentals.count == 5641
    assert result.fundamentals.coverage == pytest.approx(1.0, rel=1e-3)


# ---------------------------------------------------------------------------
# Test 6: fundamentals status "ok" when coverage >= 0.90
# ---------------------------------------------------------------------------


def test_fundamentals_status_ok_when_coverage_at_least_90_percent(tmp_path: Path) -> None:
    _write_watermark(tmp_path, {"kline": "2026-05-15", "features": "2026-05-15", "northbound": "2026-05-18"})
    fund_dir = tmp_path / "fundamentals"
    # 90% of 5641 = 5076.9 → use 5077
    count_90 = int(UNIVERSE_SIZE * 0.90) + 1
    _make_parquet_files(fund_dir, count=count_90)
    (tmp_path / "fund_flow").mkdir()
    (tmp_path / "models").mkdir()

    result = get_watermarks(tmp_path)

    assert result.fundamentals.status == "ok"


# ---------------------------------------------------------------------------
# Test 7: fundamentals status "warn" when 0.70 <= coverage < 0.90
# ---------------------------------------------------------------------------


def test_fundamentals_status_warn_when_coverage_between_70_and_90(tmp_path: Path) -> None:
    _write_watermark(tmp_path, {"kline": "2026-05-15", "features": "2026-05-15", "northbound": "2026-05-18"})
    fund_dir = tmp_path / "fundamentals"
    # exactly 80% of 5641
    count_80 = int(UNIVERSE_SIZE * 0.80)
    _make_parquet_files(fund_dir, count=count_80)
    (tmp_path / "fund_flow").mkdir()
    (tmp_path / "models").mkdir()

    result = get_watermarks(tmp_path)

    assert result.fundamentals.status == "warn"


# ---------------------------------------------------------------------------
# Test 8: fund_flow status "err" when coverage < 0.20
# ---------------------------------------------------------------------------


def test_fund_flow_status_err_when_coverage_below_20_percent(tmp_path: Path) -> None:
    _write_watermark(tmp_path, {"kline": "2026-05-15", "features": "2026-05-15", "northbound": "2026-05-18"})
    (tmp_path / "fundamentals").mkdir()
    flow_dir = tmp_path / "fund_flow"
    # fewer than 20% of 5641 = 1128.2 → use 100
    _make_parquet_files(flow_dir, count=100)
    (tmp_path / "models").mkdir()

    result = get_watermarks(tmp_path)

    assert result.fund_flow.status == "err"


# ---------------------------------------------------------------------------
# Test 9: models status "ok" when eval_results.json exists
# ---------------------------------------------------------------------------


def test_models_status_ok_when_eval_results_exists(tmp_path: Path) -> None:
    _write_watermark(tmp_path, {"kline": "2026-05-15", "features": "2026-05-15", "northbound": "2026-05-18"})
    (tmp_path / "fundamentals").mkdir()
    (tmp_path / "fund_flow").mkdir()
    models_dir = tmp_path / "models"
    models_dir.mkdir()
    (models_dir / "eval_results.json").write_text("{}")

    result = get_watermarks(tmp_path)

    assert result.models.status == "ok"


# ---------------------------------------------------------------------------
# Test 10: models status "err" when eval_results.json is missing
# ---------------------------------------------------------------------------


def test_models_status_err_when_eval_results_missing(tmp_path: Path) -> None:
    _write_watermark(tmp_path, {"kline": "2026-05-15", "features": "2026-05-15", "northbound": "2026-05-18"})
    (tmp_path / "fundamentals").mkdir()
    (tmp_path / "fund_flow").mkdir()
    (tmp_path / "models").mkdir()  # directory exists but no eval_results.json

    result = get_watermarks(tmp_path)

    assert result.models.status == "err"


# ---------------------------------------------------------------------------
# Test 11 (bonus): _count_parquet skips files starting with "_"
# ---------------------------------------------------------------------------


def test_count_parquet_skips_underscore_prefixed_files(tmp_path: Path) -> None:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "stock_001.parquet").write_bytes(b"")
    (tmp_path / "stock_002.parquet").write_bytes(b"")
    (tmp_path / "_northbound.parquet").write_bytes(b"")
    (tmp_path / "_meta.parquet").write_bytes(b"")

    assert _count_parquet(tmp_path) == 2


# ---------------------------------------------------------------------------
# Test 12 (bonus): _count_parquet returns 0 for missing directory
# ---------------------------------------------------------------------------


def test_count_parquet_returns_zero_for_missing_directory(tmp_path: Path) -> None:
    missing = tmp_path / "does_not_exist"
    assert _count_parquet(missing) == 0
