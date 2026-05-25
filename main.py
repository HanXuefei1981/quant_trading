"""主入口：分阶段运行量化系统"""
import argparse
import json
import logging
import re
import sys
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))

from src.utils.logger import setup_logger

logger = setup_logger("quant")

# 同步确认状态文件路径（在 data/ 目录下）
_SYNC_STATE_FILE = Path(__file__).parent / "data" / ".sync_state.json"


def ingest(args):
    """从 hsjday.zip 解析 K 线数据直接写入 DuckDB（无需 sync + collect 流程）。

    用法:
      python main.py ingest --zip /Volumes/Elements/5、投资/tdx_data/2026-05-21/hsjday.zip
    """
    from src.dal.schema import migrate
    from src.dal.connection import get_db
    from src.dal.raw_repo import RawRepo
    from src.data.ingest_zip import ingest_kline

    zip_path = Path(args.zip)
    if not zip_path.exists():
        logger.error("找不到 zip 文件: %s", zip_path)
        return

    logger.info("建立 DuckDB 并迁移 schema...")
    conn = get_db()
    migrate(conn)

    logger.info("解析 %s → DuckDB kline ...", zip_path.name)
    raw_repo = RawRepo(conn)
    stats = ingest_kline(zip_path, raw_repo)
    logger.info("ingest 完成: %s", stats)

    # 创建 sync_state 标记，使 phase1 可以通过门控
    state = {
        "confirmed_date": "auto",
        "expected_date": "auto",
        "zip_file": zip_path.name,
        "confirmed_at": datetime.now().isoformat(timespec="seconds"),
        "source": "ingest_command",
    }
    _SYNC_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _SYNC_STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    logger.info(
        "ingest 完成，DuckDB kline 已写入 %d 只股票。\n"
        "下一步:\n"
        "  python main.py collect            # 采集今日北向资金 + 龙虎榜\n"
        "  python main.py 1                  # 特征工程（Phase 1）\n"
        "  python main.py 2 --rolling        # 模型训练（Phase 2）\n"
        "  python main.py 3                  # 回测（Phase 3）",
        stats.ok,
    )


def sync(args):
    """同步通达信全量数据：解压 → 验证日期 → 用户确认 → 保存状态"""
    from config.settings import TDX_VIPDOC_DIR
    from src.data.tdx_reader import get_max_trading_date

    zip_path = Path(args.zip)
    if not zip_path.exists():
        logger.error(f"压缩文件不存在: {zip_path}")
        return

    # 1. 从文件名解析期望数据截止日期（文件名日期 - 1 天）
    m = re.search(r"(\d{4}-\d{2}-\d{2})", zip_path.name)
    if not m:
        logger.error(f"无法从文件名 '{zip_path.name}' 解析日期，文件名应含 YYYY-MM-DD")
        return
    zip_date = datetime.strptime(m.group(1), "%Y-%m-%d").date()
    expected_date = zip_date - timedelta(days=1)
    logger.info(f"压缩包文件名日期: {zip_date}  →  期望数据截止: {expected_date}")

    # 2. 解压到 TDX 目录（兼容 Windows zip 中的反斜杠路径）
    logger.info(f"开始解压 {zip_path.name} → {TDX_VIPDOC_DIR} ...")
    TDX_VIPDOC_DIR.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(str(zip_path), "r") as zf:
        for member in zf.infolist():
            # 将 Windows 反斜杠统一转换为正斜杠，再用 Path 规范化
            normalized = Path(member.filename.replace("\\", "/"))
            dest = TDX_VIPDOC_DIR / normalized
            if member.is_dir():
                dest.mkdir(parents=True, exist_ok=True)
            else:
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(zf.read(member.filename))
    logger.info("解压完成")

    # 3. 读取实际最大交易日并与期望日期核对
    logger.info("正在扫描 TDX 日线文件获取最新交易日...")
    actual_date = get_max_trading_date()

    matched = actual_date == expected_date
    status = "✓ 匹配" if matched else "✗ 不匹配"

    print(f"\n{'='*58}")
    print(f"  通达信数据同步验证")
    print(f"{'='*58}")
    print(f"  压缩包:           {zip_path.name}")
    print(f"  期望数据截止日期: {expected_date}（文件名日期 - 1 天）")
    print(f"  实际最新交易日:   {actual_date}")
    print(f"  核对结果:         {status}")
    print(f"{'='*58}")

    if not matched:
        logger.error("日期不匹配，请检查通达信数据完整性，同步终止")
        return

    # 4. 请求用户确认
    try:
        answer = input(f"\n确认数据截止日期 {actual_date} 无误，继续后续处理？(y/N): ").strip().lower()
    except EOFError:
        answer = ""

    if answer != "y":
        logger.info("未确认，退出。如需继续请重新运行并输入 y")
        return

    state = {
        "confirmed_date": str(actual_date),
        "expected_date": str(expected_date),
        "zip_file": zip_path.name,
        "confirmed_at": datetime.now().isoformat(timespec="seconds"),
    }
    _SYNC_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _SYNC_STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"同步确认完成，截止日期: {actual_date}")
    logger.info("现在可以运行: python main.py 1")


