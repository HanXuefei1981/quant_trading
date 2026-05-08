"""主入口：分阶段运行量化系统"""
import argparse
import logging
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))

from src.utils.logger import setup_logger

logger = setup_logger("quant")


def phase1(args):
    """Phase 1: 数据拉取 + 特征工程"""
    from src.data.pipeline import run_data_pipeline
    df = run_data_pipeline(
        sample_size=args.sample,
        delay=args.delay,
        use_cache=not args.no_cache,
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

    model, feature_cols, train_df, val_df, test_df = run_training(df)
    run_evaluation(model, feature_cols, train_df, val_df, test_df)

    logger.info("Phase 2 完成，模型已保存至 data/models/")


def phase3(args):
    """Phase 3: 回测"""
    from src.data.pipeline import load_processed_data
    from src.models.trainer import load_model, time_split
    from src.backtest.engine import generate_signals, run_backtest, build_benchmark
    from src.backtest.metrics import print_report
    from config.settings import PROCESSED_DIR

    logger.info("Phase 3 开始：加载数据与模型")
    df = load_processed_data()
    model, feature_cols = load_model()

    _, val_df, test_df = time_split(df)
    backtest_df = pd.concat([val_df, test_df], ignore_index=True)

    start = str(backtest_df["date"].min().date())
    end   = str(backtest_df["date"].max().date())
    logger.info(f"回测区间: {start} ~ {end}")

    signal_df = generate_signals(backtest_df, model, feature_cols)

    top_k          = getattr(args, "top_k", 50)
    rebalance_days = getattr(args, "rebalance", 5)

    result = run_backtest(signal_df, start, end,
                          top_k=top_k, rebalance_every=rebalance_days)
    benchmark = build_benchmark(signal_df, start, end)

    out_dir = PROCESSED_DIR.parent / "backtest"
    print_report(result["equity"], benchmark, result["trades"],
                 result["initial_capital"], out_dir)

    logger.info("Phase 3 完成，结果已保存至 data/backtest/")


def main():
    parser = argparse.ArgumentParser(description="A 股量化交易系统")
    parser.add_argument("phase", choices=["1", "2", "3"], help="运行阶段")
    parser.add_argument("--sample", type=int, default=None, help="调试时限制股票数量")
    parser.add_argument("--delay", type=float, default=0.3, help="拉取间隔（秒）")
    parser.add_argument("--no-cache", action="store_true", help="强制重新下载数据")
    parser.add_argument("--top-k", type=int, default=50, dest="top_k", help="Phase3: 每期持仓股票数（默认50）")
    parser.add_argument("--rebalance", type=int, default=5, help="Phase3: 调仓间隔交易日数（默认5）")
    args = parser.parse_args()

    phases = {"1": phase1, "2": phase2, "3": phase3}
    phases[args.phase](args)


if __name__ == "__main__":
    main()
