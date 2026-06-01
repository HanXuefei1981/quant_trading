"""龙虎榜全市场日报采集器（DAL 版）"""
from __future__ import annotations

import logging
from datetime import date, datetime

from src.collectors.base import BaseCollector, CollectStats
from src.dal.meta_repo import MetaRepo
from src.dal.raw_repo import RawRepo

logger = logging.getLogger(__name__)


class SignalCollector(BaseCollector):
    """tushare 龙虎榜日报 → DuckDB lhb 表（市场级）。"""

    def __init__(
        self,
        raw_repo: RawRepo | None = None,
        meta_repo: MetaRepo | None = None,
    ) -> None:
        if raw_repo is None:
            from src.dal.connection import get_db
            conn = get_db()
            raw_repo = RawRepo(conn)
            if meta_repo is None:
                meta_repo = MetaRepo(conn)
        if meta_repo is None:
            meta_repo = MetaRepo(raw_repo._conn)
        self._raw_repo = raw_repo
        self._meta_repo = meta_repo

    def _fetch_daily(self, target_date: date) -> pd.DataFrame | None:
        from src.data.tushare_fetchers import fetch_lhb_daily
        return fetch_lhb_daily(target_date)

    def collect(self, codes: list[str] | None = None, since: date | None = None) -> CollectStats:
        """拉取今日龙虎榜，写入 DAL lhb 表。codes 和 since 参数对市场级采集器均忽略。"""
        stats = CollectStats()
        today = datetime.now().date()

        last_date = self._meta_repo.get_last_date("lhb", "__market__")
        if last_date is not None and last_date >= today:
            stats.cached += 1
            return stats

        df = self._fetch_daily(today)
        if df is None:
            stats.fail += 1
            return stats

        self._raw_repo.upsert_lhb(df)
        self._meta_repo.set_last_date("lhb", "__market__", today, len(df))
        stats.ok += 1
        logger.info("龙虎榜 %s 已保存：%d 只股票", today, len(df))
        return stats

    def backfill(self, since: date) -> CollectStats:
        """回填 since 之后每个自然日的龙虎榜数据（跳过已有的日期）。

        非交易日接口返回空，自动跳过，不视为失败。
        """
        import time
        from datetime import timedelta

        stats = CollectStats()
        today = datetime.now().date()
        existing: set[date] = set()
        rows = self._raw_repo._conn.execute(
            "SELECT DISTINCT CAST(date AS DATE) FROM lhb WHERE date > ?", [since]
        ).fetchall()
        existing = {r[0] for r in rows}

        cur = since + timedelta(days=1)
        while cur <= today:
            if cur in existing:
                stats.cached += 1
                cur += timedelta(days=1)
                continue
            df = self._fetch_daily(cur)
            if df is not None and not df.empty:
                self._raw_repo.upsert_lhb(df)
                stats.ok += 1
                logger.info("龙虎榜 %s 补录：%d 条", cur, len(df))
            else:
                stats.skipped += 1  # 非交易日或无榜单
            cur += timedelta(days=1)
            time.sleep(0.5)

        self._meta_repo.set_last_date("lhb", "__market__", today, stats.ok)
        return stats