def fetch_fund(args):
    """批量拉取基本面数据（PE/PB/市值/流通股本）并缓存到 data/fundamentals/

    先用本地 TDX 数据过滤退市股，再对有效股票调 akshare 拉基本面。
    """
    from src.data.fundamentals import fetch_all_fundamentals
    from src.data.tdx_reader import get_active_tdx_codes

    if not _SYNC_STATE_FILE.exists():
        logger.error("未找到数据同步确认记录，请先运行 sync 命令")
        return

    codes = get_active_tdx_codes()
    if args.sample:
        codes = codes[:args.sample]
        logger.info(f"调试模式：仅拉取 {args.sample} 只股票的基本面")

    logger.info(f"开始拉取 {len(codes)} 只股票的基本面数据，延迟 {args.delay}s/只...")
    stats = fetch_all_fundamentals(codes, delay=args.delay, use_cache=not args.refresh)
    logger.info(f"完成：新拉取 {stats['ok']}，缓存复用 {stats['cached']}，失败 {stats['fail']}")
    logger.info("现在可以运行: python main.py 1  让特征工程加入基本面因子")


def fetch_flow(args):
    """批量拉取个股主力资金流向 + 北向资金并缓存到 data/fund_flow/

    个股资金流向来自东方财富接口（akshare），约 5000 只股票需 40-60 分钟。
    北向资金为一次性拉取（沪深港通历史净买入）。
    """
    from src.data.fund_flow import fetch_all_fund_flow, fetch_northbound_flow
    from src.data.tdx_reader import get_active_tdx_codes

    if not _SYNC_STATE_FILE.exists():
        logger.error("未找到数据同步确认记录，请先运行 sync 命令")
        return

    # 1. 北向资金（一次搞定）
    logger.info("拉取北向资金历史数据...")
    north_df = fetch_northbound_flow(use_cache=not args.refresh)
    if north_df is not None:
        logger.info(
            f"北向资金数据已就绪：{len(north_df)} 条"
            f"（{north_df['date'].min().date()} ~ {north_df['date'].max().date()}）"
        )
    else:
        logger.warning("北向资金拉取失败，north_net_* 因子将缺失")

    # 2. 个股主力资金流向
    codes = get_active_tdx_codes()
    if args.sample:
        codes = codes[:args.sample]
        logger.info(f"调试模式：仅拉取 {args.sample} 只股票的资金流向")

    logger.info(
        f"开始拉取 {len(codes)} 只股票的资金流向，延迟 {args.delay}s/只"
        f"（预计 {len(codes) * args.delay / 60:.0f} 分钟）..."
    )
    stats = fetch_all_fund_flow(codes, delay=args.delay, use_cache=not args.refresh)
    logger.info(f"完成：新拉取 {stats['ok']}，缓存复用 {stats['cached']}，失败 {stats['fail']}")
    logger.info("现在可以运行: python main.py 1  让特征工程加入资金流向因子")


