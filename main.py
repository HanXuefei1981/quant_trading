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

    # 2. 解压到 TDX 目录
    logger.info(f"开始解压 {zip_path.name} → {TDX_VIPDOC_DIR} ...")
    with zipfile.ZipFile(str(zip_path), "r") as zf:
        zf.extractall(str(TDX_VIPDOC_DIR))
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


def phase1(args):
    """Phase 1: 数据拉取 + 特征工程（须先通过 sync 确认）"""
    # 门控：必须先通过 sync 确认
    if not _SYNC_STATE_FILE.exists():
        logger.error("未找到数据同步确认记录，请先运行:")
        logger.error("  python main.py sync --zip <通达信压缩包路径>")
        return

    state = json.loads(_SYNC_STATE_FILE.read_text(encoding="utf-8"))
    logger.info(f"已确认数据截止日期: {state['confirmed_date']}  来源: {state.get('zip_file', '未知')}")

    # 基本面缓存检查：缺失数过半给警告（不阻断，但提示）
    fund_dir = Path(__file__).parent / "data" / "fundamentals"
    fund_count = len(list(fund_dir.glob("*.parquet"))) if fund_dir.exists() else 0
    if fund_count < 1000:
        logger.warning(
            f"基本面缓存仅 {fund_count} 只股票，模型将退化为纯技术因子。"
        )
        logger.warning("建议先运行: python main.py fetch-fund  （约 1-2 小时）")

    from src.data.pipeline import run_data_pipeline
    df = run_data_pipeline(
        sample_size=args.sample,
        delay=args.delay,
        use_cache=False,   # 全量更新，不使用缓存
    )
    logger.info(f"Phase 1 完成，数据集形状: {df.shape}")
    logger.info(f"特征列数: {df.shape[1]}")
    logger.info(f"时间范围: {df['date'].min()} ~ {df['date'].max()}")
    logger.info(f"股票数量: {df['code'].nunique()}")


def phase2(args):
    """Phase 2: 模型训练"""
    from src.data.pipeline import load_processed_data
    from src.models.trainer import run_training
    from src.models.evaluator import run_evaluation

    logger.info("Phase 2 开始：加载数据")
    try:
        df = load_processed_data()
    except FileNotFoundError:
        logger.error("未找到处理后数据，请先运行 phase1 生成特征数据")
        return

    logger.info(f"数据加载完成：{len(df)} 行，{df['code'].nunique()} 只股票")
    logger.info(f"时间范围：{df['date'].min().date()} ~ {df['date'].max().date()}")

    lgbm_model, ridge_model, w_lgbm, w_ridge, feature_cols, train_df, val_df, test_df = run_training(df)
    run_evaluation(lgbm_model, feature_cols, train_df, val_df, test_df)

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
    from src.data.pipeline import load_inference_data
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
    parser.add_argument("phase", choices=["sync", "fetch-fund", "1", "2", "3", "scan"],
                        help="运行阶段：sync=同步TDX数据 | fetch-fund=拉取基本面 | 1=特征工程 | 2=训练 | 3=回测 | scan=选股扫描")
    parser.add_argument("--zip", default=None,
                        help="sync 命令专用：通达信压缩包完整路径（如 /path/to/hsjday-2026-05-12.zip）")
    parser.add_argument("--refresh", action="store_true",
                        help="fetch-fund 专用：忽略本地缓存，强制重新拉取")
    parser.add_argument("--sample", type=int, default=None, help="调试时限制股票数量")
    parser.add_argument("--delay", type=float, default=0.3, help="拉取间隔（秒）")
    parser.add_argument("--no-cache", action="store_true", help="强制重新下载数据（phase1 已默认全量更新）")
    parser.add_argument("--top-k", type=int, default=50, dest="top_k", help="Phase3: 每期持仓股票数（默认50）")
    parser.add_argument("--rebalance", type=int, default=5, help="Phase3: 调仓间隔交易日数（默认5）")
    parser.add_argument("--max-weight", type=float, default=0.05, dest="max_weight", help="Phase3: 单股最大权重（默认0.05）")
    parser.add_argument("--max-sector-weight", type=float, default=0.40, dest="max_sector_weight", help="Phase3: 板块最大权重（默认0.40）")
    parser.add_argument("--max-turnover", type=float, default=0.5, dest="max_turnover", help="Phase3: 单次最大单向换手率（默认0.5）")
    parser.add_argument("--equal-weight", action="store_true", dest="equal_weight", help="Phase3: 等权重替代信号加权")
    parser.add_argument("--replace-only", action="store_true", dest="replace_only", help="Phase3/scan: 散户模式，只换出跌出候选池的股票，不做存量再平衡")
    args = parser.parse_args()

    if args.phase == "sync" and not args.zip:
        parser.error("sync 命令需要提供 --zip 参数，例如: python main.py sync --zip /path/to/hsjday-YYYY-MM-DD.zip")

    phases = {
        "sync": sync,
        "fetch-fund": fetch_fund,
        "1": phase1,
        "2": phase2,
        "3": phase3,
        "scan": scan,
    }
    phases[args.phase](args)


if __name__ == "__main__":
    main()
