"""数据处理流水线：拉取 → 特征工程 → 落地 Processed"""
import logging
import numpy as np
from pathlib import Path

import pandas as pd
from tqdm import tqdm

from config.settings import PROCESSED_DIR, RAW_DIR, MIN_TRADE_DAYS, TDX_VIPDOC_DIR
from src.features.indicators import add_all_features, get_feature_columns
from src.features.label import add_cross_sectional_label
from src.features.preprocessing import preprocess_features

logger = logging.getLogger(__name__)


_FUND_VALUE_COLS = [
    "pe_ttm", "pe_static", "pb", "ps", "pcf", "peg",
    "market_cap", "float_market_cap", "total_shares", "float_shares",
]

_FLOW_COLS = ["major_net_inflow", "major_net_pct"]


def _merge_fundamentals(kline: pd.DataFrame, code: str, loader) -> pd.DataFrame:
    """把本地缓存的基本面按 date 左连接到 K 线，并用 volume/float_shares 算换手率。

    loader: 函数接收 code，返回基本面 DataFrame 或 None（避免在 import 时引入 akshare）
    """
    fund_df = loader(code)
    if fund_df is None or fund_df.empty:
        return kline

    fund_df = fund_df.copy()
    fund_df["date"] = pd.to_datetime(fund_df["date"])
    merged = kline.merge(
        fund_df[["date"] + [c for c in _FUND_VALUE_COLS if c in fund_df.columns]],
        on="date", how="left",
    )

    # 用 volume / float_shares 推算换手率（百分比单位，与 akshare 习惯一致）
    if "float_shares" in merged.columns and "turnover" not in merged.columns:
        with np.errstate(divide="ignore", invalid="ignore"):
            merged["turnover"] = merged["volume"] / (merged["float_shares"] + 1e-9) * 100.0
        merged.loc[~np.isfinite(merged["turnover"]), "turnover"] = np.nan

    return merged


def _merge_fund_flow(kline: pd.DataFrame, code: str) -> pd.DataFrame:
    """把个股资金流向按 date 左连接到 K 线（无数据时原样返回）"""
    from src.data.fund_flow import load_fund_flow
    flow_df = load_fund_flow(code)
    if flow_df is None or flow_df.empty:
        return kline
    flow_df = flow_df.copy()
    flow_df["date"] = pd.to_datetime(flow_df["date"])
    return kline.merge(
        flow_df[["date"] + [c for c in _FLOW_COLS if c in flow_df.columns]],
        on="date", how="left",
    )


def _merge_northbound(kline: pd.DataFrame, north_df: pd.DataFrame) -> pd.DataFrame:
    """把北向净买入按 date 左连接到单只股票 K 线（宏观共享信号）"""
    north_slim = north_df[["date", "north_net_inflow"]].copy()
    north_slim["date"] = pd.to_datetime(north_slim["date"])
    return kline.merge(north_slim, on="date", how="left")


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
    """通达信本地数据流水线（K 线本地读取 + 基本面缓存合并）"""
    from src.data.tdx_reader import get_all_tdx_codes, read_day_file
    from src.data.fundamentals import load_fundamentals

    logger.info("Step 1: 扫描通达信本地数据目录")
    stock_df = get_all_tdx_codes()
    all_codes = stock_df["code"].tolist()

    if sample_size:
        all_codes = all_codes[:sample_size]
        logger.info(f"调试模式：仅处理 {sample_size} 只股票")

    # 北向资金一次性加载（全市场共享，按日期 join）
    from src.data.fund_flow import load_northbound_flow
    north_df = load_northbound_flow()
    if north_df is not None:
        logger.info(f"北向资金数据已加载：{len(north_df)} 条")
    else:
        logger.info("北向资金数据未找到，north_net_* 因子将全部为 NaN（可运行 fetch-flow 拉取）")

    logger.info(f"Step 2+3: 读取本地文件 + 合并基本面 + 合并资金流向 + 特征工程（共 {len(all_codes)} 只）")
    all_dfs = []
    skipped = 0
    fund_missing = 0
    flow_missing = 0

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

        raw = _merge_fundamentals(raw, code, load_fundamentals)
        if "float_shares" not in raw.columns:
            fund_missing += 1

        raw = _merge_fund_flow(raw, code)
        if "major_net_inflow" not in raw.columns:
            flow_missing += 1

        if north_df is not None:
            raw = _merge_northbound(raw, north_df)

        df = add_all_features(raw)
        df["code"] = code
        df.to_parquet(processed_path, index=False)
        all_dfs.append(df)

    logger.info(
        f"有效股票：{len(all_dfs)} 只，跳过（数据不足）：{skipped} 只，"
        f"基本面缺失：{fund_missing} 只，资金流向缺失：{flow_missing} 只"
    )

    if not all_dfs:
        raise RuntimeError("没有可用数据，请检查通达信数据目录路径")

    logger.info("Step 4: 合并数据集 + 截面标签 + 因子预处理")
    combined = pd.concat(all_dfs, ignore_index=True)
    combined = combined.dropna(subset=["future_ret", "ret1"])
    combined = combined[np.isfinite(combined["future_ret"]) & np.isfinite(combined["ret1"])]
    combined = add_cross_sectional_label(combined)
    combined = combined.dropna(subset=["label"])
    combined = combined.sort_values(["date", "code"]).reset_index(drop=True)
    feature_cols = get_feature_columns(combined)
    logger.info(f"  因子预处理：{len(feature_cols)} 个因子，MAD去极值 → 板块中性化 → Z-score")
    combined = preprocess_features(combined, feature_cols)

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

    logger.info("Step 5: 合并数据集 + 截面标签")
    combined = pd.concat(all_dfs, ignore_index=True)
    combined = combined.dropna(subset=["future_ret", "ret1"])
    combined = combined[np.isfinite(combined["future_ret"]) & np.isfinite(combined["ret1"])]
    combined = add_cross_sectional_label(combined)
    combined = combined.dropna(subset=["label"])
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


def load_inference_data() -> pd.DataFrame:
    """从个股缓存直接加载最新截面特征（推断模式，无需标签）。

    scan 专用：取每只股票的最新一行（可能含 future_ret=NaN），
    合并为同一日期的截面后做跨截面预处理，供模型直接推断信号。
    比 load_processed_data() 多包含最近 5 个无标签交易日的数据。
    """
    from src.features.indicators import get_feature_columns
    from src.features.preprocessing import preprocess_features

    parquet_files = [
        f for f in PROCESSED_DIR.glob("*.parquet")
        if f.stem != "market_features"
    ]
    if not parquet_files:
        raise FileNotFoundError("未找到个股缓存文件，请先运行 Phase 1")

    latest_rows = []
    for fpath in parquet_files:
        try:
            df = pd.read_parquet(fpath)
            if not df.empty:
                latest_rows.append(df.iloc[[-1]])
        except Exception:
            continue

    if not latest_rows:
        raise RuntimeError("无法读取任何个股缓存")

    combined = pd.concat(latest_rows, ignore_index=True)

    # 只保留最新日期截面，确保跨截面比较一致（停牌股自动排除）
    latest_date = combined["date"].max()
    combined = combined[combined["date"] == latest_date].reset_index(drop=True)
    logger.info(f"推断截面日期: {latest_date.date()}，共 {len(combined)} 只股票")

    feature_cols = get_feature_columns(combined)
    combined = preprocess_features(combined, feature_cols)
    return combined
