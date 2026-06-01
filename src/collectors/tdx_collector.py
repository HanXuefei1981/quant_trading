"""通达信 K 线采集器（DAL 版）

两种数据来源：
  - 本地 .day 二进制文件：collect() 读取 TDX 文件写入 DAL
  - mootdx TCP 网络：collect_mootdx() 每日增量写入 DAL
"""
from __future__ import annotations

import logging
from datetime import date

import pandas as pd
from tqdm import tqdm

from src.collectors.base import BaseCollector, CollectStats
from src.dal.meta_repo import MetaRepo
from src.dal.raw_repo import RawRepo

logger = logging.getLogger(__name__)

_MARKET_SH = 1
_MARKET_SZ = 0
_MARKET_BJ = 2


def _get_market(code: str) -> int:
    if code.startswith(("6", "9")):
        return _MARKET_SH
    if code.startswith("8"):
        return _MARKET_BJ
    return _MARKET_SZ


class TDXCollector(BaseCollector):
    """通达信本地 .day 文件 → DuckDB kline 表。"""

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

    def collect(self, codes: list[str] = [], since: date | None = None) -> CollectStats:
        """读取 TDX 本地 .day 文件，增量写入 DAL kline 表。"""
        from src.data.tdx_reader import read_day_file

        stats = CollectStats()
        for code in tqdm(codes, desc="TDX→DAL"):
            last_date = since if since is not None else self._meta_repo.get_last_date("kline", code)
            raw = read_day_file(code)
            if raw is None or raw.empty:
                stats.skipped += 1
                continue

            raw["date"] = pd.to_datetime(raw["date"])
            raw["code"] = code
            df = raw.sort_values("date").reset_index(drop=True)

            if last_date is not None:
                df = df[df["date"] > pd.Timestamp(last_date)]
            if df.empty:
                stats.cached += 1
                continue

            self._raw_repo.upsert_kline(df)
            self._meta_repo.set_last_date("kline", code, df["date"].max().date(), len(df))
            stats.ok += 1

        logger.info(f"TDX 转换完成：{stats}")
        return stats

    def collect_mootdx(
        self,
        codes: list[str],
        since: date,
        delay: float = 0.0,
    ) -> CollectStats:
        """通过 mootdx TCP 拉取增量 K 线，写入 DAL kline 表。"""
        import time
        from mootdx.quotes import Quotes

        calendar_days = (date.today() - since).days
        offset = max(int(calendar_days * 5 / 7) + 10, 15)
        since_ts = pd.Timestamp(since)
        logger.info(f"mootdx 增量拉取：since={since}，offset={offset} bars，共 {len(codes)} 只")

        try:
            client = Quotes.factory(market="std")
        except Exception as exc:
            logger.error(f"mootdx 连接失败: {exc}")
            stats = CollectStats()
            stats.fail = len(codes)
            return stats

        stats = CollectStats()
        for code in tqdm(codes, desc="mootdx增量K线"):
            try:
                market = _get_market(code)
                raw = client.bars(symbol=code, category=4, market=market, offset=offset)
                if raw is None or raw.empty:
                    stats.skipped += 1
                    continue

                df = raw.copy()
                df["date"] = pd.to_datetime(df.index).normalize()
                df = df.reset_index(drop=True)
                keep = ["date", "open", "high", "low", "close", "amount", "volume"]
                df = df[[c for c in keep if c in df.columns]].copy()
                if "volume" in df.columns:
                    df["volume"] = df["volume"] * 100
                df["code"] = code

                new_rows = df[df["date"] > since_ts]
                if new_rows.empty:
                    stats.skipped += 1
                    continue

                self._raw_repo.upsert_kline(new_rows)
                self._meta_repo.set_last_date(
                    "kline", code, new_rows["date"].max().date(), len(new_rows)
                )
                stats.ok += 1

            except Exception as exc:
                logger.debug(f"mootdx 拉取失败 {code}: {exc}")
                stats.fail += 1

            if delay > 0:
                time.sleep(delay)

        logger.info(f"mootdx 增量完成：{stats}")
        return stats

    def collect_tushare_daily(
        self,
        codes: list[str],
        since: date,
        delay: float = 0.1,
    ) -> CollectStats:
        """通过 tushare pro.daily 拉取增量 K 线，写入 DAL kline 表。

        替代 collect_mootdx，无需本地 TCP 连接，支持全市场批量拉取。
        """
        import time
        from src.data.tushare_fetchers import fetch_kline_daily

        since_ts = pd.Timestamp(since)
        stats = CollectStats()

        for i, code in enumerate(tqdm(codes, desc="tushare增量K线")):
            try:
                df = fetch_kline_daily(code, since=since)
            except Exception as exc:
                logger.debug("tushare kline 拉取失败 %s: %s", code, exc)
                stats.fail += 1
                continue

            if df is None or df.empty:
                stats.skipped += 1
                continue

            new_rows = df[df["date"] > since_ts]
            if new_rows.empty:
                stats.skipped += 1
                continue

            self._raw_repo.upsert_kline(new_rows)
            self._meta_repo.set_last_date(
                "kline", code, new_rows["date"].max().date(), len(new_rows)
            )
            stats.ok += 1

            if (i + 1) % 500 == 0:
                logger.info("tushare K线进度 %d/%d  %s", i + 1, len(codes), stats)

            if delay > 0:
                time.sleep(delay)

        logger.info("tushare K线增量完成：%s", stats)
        return stats

    def collect_tushare_daily_batch(self, since: date) -> CollectStats:
        """按交易日批量拉取全市场 K 线（一次 API 调用 ≈ 全市场一天）。

        水位控制：从 since+1 日迭代到今天，非交易日 tushare 返回空表自动跳过。
        比逐股方式快约 5000×：5644 只 → 每个交易日 1 次调用。
        """
        from datetime import timedelta, date as date_cls
        from src.data.tushare_fetchers import fetch_kline_by_date

        stats = CollectStats()
        today = date_cls.today()
        d = since + timedelta(days=1)

        while d <= today:
            df = fetch_kline_by_date(d)
            if df is not None and not df.empty:
                self._raw_repo.upsert_kline(df)
                stats.ok += 1
                logger.info("K线批量 %s: %d 只", d.isoformat(), len(df))
            else:
                stats.skipped += 1
                logger.debug("K线 %s: 非交易日，跳过", d.isoformat())
            d += timedelta(days=1)

        logger.info("K线批量增量完成：交易日=%d 跳过=%d 失败=%d", stats.ok, stats.skipped, stats.fail)
        return stats
