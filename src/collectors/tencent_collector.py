"""腾讯财经采集器（DAL 版）

腾讯财经 qt.gtimg.cn 接口特点：
- HTTP GET，GBK 编码，~分隔 88 字段，不封 IP
- 仅提供当前时点数据，无法拉取历史
- 每批最多 100 支，建议分批请求

写入表：fundamentals_snapshot
"""
from __future__ import annotations

import logging
import time
import urllib.request
from datetime import date, datetime

import pandas as pd
from tqdm import tqdm

from src.collectors.base import BaseCollector, CollectStats
from src.dal.meta_repo import MetaRepo
from src.dal.raw_repo import RawRepo

logger = logging.getLogger(__name__)

_BATCH_SIZE = 80
_TENCENT_URL = "https://qt.gtimg.cn/q="


def _get_prefix(code: str) -> str:
    if code.startswith(("6", "9")):
        return "sh"
    elif code.startswith("8"):
        return "bj"
    return "sz"


def _fetch_batch(codes: list[str]) -> dict[str, dict]:
    """批量拉取腾讯财经实时行情，返回 {code: {pe_ttm, pe_static, pb, ...}}。"""
    prefixed = [f"{_get_prefix(c)}{c}" for c in codes]
    url = _TENCENT_URL + ",".join(prefixed)
    req = urllib.request.Request(url)
    req.add_header("User-Agent", "Mozilla/5.0")
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        data = resp.read().decode("gbk")
    except OSError as exc:
        logger.warning("腾讯财经请求失败: %s", exc)
        return {}

    result = {}
    for line in data.strip().split(";"):
        if not line.strip() or "=" not in line or '"' not in line:
            continue
        key = line.split("=")[0].split("_")[-1]
        vals = line.split('"')[1].split("~")
        if len(vals) < 53:
            continue
        code = key[2:]

        def _f(idx: int) -> float | None:
            v = vals[idx] if idx < len(vals) else ""
            try:
                return float(v) if v else None
            except ValueError:
                return None

        result[code] = {
            "pe_ttm":        _f(39),
            "pe_static":     _f(52),
            "pb":            _f(46),
            "turnover_pct":  _f(38),
            "mcap_yi":       _f(44),
            "float_mcap_yi": _f(45),
            "price":         _f(3),
        }
    return result


class TencentCollector(BaseCollector):
    """腾讯财经每日 PE/PB/市值快照 → DuckDB fundamentals_snapshot 表。"""

    def __init__(
        self,
        raw_repo: RawRepo | None = None,
        meta_repo: MetaRepo | None = None,
        delay: float = 0.1,
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
        """批量拉取今日腾讯快照，写入 DAL fundamentals_snapshot 表。since 参数忽略。"""
        if codes is None:
            codes = []
        stats = CollectStats()
        today = datetime.now().date()
        errors = 0

        for i in range(0, len(codes), _BATCH_SIZE):
            batch_codes = codes[i:i + _BATCH_SIZE]

            all_cached = all(
                self._meta_repo.get_last_date("fundamentals_snapshot", code) == today
                for code in batch_codes
            )
            if all_cached:
                stats.cached += len(batch_codes)
                continue

            rows = _fetch_batch(batch_codes)
            if not rows:
                errors += len(batch_codes)
                stats.fail += len(batch_codes)
                if errors >= 100:
                    logger.error("腾讯财经连续失败 %d 次，终止", errors)
                    break
                continue

            errors = 0
            batch_rows = []
            for code in batch_codes:
                if code not in rows:
                    stats.fail += 1
                    continue
                row = dict(rows[code])
                row["date"] = pd.Timestamp(today)
                row["code"] = code
                batch_rows.append(row)

            if batch_rows:
                df = pd.DataFrame(batch_rows)
                self._raw_repo.upsert_fundamentals_snapshot(df)
                for code in df["code"].tolist():
                    self._meta_repo.set_last_date("fundamentals_snapshot", code, today, 1)
                stats.ok += len(batch_rows)

            if (i // _BATCH_SIZE + 1) % 10 == 0:
                logger.info("腾讯快照进度 %d/%d  %s", i + len(batch_codes), len(codes), stats)

            time.sleep(self.delay)

        logger.info("腾讯快照采集完成：%s", stats)
        return stats
