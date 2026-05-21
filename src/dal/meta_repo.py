"""MetaRepo：采集进度记录（collect_log 表）"""
from __future__ import annotations

from datetime import date, datetime

import duckdb


class MetaRepo:
    def __init__(self, conn: duckdb.DuckDBPyConnection | None = None) -> None:
        if conn is not None:
            self._conn = conn
        else:
            from src.dal.connection import get_db
            self._conn = get_db()

    def get_last_date(self, table_name: str, scope: str) -> date | None:
        """返回已采集到的最新日期，无记录时返回 None。"""
        row = self._conn.execute(
            "SELECT last_date FROM collect_log WHERE table_name = ? AND scope = ?",
            [table_name, scope],
        ).fetchone()
        if row is None or row[0] is None:
            return None
        val = row[0]
        return val if isinstance(val, date) else val.date()

    def set_last_date(
        self,
        table_name: str,
        scope: str,
        last_date: date,
        row_count: int = 0,
        status: str = "ok",
    ) -> None:
        """写入/更新进度记录（upsert）。"""
        self._conn.execute(
            """
            INSERT OR REPLACE INTO collect_log
                (table_name, scope, last_date, row_count, updated_at, status)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [table_name, scope, last_date, row_count, datetime.now(), status],
        )
