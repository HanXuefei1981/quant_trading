"""研报 + EPS 共识采集器（DAL 版）"""
from __future__ import annotations

import logging
import time
from datetime import date

import akshare as ak
import pandas as pd
from tqdm import tqdm

from src.collectors.base import BaseCollector, CollectStats
from src.dal.meta_repo import MetaRepo
from src.dal.raw_repo import RawRepo

logger = logging.getLogger(__name__)


class ReportCollector(BaseCollector):
    """研报列表 + EPS 共识 → DuckDB reports / eps_snapshot 表。

    mode='report': 东财研报列表
    mode='eps':    同花顺 EPS 共识快照
    """

    def __init__(
        self,
        raw_repo: RawRepo | None = None,
        meta_repo: MetaRepo | None = None,
        delay: float = 0.5,
        mode: str = "report",
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
        self.mode = mode

    def _fetch_report(self, code: str, since: date | None = None) -> pd.DataFrame | None:
        from src.data.tushare_fetchers import fetch_report_rc
        return fetch_report_rc(code, since=since)

    def _fetch_eps(self, code: str) -> pd.DataFrame | None:
        try:
            raw = ak.stock_profit_forecast_ths(symbol=code, indicator="预测年报每股收益")
        except Exception as exc:
            logger.warning("EPS 共识拉取失败 %s: %s", code, exc)
            return None
        if raw is None or raw.empty:
            return None
        today = pd.Timestamp.today().normalize()
        rows = raw.sort_values("年度").reset_index(drop=True)
        eps_cur = rows["均值"].iloc[0] if len(rows) >= 1 else float("nan")
        eps_next = rows["均值"].iloc[1] if len(rows) >= 2 else float("nan")
        raw_val = rows["预测机构数"].iloc[0] if len(rows) >= 1 else None
        analyst_count = int(raw_val) if pd.notna(raw_val) else 0
        return pd.DataFrame([{
            "snapshot_date": today,
            "code": code,
            "eps_cur": eps_cur,
            "eps_next": eps_next,
            "analyst_count": analyst_count,
        }])

    def collect(self, codes: list[str] | None = None, since: date | None = None) -> CollectStats:
        """批量采集研报或 EPS 共识并写入 DAL。"""
        if codes is None:
            codes = []
        stats = CollectStats()
        consecutive_errors = 0
        table = "reports" if self.mode == "report" else "eps_snapshot"

        for code in tqdm(codes, desc=f"研报采集[{self.mode}]"):
            last_date = since if since is not None else self._meta_repo.get_last_date(table, code)

            df = self._fetch_report(code, since=last_date) if self.mode == "report" else self._fetch_eps(code)

            if df is None or df.empty:
                stats.fail += 1
                consecutive_errors += 1
                if consecutive_errors >= 50:
                    logger.error("连续 50 次失败，终止采集")
                    break
                time.sleep(self.delay)
                continue

            consecutive_errors = 0
            if self.mode == "report":
                if last_date is not None:
                    df = df[df["date"] > pd.Timestamp(last_date)]
                if df.empty:
                    stats.cached += 1
                    time.sleep(self.delay)
                    continue
                self._raw_repo.upsert_reports(df)
                self._meta_repo.set_last_date("reports", code, df["date"].max().date(), len(df))
            else:
                self._raw_repo.upsert_eps_snapshot(df)
                self._meta_repo.set_last_date(
                    "eps_snapshot", code, df["snapshot_date"].max().date(), len(df)
                )

            stats.ok += 1
            time.sleep(self.delay)

        logger.info("研报采集[%s]完成：%s", self.mode, stats)
        return stats
