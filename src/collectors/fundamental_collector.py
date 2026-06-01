"""基本面采集器（DAL 版）"""
from __future__ import annotations

import logging
import time
from datetime import date

import pandas as pd
from tqdm import tqdm

from src.collectors.base import BaseCollector, CollectStats
from src.dal.meta_repo import MetaRepo
from src.dal.raw_repo import RawRepo

logger = logging.getLogger(__name__)


class FundamentalCollector(BaseCollector):
    """akshare 东方财富基本面数据 → DuckDB fundamentals 表。"""

    def __init__(
        self,
        raw_repo: RawRepo | None = None,
        meta_repo: MetaRepo | None = None,
        delay: float = 0.3,
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
        self.delay = delay

    def collect(self, codes: list[str] = [], since: date | None = None) -> CollectStats:
        """拉取基本面（tushare daily_basic），增量写入 DAL fundamentals 表。"""
        from src.data.tushare_fetchers import fetch_daily_basic

        stats = CollectStats()
        consecutive_errors = 0

        for i, code in enumerate(tqdm(codes, desc="基本面→DAL")):
            last_date = since if since is not None else self._meta_repo.get_last_date("fundamentals", code)

            try:
                df = fetch_daily_basic(code, since=last_date)
            except Exception as exc:
                logger.debug(f"基本面拉取异常 {code}: {exc}")
                df = None

            if df is None or df.empty:
                stats.fail += 1
                consecutive_errors += 1
                if consecutive_errors >= 100:
                    logger.error("连续 100 次失败，疑似被限频，终止拉取")
                    break
                time.sleep(self.delay)
                continue

            consecutive_errors = 0
            # tushare 已按 since 过滤，此处再次过滤确保严格增量（since 日当天数据排除）
            if last_date is not None:
                df = df[df["date"] > pd.Timestamp(last_date)]
            if df.empty:
                stats.cached += 1
                time.sleep(self.delay)
                continue

            self._raw_repo.upsert_fundamentals(df)
            self._meta_repo.set_last_date("fundamentals", code, df["date"].max().date(), len(df))
            stats.ok += 1

            if (i + 1) % 100 == 0:
                logger.info(f"基本面进度 {i + 1}/{len(codes)}  {stats}")

            time.sleep(self.delay)

        logger.info(f"基本面拉取完成：{stats}")
        return stats

    def collect_batch(self, since: date | None = None, delay: float = 0.0) -> CollectStats:
        """按交易日批量拉取全市场基本面（一次 API 调用 ≈ 全市场一天）。

        比逐股方式快约 5000×：5644 只 → 每个交易日 1 次调用。
        """
        from datetime import timedelta, date as date_cls
        from src.data.tushare_fetchers import fetch_fundamentals_by_date

        stats = CollectStats()

        if since is None:
            row = self._raw_repo._conn.execute(
                "SELECT MAX(CAST(date AS DATE)) FROM fundamentals"
            ).fetchone()
            since = row[0] if row and row[0] is not None else None

        today = date_cls.today()
        d = (since + timedelta(days=1)) if since else date_cls(2015, 1, 1)

        while d <= today:
            try:
                df = fetch_fundamentals_by_date(d)
            except Exception as exc:
                logger.warning("基本面批量 %s 拉取失败: %s", d.isoformat(), exc)
                stats.fail += 1
                d += timedelta(days=1)
                continue

            if df is not None and not df.empty:
                self._raw_repo.upsert_fundamentals(df)
                stats.ok += 1
                logger.info("基本面批量 %s: %d 只", d.isoformat(), len(df))
            else:
                stats.skipped += 1
                logger.debug("基本面 %s: 非交易日，跳过", d.isoformat())

            d += timedelta(days=1)
            if delay > 0:
                time.sleep(delay)

        logger.info("基本面批量增量完成：交易日=%d 跳过=%d 失败=%d", stats.ok, stats.skipped, stats.fail)
        return stats
