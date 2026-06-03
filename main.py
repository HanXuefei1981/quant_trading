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
    """按交易日批量拉取基本面（PE/PB/市值/流通股本）→ DuckDB fundamentals 表。

    每次 API 调用返回全市场当日快照，比逐股方式快约 5000×。
    """
    from src.collectors.fundamental_collector import FundamentalCollector
    from src.dal.connection import get_db
    from src.dal.schema import migrate

    conn = get_db()
    migrate(conn)

    collector = FundamentalCollector()
    since = None
    if args.since:
        from datetime import datetime
        since = datetime.strptime(args.since, "%Y-%m-%d").date()
        logger.info("指定起始日期：%s（从次日开始批量补采，用于回填缺口）", since)
    else:
        logger.info("开始按交易日批量拉取基本面（自动从上次水位续采）...")
    # collect_batch 从 since+1 天起逐日 INSERT OR REPLACE，可幂等补全残缺交易日
    stats = collector.collect_batch(since=since, delay=args.delay)
    logger.info("完成：%s", stats)
    logger.info("现在可以运行: python main.py 1  让特征工程加入基本面因子")


def fetch_flow(args):
    """按交易日批量拉取资金流向 + 北向资金历史 → DuckDB fund_flow / northbound 表。

    每次 API 调用返回全市场当日快照，比逐股方式快约 5000×。
    """
    from src.collectors.fund_flow_collector import FundFlowCollector
    from src.collectors.northbound_collector import NorthboundCollector
    from src.dal.connection import get_db
    from src.dal.schema import migrate

    conn = get_db()
    migrate(conn)

    # 1. 北向资金历史
    logger.info("拉取北向资金历史数据...")
    north_stats = NorthboundCollector().collect()
    if north_stats.fail > 0:
        logger.warning("北向资金拉取失败，north_net_* 因子将缺失")

    # 2. 个股主力资金流向（按日批量）
    collector = FundFlowCollector()
    logger.info("开始按交易日批量拉取资金流向（自动从上次水位续采）...")
    stats = collector.collect_batch(delay=args.delay)
    logger.info("完成：%s", stats)
    logger.info("现在可以运行: python main.py 1  让特征工程加入资金流向因子")


def fetch_basic(args):
    """拉取全市场股票基础信息（名称/行业/地区）→ DuckDB stock_basic 表。

    供 scan 关联名称与行业用。名称/行业变动少，需要时手动重跑刷新即可。
    """
    from src.data.tushare_fetchers import fetch_stock_basic
    from src.dal.raw_repo import RawRepo
    from src.dal.connection import get_db
    from src.dal.schema import migrate

    conn = get_db()
    migrate(conn)

    logger.info("拉取股票基础信息（tushare stock_basic）...")
    df = fetch_stock_basic()
    if df is None or df.empty:
        logger.error("stock_basic 拉取失败（检查 token / VPN）")
        return
    n = RawRepo(conn).upsert_stock_basic(df)
    logger.info("已写入 stock_basic 表：%d 只股票", n)


def fetch_financial(args):
    """批量拉取财务指标（ROE/ROA/毛利率/净利率/营收/净利润等）→ DuckDB financial_indicator 表"""
    from src.collectors.financial_collector import FinancialCollector
    from src.data.tdx_reader import get_active_tdx_codes
    from src.dal.connection import get_db
    from src.dal.schema import migrate
    from datetime import date as date_cls

    if not _SYNC_STATE_FILE.exists():
        logger.error("未找到数据同步确认记录，请先运行 sync 命令")
        return

    conn = get_db()
    migrate(conn)

    codes = get_active_tdx_codes()
    if args.sample:
        codes = codes[:args.sample]
        logger.info(f"调试模式：仅拉取 {args.sample} 只股票的财务指标")

    since = None
    if args.since:
        from datetime import datetime
        since = datetime.strptime(args.since, "%Y-%m-%d").date()
        logger.info(f"指定起始报告期：{since}")

    logger.info(
        f"开始拉取 {len(codes)} 只股票的财务指标，延迟 {args.delay}s/只"
        f"（预计 {len(codes) * args.delay * 2 / 60:.0f} 分钟，每只调用 2 个接口）..."
    )
    collector = FinancialCollector(delay=args.delay)
    stats = collector.collect(codes, since=since)
    logger.info(f"完成：{stats}")


