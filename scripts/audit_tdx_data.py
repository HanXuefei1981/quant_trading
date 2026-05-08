"""通达信本地日线数据稽核脚本

输出：
1. 各市场 A 股文件计数
2. 时间覆盖（最早 / 最近交易日，含中位数）
3. 各股交易行数分布
4. 异常价格（0 值、负值、极端涨跌）
5. 关键列 NaN 情况
6. 与配置 START_DATE / END_DATE 覆盖核对
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd
from tqdm import tqdm

from config.settings import TDX_VIPDOC_DIR, START_DATE, END_DATE
from src.data.tdx_reader import get_all_tdx_codes, _market_prefix, _RECORD_FMT, _RECORD_SIZE
import struct


def _read_full_day_file(code: str) -> pd.DataFrame | None:
    """读完整 .day（不做日期过滤、不做 0 值过滤），用于稽核"""
    mkt, prefix = _market_prefix(code)
    if mkt is None:
        return None
    path = TDX_VIPDOC_DIR / mkt / "lday" / f"{prefix}{code}.day"
    if not path.exists():
        return None
    raw = path.read_bytes()
    n = len(raw) // _RECORD_SIZE
    if n == 0:
        return None
    rows = []
    for date_int, open_, high, low, close, amount, volume, _ in struct.iter_unpack(
        _RECORD_FMT, raw[: n * _RECORD_SIZE]
    ):
        rows.append((date_int, open_ / 100.0, high / 100.0,
                     low / 100.0, close / 100.0, float(amount), int(volume)))
    df = pd.DataFrame(rows, columns=["date_int", "open", "high", "low", "close", "amount", "volume"])
    df["date"] = pd.to_datetime(df["date_int"].astype(str), format="%Y%m%d", errors="coerce")
    df = df.drop(columns=["date_int"])
    return df


def main():
    print("=" * 70)
    print(f"通达信数据稽核  目录: {TDX_VIPDOC_DIR}")
    print(f"配置区间: {START_DATE} ~ {END_DATE}")
    print("=" * 70)

    if not TDX_VIPDOC_DIR.exists():
        print(f"ERROR: 通达信数据目录不存在: {TDX_VIPDOC_DIR}")
        return

    codes_df = get_all_tdx_codes()
    print(f"\n[1] A 股文件计数")
    print(f"    总计: {len(codes_df)} 只")
    prefix_dist = codes_df["code"].str[:3].value_counts().sort_index()
    print(f"    代码前缀分布（前 15）:")
    for p, c in prefix_dist.head(15).items():
        print(f"      {p}xxx : {c}")

    cfg_start = pd.to_datetime(START_DATE)
    cfg_end = pd.to_datetime(END_DATE)

    rows_stats = []        # (code, n_total, n_in_range, dmin, dmax)
    bad_zero = []          # close <= 0
    bad_neg = []           # any of OHLC < 0
    bad_extreme = []       # 单日涨跌 > 22%
    nan_issues = []        # NaN 行
    not_cover_end = []     # 最近交易日 < cfg_end - 5
    no_data_in_range = []  # 在配置区间内 0 行
    parse_fail = []        # 读不出来

    codes = codes_df["code"].tolist()
    for code in tqdm(codes, desc="稽核"):
        df = _read_full_day_file(code)
        if df is None or df.empty:
            parse_fail.append(code)
            continue

        n_total = len(df)
        in_range = df[(df["date"] >= cfg_start) & (df["date"] <= cfg_end)]
        n_in_range = len(in_range)
        dmin = df["date"].min()
        dmax = df["date"].max()
        rows_stats.append((code, n_total, n_in_range, dmin, dmax))

        # NaN
        for col in ["date", "open", "high", "low", "close", "volume"]:
            if df[col].isna().any():
                nan_issues.append((code, col, int(df[col].isna().sum())))

        # 0 close（在配置区间内）
        n_zero = int((in_range["close"] <= 0).sum())
        if n_zero > 0:
            bad_zero.append((code, n_zero))

        # 负值
        ohlc_neg = in_range[(in_range[["open", "high", "low", "close"]] < 0).any(axis=1)]
        if not ohlc_neg.empty:
            bad_neg.append((code, len(ohlc_neg)))

        # 极端涨跌（基于配置区间内非 0 close）
        valid_ir = in_range[in_range["close"] > 0].copy()
        if len(valid_ir) > 1:
            valid_ir = valid_ir.sort_values("date")
            ret = valid_ir["close"].pct_change()
            n_ext = int((ret.abs() > 0.22).sum())
            if n_ext > 0:
                bad_extreme.append((code, n_ext))

        # 时间覆盖：股票最近一条数据离配置 END_DATE 太远（> 5 个自然日）
        if pd.notna(dmax) and (cfg_end - dmax).days > 5:
            not_cover_end.append((code, dmax.strftime("%Y-%m-%d")))

        if n_in_range == 0:
            no_data_in_range.append(code)

    if not rows_stats:
        print("\nERROR: 没有任何文件能成功解析")
        return

    stats_df = pd.DataFrame(
        rows_stats, columns=["code", "n_total", "n_in_range", "dmin", "dmax"]
    )

    print(f"\n[2] 时间覆盖")
    print(f"    全样本最早交易日（minmin）: {stats_df['dmin'].min().strftime('%Y-%m-%d')}")
    print(f"    全样本最晚交易日（maxmax）: {stats_df['dmax'].max().strftime('%Y-%m-%d')}")
    print(f"    各股最近交易日中位数      : {stats_df['dmax'].median().strftime('%Y-%m-%d')}")
    # 最近交易日分布
    last_top = stats_df['dmax'].dt.strftime('%Y-%m-%d').value_counts().sort_index(ascending=False).head(10)
    print(f"    最近交易日 Top10:")
    for d, c in last_top.items():
        print(f"      {d} : {c}")

    print(f"\n[3] 行数分布（配置区间内 {START_DATE}~{END_DATE}）")
    s = stats_df["n_in_range"]
    print(f"    min  : {s.min()}")
    print(f"    p10  : {int(s.quantile(0.1))}")
    print(f"    median: {int(s.median())}")
    print(f"    p90  : {int(s.quantile(0.9))}")
    print(f"    max  : {s.max()}")
    print(f"    < 60 行的股票数 : {(s < 60).sum()}")
    print(f"    < 250 行（< 1 年）的股票数: {(s < 250).sum()}")

    print(f"\n[4] 数据异常")
    print(f"    解析失败/空文件         : {len(parse_fail)}")
    print(f"    配置区间内 0 行         : {len(no_data_in_range)}")
    print(f"    存在 close<=0 的股票数  : {len(bad_zero)}")
    print(f"    存在 OHLC<0 的股票数    : {len(bad_neg)}")
    print(f"    单日涨跌>22% 的股票数   : {len(bad_extreme)}")
    print(f"    关键列 NaN 异常条目     : {len(nan_issues)}")

    if bad_zero:
        print("    ↳ close<=0 示例（前 5）:")
        for code, n in bad_zero[:5]:
            print(f"        {code}: {n} 行")
    if bad_extreme:
        print("    ↳ 单日涨跌>22% 示例（前 5）:")
        for code, n in bad_extreme[:5]:
            print(f"        {code}: {n} 次")

    print(f"\n[5] 是否覆盖到配置 END_DATE={END_DATE}")
    print(f"    距 END_DATE 落后 >5 天的股票数: {len(not_cover_end)}")
    if not_cover_end:
        # 按落后程度倒序展示几条
        nc_df = pd.DataFrame(not_cover_end, columns=["code", "last_date"])
        nc_df["last_date"] = pd.to_datetime(nc_df["last_date"])
        nc_df = nc_df.sort_values("last_date").head(10)
        print(f"    ↳ 最早脱节示例:")
        for _, row in nc_df.iterrows():
            print(f"        {row['code']}: 最近交易日 {row['last_date'].strftime('%Y-%m-%d')}")

    print(f"\n[6] 是否覆盖到配置 START_DATE={START_DATE}")
    early_enough = (stats_df["dmin"] <= cfg_start).sum()
    print(f"    最早数据 <= {START_DATE} 的股票数: {early_enough} / {len(stats_df)}")
    print(f"    （新股不会满足，正常）")

    print("\n" + "=" * 70)
    print("稽核结束")
    print("=" * 70)


if __name__ == "__main__":
    main()
