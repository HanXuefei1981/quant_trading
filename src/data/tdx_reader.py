"""通达信本地日线数据读取器（.day 二进制格式）"""
import os
import struct
import logging
from pathlib import Path

import numpy as np
import pandas as pd

from config.settings import TDX_VIPDOC_DIR, START_DATE, END_DATE

logger = logging.getLogger(__name__)

# 每条记录 32 字节：date(I) open(I) high(I) low(I) close(I) amount(f) volume(I) reserved(I)
_RECORD_FMT = "<IIIIIfII"
_RECORD_SIZE = struct.calcsize(_RECORD_FMT)   # 32


def _market_prefix(code: str) -> tuple[str, str]:
    """根据股票代码判断市场前缀和子目录"""
    if code.startswith("6"):
        return "sh", "sh"
    elif code.startswith(("0", "3")):
        return "sz", "sz"
    elif code.startswith("8"):
        return "bj", "bj"
    else:
        return None, None


def read_day_file(code: str) -> pd.DataFrame | None:
    """读取单只股票的通达信日线文件，返回 DataFrame，格式与 akshare 一致"""
    mkt, prefix = _market_prefix(code)
    if mkt is None:
        return None

    path = TDX_VIPDOC_DIR / mkt / "lday" / f"{prefix}{code}.day"
    if not path.exists():
        return None

    raw = path.read_bytes()
    n = len(raw) // _RECORD_SIZE
    if n == 0:
        return None

    records = struct.iter_unpack(_RECORD_FMT, raw[:n * _RECORD_SIZE])
    rows = []
    for date_int, open_, high, low, close, amount, volume, _ in records:
        rows.append((date_int, open_ / 100.0, high / 100.0,
                     low / 100.0, close / 100.0, float(amount), int(volume)))

    df = pd.DataFrame(rows, columns=["date_int", "open", "high", "low", "close", "amount", "volume"])
    df["date"] = pd.to_datetime(df["date_int"].astype(str), format="%Y%m%d")
    df = df.drop(columns=["date_int"])

    # 过滤日期范围
    start = pd.to_datetime(START_DATE, format="%Y%m%d")
    end = pd.to_datetime(END_DATE, format="%Y%m%d")
    df = df[(df["date"] >= start) & (df["date"] <= end)].reset_index(drop=True)

    if df.empty or (df["close"] == 0).all():
        return None

    # 过滤价格为 0 的异常行
    df = df[df["close"] > 0].reset_index(drop=True)
    return df


def get_max_trading_date() -> "date | None":
    """扫描所有 TDX .day 文件，返回最新的交易日期（date 对象）"""
    from datetime import date as date_type
    max_date: "date_type | None" = None
    for mkt in ("sh", "sz", "bj"):
        lday_dir = TDX_VIPDOC_DIR / mkt / "lday"
        if not lday_dir.exists():
            continue
        for fname in os.listdir(lday_dir):
            if not fname.endswith(".day"):
                continue
            fpath = lday_dir / fname
            try:
                size = fpath.stat().st_size
                if size < _RECORD_SIZE:
                    continue
                with open(fpath, "rb") as f:
                    f.seek(size - _RECORD_SIZE)
                    raw_last = f.read(_RECORD_SIZE)
                dr = struct.unpack_from("<I", raw_last, 0)[0]
                y, m, d = dr // 10000, (dr % 10000) // 100, dr % 100
                if not (2000 <= y <= 2100 and 1 <= m <= 12 and 1 <= d <= 31):
                    continue
                candidate = date_type(y, m, d)
                if max_date is None or candidate > max_date:
                    max_date = candidate
            except Exception:
                continue
    return max_date


def is_active_in_period(code: str) -> bool:
    """只读 .day 文件最后 32 字节，判断该股票在 START_DATE 之后是否还有交易记录。

    用于在 fetch-fund 等场景前快速过滤退市 / 长期停牌的旧股票，
    避免把它们送进昂贵的网络接口。
    """
    mkt, prefix = _market_prefix(code)
    if mkt is None:
        return False
    path = TDX_VIPDOC_DIR / mkt / "lday" / f"{prefix}{code}.day"
    if not path.exists():
        return False
    size = path.stat().st_size
    if size < _RECORD_SIZE:
        return False
    try:
        with open(path, "rb") as f:
            f.seek(size - _RECORD_SIZE)
            raw_last = f.read(_RECORD_SIZE)
        date_int = struct.unpack_from("<I", raw_last, 0)[0]
        return date_int >= int(START_DATE)
    except Exception:
        return False


def get_active_tdx_codes() -> list[str]:
    """返回在 START_DATE 之后仍有交易记录的 A 股代码（排除退市 / 长停牌）"""
    all_codes = get_all_tdx_codes()["code"].tolist()
    active = [c for c in all_codes if is_active_in_period(c)]
    logger.info(f"TDX 有效股票筛选：{len(active)} / {len(all_codes)}（已剔除退市与长停牌）")
    return active


def get_all_tdx_codes() -> pd.DataFrame:
    """扫描 vipdoc 目录获取全部 A 股代码列表"""
    codes = []
    def _is_a_share(f: str) -> bool:
        if not f.endswith(".day"):
            return False
        if f.startswith("sh6"):        # 沪市主板 + 科创板
            return True
        if f.startswith("sz0"):        # 深市主板 000/001/002/003
            return True
        if f.startswith("sz3"):        # 创业板 300/301，排除指数 399
            return f[2:5] in ("300", "301")
        if f.startswith("bj8"):        # 北交所
            return True
        return False

    for mkt, prefix, pattern in [
        ("sh", "sh", _is_a_share),
        ("sz", "sz", _is_a_share),
        ("bj", "bj", _is_a_share),
    ]:
        lday_dir = TDX_VIPDOC_DIR / mkt / "lday"
        if not lday_dir.exists():
            continue
        for fname in os.listdir(lday_dir):
            if pattern(fname):
                code = fname[2:-4]   # 去掉前缀和 .day
                codes.append({"code": code, "name": ""})

    df = pd.DataFrame(codes).drop_duplicates(subset="code").reset_index(drop=True)
    logger.info(f"通达信本地数据：共找到 {len(df)} 只 A 股")
    return df
