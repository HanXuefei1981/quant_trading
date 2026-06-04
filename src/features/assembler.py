"""特征组装器：从 DuckDB RawRepo 读取原始数据，完成特征工程后写入 FeatureRepo。

设计原则：
  - 不调用任何网络接口（纯本地操作）
  - 数据来源完全由 src/collectors/ 负责写入，本模块只读
  - 替代 pipeline._run_tdx_pipeline() 成为 Phase 1 的核心逻辑
  - 读取路径已迁移到 DuckDB RawRepo（B2），写入路径已迁移到 DuckDB FeatureRepo（B4）

增量模式（assemble_incremental）：
  - 从 meta_repo 读取上次特征截止日期 T
  - 每只股票只处理 date > T 的新行（取 300 天回看窗口保证滚动指标准确）
  - 跨截面步骤（标签 + 预处理）仅在新日期上运行
  - 完成后通过 meta_repo.set_last_date 更新水位
"""
import logging
import numpy as np
from datetime import date, timedelta

import pandas as pd
from tqdm import tqdm

from config.settings import MIN_TRADE_DAYS
from src.dal.raw_repo import RawRepo
from src.dal.feature_repo import FeatureRepo
from src.features.indicators import add_all_features, get_feature_columns
from src.features.label import add_cross_sectional_label
from src.features.preprocessing import get_segment, preprocess_features
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


def _load_fundamentals(code: str, raw_repo: RawRepo, since: date | None = None) -> pd.DataFrame | None:
    """加载基本面数据（从 DuckDB RawRepo）。"""
    df = raw_repo.load_fundamentals(code, since=since)
    if df.empty:
        return None
    df["date"] = pd.to_datetime(df["date"])
    return df


def _load_fund_flow(code: str, raw_repo: RawRepo, since: date | None = None) -> pd.DataFrame | None:
    """加载个股资金流向数据（从 DuckDB RawRepo）。"""
    df = raw_repo.load_fund_flow(code, since=since)
    if df.empty:
        return None
    df["date"] = pd.to_datetime(df["date"])
    return df


def _load_northbound(raw_repo: RawRepo, since: date | None = None) -> pd.DataFrame | None:
    """加载北向资金历史（从 DuckDB RawRepo）。"""
    df = raw_repo.load_northbound(since=since)
    if df.empty:
        return None
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date").reset_index(drop=True)


def _load_lhb_all(raw_repo: RawRepo, since: date | None = None) -> pd.DataFrame | None:
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


def _preprocess_and_write(combined, feature_cols, feature_repo) -> int:
    """逐日流式预处理并写表，避免一次性物化整张特征矩阵。

    MAD 去极值 / 板块中性化 / Z-score 全是**按日截面**算子，因此
    ``preprocess_features(整表)`` 与逐日切片分别处理后再合并逐字节等价。
    本函数据此把整表拆成单日切片（~MB 级）依次预处理并 upsert，峰值内存
    从「combined + result 副本(5.3GB) + feat 副本(3.1GB)」降为「combined + 单日切片」，
    消除约 8GB 重复分配，避免 8GB 机器上的 swap thrash。

    Returns:
        实际写入的行数。
    """
    total = 0
    for _, day_slice in combined.groupby("date", sort=True):
        processed = preprocess_features(day_slice, feature_cols)
        total += feature_repo.upsert_features(processed)
    return total


