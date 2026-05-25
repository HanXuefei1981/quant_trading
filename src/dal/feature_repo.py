"""FeatureRepo：预计算特征表（features）读写"""
from __future__ import annotations

import re
from datetime import date

import duckdb
import pandas as pd

_SAFE_COL = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')


class FeatureRepo:
    def __init__(self, conn: duckdb.DuckDBPyConnection | None = None) -> None:
        if conn is not None:
            self._conn = conn
        else:
            from src.dal.connection import get_db
            self._conn = get_db()

    def upsert_features(self, df: pd.DataFrame) -> int:
        """写入特征数据；新列自动追加到 features 表 schema。"""
        existing = {row[0] for row in self._conn.execute("DESCRIBE features").fetchall()}
        for col in df.columns:
            if col not in existing:
                if not _SAFE_COL.match(col):
                    raise ValueError(f"Unsafe column name rejected: {col!r}")
                if col == "label":
                    dtype = "INTEGER"
                elif hasattr(df[col], "dtype") and (
                    pd.api.types.is_string_dtype(df[col])
                    or df[col].dtype == object
                ):
                    dtype = "VARCHAR"
                else:
                    dtype = "DOUBLE"
                self._conn.execute(f'ALTER TABLE features ADD COLUMN "{col}" {dtype}')
        self._conn.register("_feat_tmp", df)
        cols = ", ".join(f'"{c}"' for c in df.columns)
        non_pk = [c for c in df.columns if c not in ("date", "code")]
        if non_pk:
            assignments = ", ".join(f'"{c}" = EXCLUDED."{c}"' for c in non_pk)
            self._conn.execute(
                f"INSERT INTO features ({cols}) SELECT {cols} FROM _feat_tmp "
                f"ON CONFLICT (date, code) DO UPDATE SET {assignments}"
            )
        else:
            self._conn.execute(
                f"INSERT INTO features ({cols}) SELECT {cols} FROM _feat_tmp "
                f"ON CONFLICT (date, code) DO NOTHING"
            )
        self._conn.unregister("_feat_tmp")
        return len(df)

    def load_features(
        self,
        date_from: date,
        date_to: date,
        codes: list[str] | None = None,
    ) -> pd.DataFrame:
        """按时间范围（+可选股票列表）读取特征，供 trainer / backtest 使用。"""
        if codes is not None:
            placeholders = ", ".join("?" for _ in codes)
            return self._conn.execute(
                f"SELECT * FROM features WHERE date >= ? AND date <= ? "
                f"AND code IN ({placeholders}) ORDER BY date, code",
                [date_from, date_to, *codes],
            ).df()
        return self._conn.execute(
            "SELECT * FROM features WHERE date >= ? AND date <= ? ORDER BY date, code",
            [date_from, date_to],
        ).df()

    def get_feature_date_range(self) -> tuple[date, date] | None:
        """返回 features 表中最早/最晚日期；表为空时返回 None。"""
        row = self._conn.execute("SELECT MIN(date), MAX(date) FROM features").fetchone()
        if row is None or row[0] is None:
            return None
        d_min, d_max = row
        def to_date(v):
            return v if isinstance(v, date) else v.date()
        return (to_date(d_min), to_date(d_max))
