"""scan 信号富信息拼装：给信号表关联股票名称、行业、估值与最新财务指标。

数据来源：
  - 名称/行业：stock_basic 表（`python main.py fetch-basic` 采集缓存）
  - 估值（PE/PB/PS/总市值）：fundamentals 表（指定交易日截面）
  - 财务（ROE/净利润同比）：financial_indicator 表（每股最新 end_date）
"""
from __future__ import annotations

from datetime import date

import duckdb
import pandas as pd

# market_cap 源单位为元，展示用亿元
_YI = 1e8


def enrich_signals(
    signal_df: pd.DataFrame,
    conn: duckdb.DuckDBPyConnection,
    trade_date: date,
) -> pd.DataFrame:
    """为信号表追加 name/industry/market_cap_yi/pe_ttm/pb/ps/roe/net_profit_yoy 列。

    缺失数据（股票不在辅助表中、当日无估值、无财报）对应列填 NaN，不抛错。
    原信号列（code/close/signal/rank 等）原样保留。
    """
    df = signal_df.copy()
    df["code"] = df["code"].astype(str).str.zfill(6)
    codes = df["code"].tolist()
    if not codes:
        return df

    ph = ", ".join("?" for _ in codes)

    sb = conn.execute(
        f"SELECT code, name, industry, area FROM stock_basic WHERE code IN ({ph})",
        codes,
    ).df()

    fund = conn.execute(
        f"SELECT code, market_cap, pe_ttm, pb, ps FROM fundamentals "
        f"WHERE CAST(date AS DATE) = ? AND code IN ({ph})",
        [trade_date, *codes],
    ).df()

    # 每股取最新 end_date 的财务指标
    fin = conn.execute(
        f"SELECT code, roe, net_profit_yoy FROM ("
        f"  SELECT code, roe, net_profit_yoy, "
        f"         ROW_NUMBER() OVER (PARTITION BY code ORDER BY end_date DESC) AS rn "
        f"  FROM financial_indicator WHERE code IN ({ph})"
        f") WHERE rn = 1",
        codes,
    ).df()

    for part in (sb, fund, fin):
        if not part.empty:
            part["code"] = part["code"].astype(str).str.zfill(6)

    out = (
        df.merge(sb, on="code", how="left")
        .merge(fund, on="code", how="left")
        .merge(fin, on="code", how="left")
    )
    out["market_cap_yi"] = out["market_cap"] / _YI
    return out