def fetch_reports(args):
    """批量拉取机构研报评级 + EPS 共识 → DuckDB reports / eps_snapshot 表

    --mode report   仅拉取研报评级（tushare pro.report_rc）
    --mode eps      仅拉取 EPS 共识（akshare stock_profit_forecast_ths）
    --mode both     两者都拉（默认）
    """
    from src.collectors.report_collector import ReportCollector
    from src.data.tdx_reader import get_active_tdx_codes
    from src.dal.connection import get_db
    from src.dal.schema import migrate

    if not _SYNC_STATE_FILE.exists():
        logger.error("未找到数据同步确认记录，请先运行 sync 命令")
        return

    conn = get_db()
    migrate(conn)

    codes = get_active_tdx_codes()
    if args.sample:
        codes = codes[:args.sample]
        logger.info(f"调试模式：仅拉取 {args.sample} 只股票")

    since = None
    if args.since:
        from datetime import datetime
        since = datetime.strptime(args.since, "%Y-%m-%d").date()
        logger.info(f"指定增量起点：{since}")

    mode = getattr(args, "report_mode", "both")

    if mode in ("report", "both"):
        logger.info(
            f"Step 1/{'2' if mode == 'both' else '1'}: 研报评级 → DuckDB reports"
            f"  {len(codes)} 只，延迟 {args.delay}s/只..."
        )
        rc = ReportCollector(mode="report", delay=args.delay)
        stats = rc.collect(codes, since=since)
        logger.info(f"研报评级完成：{stats}")

    if mode in ("eps", "both"):
        logger.info(
            f"Step {'2' if mode == 'both' else '1'}/{'2' if mode == 'both' else '1'}: EPS 共识 → DuckDB eps_snapshot"
            f"  {len(codes)} 只，延迟 {args.delay}s/只..."
        )
        ec = ReportCollector(mode="eps", delay=args.delay)
        stats = ec.collect(codes, since=since)
        logger.info(f"EPS 共识完成：{stats}")


def collect(args):
    """采集今日增量数据到 DuckDB（龙虎榜快照）。

    注意：K 线通过 `ingest` 写入；北向资金通过 `fetch-flow` 用 tushare 拉取（T+1）。
    日常增量更新：先运行 `ingest --zip <新zip>`，再运行 `collect`，最后运行 `1`。
    """
    from src.dal.connection import get_db
    from src.dal.schema import migrate
    from src.collectors.signal_collector import SignalCollector

    conn = get_db()
    migrate(conn)

    # Step 1: 龙虎榜今日数据
    logger.info("Step 1: 更新龙虎榜 → DuckDB lhb")
    signal = SignalCollector()
    signal_stats = signal.collect()
    logger.info(f"龙虎榜更新完成：{signal_stats}")

    # Step 2: 腾讯财经 PE/PB 快照（可选）
    if getattr(args, "with_tencent", False):
        from src.collectors.tencent_collector import TencentCollector
        from src.data.tdx_reader import get_active_tdx_codes
        logger.info("Step 2: 采集腾讯财经 PE/PB/市值快照 → DuckDB fundamentals_snapshot")
        codes = get_active_tdx_codes()
        if args.sample:
            codes = codes[:args.sample]
        tencent = TencentCollector()
        tencent_stats = tencent.collect(codes)
        logger.info(f"腾讯快照完成：{tencent_stats}")

    logger.info(
        "collect 完成。下一步:\n"
        "  python main.py 1  # 特征工程（Phase 1）"
    )


