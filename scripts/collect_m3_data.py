#!/usr/bin/env python3
"""M3 新因子数据采集脚本

用法：
  python scripts/collect_m3_data.py lhb              # 龙虎榜历史（922天，约10分钟）
  python scripts/collect_m3_data.py report            # 研报数量（5644只，约30分钟）
  python scripts/collect_m3_data.py eps               # EPS预测（5644只，约30分钟）
  python scripts/collect_m3_data.py all               # 依次全部运行

说明：
  - 增量模式：已采集的自动跳过，中断后可重跑续采
  - 关闭 VPN 后运行（akshare 需直连东财/THS接口）
"""
import argparse
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.utils.logger import setup_logger

logger = setup_logger("collect_m3")


def _get_trading_dates(start: str, end: str) -> list[date]:
    """从 DuckDB features 表获取指定范围内的真实交易日列表。"""
    from src.dal.connection import get_db
    start_d = pd.Timestamp(start).date()
    end_d = pd.Timestamp(end).date()
    try:
        conn = get_db()
        rows = conn.execute(
            "SELECT DISTINCT date FROM features WHERE date >= ? AND date <= ? ORDER BY date",
            [start_d, end_d],
        ).fetchall()
        if rows:
            return [r[0] if isinstance(r[0], date) else r[0].date() for r in rows]
        logger.warning("features 表中无数据，使用 bdate_range 近似交易日")
    except Exception as exc:
        logger.warning(f"查询 features 表失败，使用 bdate_range 近似交易日: {exc}")
    return list(pd.bdate_range(start, end).date)


def collect_lhb(start_date: str = "2022-07-01", end_date: str = None):
    """采集龙虎榜历史（按日期，全市场一次性拉取）。"""
    from src.collectors.signal_collector import SignalCollector

    if end_date is None:
        end_date = date.today().strftime("%Y-%m-%d")

    logger.info(f"龙虎榜采集范围：{start_date} ~ {end_date}")
    trading_dates = _get_trading_dates(start_date, end_date)
    logger.info(f"共 {len(trading_dates)} 个交易日需要检查")

    collector = SignalCollector()
    ok = cached = fail = 0

    for d in tqdm(trading_dates, desc="龙虎榜日报"):
        stats = collector.fetch_all(codes=[], date=d, incremental=True)
        ok += stats.ok
        cached += stats.cached
        fail += stats.fail

    logger.info(f"龙虎榜完成 | 新采：{ok}，跳过缓存：{cached}，失败（无上榜/接口）：{fail}")
    return ok, cached, fail


def collect_reports(delay: float = 0.4):
    """采集研报历史（per-stock，东财接口）。"""
    from src.collectors.report_collector import ReportCollector
    from src.data.tdx_reader import get_active_tdx_codes

    codes = get_active_tdx_codes()
    logger.info(f"研报采集：{len(codes)} 只股票，delay={delay}s")

    collector = ReportCollector(delay=delay)
    stats = collector.fetch_all(codes, mode="report", incremental=True)
    logger.info(f"研报完成 | 新采：{stats.ok}，跳过：{stats.cached}，失败：{stats.fail}")
    return stats


def collect_eps(delay: float = 0.5):
    """采集 EPS 一致预期（per-stock，同花顺THS接口）。"""
    from src.collectors.report_collector import ReportCollector
    from src.data.tdx_reader import get_active_tdx_codes

    codes = get_active_tdx_codes()
    logger.info(f"EPS采集：{len(codes)} 只股票，delay={delay}s")

    collector = ReportCollector(delay=delay)
    stats = collector.fetch_all(codes, mode="eps", incremental=True)
    logger.info(f"EPS完成 | 新采：{stats.ok}，跳过：{stats.cached}，失败：{stats.fail}")
    return stats


def main():
    parser = argparse.ArgumentParser(description="M3 新因子数据采集")
    parser.add_argument("target", choices=["lhb", "report", "eps", "all"],
                        help="采集目标：lhb=龙虎榜 | report=研报 | eps=EPS预测 | all=依次全部")
    parser.add_argument("--start", default="2022-07-01",
                        help="LHB 历史起始日期（默认 2022-07-01）")
    parser.add_argument("--end", default=None,
                        help="LHB 历史截止日期（默认今日）")
    parser.add_argument("--delay", type=float, default=None,
                        help="请求间隔（秒）。report 默认0.4，eps 默认0.5")
    args = parser.parse_args()

    if args.target in ("lhb", "all"):
        logger.info("=== Step 1: 龙虎榜历史 ===")
        collect_lhb(args.start, args.end)

    if args.target in ("report", "all"):
        logger.info("=== Step 2: 研报数量 ===")
        collect_reports(delay=args.delay or 0.4)

    if args.target in ("eps", "all"):
        logger.info("=== Step 3: EPS 一致预期 ===")
        collect_eps(delay=args.delay or 0.5)

    logger.info("采集完成。运行以下命令更新特征：")
    logger.info("  python main.py 1       # 全量重建（~20分钟）")
    logger.info("  python main.py 2 --rolling  # 重训模型")


if __name__ == "__main__":
    main()
