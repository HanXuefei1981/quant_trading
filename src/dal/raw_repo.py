"""RawRepo：RAW 层 7 张表的 CRUD"""
from __future__ import annotations

from datetime import date

import duckdb
import pandas as pd


class RawRepo:
    def __init__(self, conn: duckdb.DuckDBPyConnection | None = None) -> None:
        if conn is not None:
            self._conn = conn
        else:
            from src.dal.connection import get_db
            self._conn = get_db()

    def _insert_or_replace(self, table: str, cols: list[str], df: pd.DataFrame) -> int:
        col_str = ", ".join(cols)
        self._conn.register("_tmp", df)
        self._conn.execute(f"INSERT OR REPLACE INTO {table} SELECT {col_str} FROM _tmp")
        self._conn.unregister("_tmp")
        return len(df)

    # ── kline ──────────────────────────────────────────────────────────────────

    def upsert_kline(self, df: pd.DataFrame) -> int:
        return self._insert_or_replace(
            "kline",
            ["date", "code", "open", "high", "low", "close", "amount", "volume"],
            df,
        )

    def load_kline(self, code: str, since: date | None = None) -> pd.DataFrame:
        if since is not None:
            return self._conn.execute(
                "SELECT date, code, open, high, low, close, amount, volume "
                "FROM kline WHERE code = ? AND date > ? ORDER BY date",
                [code, since],
            ).df()
        return self._conn.execute(
            "SELECT date, code, open, high, low, close, amount, volume "
            "FROM kline WHERE code = ? ORDER BY date",
            [code],
        ).df()

    # ── northbound ────────────────────────────────────────────────────────────

    def upsert_northbound(self, df: pd.DataFrame) -> int:
        return self._insert_or_replace(
            "northbound",
            ["date", "north_net_inflow", "hgt_yi", "sgt_yi"],
            df,
        )

    def load_northbound(self, since: date | None = None) -> pd.DataFrame:
        if since is not None:
            return self._conn.execute(
                "SELECT date, north_net_inflow, hgt_yi, sgt_yi "
                "FROM northbound WHERE date > ? ORDER BY date",
                [since],
            ).df()
        return self._conn.execute(
            "SELECT date, north_net_inflow, hgt_yi, sgt_yi FROM northbound ORDER BY date"
        ).df()

    # ── fundamentals ──────────────────────────────────────────────────────────

    def upsert_fundamentals(self, df: pd.DataFrame) -> int:
        return self._insert_or_replace(
            "fundamentals",
            ["date", "code", "pe_ttm", "pe_static", "pb", "ps", "pcf", "peg",
             "market_cap", "float_market_cap", "total_shares", "float_shares"],
            df,
        )

    def load_fundamentals(self, code: str) -> pd.DataFrame:
        return self._conn.execute(
            "SELECT * FROM fundamentals WHERE code = ? ORDER BY date", [code]
        ).df()

    # ── fund_flow ─────────────────────────────────────────────────────────────

    def upsert_fund_flow(self, df: pd.DataFrame) -> int:
        return self._insert_or_replace(
            "fund_flow",
            ["date", "code", "major_net_inflow", "major_net_pct"],
            df,
        )

    def load_fund_flow(self, code: str) -> pd.DataFrame:
        return self._conn.execute(
            "SELECT * FROM fund_flow WHERE code = ? ORDER BY date", [code]
        ).df()

    # ── lhb ───────────────────────────────────────────────────────────────────

    def upsert_lhb(self, df: pd.DataFrame) -> int:
        return self._insert_or_replace(
            "lhb",
            ["date", "code", "lhb_net_buy", "lhb_buy_amount", "lhb_sell_amount"],
            df,
        )

    def load_lhb(self, code: str, since: date | None = None) -> pd.DataFrame:
        if since is not None:
            return self._conn.execute(
                "SELECT * FROM lhb WHERE code = ? AND date > ? ORDER BY date",
                [code, since],
            ).df()
        return self._conn.execute(
            "SELECT * FROM lhb WHERE code = ? ORDER BY date", [code]
        ).df()

    # ── reports ───────────────────────────────────────────────────────────────

    def upsert_reports(self, df: pd.DataFrame) -> int:
        return self._insert_or_replace(
            "reports",
            ["date", "code", "institution", "rating"],
            df,
        )

    def load_reports(self, code: str) -> pd.DataFrame:
        return self._conn.execute(
            "SELECT * FROM reports WHERE code = ? ORDER BY date", [code]
        ).df()

    # ── eps_snapshot ──────────────────────────────────────────────────────────

    def upsert_eps_snapshot(self, df: pd.DataFrame) -> int:
        return self._insert_or_replace(
            "eps_snapshot",
            ["snapshot_date", "code", "eps_cur", "eps_next", "analyst_count"],
            df,
        )

    def load_eps_snapshots(self, code: str) -> pd.DataFrame:
        return self._conn.execute(
            "SELECT * FROM eps_snapshot WHERE code = ? ORDER BY snapshot_date", [code]
        ).df()
