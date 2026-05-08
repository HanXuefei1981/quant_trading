"""A 股历史 K 线数据拉取与本地缓存"""
import io
import time
import logging
import warnings
from pathlib import Path
from typing import Optional

import akshare as ak
import pandas as pd
import requests
import urllib3

from config.settings import RAW_DIR, START_DATE, END_DATE, ADJUST

logger = logging.getLogger(__name__)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def _get_sh_codes() -> pd.DataFrame:
    df = ak.stock_info_sh_name_code(symbol="主板A股")
    df2 = ak.stock_info_sh_name_code(symbol="科创板")
    combined = pd.concat([df, df2], ignore_index=True)
    result = combined[["证券代码", "证券简称"]].copy()
    result.columns = ["code", "name"]
    result["code"] = result["code"].astype(str).str.zfill(6)
    return result


def _get_sz_codes() -> pd.DataFrame:
    url = "https://www.szse.cn/api/report/ShowReport"
    params = {"SHOWTYPE": "xlsx", "CATALOGID": "1110", "TABKEY": "tab1", "random": "0.1"}
    try:
        r = requests.get(url, params=params, timeout=20, verify=False)
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            df = pd.read_excel(io.BytesIO(r.content))
        code_col = [c for c in df.columns if "代码" in str(c)][0]
        name_col = [c for c in df.columns if "简称" in str(c) or "名称" in str(c)][0]
        result = df[[code_col, name_col]].copy()
        result.columns = ["code", "name"]
        result["code"] = result["code"].astype(str).str.zfill(6).str.replace(r"\D", "", regex=True).str.zfill(6)
        result = result[result["code"].str.match(r"^\d{6}$")]
        return result.reset_index(drop=True)
    except Exception as e:
        logger.warning(f"深交所接口失败，跳过深市股票: {e}")
        return pd.DataFrame(columns=["code", "name"])


def get_all_stock_codes() -> pd.DataFrame:
    """获取全 A 股股票列表（上交所 + 深交所）"""
    sh = _get_sh_codes()
    sz = _get_sz_codes()
    combined = pd.concat([sh, sz], ignore_index=True)
    combined = combined.drop_duplicates(subset="code").reset_index(drop=True)
    logger.info(f"股票列表: 沪市 {len(sh)} 只, 深市 {len(sz)} 只, 合计 {len(combined)} 只")
    return combined


def get_stock_kline(
    code: str,
    start_date: str = START_DATE,
    end_date: str = END_DATE,
    adjust: str = ADJUST,
    use_cache: bool = True,
) -> Optional[pd.DataFrame]:
    """
    拉取单只股票日 K 线，优先读取本地 Parquet 缓存。
    返回列：date, open, high, low, close, volume, amount, turnover
    """
    cache_path = RAW_DIR / f"{code}.parquet"

    if use_cache and cache_path.exists():
        df = pd.read_parquet(cache_path)
        # 检查缓存是否覆盖所需日期范围
        if (
            str(df["date"].min())[:10].replace("-", "") <= start_date
            and str(df["date"].max())[:10].replace("-", "") >= end_date
        ):
            mask = (df["date"] >= pd.to_datetime(start_date)) & (
                df["date"] <= pd.to_datetime(end_date)
            )
            return df[mask].reset_index(drop=True)

    try:
        raw = ak.stock_zh_a_hist(
            symbol=code,
            period="daily",
            start_date=start_date,
            end_date=end_date,
            adjust=adjust,
        )
    except Exception as e:
        logger.warning(f"拉取 {code} 失败: {e}")
        return None

    if raw is None or raw.empty:
        return None

    df = _normalize_kline(raw)
    df.to_parquet(cache_path, index=False)
    return df


def _normalize_kline(df: pd.DataFrame) -> pd.DataFrame:
    """统一列名和数据类型"""
    col_map = {
        "日期": "date",
        "开盘": "open",
        "最高": "high",
        "最低": "low",
        "收盘": "close",
        "成交量": "volume",
        "成交额": "amount",
        "换手率": "turnover",
    }
    df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})
    df["date"] = pd.to_datetime(df["date"])
    for col in ["open", "high", "low", "close", "volume", "amount", "turnover"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.sort_values("date").reset_index(drop=True)
    return df


def fetch_all_stocks(
    codes: list[str],
    delay: float = 0.3,
    max_errors: int = 50,
) -> dict[str, pd.DataFrame]:
    """
    批量拉取股票 K 线数据。
    delay: 每次请求间隔（秒），避免触发限频
    """
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    results = {}
    errors = 0

    for i, code in enumerate(codes):
        df = get_stock_kline(code)
        if df is not None and len(df) > 0:
            results[code] = df
        else:
            errors += 1
            if errors >= max_errors:
                logger.error(f"错误数超过 {max_errors}，终止拉取")
                break

        if i % 100 == 0:
            logger.info(f"进度 {i}/{len(codes)}，已成功 {len(results)} 只")

        time.sleep(delay)

    logger.info(f"拉取完成：{len(results)}/{len(codes)} 只成功")
    return results


def filter_valid_stocks(
    codes: list[str],
    min_trade_days: int = 250,
    exclude_st: bool = True,
    stock_names: Optional[dict] = None,
) -> list[str]:
    """过滤不合格标的：交易日不足、ST、新股"""
    valid = []
    for code in codes:
        cache_path = RAW_DIR / f"{code}.parquet"
        if not cache_path.exists():
            continue
        try:
            df = pd.read_parquet(cache_path)
        except Exception:
            continue
        if len(df) < min_trade_days:
            continue
        if exclude_st and stock_names:
            name = stock_names.get(code, "")
            if "ST" in name.upper():
                continue
        valid.append(code)
    return valid
