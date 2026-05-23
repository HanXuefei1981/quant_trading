"""Tests for assembler.py read-path DAL migration."""
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


@pytest.fixture
def conn():
    c = duckdb.connect(":memory:")
    migrate(c)
    yield c
    c.close()


def _insert_kline(conn, code: str, dates: list[str]) -> None:
    for d in dates:
        conn.execute(
            "INSERT INTO kline VALUES (?, ?, 10.0, 11.0, 9.5, 10.5, 1e8, 1e6)",
            [d, code],
        )


# ── _get_kline_codes ───────────────────────────────────────────────────────────

def test_get_kline_codes_returns_distinct_sorted(conn):
    from src.features.assembler import _get_kline_codes

    _insert_kline(conn, "000002", ["2024-01-02"])
    _insert_kline(conn, "000001", ["2024-01-02"])

    raw_repo = RawRepo(conn)
    codes = _get_kline_codes(raw_repo)
    assert codes == ["000001", "000002"]


def test_get_kline_codes_empty_returns_empty(conn):
    from src.features.assembler import _get_kline_codes

    raw_repo = RawRepo(conn)
    assert _get_kline_codes(raw_repo) == []


# ── _load_northbound ──────────────────────────────────────────────────────────

def test_load_northbound_returns_none_when_empty(conn):
    from src.features.assembler import _load_northbound

    raw_repo = RawRepo(conn)
    assert _load_northbound(raw_repo) is None


def test_load_northbound_with_since(conn):
    from src.features.assembler import _load_northbound

    conn.execute("INSERT INTO northbound VALUES ('2024-01-02', 1e8, 5e7, 5e7)")
    conn.execute("INSERT INTO northbound VALUES ('2024-03-01', 2e8, 1e8, 1e8)")
    raw_repo = RawRepo(conn)

    result = _load_northbound(raw_repo, since=date(2024, 1, 2))
    assert result is not None
    assert len(result) == 1
    assert pd.to_datetime(result["date"].iloc[0]).date() == date(2024, 3, 1)


# ── _load_lhb_all ─────────────────────────────────────────────────────────────

def test_load_lhb_all_returns_none_when_empty(conn):
    from src.features.assembler import _load_lhb_all

    raw_repo = RawRepo(conn)
    assert _load_lhb_all(raw_repo) is None


def test_load_lhb_all_with_since(conn):
    from src.features.assembler import _load_lhb_all

    conn.execute("INSERT INTO lhb VALUES ('2024-01-02', '000001', 1e6, 5e6, 4e6)")
    conn.execute("INSERT INTO lhb VALUES ('2024-03-01', '000001', 2e6, 6e6, 4e6)")
    raw_repo = RawRepo(conn)

    result = _load_lhb_all(raw_repo, since=date(2024, 1, 2))
    assert result is not None
    assert len(result) == 1


# ── assemble() raises when kline is empty ─────────────────────────────────────

def test_assemble_raises_when_no_kline(conn):
    from src.features.assembler import assemble
    from src.dal.feature_repo import FeatureRepo

    raw_repo = RawRepo(conn)
    feature_repo = FeatureRepo(conn)

    with pytest.raises(RuntimeError, match="kline"):
        assemble(raw_repo=raw_repo, feature_repo=feature_repo)


# ── incremental mode passes since to all reads ────────────────────────────────

def test_incremental_passes_since_to_load_kline(conn):
    """In incremental mode, load_kline is called with since=lookback_date."""
    from src.features.assembler import assemble_incremental
    from src.dal.feature_repo import FeatureRepo
    from src.dal.meta_repo import MetaRepo

    raw_repo = RawRepo(conn)
    feature_repo = FeatureRepo(conn)
    meta_repo = MetaRepo(conn)
    meta_repo.set_last_date("features", "__market__", date(2024, 3, 1))

    with patch.object(raw_repo, "load_kline", return_value=pd.DataFrame()) as mock_load:
        with patch("src.features.assembler._get_kline_codes", return_value=["000001"]):
            with patch("src.features.assembler._load_northbound", return_value=None):
                with patch("src.features.assembler._load_lhb_all", return_value=None):
                    assemble_incremental(
                        raw_repo=raw_repo,
                        feature_repo=feature_repo,
                        meta_repo=meta_repo,
                    )
    mock_load.assert_called_once()
    call_kwargs = mock_load.call_args
    from datetime import timedelta
    expected_since = date(2024, 3, 1) - timedelta(days=300)
    assert call_kwargs[1].get("since") == expected_since or call_kwargs[0][1] == expected_since