def collect(args):
    """采集所有原始数据到 data/raw/（解耦层：只负责数据采集，不做特征工程）

    执行步骤：
      1. TDX → data/raw/kline/       （本地二进制转 Parquet，极快）
      2. 北向资金 → data/raw/northbound.parquet  （同花顺 THS API，当日快照追加）
      3. 腾讯财经 → data/raw/tencent/ （可选，--with-tencent，当日快照追加）

    基本面与个股资金流向缓存路径不变（data/fundamentals/, data/fund_flow/），
    使用原有的 fetch-fund / fetch-flow 命令更新。
    """
    from src.collectors.tdx_collector import TDXCollector
    from src.collectors.northbound_collector import NorthboundCollector
    from src.data.tdx_reader import get_active_tdx_codes

    if not _SYNC_STATE_FILE.exists():
        logger.error("未找到数据同步确认记录，请先运行 sync 命令")
        return

    # Step 1: TDX K 线 → data/raw/kline/
    logger.info("Step 1: 转换 TDX K 线 → data/raw/kline/")
    tdx = TDXCollector()
    codes = get_active_tdx_codes()
    if args.sample:
        codes = codes[:args.sample]
    incremental = not args.refresh
    kline_stats = tdx.fetch_all(codes, incremental=incremental)
    logger.info(f"K 线转换完成：{kline_stats}")

    # Step 2: 北向资金今日快照
    logger.info("Step 2: 更新北向资金快照 → data/raw/northbound.parquet")
    north = NorthboundCollector()
    north_stats = north.fetch_all([])
    logger.info(f"北向资金更新完成：{north_stats}")

    # Step 3: 腾讯财经（可选）
    if getattr(args, "with_tencent", False):
        from src.collectors.tencent_collector import TencentCollector
        logger.info("Step 3: 采集腾讯财经 PE/PB/市值快照 → data/raw/tencent/")
        tencent = TencentCollector()
        tencent_stats = tencent.fetch_all(codes, incremental=incremental)
        logger.info(f"腾讯快照完成：{tencent_stats}")

    logger.info("collect 完成，现在可以运行: python main.py 1")


