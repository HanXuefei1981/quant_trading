"""RawRepo：RAW 层 7 张表的 CRUD"""
from __future__ import annotations

from datetime import date

import duckdb
import pandas as pd
from pandas.api.types import is_extension_array_dtype


def _coerce_nullable_to_float(df: pd.DataFrame) -> pd.DataFrame:
    """将 pandas nullable 数值扩展类型（Int64/Float64/boolean）归一化为 numpy float64。

    DuckDB 的 .df() 在 BIGINT/INT 列含 NULL 时会返回 nullable Int64，其缺失值是
    pd.NA（而非 np.nan），下游 .values.astype(np.float64) 会因 float(pd.NA) 抛
    TypeError。此处在数据访问边界统一转为 float64（pd.NA → np.nan），
    使整条特征流水线只见到 numpy 浮点缺失值。datetime / 字符串列不受影响。
    """
    for col in df.columns:
        dtype = df[col].dtype
        if is_extension_array_dtype(dtype) and dtype.kind in ("i", "u", "f", "b"):
            df[col] = df[col].astype("float64")
    return df


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

    def upsert_stock_basic(self, df: pd.DataFrame) -> int:
        return self._insert_or_replace(
            "stock_basic",
            ["code", "name", "industry", "area", "market", "list_date"],
            df,
        )

    def load_fundamentals(self, code: str, since: date | None = None) -> pd.DataFrame:
        if since is not None:
            df = self._conn.execute(
                "SELECT * FROM fundamentals WHERE code = ? AND date > ? ORDER BY date",
                [code, since],
            ).df()
        else:
            df = self._conn.execute(
                "SELECT * FROM fundamentals WHERE code = ? ORDER BY date", [code]
            ).df()
        return _coerce_nullable_to_float(df)

    # ── fund_flow ─────────────────────────────────────────────────────────────

    def upsert_fund_flow(self, df: pd.DataFrame) -> int:
        return self._insert_or_replace(
            "fund_flow",
            ["date", "code", "major_net_inflow", "major_net_pct"],
            df,
        )

    def load_fund_flow(self, code: str, since: date | None = None) -> pd.DataFrame:
        if since is not None:
            return self._conn.execute(
                "SELECT * FROM fund_flow WHERE code = ? AND date > ? ORDER BY date",
                [code, since],
            ).df()
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

    def load_all_lhb(self, since: date | None = None) -> pd.DataFrame:
        """全市场龙虎榜，供 assembler 一次性加载后按 code 过滤。"""
        if since is not None:
            return self._conn.execute(
                "SELECT * FROM lhb WHERE date > ? ORDER BY date, code", [since]
            ).df()
        return self._conn.execute(
            "SELECT * FROM lhb ORDER BY date, code"
        ).df()

    # ── reports ───────────────────────────────────────────────────────────────

    def upsert_reports(self, df: pd.DataFrame) -> int:
        return self._insert_or_replace(
            "reports",
            ["date", "code", "institution", "rating"],
            df,
        )

    def load_reports(self, code: str, since: date | None = None) -> pd.DataFrame:
        if since is not None:
            return self._conn.execute(
                "SELECT * FROM reports WHERE code = ? AND date > ? ORDER BY date",
                [code, since],
            ).df()
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

    def load_eps_snapshots(self, code: str, since: date | None = None) -> pd.DataFrame:
        if since is not None:
            return self._conn.execute(
                "SELECT * FROM eps_snapshot WHERE code = ? AND snapshot_date > ? ORDER BY snapshot_date",
                [code, since],
            ).df()
        return self._conn.execute(
            "SELECT * FROM eps_snapshot WHERE code = ? ORDER BY snapshot_date", [code]
        ).df()

    # ── fundamentals_snapshot ─────────────────────────────────────────────────

    def upsert_fundamentals_snapshot(self, df: pd.DataFrame) -> int:
        return self._insert_or_replace(
            "fundamentals_snapshot",
            ["date", "code", "pe_ttm", "pe_static", "pb",
             "turnover_pct", "mcap_yi", "float_mcap_yi", "price"],
            df,
        )

    def load_fundamentals_snapshot(
        self, code: str, since: date | None = None
    ) -> pd.DataFrame:
        if since is not None:
            return self._conn.execute(
                "SELECT * FROM fundamentals_snapshot "
                "WHERE code = ? AND date > ? ORDER BY date",
                [code, since],
            ).df()
        return self._conn.execute(
            "SELECT * FROM fundamentals_snapshot WHERE code = ? ORDER BY date",
            [code],
        ).df()

    # ── financial_indicator ───────────────────────────────────────────────────

    _FI_COLS = [
        "code", "end_date", "ann_date", "eps", "diluted_eps",
        "revenue", "revenue_yoy", "net_profit", "net_profit_yoy",
        "roe", "roa", "gross_margin", "net_margin",
        "debt_ratio", "bps", "oc_ps", "free_cash_flow",
    ]

    def upsert_financial_indicator(self, df: pd.DataFrame) -> int:
        return self._insert_or_replace("financial_indicator", self._FI_COLS, df)

    def load_financial_indicator(
        self, code: str, since: date | None = None
    ) -> pd.DataFrame:
        if since is not None:
            return self._conn.execute(
                "SELECT * FROM financial_indicator "
                "WHERE code = ? AND end_date > ? ORDER BY end_date",
                [code, since],
            ).df()
        return self._conn.execute(
            "SELECT * FROM financial_indicator WHERE code = ? ORDER BY end_date",
            [code],
        ).df()
