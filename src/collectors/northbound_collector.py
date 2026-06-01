"""北向资金采集器（DAL 版）"""
from __future__ import annotations

import logging
from datetime import date

import pandas as pd

from src.collectors.base import BaseCollector, CollectStats
from src.dal.meta_repo import MetaRepo
from src.dal.raw_repo import RawRepo

logger = logging.getLogger(__name__)


class NorthboundCollector(BaseCollector):
    """tushare moneyflow_hsgt → DuckDB northbound 表（市场级，T+1）。

    按 date 增量采集，首次调用拉取自 2014-11-17（沪港通开通日）至今的全量历史。
    """

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

    def collect(self, codes: list[str] | None = None, since: date | None = None) -> CollectStats:
        """拉取北向资金历史，增量写入 DAL northbound 表。

        codes 参数对市场级采集器忽略。
        since=None 时从 meta_repo 读取上次写入日期作为增量起点。
        """
        from src.data.tushare_fetchers import fetch_northbound_hsgt

        stats = CollectStats()
        last_date = since if since is not None else self._meta_repo.get_last_date(
            "northbound", "__market__"
        )

        df = fetch_northbound_hsgt(since=last_date)
        if df is None or df.empty:
            stats.fail += 1
            logger.warning("北向资金拉取失败（tushare moneyflow_hsgt）")
            return stats

        if last_date is not None:
            df = df[df["date"] > pd.Timestamp(last_date)]
        if df.empty:
            stats.cached += 1
            logger.info("北向资金已是最新（T+1，无新数据）")
            return stats

        self._raw_repo.upsert_northbound(df)
        self._meta_repo.set_last_date(
            "northbound", "__market__", df["date"].max().date(), len(df)
        )
        stats.ok += 1
        logger.info(
            "北向资金已更新：%d 条（%s ~ %s，T+1）",
            len(df), df["date"].min().date(), df["date"].max().date(),
        )
        return stats
