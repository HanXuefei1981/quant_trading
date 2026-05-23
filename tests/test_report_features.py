"""Tests for report.py — DAL-backed version."""
import sys
from datetime import date
from pathlib import Path

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


def _make_kline(dates: list[str]) -> pd.DataFrame:
    return pd.DataFrame({
        "date": pd.to_datetime(dates),
        "code": "000001",
        "close": [10.0] * len(dates),
    })


def _insert_reports(conn, dates: list[str], code: str = "000001") -> None:
    for i, d in enumerate(dates):
        conn.execute(
            "INSERT INTO reports VALUES (?, ?, ?, ?)",
            [d, code, f"机构{i}", "买入"],
        )


def _insert_eps(conn, snapshot_dates: list[str], eps_vals: list[float], code: str = "000001") -> None:
    for d, eps in zip(snapshot_dates, eps_vals):
        conn.execute(
            "INSERT INTO eps_snapshot VALUES (?, ?, ?, ?, ?)",
            [d, code, eps, eps + 0.1, 5],
        )


# ── report_count_30d ──────────────────────────────────────────────────────────

def test_report_count_30d_rolling(conn):
    from src.features.report import add_report_features

    _insert_reports(conn, ["2026-05-01", "2026-05-10"])
    kline = _make_kline(["2026-05-11", "2026-05-12", "2026-05-13"])
    raw_repo = RawRepo(conn)

    result = add_report_features(kline, "000001", raw_repo=raw_repo)
    assert "report_count_30d" in result.columns
    assert result["report_count_30d"].iloc[0] == 2


def test_no_reports_fills_nan(conn):
    from src.features.report import add_report_features

    kline = _make_kline(["2026-05-11"])
    raw_repo = RawRepo(conn)

    result = add_report_features(kline, "000001", raw_repo=raw_repo)
    assert result["report_count_30d"].isna().all()
    assert result["analyst_count"].isna().all()


# ── eps features ──────────────────────────────────────────────────────────────

def test_eps_consensus_forward_fill(conn):
    from src.features.report import add_report_features

    _insert_eps(conn, ["2026-01-01", "2026-04-01"], [1.0, 1.2])
    kline = _make_kline(["2026-02-01", "2026-05-01"])
    raw_repo = RawRepo(conn)

    result = add_report_features(kline, "000001", raw_repo=raw_repo)
    assert result["eps_consensus_cur"].iloc[0] == pytest.approx(1.0)
    assert result["eps_consensus_cur"].iloc[1] == pytest.approx(1.2)


def test_eps_revision_direction(conn):
    from src.features.report import add_report_features

    _insert_eps(conn, ["2026-01-01", "2026-04-01"], [1.0, 1.2])
    kline = _make_kline(["2026-05-01"])
    raw_repo = RawRepo(conn)

    result = add_report_features(kline, "000001", raw_repo=raw_repo)
    assert result["eps_revision"].iloc[0] == 1  # upward revision


# ── since param propagation ───────────────────────────────────────────────────

def test_since_filters_reports(conn):
    """since 参数真正生效：两条研报在同一 30 日窗口内，过滤后只剩一条。"""
    from src.features.report import add_report_features

    _insert_reports(conn, ["2024-01-05", "2024-01-10"])
    kline = _make_kline(["2024-01-20"])
    raw_repo = RawRepo(conn)

    # 不过滤：两条均在 30 日窗口内，count == 2
    result_all = add_report_features(kline, "000001", raw_repo=raw_repo)
    assert result_all["report_count_30d"].iloc[0] == 2

    # since=2024-01-08 → 只加载 2024-01-10 那条，count == 1
    result_filtered = add_report_features(kline, "000001", raw_repo=raw_repo, since=date(2024, 1, 8))
    assert result_filtered["report_count_30d"].iloc[0] == 1
