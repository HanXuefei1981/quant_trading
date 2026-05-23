"""Tests for assembler.py write-path DAL migration."""
import sys
from datetime import date
from pathlib import Path
from unittest.mock import patch
import duckdb
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.dal.schema import migrate
from src.dal.raw_repo import RawRepo
from src.dal.feature_repo import FeatureRepo


@pytest.fixture
def conn():
    c = duckdb.connect(":memory:")
    migrate(c)
    yield c
    c.close()


def _seed_kline(conn, code: str = "000001", n: int = 300) -> None:
    from datetime import timedelta
    base = date(2023, 1, 1)
    for i in range(n):
        d = base + timedelta(days=i)
        conn.execute(
            "INSERT OR IGNORE INTO kline VALUES (?, ?, 10.0, 11.0, 9.5, 10.5, 1e8, 1e6)",
            [d.isoformat(), code],
        )


def _make_combined(code: str = "000001") -> pd.DataFrame:
    return pd.DataFrame({
        "date": pd.to_datetime(["2024-01-02", "2024-01-03"]),
        "code": code,
        "close": [10.5, 11.0],
        "future_ret": [0.01, 0.02],
        "ret1": [0.005, 0.01],
        "label": [1, 2],
    })


# ── assemble() writes to FeatureRepo ─────────────────────────────────────────

def test_assemble_writes_to_feature_repo(conn):
    from src.features.assembler import assemble

    _seed_kline(conn, "000001", n=300)
    raw_repo = RawRepo(conn)
    feature_repo = FeatureRepo(conn)
    combined = _make_combined()

    with patch("src.features.assembler.add_all_features", return_value=combined), \
         patch("src.features.assembler.add_report_features", side_effect=lambda df, c, **kw: df), \
         patch("src.features.assembler.add_signal_features", side_effect=lambda df, c, **kw: df), \
         patch("src.features.assembler.add_cross_sectional_label", side_effect=lambda df: df.assign(label=1)), \
         patch("src.features.assembler.preprocess_features", side_effect=lambda df, cols: df), \
         patch("src.features.assembler._load_northbound", return_value=None), \
         patch("src.features.assembler._load_lhb_all", return_value=None):
        assemble(raw_repo=raw_repo, feature_repo=feature_repo)

    date_range = feature_repo.get_feature_date_range()
    assert date_range is not None, "features table should have data after assemble()"


# ── assemble() is idempotent ──────────────────────────────────────────────────

def test_assemble_is_idempotent(conn):
    from src.features.assembler import assemble

    _seed_kline(conn, "000001", n=300)
    raw_repo = RawRepo(conn)
    feature_repo = FeatureRepo(conn)
    combined = _make_combined()

    import contextlib
    patches = [
        patch("src.features.assembler.add_all_features", return_value=combined),
        patch("src.features.assembler.add_report_features", side_effect=lambda df, c, **kw: df),
        patch("src.features.assembler.add_signal_features", side_effect=lambda df, c, **kw: df),
        patch("src.features.assembler.add_cross_sectional_label", side_effect=lambda df: df.assign(label=1)),
        patch("src.features.assembler.preprocess_features", side_effect=lambda df, cols: df),
        patch("src.features.assembler._load_northbound", return_value=None),
        patch("src.features.assembler._load_lhb_all", return_value=None),
    ]
    with contextlib.ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        assemble(raw_repo=raw_repo, feature_repo=feature_repo)
        assemble(raw_repo=raw_repo, feature_repo=feature_repo)  # 第二次：幂等

    result = conn.execute("SELECT COUNT(*) FROM features").fetchone()[0]
    assert result == len(combined), "idempotent upsert must not duplicate rows"


# ── assemble_inference() loads latest cross-section ──────────────────────────

def test_assemble_inference_returns_latest_date(conn):
    from src.features.assembler import assemble_inference

    feature_repo = FeatureRepo(conn)
    df1 = _make_combined("000001")
    df2 = pd.DataFrame({
        "date": pd.to_datetime(["2024-01-05"]),
        "code": "000002",
        "close": [20.0],
        "future_ret": [0.03],
        "ret1": [0.015],
        "label": [0],
    })
    feature_repo.upsert_features(pd.concat([df1, df2], ignore_index=True))

    with patch("src.features.assembler.get_feature_columns", return_value=[]), \
         patch("src.features.assembler.preprocess_features", side_effect=lambda df, cols: df):
        result = assemble_inference(feature_repo=feature_repo)

    assert pd.to_datetime(result["date"]).dt.date.max() == date(2024, 1, 5)


def test_assemble_inference_raises_when_empty(conn):
    from src.features.assembler import assemble_inference

    feature_repo = FeatureRepo(conn)
    with pytest.raises(FileNotFoundError, match="features"):
        assemble_inference(feature_repo=feature_repo)