def update(args):
    """每日增量更新：mootdx拉取新K线 → 北向快照 → [腾讯快照] → 增量特征 → [可选重训]

    日常用法（无需依赖本地 TDX .day 文件，直接通过 mootdx TCP 拉取新数据）：
      python main.py update                   # 标准每日更新（2-3 分钟）
      python main.py update --with-tencent    # 额外更新腾讯 PE/PB 快照
      python main.py update --retrain         # 更新后触发 Phase 2 重训

    月初全量刷新（使用压缩包重建完整历史，之后恢复每日 update）：
      python main.py sync --zip hsjday-YYYY-MM-DD.zip  &&  python main.py 1
    """
    from src.data import watermark as wm
    from src.collectors.tdx_collector import TDXCollector
    from src.collectors.northbound_collector import NorthboundCollector
    from src.data.tdx_reader import get_active_tdx_codes
    from src.features.assembler import assemble_incremental
    from datetime import date as date_cls

    # 读取当前水位
    kline_since = wm.get_since("kline")
    from src.dal.connection import get_db
    from src.dal.meta_repo import MetaRepo as _MetaRepo
    feat_since = _MetaRepo(get_db()).get_last_date("features", "__market__")
    logger.info(f"K线水位: {kline_since}  特征水位: {feat_since}")

    codes = get_active_tdx_codes()
    if args.sample:
        codes = codes[:args.sample]

    # Step 1: 通过 mootdx TCP 拉取增量 K 线
    logger.info("Step 1: mootdx 增量K线拉取")
    tdx = TDXCollector()
    if kline_since is None:
        logger.error(
            "未找到K线水位记录，首次建库请运行:\n"
            "  python main.py sync --zip <压缩包>  &&  python main.py collect  &&  python main.py 1"
        )
        return
    kline_stats = tdx.fetch_incremental_mootdx(codes, since=kline_since)
    logger.info(f"K线更新：{kline_stats}")

    # 更新 kline 水位：只在有新数据时更新，从实际更新的股票中找最新日期
    if kline_stats.ok > 0:
        kline_dir = Path(__file__).parent / "data" / "raw" / "kline"
        for c in codes:
            try:
                sample_df = pd.read_parquet(kline_dir / f"{c}.parquet")
                wm.update("kline", pd.to_datetime(sample_df["date"]).max().date())
                break
            except Exception:
                continue

    # Step 2: 北向资金今日快照
    logger.info("Step 2: 更新北向资金快照")
    north = NorthboundCollector()
    north_stats = north.fetch_all([])
    logger.info(f"北向资金：{north_stats}")
    if north_stats.ok > 0:
        wm.update("northbound", date_cls.today())

    # Step 3: 腾讯财经快照（可选）
    if getattr(args, "with_tencent", False):
        from src.collectors.tencent_collector import TencentCollector
        logger.info("Step 3: 腾讯财经 PE/PB 快照")
        tencent = TencentCollector()
        tencent_stats = tencent.fetch_all(codes, incremental=True)
        logger.info(f"腾讯快照：{tencent_stats}")
        if tencent_stats.ok > 0:
            wm.update("tencent", date_cls.today())

    # Step 4: 增量特征组装
    logger.info("Step 4: 增量特征组装")
    new_df = assemble_incremental()
    if new_df.empty:
        logger.info("无新特征数据，今日可能已更新或无新交易日")
    else:
        logger.info(f"增量特征完成，新增 {len(new_df)} 行")

    # Step 5: 可选重训
    if getattr(args, "retrain", False):
        logger.info("Step 5: 触发 Phase 2 重训（--retrain 已指定）")

        class _RetrainArgs:
            walk_forward = False
            rolling = True
            train_years = getattr(args, "train_years", 2)
            final = False

        phase2(_RetrainArgs())

    logger.info("update 完成")


def phase1(args):
    """Phase 1: 特征工程（须先通过 sync 确认 + collect 采集数据）"""
    # 门控：必须先通过 sync 确认
    if not _SYNC_STATE_FILE.exists():
        logger.error("未找到数据同步确认记录，请先运行:")
        logger.error("  python main.py sync --zip <通达信压缩包路径>")
        return

    state = json.loads(_SYNC_STATE_FILE.read_text(encoding="utf-8"))
    logger.info(f"已确认数据截止日期: {state['confirmed_date']}  来源: {state.get('zip_file', '未知')}")

    # 检查数据来源：DuckDB 优先，兼容旧 raw/kline/ parquet
    from src.dal.connection import get_db
    from src.dal.raw_repo import RawRepo
    conn = get_db()
    raw_repo = RawRepo(conn)
    db_kline_count = conn.execute("SELECT COUNT(DISTINCT code) FROM kline").fetchone()[0]

    kline_dir = Path(__file__).parent / "data" / "raw" / "kline"
    parquet_count = len(list(kline_dir.glob("*.parquet"))) if kline_dir.exists() else 0

    if db_kline_count == 0 and parquet_count == 0:
        logger.error("DuckDB kline 表和 data/raw/kline/ 均无数据。")
        logger.error("请先运行: python main.py ingest --zip <hsjday.zip路径>")
        return
    elif db_kline_count > 0:
        logger.info(f"DuckDB kline 表已有 {db_kline_count} 只股票数据")
    else:
        logger.info(f"使用旧 data/raw/kline/ ({parquet_count} 只 parquet 文件)")

    # 基本面缓存检查
    fund_dir = Path(__file__).parent / "data" / "fundamentals"
    fund_count = len(list(fund_dir.glob("*.parquet"))) if fund_dir.exists() else 0
    if fund_count < 1000:
        logger.warning(
            f"基本面缓存仅 {fund_count} 只股票，模型将退化为纯技术因子。"
        )
        logger.warning("建议先运行: python main.py fetch-fund  （约 1-2 小时）")

    from src.features.assembler import assemble
    df = assemble(
        sample_size=args.sample,
    )
    logger.info(f"Phase 1 完成，数据集形状: {df.shape}")
    logger.info(f"特征列数: {df.shape[1]}")
    logger.info(f"时间范围: {df['date'].min()} ~ {df['date'].max()}")
    logger.info(f"股票数量: {df['code'].nunique()}")


