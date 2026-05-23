"""个股主力资金流向采集器（DAL 版）"""
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


class FundFlowCollector(BaseCollector):
    """akshare 东方财富资金流向 → DuckDB fund_flow 表。"""

    def __init__(
        self,
        raw_repo: RawRepo | None = None,
        meta_repo: MetaRepo | None = None,
        delay: float = 0.5,
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
        """拉取资金流向，增量写入 DAL fund_flow 表。"""
        if codes is None:
            codes = []
        from src.data.fund_flow import fetch_fund_flow

        stats = CollectStats()
        consecutive_errors = 0

        for i, code in enumerate(tqdm(codes, desc="资金流向→DAL")):
            last_date = since if since is not None else self._meta_repo.get_last_date("fund_flow", code)

            try:
                df = fetch_fund_flow(code, use_cache=False)
            except Exception as exc:
                logger.warning(f"资金流向拉取异常 {code}: {exc}")
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
                df = df[df["date"] > pd.Timestamp(last_date)]
            if df.empty:
                stats.cached += 1
                time.sleep(self.delay)
                continue

            df = df.copy()
            df["code"] = code
            self._raw_repo.upsert_fund_flow(df)
            self._meta_repo.set_last_date("fund_flow", code, df["date"].max().date(), len(df))
            stats.ok += 1

            if (i + 1) % 200 == 0:
                logger.info(f"资金流向进度 {i + 1}/{len(codes)}  {stats}")

            time.sleep(self.delay)

        logger.info(f"资金流向拉取完成：{stats}")
        return stats
