"""特征组装器：从 DuckDB RawRepo 读取原始数据，输出特征工程结果到 data/processed/

设计原则：
  - 不调用任何网络接口（纯本地操作）
  - 数据来源完全由 src/collectors/ 负责写入，本模块只读
  - 替代 pipeline._run_tdx_pipeline() 成为 Phase 1 的核心逻辑
  - 读取路径已迁移到 DuckDB RawRepo（B2），写入路径保留 Parquet（待 B4 迁移）

增量模式（assemble_incremental）：
  - 从 meta_repo 读取上次特征截止日期 T
  - 每只股票只处理 date > T 的新行（取 300 天回看窗口保证滚动指标准确）
  - 跨截面步骤（标签 + 预处理）仅在新日期上运行
  - 完成后更新 watermark["features"]
"""
import logging
import numpy as np
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd
from tqdm import tqdm

from config.settings import PROCESSED_DIR, MIN_TRADE_DAYS
from src.dal.raw_repo import RawRepo
from src.dal.feature_repo import FeatureRepo
from src.features.indicators import add_all_features, get_feature_columns
from src.features.label import add_cross_sectional_label
from src.features.preprocessing import preprocess_features
from src.features.report import add_report_features
from src.features.signal import add_signal_features

logger = logging.getLogger(__name__)

_FUND_VALUE_COLS = [
    "pe_ttm", "pe_static", "pb", "ps", "pcf", "peg",
    "market_cap", "float_market_cap", "total_shares", "float_shares",
]
_FLOW_COLS = ["major_net_inflow", "major_net_pct"]


def _get_kline_codes(raw_repo: RawRepo) -> list[str]:
    """返回 DuckDB kline 表中所有已缓存的股票代码（去重排序）。"""
    return [r[0] for r in raw_repo._conn.execute(
        "SELECT DISTINCT code FROM kline ORDER BY code"
    ).fetchall()]


def _load_fundamentals(code: str, raw_repo: RawRepo, since: date | None = None) -> Optional[pd.DataFrame]:
    """加载基本面数据（从 DuckDB RawRepo）。"""
    df = raw_repo.load_fundamentals(code, since=since)
    if df.empty:
        return None
    df["date"] = pd.to_datetime(df["date"])
    return df


def _load_fund_flow(code: str, raw_repo: RawRepo, since: date | None = None) -> Optional[pd.DataFrame]:
    """加载个股资金流向数据（从 DuckDB RawRepo）。"""
    df = raw_repo.load_fund_flow(code, since=since)
    if df.empty:
        return None
    df["date"] = pd.to_datetime(df["date"])
    return df


def _load_northbound(raw_repo: RawRepo, since: date | None = None) -> Optional[pd.DataFrame]:
    """加载北向资金历史（从 DuckDB RawRepo）。"""
    df = raw_repo.load_northbound(since=since)
    if df.empty:
        return None
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date").reset_index(drop=True)


def _load_lhb_all(raw_repo: RawRepo, since: date | None = None) -> Optional[pd.DataFrame]:
    """加载全市场龙虎榜历史（从 DuckDB RawRepo）。"""
    df = raw_repo.load_all_lhb(since=since)
    if df.empty:
        return None
    df["date"] = pd.to_datetime(df["date"])
    return df.drop_duplicates(subset=["date", "code"]).sort_values("date")


def _merge_fundamentals(kline: pd.DataFrame, code: str, raw_repo: RawRepo, since: date | None = None) -> pd.DataFrame:
    """左连接基本面数据到 K 线，并计算换手率。"""
    fund_df = _load_fundamentals(code, raw_repo, since=since)
    if fund_df is None or fund_df.empty:
        return kline

    fund_df = fund_df.copy()
    fund_df["date"] = pd.to_datetime(fund_df["date"])
    cols = ["date"] + [c for c in _FUND_VALUE_COLS if c in fund_df.columns]
    merged = kline.merge(fund_df[cols], on="date", how="left")

    if "float_shares" in merged.columns and "turnover" not in merged.columns:
        with np.errstate(divide="ignore", invalid="ignore"):
            merged["turnover"] = merged["volume"] / (merged["float_shares"] + 1e-9) * 100.0
        merged.loc[~np.isfinite(merged["turnover"]), "turnover"] = np.nan

    return merged


