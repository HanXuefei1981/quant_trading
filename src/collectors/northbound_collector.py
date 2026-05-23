"""北向资金采集器（DAL 版）"""
from __future__ import annotations

import logging
from datetime import date, datetime

import pandas as pd
import requests

from src.collectors.base import BaseCollector, CollectStats
from src.dal.meta_repo import MetaRepo
from src.dal.raw_repo import RawRepo

logger = logging.getLogger(__name__)

_HSGT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/117.0.0.0 Safari/537.36"
    ),
    "Host": "data.hexin.cn",
    "Referer": "https://data.hexin.cn/",
}

_HSGT_URL = "https://data.hexin.cn/market/hsgtApi/method/dayChart/"


def _fetch_today_snapshot() -> dict | None:
    """从同花顺拉取今日实时北向流向，返回 {date, north_net_inflow, hgt_yi, sgt_yi} 或 None。"""
    try:
        r = requests.get(_HSGT_URL, headers=_HSGT_HEADERS, timeout=10)
        r.raise_for_status()
    except requests.exceptions.RequestException as exc:
        logger.warning("同花顺 hsgtApi 网络请求失败: %s", exc)
        return None

    try:
        d = r.json()
    except ValueError as exc:
        logger.warning("同花顺 hsgtApi 响应非 JSON: %s", exc)
        return None

    times = d.get("time", [])
    hgt = d.get("hgt", [])
    sgt = d.get("sgt", [])

    if not times:
        logger.warning("同花顺 hsgtApi 返回空数据")
        return None

    n = len(times)
    hgt_vals = hgt[:n] + [None] * max(0, n - len(hgt))
    sgt_vals = sgt[:n] + [None] * max(0, n - len(sgt))

    last_hgt = next((v for v in reversed(hgt_vals) if v is not None), None)
    last_sgt = next((v for v in reversed(sgt_vals) if v is not None), None)

    if last_hgt is None and last_sgt is None:
        return None

    hgt_val = float(last_hgt) if last_hgt is not None else 0.0
    sgt_val = float(last_sgt) if last_sgt is not None else 0.0
    return {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "north_net_inflow": (hgt_val + sgt_val) * 1e8,
        "hgt_yi": hgt_val,
        "sgt_yi": sgt_val,
    }


class NorthboundCollector(BaseCollector):
    """同花顺北向资金日度快照 → DuckDB northbound 表（市场级）。"""

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
        """拉取今日北向快照，写入 DAL northbound 表。codes 和 since 参数对市场级采集器均忽略。"""
        stats = CollectStats()
        today = datetime.now().date()

        last_date = self._meta_repo.get_last_date("northbound", "__market__")
        if last_date is not None and last_date >= today:
            stats.cached += 1
            return stats

        snapshot = _fetch_today_snapshot()
        if snapshot is None:
            stats.fail += 1
            logger.warning("北向资金今日快照拉取失败")
            return stats

        df = pd.DataFrame([{
            "date": pd.Timestamp(snapshot["date"]),
            "north_net_inflow": snapshot["north_net_inflow"],
            "hgt_yi": snapshot["hgt_yi"],
            "sgt_yi": snapshot["sgt_yi"],
        }])
        self._raw_repo.upsert_northbound(df)
        self._meta_repo.set_last_date("northbound", "__market__", today, 1)
        stats.ok += 1
        logger.info("北向资金已更新：%s", today)
        return stats
