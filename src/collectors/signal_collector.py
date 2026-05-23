"""龙虎榜全市场日报采集器（DAL 版）"""
from __future__ import annotations

import logging
from datetime import date, datetime

import akshare as ak
import pandas as pd

from src.collectors.base import BaseCollector, CollectStats
from src.dal.meta_repo import MetaRepo
from src.dal.raw_repo import RawRepo

logger = logging.getLogger(__name__)

_COL_MAP = {
    "代码": "code",
    "股票代码": "code",
    "上榜日期": "date",
    "龙虎榜净买额": "lhb_net_buy",
    "买入额合计": "lhb_buy_amount",
    "卖出额合计": "lhb_sell_amount",
}


class SignalCollector(BaseCollector):
    """东财龙虎榜日报 → DuckDB lhb 表（市场级）。

    ⚠️ akshare.stock_lhb_detail_em 在 VPN 开启时有 SSL 错误，需关 VPN 后运行。
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

    def _fetch_daily(self, target_date: date) -> pd.DataFrame | None:
        date_str = target_date.strftime("%Y%m%d")
        try:
            raw = ak.stock_lhb_detail_em(start_date=date_str, end_date=date_str)
        except Exception as exc:
            logger.warning("龙虎榜拉取失败 %s: %s", date_str, exc)
            return None

        if raw is None or raw.empty:
            return None

        df = raw.rename(columns={k: v for k, v in _COL_MAP.items() if k in raw.columns})
        if "code" not in df.columns:
            logger.warning("龙虎榜字段不全 %s，已有列: %s", date_str, list(df.columns))
            return None

        if "date" not in df.columns:
            df["date"] = pd.to_datetime(target_date)

        df["code"] = df["code"].astype(str).str.zfill(6)
        df["lhb_net_buy"] = pd.to_numeric(df.get("lhb_net_buy", 0), errors="coerce")
        df["lhb_buy_amount"] = pd.to_numeric(df.get("lhb_buy_amount", 0), errors="coerce")
        df["lhb_sell_amount"] = pd.to_numeric(df.get("lhb_sell_amount", 0), errors="coerce")

        keep = ["date", "code", "lhb_net_buy", "lhb_buy_amount", "lhb_sell_amount"]
        df = df[[c for c in keep if c in df.columns]]
        df = df.dropna(subset=["code"]).drop_duplicates(subset=["code"])
        return df if not df.empty else None

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