def phase2(args):
    """Phase 2: 模型训练"""
    import json
    from src.data.pipeline import load_features_from_db
    from src.models.trainer import run_training, run_training_rolling, run_training_final, walk_forward_cv
    from src.models.evaluator import run_evaluation
    from config.settings import PROCESSED_DIR

    logger.info("Phase 2 开始：从 DuckDB features 表加载数据")
    try:
        df = load_features_from_db()
    except FileNotFoundError as exc:
        logger.error(str(exc))
        return

    logger.info(f"数据加载完成：{len(df)} 行，{df['code'].nunique()} 只股票")
    logger.info(f"时间范围：{df['date'].min().date()} ~ {df['date'].max().date()}")

    walk_forward = getattr(args, "walk_forward", False)
    rolling = getattr(args, "rolling", False)

    if walk_forward:
        logger.info("=== Walk-Forward CV 诊断（扩展窗口，18个月起步，每折6个月 OOS）===")
        wf_results = walk_forward_cv(df, min_train_months=18, pred_months=6)

        model_dir = PROCESSED_DIR.parent / "models"
        model_dir.mkdir(parents=True, exist_ok=True)
        wf_path = model_dir / "walk_forward_results.json"
        with open(wf_path, "w", encoding="utf-8") as f:
            json.dump(wf_results, f, ensure_ascii=False, indent=2)
        logger.info(f"Walk-Forward 结果已保存至 {wf_path}")

        logger.info("=== 生产模型：滚动窗口重训（最近 2 年数据）===")
        lgbm_model, ridge_model, w_lgbm, w_ridge, feature_cols, train_df, val_df, test_df = \
            run_training_rolling(df, train_years=2)
    elif rolling:
        train_years = getattr(args, "train_years", 2)
        logger.info(f"=== 滚动窗口训练（最近 {train_years} 年，跳过 WF CV）===")
        lgbm_model, ridge_model, w_lgbm, w_ridge, feature_cols, train_df, val_df, test_df = \
            run_training_rolling(df, train_years=train_years)
    else:
        lgbm_model, ridge_model, w_lgbm, w_ridge, feature_cols, train_df, val_df, test_df = run_training(df)

    run_evaluation(lgbm_model, feature_cols, train_df, val_df, test_df)

    if getattr(args, "final", False):
        run_training_final(df, lgbm_model.best_iteration, w_lgbm, w_ridge, feature_cols)

    logger.info("Phase 2 完成，模型已保存至 data/models/")


def phase3(args):
    """Phase 3: 回测"""
    from src.data.pipeline import load_processed_data
    from src.models.trainer import load_ensemble, time_split
    from src.backtest.engine import generate_signals, run_backtest, build_benchmark
    from src.backtest.metrics import print_report
    from config.settings import PROCESSED_DIR

    logger.info("Phase 3 开始：加载数据与模型")
    df = load_processed_data()
    lgbm_model, ridge_model, w_lgbm, w_ridge, feature_cols = load_ensemble()

    _, val_df, test_df = time_split(df)
    backtest_df = pd.concat([val_df, test_df], ignore_index=True)

    start = str(backtest_df["date"].min().date())
    end   = str(backtest_df["date"].max().date())
    logger.info(f"回测区间: {start} ~ {end}")

    from src.data.stock_filter import get_st_codes
    st_codes = get_st_codes()
    signal_df = generate_signals(backtest_df, lgbm_model, feature_cols, ridge_model, w_lgbm, w_ridge,
                                 exclude_codes=st_codes)

    top_k          = getattr(args, "top_k", 50)
    rebalance_days = getattr(args, "rebalance", 5)

    result = run_backtest(
        signal_df, start, end,
        top_k=top_k,
        rebalance_every=rebalance_days,
        min_signal=getattr(args, "min_signal", 0.0),
        signal_weighted=not getattr(args, "equal_weight", False),
        max_stock_weight=getattr(args, "max_weight", 0.05),
        max_sector_weight=getattr(args, "max_sector_weight", 0.40),
        max_turnover=getattr(args, "max_turnover", 0.5),
        replace_only=getattr(args, "replace_only", False),
    )
    benchmark = build_benchmark(signal_df, start, end)

    out_dir = PROCESSED_DIR.parent / "backtest"
    print_report(result["equity"], benchmark, result["trades"],
                 result["initial_capital"], out_dir)

    logger.info("Phase 3 完成，结果已保存至 data/backtest/")