def assemble(
    raw_repo=None,
    feature_repo=None,
    codes: list[str] | None = None,
    sample_size: int | None = None,
) -> pd.DataFrame:
    """从 DuckDB RawRepo 读取原始数据，完成特征工程，写入 FeatureRepo (features 表)。

    Args:
        raw_repo:    RawRepo 实例；None 时自动从默认连接创建。
        feature_repo: FeatureRepo 实例；None 时自动创建。
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
        all_dfs.append(df)

    logger.info(
        f"有效股票：{len(all_dfs)} 只，跳过（数据不足/缺失）：{skipped} 只，"
        f"基本面缺失：{fund_missing} 只，资金流向缺失：{flow_missing} 只"
    )

    if not all_dfs:
        raise RuntimeError("没有可用数据，请检查 kline 表是否有数据")

    logger.info("合并数据集 + 截面标签 + 因子预处理...")
    combined = pd.concat(all_dfs, ignore_index=True)
    all_dfs.clear()  # 释放每股 DataFrame 列表（~5.3GB），降低 concat 后的并存峰值
    # 仅按 ret1（过去收益）剔除无效行；future_ret 为 NaN 的最近 5 个交易日要**保留**——
    # 它们特征有效、仅缺 5 日后标签，写入表供 scan 推断读取（推断截面=最新 K 线日，
    # 不再滞后 5 日）；训练/回测侧按 label 非空过滤。future_ret 为 inf 的坏值仍剔除。
    combined = combined[np.isfinite(combined["ret1"])]
    combined = combined[~np.isinf(combined["future_ret"])]
    combined = add_cross_sectional_label(combined)  # 无 future_ret 的行 label=NaN，保留
    combined = combined.sort_values(["date", "code"]).reset_index(drop=True)

    feature_cols = get_feature_columns(combined)
    logger.info(f"因子预处理：{len(feature_cols)} 个因子，MAD去极值 → 板块中性化 → Z-score（逐日流式写表）")
    # 逐日流式预处理 + 写表：避免一次性物化整张 ~3.3GB 特征矩阵与 5.3GB result 副本，
    # 把 8GB 机器上 ~13.7GB 的虚拟内存峰值压到 combined 本身，消除 swap thrash。
    total = _preprocess_and_write(combined, feature_cols, feature_repo)
    logger.info(f"全市场特征已写入 FeatureRepo，共 {total} 行")

    # 返回值仅用于上层日志的形状/日期/股票数统计（trainer/scan 均从表读取，不依赖此处特征值）。
    # 补 segment 列以保持与历史返回结构一致（向量化映射，仅 ~数千唯一 code，开销极小）。
    seg_map = {c: get_segment(c) for c in combined["code"].unique()}
    combined["segment"] = combined["code"].map(seg_map)
    return combined



def _write_features_watermark(df: "pd.DataFrame", meta_repo) -> "date | None":
    """把 features 水位写为 df 中最后【有标签】日（无标签则用最大日）。

    增量与全量两条路径共用，保证水位语义一致：下次增量从此日向前重算，
    给之前无标签的最近行补标签并延伸新尾部。

    Args:
        df: 含 date / label 列的特征 DataFrame。
        meta_repo: MetaRepo 实例。
    Returns:
        实际写入的日期；df 为空时返回 None（不写）。
    """
    if df is None or df.empty:
        return None
    labeled = df[df["label"].notna()]
    src = labeled if not labeled.empty else df
    new_max = src["date"].max()
    if hasattr(new_max, "date"):
        new_max = new_max.date()
    meta_repo.set_last_date("features", "__market__", new_max, row_count=len(df))
    return new_max


def assemble_incremental(
    raw_repo=None,
    feature_repo=None,
    meta_repo=None,
) -> pd.DataFrame:
    """增量特征组装：只处理 meta_repo["features"] 之后的新交易日。

    步骤：
      1. 读 meta_repo 获取上次截止日期 T
      2. 对每只股票：读 kline 近 300 天 → merge 基本面/资金流向/北向 → 计算特征
         → 保留 date > T 的新行
      3. 汇总所有新行 → 跨截面标签 + 预处理 → 写入 FeatureRepo（features 表）
      4. 更新水位（通过 meta_repo.set_last_date）

    Returns:
        新增的 DataFrame（可能为空 DataFrame 如当日已是最新）。
    """
    if raw_repo is None or feature_repo is None or meta_repo is None:
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

    # 读取 watermark：从 MetaRepo 获取 features 截止日期
    since: date | None = meta_repo.get_last_date("features", "__market__")

    if since is None:
        logger.info("features 水位为空，执行全量组装")
        result = assemble(raw_repo=raw_repo, feature_repo=feature_repo)
        _write_features_watermark(result, meta_repo)
        return result

    since_ts = pd.Timestamp(since)
    lookback_date = since - timedelta(days=300)

    logger.info(f"增量模式：处理 date > {since} 的新数据（回看窗口起点: {lookback_date}）")

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

        new_dfs.append(new_rows)

    logger.info(f"增量：有新数据 {len(new_dfs)} 只股票，跳过 {skipped} 只")

    if not new_dfs:
        logger.info("无新数据，market_features.parquet 无需更新")
        return pd.DataFrame()

    combined = pd.concat(new_dfs, ignore_index=True)
    # 保留 future_ret 为 NaN 的最近交易日行（见 assemble() 同段说明）；仅剔 ret1 无效与 inf。
    combined = combined[np.isfinite(combined["ret1"])]
    combined = combined[~np.isinf(combined["future_ret"])]
    if combined.empty:
        logger.info("无有效新数据")
        return pd.DataFrame()

    combined = add_cross_sectional_label(combined)  # 无 future_ret 的行 label=NaN，保留
    combined = combined.sort_values(["date", "code"]).reset_index(drop=True)

    feature_cols = get_feature_columns(combined)
    combined = preprocess_features(combined, feature_cols)

    feature_repo.upsert_features(combined)

    # 水位设为最后**有标签**日：下次增量从此向前重算，给之前无标签的最近行补上标签，
    # 并延伸新的无标签尾部，保证标签最终都被填充。
    new_max_date = _write_features_watermark(combined, meta_repo)
    logger.info(
        f"增量完成：新增 {len(combined)} 行（含无标签最近行），已写入 FeatureRepo，"
        f"水位（最后有标签日）更新至 {new_max_date}"
    )
    return combined


def assemble_inference(feature_repo: FeatureRepo | None = None) -> pd.DataFrame:
    """推断模式：从 features 表读**最新交易日**截面（scan 专用，无需标签）。

    结构与训练统一：Phase 1（assemble）已把最近无标签的交易日（label=NaN）也写入表并
    完成预处理，故表的最新日 = 最新 K 线日，不再滞后 5 日。表中特征已是预处理后的值，
    **直接返回、不二次预处理**——与训练读法一致（训练 prepare_xy 也不预处理），
    避免 train/inference 预处理不一致的偏差。
    """
    if feature_repo is None:
        from src.dal.connection import get_db
        # 只读连接：scan 仅读表，可与开着的 monitor 并行
        feature_repo = FeatureRepo(get_db(read_only=True))

    date_range = feature_repo.get_feature_date_range()
    if date_range is None:
        raise FileNotFoundError("features 表为空，请先运行 Phase 1")
    latest_date = date_range[1]
    combined = feature_repo.load_features(latest_date, latest_date)
    if combined.empty:
        raise RuntimeError(f"特征截面 {latest_date} 无数据")
    logger.info(f"推断截面日期: {latest_date}，共 {len(combined)} 只股票")
    return combined