def _merge_fund_flow(kline: pd.DataFrame, code: str, raw_repo: RawRepo, since: date | None = None) -> pd.DataFrame:
    """左连接个股资金流向到 K 线。"""
    flow_df = _load_fund_flow(code, raw_repo, since=since)
    if flow_df is None or flow_df.empty:
        return kline
    flow_df = flow_df.copy()
    flow_df["date"] = pd.to_datetime(flow_df["date"])
    cols = ["date"] + [c for c in _FLOW_COLS if c in flow_df.columns]
    return kline.merge(flow_df[cols], on="date", how="left")


def _merge_northbound(kline: pd.DataFrame, north_df: pd.DataFrame) -> pd.DataFrame:
    """左连接北向资金净买入到单只股票 K 线（宏观共享信号）。"""
    north_slim = north_df[["date", "north_net_inflow"]].copy()
    north_slim["date"] = pd.to_datetime(north_slim["date"])
    return kline.merge(north_slim, on="date", how="left")


def assemble(
    raw_repo=None,
    feature_repo=None,
    codes: Optional[list[str]] = None,
    sample_size: Optional[int] = None,
) -> pd.DataFrame:
    """从 DuckDB RawRepo 读取原始数据，完成特征工程，输出 market_features.parquet。

    Args:
        raw_repo:    RawRepo 实例；None 时自动从默认连接创建。
        feature_repo: FeatureRepo 实例（B4 写入迁移后使用）；None 时自动创建。
        codes:       指定处理的股票列表；None 表示处理所有已缓存 K 线。
        sample_size: 调试用，限制处理的股票数量。

    Returns:
        包含全部特征与标签的 DataFrame。
    """
    if raw_repo is None or feature_repo is None:
        from src.dal.connection import get_db
        conn = get_db()
        if raw_repo is None:
            from src.dal.raw_repo import RawRepo
            raw_repo = RawRepo(conn)
        if feature_repo is None:
            from src.dal.feature_repo import FeatureRepo
            feature_repo = FeatureRepo(conn)

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

    if codes is None:
        codes = _get_kline_codes(raw_repo)

    if not codes:
        raise RuntimeError(
            "kline 表无数据，请先运行: python main.py collect"
        )

    if sample_size:
        codes = codes[:sample_size]
        logger.info(f"调试模式：仅处理 {sample_size} 只股票")

    # 北向资金一次性加载（全市场共享）
    north_df = _load_northbound(raw_repo)
    if north_df is not None:
        logger.info(f"北向资金已加载：{len(north_df)} 条")
    else:
        logger.info("北向资金数据未找到，north_net_* 因子将全部为 NaN")

    lhb_df = _load_lhb_all(raw_repo)
    if lhb_df is not None:
        logger.info(f"龙虎榜历史已加载：{len(lhb_df)} 条，覆盖 {lhb_df['code'].nunique()} 只股票")
    else:
        logger.info("龙虎榜数据未找到，lhb_* 因子将全部为 NaN")

    logger.info(f"开始特征组装，共 {len(codes)} 只股票...")
    all_dfs: list[pd.DataFrame] = []
    skipped = 0
    fund_missing = 0
    flow_missing = 0

    for code in tqdm(codes, desc="特征组装"):
        processed_path = PROCESSED_DIR / f"{code}.parquet"

        # 读取 K 线（从 DuckDB RawRepo）
        raw = raw_repo.load_kline(code)
        if raw.empty or len(raw) < MIN_TRADE_DAYS:
            skipped += 1
            continue

        raw["date"] = pd.to_datetime(raw["date"])
        raw = raw.sort_values("date").reset_index(drop=True)

        # 合并基本面
        raw = _merge_fundamentals(raw, code, raw_repo)
        if "float_shares" not in raw.columns:
            fund_missing += 1

        # 合并个股资金流向
        raw = _merge_fund_flow(raw, code, raw_repo)
        if "major_net_inflow" not in raw.columns:
            flow_missing += 1

        # 特征工程（北向资金由 add_signal_features 内部 merge，勿在此重复合并）
        df = add_all_features(raw)
        df = add_report_features(df, code, raw_repo=raw_repo)
        df = add_signal_features(df, code, lhb_df=lhb_df, north_df=north_df)
        df["code"] = code
        df.to_parquet(processed_path, index=False)  # B4 将迁移此写入路径
        all_dfs.append(df)

    logger.info(
        f"有效股票：{len(all_dfs)} 只，跳过（数据不足/缺失）：{skipped} 只，"
        f"基本面缺失：{fund_missing} 只，资金流向缺失：{flow_missing} 只"
    )

    if not all_dfs:
        raise RuntimeError("没有可用数据，请检查 kline 表是否有数据")

    logger.info("合并数据集 + 截面标签 + 因子预处理...")
    combined = pd.concat(all_dfs, ignore_index=True)
    combined = combined.dropna(subset=["future_ret", "ret1"])
    combined = combined[np.isfinite(combined["future_ret"]) & np.isfinite(combined["ret1"])]
    combined = add_cross_sectional_label(combined)
    combined = combined.dropna(subset=["label"])
    combined = combined.sort_values(["date", "code"]).reset_index(drop=True)

    feature_cols = get_feature_columns(combined)
    logger.info(f"因子预处理：{len(feature_cols)} 个因子，MAD去极值 → 板块中性化 → Z-score")
    combined = preprocess_features(combined, feature_cols)

    out_path = PROCESSED_DIR / "market_features.parquet"
    combined.to_parquet(out_path, index=False)  # B4 将迁移此写入路径
    logger.info(f"全市场特征数据已保存至 {out_path}，共 {len(combined)} 行")
    return combined