def update(args):
    """日 K 线增量更新：全市场按日批量拉取 + 北向资金（T+1）。

    职责仅限 K 线与北向，特征工程须在所有接口数据（基本面、主力资金、龙虎榜等）
    采集完毕后，单独运行 Phase 1 触发。

    用法：
      python main.py update                 # 标准每日更新
      python main.py update --with-tencent  # 额外更新腾讯 PE/PB 快照

    月初全量刷新：
      python main.py sync --zip hsjday-YYYY-MM-DD.zip  &&  python main.py 1
    """
    from src.data import watermark as wm
    from src.collectors.tdx_collector import TDXCollector
    from datetime import date as date_cls

    # 读取当前水位
    kline_since = wm.get_since("kline")
    logger.info(f"K线水位: {kline_since}")

    # Step 1: 全市场批量 K 线（按交易日一次取全市场，水位控制起点）
    logger.info("Step 1: tushare 全市场批量K线（按日，水位: %s）", kline_since)
    tdx = TDXCollector()
    if kline_since is None:
        logger.error(
            "未找到K线水位记录，首次建库请运行:\n"
            "  python main.py sync --zip <压缩包>  &&  python main.py collect  &&  python main.py 1"
        )
        return
    kline_stats = tdx.collect_tushare_daily_batch(since=kline_since)
    logger.info(f"K线更新：{kline_stats}")

    # 更新 kline 水位：从 DuckDB 查询最新日期
    if kline_stats.ok > 0:
        from src.dal.connection import get_db as _get_db
        _max = _get_db().execute("SELECT MAX(CAST(date AS DATE)) FROM kline").fetchone()[0]
        if _max:
            wm.update("kline", _max)

    # Step 2: 北向资金增量更新（tushare moneyflow_hsgt，T+1）
    logger.info("Step 2: 北向资金增量更新（tushare T+1）")
    from src.collectors.northbound_collector import NorthboundCollector
    north_stats = NorthboundCollector().collect()
    logger.info(f"北向资金：{north_stats}")

    # Step 3: 腾讯财经快照（可选）
    if getattr(args, "with_tencent", False):
        from src.collectors.tencent_collector import TencentCollector
        from src.data.tdx_reader import get_active_tdx_codes
        logger.info("Step 3: 腾讯财经 PE/PB 快照")
        codes = get_active_tdx_codes()
        tencent = TencentCollector()
        tencent_stats = tencent.fetch_all(codes, incremental=True)
        logger.info(f"腾讯快照：{tencent_stats}")
        if tencent_stats.ok > 0:
            wm.update("tencent", date_cls.today())

    logger.info("update 完成（特征工程请在所有数据采集完毕后运行 Phase 1）")


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
    from src.data.pipeline import load_features_from_db
    from src.models.trainer import load_ensemble, time_split
    from src.backtest.engine import generate_signals, run_backtest, build_benchmark
    from src.backtest.metrics import print_report
    from config.settings import PROCESSED_DIR

    logger.info("Phase 3 开始：加载数据与模型")
    df = load_features_from_db()
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
        confirm_streak=int(getattr(args, "confirm", 0) or 0),
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

    # 关联名称/行业 + 估值（当日 fundamentals）+ 最新财务（ROE/净利同比）
    from src.features.scan_enrich import enrich_signals
    from src.dal.connection import get_db
    top_df = enrich_signals(
        signal_df.head(buffer), get_db(read_only=True), latest_date.date()
    )
    top_df["code"] = top_df["code"].astype(str)

    # 连榜（截至当前连续在 Top-buffer 的次数）：过滤"一日游"，连榜=1 多为单日异动
    from src.features.scan_history import (
        load_scan_history, consecutive_streaks, classify_actions,
    )
    out_dir = PROCESSED_DIR.parent / "backtest"
    out_dir.mkdir(parents=True, exist_ok=True)
    _prior = load_scan_history(str(out_dir), str(latest_date.date()))
    _streaks = consecutive_streaks(_prior + [set(top_df["code"])])
    top_df["streak"] = top_df["code"].map(lambda c: _streaks.get(str(c), 1))

    confirm_k = int(getattr(args, "confirm", 0) or 0)

    def _fmt(v, fmt="{:.1f}"):
        return "—" if pd.isna(v) else fmt.format(v)

    def _disp_w(s):
        # 中文/全角字符按 2 列宽度计
        return sum(2 if ord(c) > 0x2E7F else 1 for c in str(s))

    def _pad(s, width, left=False):
        # 按显示宽度补空格，保证中文表头与数值列对齐
        s = str(s)
        gap = max(width - _disp_w(s), 0)
        return s + " " * gap if left else " " * gap + s

    # 列定义：(中文表头, 显示宽度, 取值函数, 是否左对齐)
    columns = [
        ("排名", 4, lambda r: int(r["rank"]), False),
        ("代码", 7, lambda r: r["code"], False),
        ("名称", 9, lambda r: str(r["name"])[:6] if pd.notna(r["name"]) else "—", True),
        ("行业", 9, lambda r: str(r["industry"])[:6] if pd.notna(r["industry"]) else "—", True),
        ("收盘价", 8, lambda r: f"{r['close']:.2f}", False),
        ("信号值", 8, lambda r: f"{r['signal']:.3f}", False),
        ("信号分位", 9, lambda r: f"{r['signal_pct']:.1%}", False),
        ("总市值亿", 9, lambda r: _fmt(r["market_cap_yi"], "{:.0f}"), False),
        ("市盈率", 8, lambda r: _fmt(r["pe_ttm"]), False),
        ("市净率", 7, lambda r: _fmt(r["pb"]), False),
        ("市销率", 7, lambda r: _fmt(r["ps"]), False),
        ("净资收益率", 11, lambda r: _fmt(r["roe"]), False),
        ("净利润同比", 11, lambda r: _fmt(r["net_profit_yoy"]), False),
        ("连榜", 5, lambda r: int(r["streak"]), False),
    ]

    def _marker(r):
        # 标注建仓建议：连榜≥confirm_k 为可建仓✓，不足为观察⏳；buffer 区为缓冲
        if r["rank"] > top_k:
            return ""
        if confirm_k <= 0:
            return " ◀"
        return " ✓可建仓" if r["streak"] >= confirm_k else " ⏳观察"

    header = " ".join(_pad(c[0], c[1], c[3]) for c in columns)
    table_w = _disp_w(header) + 6
    print(f"\n{'=' * table_w}")
    _conf = f"  确认阈值：连榜≥{confirm_k}" if confirm_k > 0 else ""
    print(f"  信号扫描结果  |  日期：{latest_date.date()}  |  Top-{buffer} 候选池（前 {top_k} 只为建仓候选）{_conf}")
    print(f"{'=' * table_w}")
    print(header)
    print('-' * table_w)
    for _, r in top_df.iterrows():
        print(" ".join(_pad(c[2](r), c[1], c[3]) for c in columns) + _marker(r))
    print('=' * table_w)
    if top_df["name"].isna().all():
        print("  ⚠ 名称/行业全为空：请先运行 python main.py fetch-basic 采集 stock_basic")
    if confirm_k > 0:
        print(f"  说明：前 {top_k} 只中【连榜≥{confirm_k}】标 ✓可建仓，连榜不足标 ⏳观察（疑似一日游，建议观望）；"
              f"持仓跌出前 {buffer} 名 → 卖出候选")
    else:
        print(f"  说明：前 {top_k} 只（标◀）为当期建仓候选，{top_k+1}~{buffer} 只为观察缓冲区；"
              f"持仓跌出前 {buffer} 名 → 卖出候选（加 --confirm K 启用连榜确认）")
    print(f"  字段：连榜=截至今日连续在榜次数（=1 多为单日异动）  总市值亿元  市盈率 TTM（亏损股 —）\n")

    # ── replace-only 持仓分类（提供 --holdings 时）：继续持有 / 卖出 / 新建仓 ──
    holdings_arg = getattr(args, "holdings", None)
    if holdings_arg:
        held = {c.strip().zfill(6) for c in str(holdings_arg).split(",") if c.strip()}
        buffer_codes = set(top_df["code"])
        topk_codes = set(top_df.loc[top_df["rank"] <= top_k, "code"])
        acts = classify_actions(buffer_codes, topk_codes, _streaks, held, max(confirm_k, 1))
        name_of = dict(zip(top_df["code"], top_df["name"].fillna("—")))
        rank_of = dict(zip(top_df["code"], top_df["rank"]))
        # 已跌出榜单的持仓在 top_df 里没有名称，从 stock_basic 补查
        missing = [c for c in held if c not in name_of]
        if missing:
            try:
                _ph = ", ".join("?" for _ in missing)
                _nb = get_db(read_only=True).execute(
                    f"SELECT code, name FROM stock_basic WHERE code IN ({_ph})", missing
                ).fetchall()
                for _c, _n in _nb:
                    name_of[str(_c).zfill(6)] = _n
            except Exception:
                pass

        def _line(codes, with_rank=True):
            if not codes:
                return "  （无）"
            parts = []
            for c in sorted(codes, key=lambda x: rank_of.get(x, 999)):
                tag = f"#{rank_of[c]}" if (with_rank and c in rank_of) else ""
                parts.append(f"{c} {name_of.get(c, '—')}{tag}")
            return "  " + " · ".join(parts)

        print(f"{'-' * table_w}")
        print(f"  【replace-only 操作建议】现持仓 {len(held)} 只，确认阈值连榜≥{max(confirm_k, 1)}")
        print(f"  ✅ 继续持有（仍在 Top-{buffer}，{len(acts['hold'])} 只）：")
        print(_line(acts["hold"]))
        print(f"  ❌ 卖出（已跌出 Top-{buffer}，{len(acts['sell'])} 只）：")
        print(_line(acts["sell"], with_rank=False))
        print(f"  🆕 新建仓（Top-{top_k} 且连榜达标且未持有，{len(acts['buy'])} 只）：")
        print(_line(acts["buy"]))
        print(f"{'=' * table_w}\n")

    # 保存富信息 CSV（列名中文）
    out_path = PROCESSED_DIR.parent / "backtest" / f"scan_{latest_date.date()}.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    csv_rename = {
        "rank": "排名", "code": "代码", "name": "名称", "industry": "行业", "segment": "板块",
        "close": "收盘价", "signal": "信号值", "signal_pct": "信号分位",
        "market_cap_yi": "总市值亿", "pe_ttm": "市盈率", "pb": "市净率", "ps": "市销率",
        "roe": "净资收益率", "net_profit_yoy": "净利润同比", "streak": "连榜",
    }
    save_cols = list(csv_rename.keys())
    save_df = top_df[[c for c in save_cols if c in top_df.columns]].rename(columns=csv_rename)
    save_df.to_csv(out_path, index=False, encoding="utf-8-sig")
    logger.info(f"完整候选列表（含基本面）已保存至 {out_path}")