def scan(args):
    """快捷扫描：输出当前最新截面的 Top-N 信号排名，供散户做参考"""
    from src.features.assembler import assemble_inference as load_inference_data
    from src.models.trainer import load_ensemble
    from src.backtest.engine import generate_signals
    from src.data.stock_filter import get_st_codes
    from src.features.preprocessing import get_segment
    from config.settings import PROCESSED_DIR

    top_k = getattr(args, "top_k", 20)
    buffer = int(top_k * 1.5)

    logger.info("加载个股缓存推断特征（推断模式，无需标签）...")
    latest_df = load_inference_data()
    lgbm_model, ridge_model, w_lgbm, w_ridge, feature_cols = load_ensemble()

    # 获取 ST 股票列表（科创板已在 generate_signals 内部过滤）
    st_codes = get_st_codes()

    latest_date = latest_df["date"].max()
    logger.info(f"扫描日期：{latest_date.date()}，共 {len(latest_df)} 只股票有数据")

    signal_df = generate_signals(latest_df, lgbm_model, feature_cols, ridge_model, w_lgbm, w_ridge,
                                 exclude_codes=st_codes)

    # 排序、附加板块信息
    signal_df = signal_df.sort_values("signal", ascending=False).reset_index(drop=True)
    signal_df["rank"] = signal_df.index + 1
    signal_df["segment"] = signal_df["code"].apply(get_segment)
    signal_df["signal_pct"] = signal_df["signal"].rank(pct=True).round(3)

    top_df = signal_df.head(buffer)[["rank", "code", "segment", "close", "signal", "signal_pct"]]

    print(f"\n{'='*65}")
    print(f"  信号扫描结果  |  日期：{latest_date.date()}  |  Top-{buffer} 候选池")
    print(f"{'='*65}")
    print(f"{'排名':>4}  {'代码':>8}  {'板块':>6}  {'收盘价':>8}  {'信号值':>8}  {'全市场分位':>10}")
    print(f"{'-'*65}")
    for _, row in top_df.iterrows():
        marker = " ◀ 推荐" if row["rank"] <= top_k else ""
        print(f"{int(row['rank']):>4}  {row['code']:>8}  {row['segment']:>6}  "
              f"{row['close']:>8.2f}  {row['signal']:>8.4f}  {row['signal_pct']:>10.1%}{marker}")
    print(f"{'='*65}")
    print(f"  说明：排名前 {top_k} 只（标◀）为当期建仓候选，{top_k+1}~{buffer} 只为观察缓冲区")
    print(f"  持仓股仍在前 {buffer} 名内 → 继续持有；跌出前 {buffer} 名 → 纳入卖出候选\n")

    # 保存 CSV
    out_path = PROCESSED_DIR.parent / "backtest" / f"scan_{latest_date.date()}.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    signal_df.head(buffer).to_csv(out_path, index=False, encoding="utf-8-sig")
    logger.info(f"完整候选列表已保存至 {out_path}")


