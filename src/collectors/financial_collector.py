"""财务指标采集器（DAL 版）"""
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


class FinancialCollector(BaseCollector):
    """tushare fina_indicator + income → DuckDB financial_indicator 表。

    按报告期（end_date）增量采集，季报/年报频率，每股增量通常 1-4 条。
    """

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

    def collect(self, codes: list[str] | None = None, since: date | None = None) -> CollectStats:
        """拉取财务指标，增量写入 DAL financial_indicator 表。

        增量键为 end_date（报告期）：只写入比 last_end_date 更新的季报/年报。
        """
        from src.data.tushare_fetchers import fetch_fina_indicator

        if codes is None:
            codes = []
        stats = CollectStats()
        consecutive_errors = 0

        for i, code in enumerate(tqdm(codes, desc="财务指标→DAL")):
            last_date = since if since is not None else self._meta_repo.get_last_date(
                "financial_indicator", code
            )

            try:
                df = fetch_fina_indicator(code, since=last_date)
            except Exception as exc:
                logger.debug("财务指标拉取异常 %s: %s", code, exc)
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
            if last_date is not None:
                df = df[df["end_date"] > pd.Timestamp(last_date)]
            if df.empty:
                stats.cached += 1
                time.sleep(self.delay)
                continue

            self._raw_repo.upsert_financial_indicator(df)
            self._meta_repo.set_last_date(
                "financial_indicator", code, df["end_date"].max().date(), len(df)
            )
            stats.ok += 1

            if (i + 1) % 200 == 0:
                logger.info("财务指标进度 %d/%d  %s", i + 1, len(codes), stats)

            time.sleep(self.delay)

        logger.info("财务指标拉取完成：%s", stats)
        return stats
