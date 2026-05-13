"""截面分位标签：替代固定阈值，适配牛熊切换"""
import numpy as np
import pandas as pd

from config.settings import LABEL_TOP_PCT, LABEL_BOTTOM_PCT


def add_cross_sectional_label(
    df: pd.DataFrame,
    top_pct: float = LABEL_TOP_PCT,
    bottom_pct: float = LABEL_BOTTOM_PCT,
) -> pd.DataFrame:
    """
    对合并后的全市场数据，按交易日截面对 future_ret 排名，分三档打标签。

      Top top_pct      →  1  （跑赢市场，相对强势）
      Bottom bottom_pct → -1  （跑输市场，相对弱势）
      中间              →  0

    要求 df 已包含 future_ret 列，且已过滤掉 future_ret 为 NaN/inf 的行。
    """
    df = df.copy()
    rank = df.groupby("date")["future_ret"].rank(pct=True, na_option="keep")
    df["label"] = np.where(
        rank.isna(), np.nan,
        np.where(rank >= 1.0 - top_pct, 1.0,
                 np.where(rank <= bottom_pct, -1.0, 0.0))
    )
    return df
