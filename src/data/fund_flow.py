"""个股资金流向数据拉取与本地缓存（akshare 东方财富接口）

缓存路径：data/fund_flow/{code}.parquet

列（标准化后）：
  date               交易日（datetime64）
  major_net_inflow   主力净流入额（元，正=净买，负=净卖）
  major_net_pct      主力净流入占成交额百分比（%）
"""
import logging
import time
from pathlib import Path
from typing import Optional

import pandas as pd

from config.settings import DATA_DIR

logger = logging.getLogger(__name__)

FUND_FLOW_DIR = DATA_DIR / "fund_flow"

# 个股资金流向列映射（兼容 akshare 不同版本的列名）
_INFLOW_AMOUNT_CANDIDATES = [
    "主力净流入-净额",
    "主力净流入净额",
    "主力净流入",
    "主力净买额",
]
_INFLOW_PCT_CANDIDATES = [
    "主力净流入-净占比",
    "主力净流入净占比",
    "主力净占比",
]

# 北向资金列映射
_NORTH_AMOUNT_CANDIDATES = [
    "当日资金流向-净买入额",
    "当日成交净买额",
    "净买额",
    "北向资金净买额",
]


def _market_str(code: str) -> Optional[str]:
    """根据代码前缀返回 akshare 所需的 market 参数"""
    if code.startswith("6"):
        return "sh"
    elif code.startswith(("0", "3")):
        return "sz"
    return None


def _find_col(df: pd.DataFrame, candidates: list[str]) -> Optional[str]:
    """从候选列名列表中找到第一个存在于 df 中的列"""
    for c in candidates:
        if c in df.columns:
            return c
    return None


def _normalize_fund_flow(raw: pd.DataFrame) -> Optional[pd.DataFrame]:
    """标准化个股资金流向 DataFrame；缺关键列时返回 None"""
    date_col = _find_col(raw, ["日期", "date", "DATE"])
    amount_col = _find_col(raw, _INFLOW_AMOUNT_CANDIDATES)
    pct_col = _find_col(raw, _INFLOW_PCT_CANDIDATES)

    if date_col is None or amount_col is None:
        return None

    df = raw[[c for c in [date_col, amount_col, pct_col] if c]].copy()
    df = df.rename(columns={
        date_col: "date",
        amount_col: "major_net_inflow",
        **({pct_col: "major_net_pct"} if pct_col else {}),
    })
    df["date"] = pd.to_datetime(df["date"])
    df["major_net_inflow"] = pd.to_numeric(df["major_net_inflow"], errors="coerce")
    if "major_net_pct" in df.columns:
        df["major_net_pct"] = pd.to_numeric(df["major_net_pct"], errors="coerce")
    df = df.sort_values("date").drop_duplicates(subset="date", keep="last").reset_index(drop=True)
    return df


def fetch_fund_flow(code: str, use_cache: bool = True) -> Optional[pd.DataFrame]:
    """拉取单只股票的个股资金流向，优先读取本地缓存"""
    FUND_FLOW_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = FUND_FLOW_DIR / f"{code}.parquet"

    if use_cache and cache_path.exists():
        try:
            return pd.read_parquet(cache_path)
        except Exception as exc:
            logger.warning(f"资金流向缓存读取失败 {code}: {exc}，将重新拉取")

    mkt = _market_str(code)
    if mkt is None:
        return None

    try:
        import akshare as ak
        raw = ak.stock_individual_fund_flow(stock=code, market=mkt)
    except Exception as exc:
        logger.debug(f"个股资金流向拉取失败 {code}: {exc}")
        return None

    if raw is None or raw.empty:
        return None

    df = _normalize_fund_flow(raw)
    if df is None or df.empty:
        return None
    return df


def fetch_all_fund_flow(
    codes: list[str],
    delay: float = 0.5,
    use_cache: bool = True,
    max_errors: int = 100,
) -> dict[str, int]:
    """批量拉取个股资金流向。返回统计字典：{ok, fail, cached}"""
    FUND_FLOW_DIR.mkdir(parents=True, exist_ok=True)
    stats = {"ok": 0, "fail": 0, "cached": 0}
    consecutive_errors = 0

    for i, code in enumerate(codes):
        cache_path = FUND_FLOW_DIR / f"{code}.parquet"
        if use_cache and cache_path.exists():
            stats["cached"] += 1
            continue

        df = fetch_fund_flow(code, use_cache=False)
        if df is not None and not df.empty:
            stats["ok"] += 1
            consecutive_errors = 0
        else:
            stats["fail"] += 1
            consecutive_errors += 1
            if consecutive_errors >= max_errors:
                logger.error(f"连续 {max_errors} 次失败，疑似被限频，终止拉取")
                break

        if (i + 1) % 200 == 0:
            logger.info(
                f"资金流向拉取进度 {i + 1}/{len(codes)}  "
                f"新拉取 {stats['ok']}  缓存 {stats['cached']}  失败 {stats['fail']}"
            )

        time.sleep(delay)

    logger.info(
        f"资金流向拉取完成：新拉取 {stats['ok']}，缓存复用 {stats['cached']}，失败 {stats['fail']}"
    )
    return stats


def load_fund_flow(code: str) -> Optional[pd.DataFrame]:
    """从本地缓存加载个股资金流向（不触发网络请求）"""
    cache_path = FUND_FLOW_DIR / f"{code}.parquet"
    if not cache_path.exists():
        return None
    try:
        return pd.read_parquet(cache_path)
    except Exception:
        return None


