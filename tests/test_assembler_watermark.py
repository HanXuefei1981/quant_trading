"""Tests for assembler.py watermark lifecycle via MetaRepo."""
import contextlib
import sys
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import duckdb
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.dal.schema import migrate
from src.dal.raw_repo import RawRepo
from src.dal.feature_repo import FeatureRepo
from src.dal.meta_repo import MetaRepo


@pytest.fixture
def conn():
    c = duckdb.connect(":memory:")
    migrate(c)
    yield c
    c.close()


# ── 首次运行（无 watermark）委托给全量 assemble ─────────────────────────────────

def test_incremental_no_watermark_delegates_to_full_assemble(conn):
    """When features watermark is absent, assemble_incremental() delegates to assemble()
    and writes the watermark afterwards."""
    from src.features.assembler import assemble_incremental

    raw_repo = RawRepo(conn)
    feature_repo = FeatureRepo(conn)
    meta_repo = MetaRepo(conn)

    assert meta_repo.get_last_date("features", "__market__") is None

    mock_result = pd.DataFrame({
        "date": pd.to_datetime(["2024-05-01"]),
        "code": "000001",
        "future_ret": [0.01],
        "ret1": [0.005],
        "label": [1],
    })

    with patch("src.features.assembler.assemble", return_value=mock_result) as mock_assemble:
        assemble_incremental(raw_repo=raw_repo, feature_repo=feature_repo, meta_repo=meta_repo)

    mock_assemble.assert_called_once()
    since = meta_repo.get_last_date("features", "__market__")
    assert since == date(2024, 5, 1), "watermark must be set to max date from assemble() result"


# ── 增量运行从 MetaRepo 读取 watermark ────────────────────────────────────────

def test_incremental_reads_watermark_from_metarepo(conn):
    from src.features.assembler import assemble_incremental

    raw_repo = RawRepo(conn)
    feature_repo = FeatureRepo(conn)
    meta_repo = MetaRepo(conn)
    meta_repo.set_last_date("features", "__market__", date(2024, 3, 1))

    called_since = {}

    def spy_load_kline(self, code, since=None):
        called_since["since"] = since
        return pd.DataFrame()  # empty → skipped

    with patch.object(RawRepo, "load_kline", spy_load_kline), \
         patch("src.features.assembler._get_kline_codes", return_value=["000001"]), \
         patch("src.features.assembler._load_northbound", return_value=None), \
         patch("src.features.assembler._load_lhb_all", return_value=None):
        assemble_incremental(
            raw_repo=raw_repo,
            feature_repo=feature_repo,
            meta_repo=meta_repo,
        )

    expected = date(2024, 3, 1) - timedelta(days=300)
    assert called_since.get("since") == expected, \
        f"Expected since={expected}, got {called_since.get('since')}"
    # watermark must not change when there is no new data
    assert meta_repo.get_last_date("features", "__market__") == date(2024, 3, 1)


# ── 增量运行后 watermark 更新 ─────────────────────────────────────────────────

def test_watermark_updated_after_incremental(conn):
    from src.features.assembler import assemble_incremental

    raw_repo = RawRepo(conn)
    feature_repo = FeatureRepo(conn)
    meta_repo = MetaRepo(conn)
    meta_repo.set_last_date("features", "__market__", date(2024, 4, 1))

    # 25 business-day kline rows spanning before + after the watermark (passes len < 20 guard)
    kline_dates = pd.bdate_range("2024-03-01", periods=25)
    kline_data = pd.DataFrame({
        "date": kline_dates,
        "code": "000001",
        "open": [10.0] * 25,
        "high": [11.0] * 25,
        "low": [9.0] * 25,
        "close": [10.5] * 25,
        "volume": [1000.0] * 25,
    })
    # feature_data has a row after the watermark date (2024-04-01), so new_rows is non-empty
    feature_data = pd.DataFrame({
        "date": pd.to_datetime(["2024-05-01"]),
        "code": "000001",
        "future_ret": [0.01],
        "ret1": [0.005],
        "label": [1],
    })

    patches = [
        patch("src.features.assembler._get_kline_codes", return_value=["000001"]),
        patch("src.features.assembler._load_northbound", return_value=None),
        patch("src.features.assembler._load_lhb_all", return_value=None),
        patch.object(RawRepo, "load_kline", return_value=kline_data),
        patch("src.features.assembler.add_all_features", return_value=feature_data),
        patch("src.features.assembler.add_report_features", side_effect=lambda df, c, **kw: df),
        patch("src.features.assembler.add_signal_features", side_effect=lambda df, c, **kw: df),
        patch("src.features.assembler.add_cross_sectional_label", side_effect=lambda df: df.assign(label=1)),
        patch("src.features.assembler.preprocess_features", side_effect=lambda df, cols: df),
        patch.object(FeatureRepo, "upsert_features", return_value=None),
    ]
    with contextlib.ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        assemble_incremental(
            raw_repo=raw_repo,
            feature_repo=feature_repo,
            meta_repo=meta_repo,
        )

    updated = meta_repo.get_last_date("features", "__market__")
    assert updated == date(2024, 5, 1), f"watermark must advance to max new date, got {updated}"
