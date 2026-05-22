"""A 股基本面数据拉取与本地缓存（akshare 东方财富估值接口）

数据列：
    date              交易日（datetime64）
    pe_ttm            滚动市盈率
    pe_static         静态市盈率
    pb                市净率
    ps                市销率
    pcf               市现率
    peg               PEG 值
    market_cap        总市值（元）
    float_market_cap  流通市值（元）
    total_shares      总股本（股）
    float_shares      流通股本（股）

缓存路径：data/fundamentals/{code}.parquet
"""
import logging
import time
from pathlib import Path
from typing import Optional

import pandas as pd

from config.settings import DATA_DIR

logger = logging.getLogger(__name__)

FUNDAMENTALS_DIR = DATA_DIR / "fundamentals"


_COL_MAP = {
    "数据日期": "date",
    "PE(TTM)": "pe_ttm",
    "PE(静)": "pe_static",
    "市净率": "pb",
    "市销率": "ps",
    "市现率": "pcf",
    "PEG值": "peg",
    "总市值": "market_cap",
    "流通市值": "float_market_cap",
    "总股本": "total_shares",
    "流通股本": "float_shares",
}


def _normalize_fundamentals(raw: pd.DataFrame) -> pd.DataFrame:
    """统一列名与数据类型"""
    df = raw.rename(columns={k: v for k, v in _COL_MAP.items() if k in raw.columns}).copy()
    keep_cols = [c for c in _COL_MAP.values() if c in df.columns]
    df = df[keep_cols]

    df["date"] = pd.to_datetime(df["date"])
    for col in keep_cols:
        if col != "date":
            df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.sort_values("date").drop_duplicates(subset="date", keep="last").reset_index(drop=True)
    return df


def fetch_fundamentals(code: str, use_cache: bool = True) -> Optional[pd.DataFrame]:
    """拉取单只股票的历史基本面，优先读取本地缓存。

    use_cache=False 时强制从网络拉取并覆盖缓存。
    """
    cache_path = FUNDAMENTALS_DIR / f"{code}.parquet"
    if use_cache and cache_path.exists():
        try:
            return pd.read_parquet(cache_path)
        except Exception as exc:
            logger.warning(f"基本面缓存读取失败 {code}: {exc}，将重新拉取")

    try:
        import akshare as ak
        raw = ak.stock_value_em(symbol=code)
    except Exception as exc:
        logger.warning(f"akshare 基本面拉取失败 {code}: {exc}")
        return None

    if raw is None or raw.empty:
        return None

    df = _normalize_fundamentals(raw)
    return df


def fetch_all_fundamentals(
    codes: list[str],
    delay: float = 0.3,
    use_cache: bool = True,
    max_errors: int = 100,
) -> dict[str, int]:
    """批量拉取基本面数据。返回统计字典：{ok, fail, cached}"""
    FUNDAMENTALS_DIR.mkdir(parents=True, exist_ok=True)
    stats = {"ok": 0, "fail": 0, "cached": 0}
    consecutive_errors = 0

    for i, code in enumerate(codes):
        cache_path = FUNDAMENTALS_DIR / f"{code}.parquet"
        if use_cache and cache_path.exists():
            stats["cached"] += 1
            continue

        df = fetch_fundamentals(code, use_cache=False)
        if df is not None and not df.empty:
            stats["ok"] += 1
            consecutive_errors = 0
        else:
            stats["fail"] += 1
            consecutive_errors += 1
            if consecutive_errors >= max_errors:
                logger.error(f"连续 {max_errors} 次失败，疑似被限频，终止拉取")
                break

        if (i + 1) % 100 == 0:
            logger.info(
                f"基本面拉取进度 {i + 1}/{len(codes)}  "
                f"新拉取 {stats['ok']}  缓存 {stats['cached']}  失败 {stats['fail']}"
            )

        time.sleep(delay)

    logger.info(
        f"基本面拉取完成：新拉取 {stats['ok']}，缓存复用 {stats['cached']}，失败 {stats['fail']}"
    )
    return stats


def load_fundamentals(code: str) -> Optional[pd.DataFrame]:
    """从本地缓存加载基本面数据（不触发网络请求）"""
    cache_path = FUNDAMENTALS_DIR / f"{code}.parquet"
    if not cache_path.exists():
        return None
    try:
        return pd.read_parquet(cache_path)
    except Exception:
        return None