def main():
    parser = argparse.ArgumentParser(description="A 股量化交易系统")
    parser.add_argument("phase", choices=["ingest", "sync", "collect", "fetch-fund", "fetch-flow", "update", "1", "2", "3", "scan"],
                        help="运行阶段：sync=同步TDX数据 | collect=采集原始数据 | fetch-fund=拉取基本面 | fetch-flow=拉取资金流向 | update=每日增量更新 | 1=特征工程 | 2=训练 | 3=回测 | scan=选股扫描")
    parser.add_argument("--zip", default=None,
                        help="sync 命令专用：通达信压缩包完整路径（如 /path/to/hsjday-2026-05-12.zip）")
    parser.add_argument("--refresh", action="store_true",
                        help="fetch-fund 专用：忽略本地缓存，强制重新拉取")
    parser.add_argument("--sample", type=int, default=None, help="调试时限制股票数量")
    parser.add_argument("--delay", type=float, default=0.3, help="拉取间隔（秒）")
    parser.add_argument("--no-cache", action="store_true", help="强制重新下载数据（phase1 已默认全量更新）")
    parser.add_argument("--with-tencent", action="store_true", dest="with_tencent",
                        help="collect: 同时采集腾讯财经 PE/PB/市值快照（data/raw/tencent/）")
    parser.add_argument("--walk-forward", action="store_true", dest="walk_forward",
                        help="Phase2: 先跑扩展窗口 Walk-Forward CV 诊断，再用最近 2 年数据训练生产模型")
    parser.add_argument("--rolling", action="store_true", dest="rolling",
                        help="Phase2: 直接用滚动窗口训练（最近 N 年），跳过 WF CV（更快）")
    parser.add_argument("--train-years", type=int, default=2, dest="train_years",
                        help="Phase2 --rolling: 滚动窗口训练年数（默认 2 年）")
    parser.add_argument("--min-signal", type=float, default=0.0, dest="min_signal",
                        help="Phase3: 信号最低阈值，低于此值的股票不纳入选股池（默认 0.0，即不过滤）")
    parser.add_argument("--final", action="store_true", dest="final",
                        help="Phase2: 评估后用全量有标签数据重训生产模型（固定迭代轮数，不影响评估指标）")
    parser.add_argument("--top-k", type=int, default=50, dest="top_k", help="Phase3: 每期持仓股票数（默认50）")
    parser.add_argument("--rebalance", type=int, default=5, help="Phase3: 调仓间隔交易日数（默认5）")
    parser.add_argument("--max-weight", type=float, default=0.05, dest="max_weight", help="Phase3: 单股最大权重（默认0.05）")
    parser.add_argument("--max-sector-weight", type=float, default=0.40, dest="max_sector_weight", help="Phase3: 板块最大权重（默认0.40）")
    parser.add_argument("--max-turnover", type=float, default=0.5, dest="max_turnover", help="Phase3: 单次最大单向换手率（默认0.5）")
    parser.add_argument("--equal-weight", action="store_true", dest="equal_weight", help="Phase3: 等权重替代信号加权")
    parser.add_argument("--replace-only", action="store_true", dest="replace_only", help="Phase3/scan: 散户模式，只换出跌出候选池的股票，不做存量再平衡")
    parser.add_argument("--retrain", action="store_true", dest="retrain",
                        help="update: 增量更新完成后触发 Phase 2 滚动窗口重训")
    args = parser.parse_args()

    if args.phase == "ingest" and not args.zip:
        parser.error("ingest 命令需要提供 --zip 参数，例如: python main.py ingest --zip /path/to/hsjday.zip")

    if args.phase == "sync" and not args.zip:
        parser.error("sync 命令需要提供 --zip 参数，例如: python main.py sync --zip /path/to/hsjday-YYYY-MM-DD.zip")

    phases = {
        "ingest": ingest,
        "sync": sync,
        "collect": collect,
        "fetch-fund": fetch_fund,
        "fetch-flow": fetch_flow,
        "update": update,
        "1": phase1,
        "2": phase2,
        "3": phase3,
        "scan": scan,
    }
    phases[args.phase](args)


if __name__ == "__main__":
    main()