def assemble_incremental(
    raw_repo=None,
    feature_repo=None,
    meta_repo=None,
) -> pd.DataFrame:
    """增量特征组装：只处理 meta_repo["features"] 之后的新交易日。

    步骤：
      1. 读 meta_repo 获取上次截止日期 T
      2. 对每只股票：读 kline 近 300 天 → merge 基本面/资金流向/北向 → 计算特征
         → 保留 date > T 的新行 → append 到 data/processed/{code}.parquet
      3. 汇总所有新行 → 跨截面标签 + 预处理 → append 到 market_features.parquet
      4. 更新 watermark（B5 迁移后由 meta_repo 写入）

    Returns:
        新增的 DataFrame（可能为空 DataFrame 如当日已是最新）。
    """
    if raw_repo is None or feature_repo is None:
        from src.dal.connection import get_db
        conn = get_db()
        if raw_repo is None:
            from src.dal.raw_repo import RawRepo
            raw_repo = RawRepo(conn)
        if feature_repo is None:
            from src.dal.feature_repo import FeatureRepo
            feature_repo = FeatureRepo(conn)
        if meta_repo is None:
            from src.dal.meta_repo import MetaRepo as _MetaRepo
            meta_repo = _MetaRepo(conn)

    # 读取 watermark：优先使用 meta_repo，向后兼容旧版 watermark 模块
    since: date | None = None
    if meta_repo is not None:
        since = meta_repo.get_last_date("features", "__market__")
    if since is None:
        # 向后兼容：尝试旧版 watermark 模块
        try:
            from src.data import watermark as wm
            since = wm.get_since("features")
        except Exception:
            pass

    if since is None:
        logger.info("无特征水位记录，执行全量组装（等同于 assemble()）")
        return assemble(raw_repo=raw_repo, feature_repo=feature_repo)

    since_ts = pd.Timestamp(since)
    lookback_date = since - timedelta(days=300)

    logger.info(f"增量模式：处理 date > {since} 的新数据（回看窗口起点: {lookback_date}）")

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    codes = _get_kline_codes(raw_repo)
    if not codes:
        raise RuntimeError("kline 表无数据，请先运行: python main.py collect")

    north_df = _load_northbound(raw_repo, since=lookback_date)
    lhb_df = _load_lhb_all(raw_repo, since=lookback_date)

    new_dfs: list[pd.DataFrame] = []
    skipped = 0

    for code in tqdm(codes, desc="增量特征组装"):
        # 从 DuckDB RawRepo 读取回看窗口 K 线
        raw = raw_repo.load_kline(code, since=lookback_date)
        if raw.empty:
            skipped += 1
            continue

        raw["date"] = pd.to_datetime(raw["date"])

        # 新数据检查：kline 最大日期必须 > since
        if raw["date"].max() <= since_ts:
            skipped += 1
            continue

        raw = raw.sort_values("date").reset_index(drop=True)
        if len(raw) < 20:
            skipped += 1
            continue

        raw = _merge_fundamentals(raw, code, raw_repo, since=lookback_date)
        raw = _merge_fund_flow(raw, code, raw_repo, since=lookback_date)

        df = add_all_features(raw)
        df = add_report_features(df, code, raw_repo=raw_repo, since=lookback_date)
        df = add_signal_features(df, code, lhb_df=lhb_df, north_df=north_df)
        df["code"] = code

        # 只保留真正新的行
        new_rows = df[df["date"] > since_ts].copy()
        if new_rows.empty:
            continue

        # Append 到个股缓存（B4 将迁移此写入路径）
        processed_path = PROCESSED_DIR / f"{code}.parquet"
        if processed_path.exists():
            try:
                existing = pd.read_parquet(processed_path)
                existing["date"] = pd.to_datetime(existing["date"])
                combined_stock = (
                    pd.concat([existing, new_rows], ignore_index=True)
                    .drop_duplicates(["date", "code"], keep="last")
                    .sort_values("date")
                    .reset_index(drop=True)
                )
            except Exception:
                combined_stock = new_rows
        else:
            combined_stock = new_rows
        combined_stock.to_parquet(processed_path, index=False)  # B4 将迁移

        new_dfs.append(new_rows)

    logger.info(f"增量：有新数据 {len(new_dfs)} 只股票，跳过 {skipped} 只")

    if not new_dfs:
        logger.info("无新数据，market_features.parquet 无需更新")
        return pd.DataFrame()

    combined = pd.concat(new_dfs, ignore_index=True)
    combined = combined.dropna(subset=["future_ret", "ret1"])
    combined = combined[np.isfinite(combined["future_ret"]) & np.isfinite(combined["ret1"])]
    if combined.empty:
        logger.info("新数据全部因 future_ret 为 NaN 被过滤（最后几个交易日无标签，属正常）")
        return pd.DataFrame()

    combined = add_cross_sectional_label(combined)
    combined = combined.dropna(subset=["label"])
    combined = combined.sort_values(["date", "code"]).reset_index(drop=True)

    feature_cols = get_feature_columns(combined)
    combined = preprocess_features(combined, feature_cols)

    # Append 到 market_features.parquet（B4 将迁移此写入路径）
    mf_path = PROCESSED_DIR / "market_features.parquet"
    if mf_path.exists():
        try:
            existing_mf = pd.read_parquet(mf_path)
            existing_mf["date"] = pd.to_datetime(existing_mf["date"])
            merged_mf = (
                pd.concat([existing_mf, combined], ignore_index=True)
                .drop_duplicates(["date", "code"], keep="last")
                .sort_values(["date", "code"])
                .reset_index(drop=True)
            )
        except Exception:
            merged_mf = combined
    else:
        merged_mf = combined
    merged_mf.to_parquet(mf_path, index=False)  # B4 将迁移

    new_max_date = combined["date"].max().date()
    # watermark 更新（B5 迁移后改为 meta_repo.set_last_date）
    try:
        from src.data import watermark as wm
        wm.update("features", new_max_date)
    except Exception:
        pass
    logger.info(
        f"增量完成：新增 {len(combined)} 行，"
        f"market_features.parquet 现共 {len(merged_mf)} 行，"
        f"水位更新至 {new_max_date}"
    )
    return combined


def assemble_inference() -> pd.DataFrame:
    """推断模式：加载个股最新截面特征（scan 专用，无需标签）。

    读取 data/processed/{code}.parquet，取每只股票最新一行，
    过滤到同一截面日期，做跨截面预处理后返回。
    """
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
    latest_date = combined["date"].max()
    combined = combined[combined["date"] == latest_date].reset_index(drop=True)
    logger.info(f"推断截面日期: {latest_date.date()}，共 {len(combined)} 只股票")

    feature_cols = get_feature_columns(combined)
    combined = preprocess_features(combined, feature_cols)
    return combined
