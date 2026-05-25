"""hsjday.zip → DuckDB kline 直接解析器

解析规则：
  - 仅处理路径匹配 `*/lday/[sh|sz|bj]<6位数字>.day` 的文件
  - 每条记录 32 字节，格式 <IIIIIfII
  - 过滤 close == 0 的异常行
  - 过滤 date < START_DATE 的历史记录
  - 每处理 500 只股票批量写入 DuckDB（减少事务开销）
"""
from __future__ import annotations

import re
import struct
import zipfile
import logging
from pathlib import Path

import pandas as pd

from config.settings import START_DATE
from src.collectors.base import CollectStats
from src.dal.raw_repo import RawRepo

logger = logging.getLogger(__name__)

_RECORD_FMT = "<IIIIIfII"
_RECORD_SIZE = struct.calcsize(_RECORD_FMT)   # 32 bytes
_DAY_PATTERN = re.compile(r'(?:sh|sz|bj)[/\\]lday[/\\](?:sh|sz|bj)(\d{6})\.day$', re.IGNORECASE)
_START_DATE_INT = int(START_DATE)             # e.g. 20210101
_BATCH_STOCKS = 500


def _parse_day_bytes(data: bytes, code: str) -> pd.DataFrame | None:
    """将单只股票的 .day 二进制内容解析为 DataFrame。"""
    n = len(data) // _RECORD_SIZE
    if n == 0:
        return None

    rows = []
    for i in range(n):
        off = i * _RECORD_SIZE
        date_int, open_i, high_i, low_i, close_i, amount, volume, _ = \
            struct.unpack_from(_RECORD_FMT, data, off)
        if date_int < _START_DATE_INT:
            continue
        close = close_i / 100.0
        if close <= 0:
            continue
        rows.append({
            "date":   pd.Timestamp(str(date_int)[:4] + "-"
                                   + str(date_int)[4:6] + "-"
                                   + str(date_int)[6:8]),
            "code":   code,
            "open":   open_i / 100.0,
            "high":   high_i / 100.0,
            "low":    low_i / 100.0,
            "close":  close,
            "amount": float(amount),
            "volume": int(volume),
        })

    if not rows:
        return None
    return pd.DataFrame(rows)


def ingest_kline(
    zip_path: str | Path,
    raw_repo: RawRepo | None = None,
    batch_size: int = _BATCH_STOCKS,
) -> CollectStats:
    """解析 hsjday.zip，将所有股票的 K 线数据批量写入 DuckDB kline 表。

    Args:
        zip_path:   hsjday.zip 的完整路径
        raw_repo:   RawRepo 实例；None 时从默认连接自动创建
        batch_size: 每批写入的股票数量（控制内存峰值）

    Returns:
        CollectStats: ok=成功股票数, fail=解析失败数, skipped=空文件数
    """
    if raw_repo is None:
        from src.dal.connection import get_db
        raw_repo = RawRepo(get_db())

    stats = CollectStats()
    batch_dfs: list[pd.DataFrame] = []

    def _flush():
        if batch_dfs:
            combined = pd.concat(batch_dfs, ignore_index=True)
            raw_repo.upsert_kline(combined)
            batch_dfs.clear()

    with zipfile.ZipFile(zip_path, 'r') as zf:
        names = zf.namelist()
        day_files = [(name, m.group(1))
                     for name in names
                     if (m := _DAY_PATTERN.search(name))]
        total = len(day_files)
        logger.info("hsjday.zip: 共 %d 只股票文件", total)

        for i, (name, code) in enumerate(day_files, 1):
            try:
                data = zf.read(name)
                df = _parse_day_bytes(data, code)
                if df is None:
                    stats.skipped += 1
                    continue
                batch_dfs.append(df)
                stats.ok += 1
            except Exception as exc:
                logger.debug("解析失败 %s: %s", name, exc)
                stats.fail += 1

            if len(batch_dfs) >= batch_size:
                _flush()
                logger.info("进度 %d/%d  %s", i, total, stats)

        _flush()   # 最后一批

    logger.info("ingest_kline 完成：%s", stats)
    return stats
