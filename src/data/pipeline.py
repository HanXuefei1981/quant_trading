"""数据处理流水线：拉取 → 特征工程 → 落地 Processed"""
import logging
import numpy as np
from pathlib import Path

import pandas as pd
from tqdm import tqdm

from config.settings import PROCESSED_DIR, RAW_DIR, MIN_TRADE_DAYS, TDX_VIPDOC_DIR
from src.features.indicators import add_all_features

logger = logging.getLogger(__name__)


def run_data_pipeline(
    sample_size: int = None,
    delay: float = 0.3,
    use_cache: bool = True,
    use_tdx: bool = None,
) -> pd.DataFrame:
    """
    完整数据流水线。
    use_tdx: True=读本地通达信数据，False=akshare网络拉取，None=自动判断（有本地数据优先用本地）
    sample_size: 调试时限制股票数量
    """
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    RAW_DIR.mkdir(parents=True, exist_ok=True)

    # 自动判断数据源
    if use_tdx is None:
        use_tdx = TDX_VIPDOC_DIR.exists()

    if use_tdx:
        return _run_tdx_pipeline(sample_size, use_cache)
    else:
        return _run_akshare_pipeline(sample_size, delay, use_cache)


def _run_tdx_pipeline(sample_size: int = None, use_cache: bool = True) -> pd.DataFrame:
    """通达信本地数据流水线（快速，无需网络）"""
    from src.data.tdx_reader import get_all_tdx_codes, read_day_file

    logger.info("Step 1: 扫描通达信本地数据目录")
    stock_df = get_all_tdx_codes()
    all_codes = stock_df["code"].tolist()

    if sample_size:
        all_codes = all_codes[:sample_size]
        logger.info(f"调试模式：仅处理 {sample_size} 只股票")

    logger.info(f"Step 2+3: 读取本地文件 + 过滤 + 特征工程（共 {len(all_codes)} 只）")
    all_dfs = []
    skipped = 0

    for code in tqdm(all_codes, desc="读取TDX数据"):
        processed_path = PROCESSED_DIR / f"{code}.parquet"
        if use_cache and processed_path.exists():
            df = pd.read_parquet(processed_path)
            all_dfs.append(df)
            continue

        raw = read_day_file(code)
        if raw is None or len(raw) < MIN_TRADE_DAYS:
            skipped += 1
            continue

        df = add_all_features(raw)
        df["code"] = code
        df.to_parquet(processed_path, index=False)
        all_dfs.append(df)

    logger.info(f"有效股票：{len(all_dfs)} 只，跳过（数据不足）：{skipped} 只")

    if not all_dfs:
        raise RuntimeError("没有可用数据，请检查通达信数据目录路径")

    logger.info("Step 4: 合并数据集")
    combined = pd.concat(all_dfs, ignore_index=True)
    combined = combined.dropna(subset=["label", "ret1", "future_ret"])
    combined = combined[np.isfinite(combined["future_ret"]) & np.isfinite(combined["ret1"])]
    combined = combined.sort_values(["date", "code"]).reset_index(drop=True)

    out_path = PROCESSED_DIR / "market_features.parquet"
    combined.to_parquet(out_path, index=False)
    logger.info(f"全市场特征数据已保存至 {out_path}，共 {len(combined)} 行")
    return combined


def _run_akshare_pipeline(
    sample_size: int = None,
    delay: float = 0.3,
    use_cache: bool = True,
) -> pd.DataFrame:
    """akshare 网络拉取流水线（备用）"""
    from src.data.fetcher import (
        get_all_stock_codes, fetch_all_stocks,
        filter_valid_stocks, get_stock_kline,
    )

    logger.info("Step 1: 获取股票列表（akshare）")
    stock_df = get_all_stock_codes()
    code_name_map = dict(zip(stock_df["code"], stock_df["name"]))
    all_codes = stock_df["code"].tolist()

    if sample_size:
        all_codes = all_codes[:sample_size]
        logger.info(f"调试模式：仅处理 {sample_size} 只股票")

    logger.info(f"Step 2: 拉取 K 线数据（共 {len(all_codes)} 只）")
    fetch_all_stocks(all_codes, delay=delay)

    logger.info("Step 3: 过滤无效标的")
    valid_codes = filter_valid_stocks(
        all_codes, min_trade_days=MIN_TRADE_DAYS, stock_names=code_name_map,
    )
    logger.info(f"有效股票数量：{len(valid_codes)}")

    logger.info("Step 4: 特征工程")
    all_dfs = []
    for code in tqdm(valid_codes, desc="特征工程"):
        processed_path = PROCESSED_DIR / f"{code}.parquet"
        if use_cache and processed_path.exists():
            df = pd.read_parquet(processed_path)
        else:
            df = get_stock_kline(code)
            if df is None or len(df) < MIN_TRADE_DAYS:
                continue
            df = add_all_features(df)
            df["code"] = code
            df.to_parquet(processed_path, index=False)
        all_dfs.append(df)

    if not all_dfs:
        raise RuntimeError("没有可用数据，请检查网络或数据源")

    logger.info("Step 5: 合并数据集")
    combined = pd.concat(all_dfs, ignore_index=True)
    combined = combined.dropna(subset=["label", "ret1", "future_ret"])
    combined = combined[np.isfinite(combined["future_ret"]) & np.isfinite(combined["ret1"])]
    combined = combined.sort_values(["date", "code"]).reset_index(drop=True)

    out_path = PROCESSED_DIR / "market_features.parquet"
    combined.to_parquet(out_path, index=False)
    logger.info(f"全市场特征数据已保存至 {out_path}，共 {len(combined)} 行")
    return combined


def load_processed_data() -> pd.DataFrame:
    """加载已处理的全市场特征数据"""
    path = PROCESSED_DIR / "market_features.parquet"
    if not path.exists():
        raise FileNotFoundError(f"找不到 {path}，请先运行 run_data_pipeline()")
    return pd.read_parquet(path)