def main():
    parser = argparse.ArgumentParser(description="A 股量化交易系统")
    parser.add_argument("phase", choices=["ingest", "sync", "collect", "fetch-fund", "fetch-flow", "fetch-financial", "fetch-reports", "fetch-basic", "update", "1", "2", "3", "scan"],
                        help="运行阶段：sync=同步TDX数据 | collect=采集原始数据 | fetch-fund=拉取基本面 | fetch-flow=拉取资金流向 | fetch-financial=拉取财务指标 | fetch-reports=拉取研报/EPS | fetch-basic=拉取股票名称/行业 | update=每日增量更新 | 1=特征工程 | 2=训练 | 3=回测 | scan=选股扫描")
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
    parser.add_argument("--confirm", type=int, default=2,
                        help="scan/Phase3: 连榜确认阈值 K，仅连续 K 次在榜的票可建仓（过滤一日游）。默认 2（回测验证最优）；传 0 关闭")
    parser.add_argument("--holdings", default=None,
                        help="scan: 现持仓代码（逗号分隔），输出 继续持有/卖出/新建仓 三栏操作建议")
    parser.add_argument("--retrain", action="store_true", dest="retrain",
                        help="update: 增量更新完成后触发 Phase 2 滚动窗口重训")
    parser.add_argument("--since", default=None,
                        help="fetch-financial / fetch-reports: 增量起点（YYYY-MM-DD），不填则按各股增量水位自动判断")
    parser.add_argument("--mode", default="both", dest="report_mode",
                        choices=["report", "eps", "both"],
                        help="fetch-reports: report=仅研报评级 | eps=仅EPS共识 | both=两者都拉（默认）")
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
        "fetch-financial": fetch_financial,
        "fetch-reports": fetch_reports,
        "fetch-basic": fetch_basic,
        "update": update,
        "1": phase1,
        "2": phase2,
        "3": phase3,
        "scan": scan,
    }
    phases[args.phase](args)


if __name__ == "__main__":
    main()
