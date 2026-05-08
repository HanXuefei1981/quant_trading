"""回测绩效指标计算与报告"""
import logging
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def calc_metrics(equity: pd.Series, initial_capital: float, risk_free: float = 0.02) -> dict:
    """计算常用回测指标"""
    daily_ret = equity.pct_change().dropna()
    total_days = len(daily_ret)
    ann_factor = 252

    total_return = equity.iloc[-1] / initial_capital - 1
    ann_return = (1 + total_return) ** (ann_factor / max(total_days, 1)) - 1

    ann_vol = daily_ret.std() * np.sqrt(ann_factor)
    sharpe = (ann_return - risk_free) / ann_vol if ann_vol > 0 else 0.0

    rolling_max = equity.cummax()
    drawdown = (equity - rolling_max) / rolling_max
    max_drawdown = drawdown.min()

    calmar = ann_return / abs(max_drawdown) if max_drawdown < 0 else float("inf")

    win_days = (daily_ret > 0).sum()
    win_rate = win_days / total_days if total_days > 0 else 0.0

    return {
        "总收益率":     f"{total_return*100:.2f}%",
        "年化收益率":   f"{ann_return*100:.2f}%",
        "年化波动率":   f"{ann_vol*100:.2f}%",
        "夏普比率":     f"{sharpe:.3f}",
        "最大回撤":     f"{max_drawdown*100:.2f}%",
        "卡玛比率":     f"{calmar:.3f}",
        "日胜率":       f"{win_rate*100:.1f}%",
        "交易天数":     total_days,
    }


def print_report(
    strategy_equity: pd.Series,
    benchmark_equity: pd.Series,
    trades: pd.DataFrame,
    initial_capital: float,
    out_dir: Path,
) -> None:
    """打印绩效报告并保存图表"""
    logger.info("\n" + "=" * 60)
    logger.info("【策略绩效】")
    s_metrics = calc_metrics(strategy_equity, initial_capital)
    for k, v in s_metrics.items():
        logger.info(f"  {k}: {v}")

    logger.info("\n【基准绩效（等权全市场）】")
    b_metrics = calc_metrics(benchmark_equity, initial_capital)
    for k, v in b_metrics.items():
        logger.info(f"  {k}: {v}")

    if not trades.empty:
        logger.info(f"\n【交易统计】")
        logger.info(f"  总交易笔数: {len(trades)}")
        logger.info(f"  买入笔数:   {(trades['side']=='buy').sum()}")
        logger.info(f"  卖出笔数:   {(trades['side']=='sell').sum()}")
        logger.info(f"  总手续费:   {trades['cost'].sum():,.0f} 元")
    logger.info("=" * 60)

    _save_chart(strategy_equity, benchmark_equity, out_dir)
    _save_csv(strategy_equity, benchmark_equity, trades, out_dir)


def _save_chart(strategy: pd.Series, benchmark: pd.Series, out_dir: Path) -> None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates

        fig, axes = plt.subplots(2, 1, figsize=(12, 8), gridspec_kw={"height_ratios": [3, 1]})

        # 上图：净值曲线
        ax = axes[0]
        s_norm = strategy / strategy.iloc[0]
        b_norm = benchmark / benchmark.iloc[0]
        ax.plot(s_norm.index, s_norm.values, label="Strategy", color="steelblue", linewidth=1.5)
        ax.plot(b_norm.index, b_norm.values, label="Benchmark (EW)", color="orange",
                linewidth=1.2, linestyle="--")
        ax.axhline(1.0, color="gray", linewidth=0.8, linestyle=":")
        ax.set_ylabel("Normalized NAV")
        ax.set_title("Strategy vs Benchmark")
        ax.legend()
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
        ax.xaxis.set_major_locator(mdates.MonthLocator(interval=2))
        plt.setp(ax.xaxis.get_majorticklabels(), rotation=30, ha="right")
        ax.grid(alpha=0.3)

        # 下图：策略回撤
        rolling_max = strategy.cummax()
        drawdown = (strategy - rolling_max) / rolling_max * 100
        ax2 = axes[1]
        ax2.fill_between(drawdown.index, drawdown.values, 0, color="red", alpha=0.4)
        ax2.set_ylabel("Drawdown (%)")
        ax2.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
        ax2.xaxis.set_major_locator(mdates.MonthLocator(interval=2))
        plt.setp(ax2.xaxis.get_majorticklabels(), rotation=30, ha="right")
        ax2.grid(alpha=0.3)

        plt.tight_layout()
        out_dir.mkdir(parents=True, exist_ok=True)
        fig.savefig(out_dir / "backtest_equity.png", dpi=120)
        plt.close(fig)
        logger.info(f"净值曲线图已保存至 {out_dir / 'backtest_equity.png'}")
    except Exception as e:
        logger.warning(f"图表保存失败: {e}")


def _save_csv(strategy: pd.Series, benchmark: pd.Series, trades: pd.DataFrame, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"strategy": strategy, "benchmark": benchmark}).to_csv(
        out_dir / "equity_curve.csv"
    )
    if not trades.empty:
        trades.to_csv(out_dir / "trades.csv", index=False)
    logger.info(f"数据已保存至 {out_dir}")
