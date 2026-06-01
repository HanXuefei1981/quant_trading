"""Tushare Pro 底层数据拉取函数

单位约定（与 DuckDB 表一致）：
  kline.volume      股（手×100）
  kline.amount      元（千元×1000）
  fundamentals.market_cap / float_market_cap  元（万元×10000）
  fundamentals.total_shares / float_shares    股（万股×10000）
  northbound.north_net_inflow  元（万元×10000）
  northbound.hgt_yi / sgt_yi  亿元（万元÷10000）
  fund_flow.major_net_inflow   元（万元×10000）
  fund_flow.major_net_pct      % (主力净额÷全市场买入额×100)
  lhb.lhb_net_buy / buy / sell 元（top_list 原生元，无转换）
"""
from __future__ import annotations

import logging
from datetime import date

import pandas as pd

from config.settings import START_DATE

logger = logging.getLogger(__name__)

_DEFAULT_SINCE = "20100101"


# ──────────────────────────── 辅助函数 ────────────────────────────

def _to_ts_code(code: str) -> str:
    """6 位代码 → tushare 格式，如 '000001' → '000001.SZ'"""
    if code.startswith(("6", "9")):
        return f"{code}.SH"
    if code.startswith("8"):
        return f"{code}.BJ"
    return f"{code}.SZ"


def _date_str(d: date | None, fallback: str = _DEFAULT_SINCE) -> str:
    return d.strftime("%Y%m%d") if d is not None else fallback


def _safe(df: pd.DataFrame, col: str) -> pd.Series:
    """返回列（数值化），不存在时返回 0 Series"""
    if col in df.columns:
        return pd.to_numeric(df[col], errors="coerce").fillna(0)
    return pd.Series(0.0, index=df.index)


# ──────────────────────── 1. K 线日线（增量） ─────────────────────

def fetch_kline_daily(code: str, since: date | None = None) -> pd.DataFrame | None:
    """拉取单只股票的日线行情（tushare pro.daily）。

    since 为 None 时从 START_DATE 全量拉取，否则从 since+1 日起增量拉取。
    返回列：date, code, open, high, low, close, amount(元), volume(股)
    """
    from src.data.tushare_client import get_pro_api
    from datetime import date as date_cls

    pro = get_pro_api()
    ts_code = _to_ts_code(code)
    start = _date_str(since, fallback=START_DATE)
    today = date_cls.today().strftime("%Y%m%d")

    try:
        raw = pro.daily(ts_code=ts_code, start_date=start, end_date=today)
    except Exception as exc:
        logger.warning("tushare daily 拉取失败 %s: %s", code, exc)
        return None

    if raw is None or raw.empty:
        return None

    df = raw[["trade_date", "open", "high", "low", "close", "vol", "amount"]].copy()
    df["date"] = pd.to_datetime(df["trade_date"], format="%Y%m%d")
    df["code"] = code
    df["volume"] = (pd.to_numeric(df["vol"], errors="coerce") * 100).astype("int64")
    df["amount"] = pd.to_numeric(df["amount"], errors="coerce") * 1000  # 千元→元

    df = df[["date", "code", "open", "high", "low", "close", "amount", "volume"]]
    return df.sort_values("date").reset_index(drop=True)


def fetch_kline_by_date(trade_date: date) -> pd.DataFrame | None:
    """拉取全市场单日 K 线（tushare pro.daily，一次调用取全市场）。

    trade_date 为非交易日时返回 None（tushare 返回空表）。
    返回列：date, code, open, high, low, close, amount(元), volume(股)
    """
    from src.data.tushare_client import get_pro_api

    pro = get_pro_api()
    date_str = trade_date.strftime("%Y%m%d")
    try:
        raw = pro.daily(trade_date=date_str)
    except Exception as exc:
        logger.warning("fetch_kline_by_date %s 失败: %s", date_str, exc)
        return None

    if raw is None or raw.empty:
        return None

    df = raw[["ts_code", "trade_date", "open", "high", "low", "close", "vol", "amount"]].copy()
    df["date"] = pd.to_datetime(df["trade_date"], format="%Y%m%d")
    df["code"] = df["ts_code"].str[:6]
    df["volume"] = (pd.to_numeric(df["vol"], errors="coerce") * 100).astype("int64")
    df["amount"] = pd.to_numeric(df["amount"], errors="coerce") * 1000  # 千元→元
    return df[["date", "code", "open", "high", "low", "close", "amount", "volume"]].reset_index(drop=True)


# ──────────────────────── 2. 基本面（每日快照） ───────────────────

def fetch_daily_basic(code: str, since: date | None = None) -> pd.DataFrame | None:
    """拉取单只股票的每日估值与市值（tushare pro.daily_basic）。

    返回列对应 DuckDB fundamentals 表：
      date, code, pe_ttm, pe_static, pb, ps, pcf(None), peg(None),
      market_cap(元), float_market_cap(元), total_shares(股), float_shares(股)
    """
    from src.data.tushare_client import get_pro_api
    from datetime import date as date_cls

    pro = get_pro_api()
    ts_code = _to_ts_code(code)
    start = _date_str(since, fallback=_DEFAULT_SINCE)
    today = date_cls.today().strftime("%Y%m%d")

    try:
        raw = pro.daily_basic(ts_code=ts_code, start_date=start, end_date=today)
    except Exception as exc:
        logger.warning("tushare daily_basic 拉取失败 %s: %s", code, exc)
        return None

    if raw is None or raw.empty:
        return None

    need = ["trade_date", "pe_ttm", "pe", "pb", "ps",
            "total_share", "float_share", "total_mv", "circ_mv"]
    df = raw[[c for c in need if c in raw.columns]].copy()

    for col in need[1:]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    df["date"] = pd.to_datetime(df["trade_date"], format="%Y%m%d")
    df["code"] = code
    df["pe_static"] = df.get("pe")          # 静态 PE
    df["pcf"] = None
    df["peg"] = None
    df["total_shares"] = df.get("total_share", pd.Series(dtype=float)) * 1e4   # 万股→股
    df["float_shares"] = df.get("float_share", pd.Series(dtype=float)) * 1e4
    df["market_cap"] = df.get("total_mv", pd.Series(dtype=float)) * 1e4        # 万元→元
    df["float_market_cap"] = df.get("circ_mv", pd.Series(dtype=float)) * 1e4

    keep = ["date", "code", "pe_ttm", "pe_static", "pb", "ps", "pcf", "peg",
            "market_cap", "float_market_cap", "total_shares", "float_shares"]
    df = df[[c for c in keep if c in df.columns]]
    return df.sort_values("date").reset_index(drop=True)


def fetch_fundamentals_by_date(trade_date: date) -> pd.DataFrame | None:
    """拉取全市场单日基本面快照（tushare pro.daily_basic，一次调用取全市场）。

    trade_date 为非交易日时返回 None。
    返回列：date, code, pe_ttm, pe_static, pb, ps, pcf, peg,
            market_cap(元), float_market_cap(元), total_shares(股), float_shares(股)
    """
    from src.data.tushare_client import get_pro_api

    pro = get_pro_api()
    date_str = trade_date.strftime("%Y%m%d")
    try:
        raw = pro.daily_basic(trade_date=date_str)
    except Exception as exc:
        logger.warning("fetch_fundamentals_by_date %s 失败: %s", date_str, exc)
        return None

    if raw is None or raw.empty:
        return None

    need = ["ts_code", "trade_date", "pe_ttm", "pe", "pb", "ps",
            "total_share", "float_share", "total_mv", "circ_mv"]
    df = raw[[c for c in need if c in raw.columns]].copy()

    for col in need[2:]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    df["date"] = pd.to_datetime(df["trade_date"], format="%Y%m%d")
    df["code"] = df["ts_code"].str[:6]
    df["pe_static"] = df.get("pe")
    df["pcf"] = None
    df["peg"] = None
    df["total_shares"] = df.get("total_share", pd.Series(dtype=float)) * 1e4
    df["float_shares"] = df.get("float_share", pd.Series(dtype=float)) * 1e4
    df["market_cap"] = df.get("total_mv", pd.Series(dtype=float)) * 1e4
    df["float_market_cap"] = df.get("circ_mv", pd.Series(dtype=float)) * 1e4

    keep = ["date", "code", "pe_ttm", "pe_static", "pb", "ps", "pcf", "peg",
            "market_cap", "float_market_cap", "total_shares", "float_shares"]
    return df[[c for c in keep if c in df.columns]].reset_index(drop=True)


def fetch_stock_basic() -> pd.DataFrame | None:
    """拉取全市场股票基础信息（tushare pro.stock_basic，一次调用取全部上市股）。

    返回列对应 DuckDB stock_basic 表：code, name, industry, area, market, list_date
    """
    from src.data.tushare_client import get_pro_api

    pro = get_pro_api()
    try:
        raw = pro.stock_basic(
            exchange="", list_status="L",
            fields="ts_code,symbol,name,area,industry,market,list_date",
        )
    except Exception as exc:
        logger.warning("fetch_stock_basic 失败: %s", exc)
        return None

    if raw is None or raw.empty:
        return None

    df = raw.rename(columns={"symbol": "code"})
    keep = ["code", "name", "industry", "area", "market", "list_date"]
    return df[[c for c in keep if c in df.columns]].reset_index(drop=True)


# ──────────────────────── 3. 北向资金历史 ────────────────────────

def fetch_northbound_hsgt(since: date | None = None) -> pd.DataFrame | None:
    """拉取沪深港通北向资金历史（tushare pro.moneyflow_hsgt）。

    返回列对应 DuckDB northbound 表：
      date, north_net_inflow(元), hgt_yi(亿元), sgt_yi(亿元)

    tushare 单位为万元，转换规则：
      north_net_inflow = north_money × 10000
      hgt_yi = hgt / 10000
      sgt_yi = sgt / 10000
    """
    from src.data.tushare_client import get_pro_api
    from datetime import date as date_cls

    pro = get_pro_api()
    start = _date_str(since, fallback="20141117")  # 沪港通开通日
    today = date_cls.today().strftime("%Y%m%d")

    try:
        raw = pro.moneyflow_hsgt(start_date=start, end_date=today)
    except Exception as exc:
        logger.warning("tushare moneyflow_hsgt 拉取失败: %s", exc)
        return None

    if raw is None or raw.empty:
        return None

    df = raw[["trade_date", "north_money", "hgt", "sgt"]].copy()
    df["date"] = pd.to_datetime(df["trade_date"], format="%Y%m%d")
    df["north_net_inflow"] = pd.to_numeric(df["north_money"], errors="coerce") * 1e4  # 万元→元
    df["hgt_yi"] = pd.to_numeric(df["hgt"], errors="coerce") * 1e-4  # 万元→亿元
    df["sgt_yi"] = pd.to_numeric(df["sgt"], errors="coerce") * 1e-4

    df = df[["date", "north_net_inflow", "hgt_yi", "sgt_yi"]]
    return df.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)


# ──────────────────────── 4. 个股资金流向 ────────────────────────

def fetch_moneyflow(code: str, since: date | None = None) -> pd.DataFrame | None:
    """拉取单只股票的主力资金流向（tushare pro.moneyflow）。

    主力 = 大单(lg) + 超大单(elg)
    返回列对应 DuckDB fund_flow 表：
      date, code, major_net_inflow(元), major_net_pct(%)

    tushare 金额单位为万元，转换：major_net_inflow = 净额(万元) × 10000
    major_net_pct = 主力净额 / 全市场买入额 × 100
    """
    from src.data.tushare_client import get_pro_api
    from datetime import date as date_cls

    pro = get_pro_api()
    ts_code = _to_ts_code(code)
    start = _date_str(since, fallback=_DEFAULT_SINCE)
    today = date_cls.today().strftime("%Y%m%d")

    try:
        raw = pro.moneyflow(ts_code=ts_code, start_date=start, end_date=today)
    except Exception as exc:
        logger.debug("tushare moneyflow 拉取失败 %s: %s", code, exc)
        return None

    if raw is None or raw.empty:
        return None

    df = raw.copy()

    # 主力净额（大单 + 超大单），万元
    major_net = (
        _safe(df, "buy_lg_amount") - _safe(df, "sell_lg_amount")
        + _safe(df, "buy_elg_amount") - _safe(df, "sell_elg_amount")
    )
    df["major_net_inflow"] = major_net * 1e4  # 万元→元

    # 主力净占比 = 主力净额 / 全市场买入额（小+中+大+超大）× 100
    total_buy = (
        _safe(df, "buy_sm_amount")
        + _safe(df, "buy_md_amount")
        + _safe(df, "buy_lg_amount")
        + _safe(df, "buy_elg_amount")
    )
    df["major_net_pct"] = major_net.where(total_buy > 0) / total_buy.where(total_buy > 0) * 100

    df["date"] = pd.to_datetime(df["trade_date"], format="%Y%m%d")
    df["code"] = code

    df = df[["date", "code", "major_net_inflow", "major_net_pct"]]
    return df.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)


def fetch_moneyflow_by_date(trade_date: date) -> pd.DataFrame | None:
    """全市场单日资金流向，一次 API 调用。

    返回列：date, code, major_net_inflow(元), major_net_pct(%)
    """
    from src.data.tushare_client import get_pro_api

    pro = get_pro_api()
    date_str = trade_date.strftime("%Y%m%d")

    try:
        raw = pro.moneyflow(trade_date=date_str)
    except Exception as exc:
        logger.debug("moneyflow by_date %s 失败: %s", date_str, exc)
        return None

    if raw is None or raw.empty:
        return None

    df = raw.copy()

    major_net = (
        _safe(df, "buy_lg_amount") - _safe(df, "sell_lg_amount")
        + _safe(df, "buy_elg_amount") - _safe(df, "sell_elg_amount")
    )
    df["major_net_inflow"] = major_net * 1e4  # 万元→元

    total_buy = (
        _safe(df, "buy_sm_amount")
        + _safe(df, "buy_md_amount")
        + _safe(df, "buy_lg_amount")
        + _safe(df, "buy_elg_amount")
    )
    df["major_net_pct"] = major_net.where(total_buy > 0) / total_buy.where(total_buy > 0) * 100

    df["date"] = pd.to_datetime(df["trade_date"], format="%Y%m%d")
    df["code"] = df["ts_code"].str[:6]

    return df[["date", "code", "major_net_inflow", "major_net_pct"]].reset_index(drop=True)


# ──────────────────────── 5. 龙虎榜日报 ──────────────────────────

def fetch_lhb_daily(trade_date: date) -> pd.DataFrame | None:
    """拉取指定交易日的龙虎榜（tushare pro.top_list）。

    同一股票因上榜原因不同可能有多行，按股票聚合求和。
    返回列对应 DuckDB lhb 表：
      date, code, lhb_net_buy(元), lhb_buy_amount(元), lhb_sell_amount(元)

    tushare top_list 金额已是元，无需单位转换。
    """
    from src.data.tushare_client import get_pro_api

    pro = get_pro_api()
    date_str = trade_date.strftime("%Y%m%d")

    try:
        raw = pro.top_list(trade_date=date_str)
    except Exception as exc:
        logger.debug("tushare top_list 拉取失败 %s: %s", date_str, exc)
        return None

    if raw is None or raw.empty:
        return None

    df = raw.copy()
    df["l_buy"] = pd.to_numeric(df["l_buy"], errors="coerce").fillna(0)
    df["l_sell"] = pd.to_numeric(df["l_sell"], errors="coerce").fillna(0)
    df["net_amount"] = pd.to_numeric(df["net_amount"], errors="coerce").fillna(0)

    # 同一股票按所有上榜原因聚合
    agg = df.groupby("ts_code", as_index=False).agg(
        lhb_buy_amount=("l_buy", "sum"),
        lhb_sell_amount=("l_sell", "sum"),
        lhb_net_buy=("net_amount", "sum"),
    )

    agg["code"] = agg["ts_code"].str[:6]
    agg["date"] = pd.to_datetime(trade_date)

    return agg[["date", "code", "lhb_net_buy", "lhb_buy_amount", "lhb_sell_amount"]].reset_index(drop=True)


# ──────────────────────── 6. 研报评级 ────────────────────────────

def fetch_report_rc(code: str, since: date | None = None) -> pd.DataFrame | None:
    """拉取单只股票的机构研报评级（tushare pro.report_rc）。

    返回列对应 DuckDB reports 表：
      date, code, institution, rating
    """
    from src.data.tushare_client import get_pro_api
    from datetime import date as date_cls

    pro = get_pro_api()
    ts_code = _to_ts_code(code)
    start = _date_str(since, fallback=_DEFAULT_SINCE)
    today = date_cls.today().strftime("%Y%m%d")

    try:
        raw = pro.report_rc(ts_code=ts_code, start_date=start, end_date=today)
    except Exception as exc:
        logger.debug("tushare report_rc 拉取失败 %s: %s", code, exc)
        return None

    if raw is None or raw.empty:
        return None

    df = raw[["report_date", "org_name", "rating"]].copy()
    df["date"] = pd.to_datetime(df["report_date"], format="%Y%m%d", errors="coerce")
    df["code"] = code
    df["institution"] = df["org_name"]

    df = df[["date", "code", "institution", "rating"]]
    df = (
        df.dropna(subset=["date"])
        .sort_values("date")
        .drop_duplicates(subset=["date", "code", "institution"], keep="last")
        .reset_index(drop=True)
    )
    return df if not df.empty else None


# ──────────────────────── 7. 财务指标（季报/年报） ──────────────────

def fetch_fina_indicator(code: str, since: date | None = None) -> pd.DataFrame | None:
    """拉取单只股票的财务指标（合并两个接口）。

    - pro.fina_indicator()：ROE/ROA/毛利率/净利率/负债率/每股指标/同比增速
    - pro.income(report_type='1')：营业总收入、归母净利润（元→万元）

    两者按 end_date 左连接，以 fina_indicator 为主表。

    单位约定（与 DuckDB financial_indicator 表一致）：
      revenue / net_profit   万元（income 原始元单位 ÷ 10000）
      free_cash_flow         万元（fina_indicator.fcff 原生万元）
      revenue_yoy / net_profit_yoy  %（直接来自 tushare）
      其余比率字段            %（直接来自 tushare）
    """
    from src.data.tushare_client import get_pro_api
    from datetime import date as date_cls

    pro = get_pro_api()
    ts_code = _to_ts_code(code)
    start = _date_str(since, fallback="20100101")
    today = date_cls.today().strftime("%Y%m%d")

    # ── fina_indicator（主表）
    try:
        fi = pro.fina_indicator(ts_code=ts_code, start_date=start, end_date=today)
    except Exception as exc:
        logger.debug("tushare fina_indicator 拉取失败 %s: %s", code, exc)
        return None

    if fi is None or fi.empty:
        return None

    fi = fi[["ts_code", "ann_date", "end_date", "eps", "diluted2_eps",
             "tr_yoy", "netprofit_yoy", "roe", "roa",
             "grossprofit_margin", "netprofit_margin", "debt_to_assets",
             "bps", "ocfps", "fcff"]].copy()

    for col in fi.columns[2:]:
        fi[col] = pd.to_numeric(fi[col], errors="coerce")

    # ── income（补充绝对金额，只取合并报表 report_type=1）
    try:
        inc = pro.income(ts_code=ts_code, start_date=start, end_date=today,
                         report_type="1")
    except Exception as exc:
        logger.debug("tushare income 拉取失败 %s: %s", code, exc)
        inc = None

    if inc is not None and not inc.empty:
        inc = inc[["end_date", "total_revenue", "n_income_attr_p"]].copy()
        inc["end_date"] = inc["end_date"].astype(str)
        fi["end_date"] = fi["end_date"].astype(str)
        inc = inc.drop_duplicates("end_date", keep="last")
        inc["total_revenue"] = pd.to_numeric(inc["total_revenue"], errors="coerce") / 1e4
        inc["n_income_attr_p"] = pd.to_numeric(inc["n_income_attr_p"], errors="coerce") / 1e4
        fi = fi.merge(inc, on="end_date", how="left")
    else:
        fi["total_revenue"] = None
        fi["n_income_attr_p"] = None

    # ── 整理输出列
    fi["code"] = code
    fi["end_date"] = pd.to_datetime(fi["end_date"], format="%Y%m%d", errors="coerce")
    fi["ann_date"] = pd.to_datetime(fi["ann_date"], format="%Y%m%d", errors="coerce")

    fi = fi.rename(columns={
        "diluted2_eps":       "diluted_eps",
        "tr_yoy":             "revenue_yoy",
        "netprofit_yoy":      "net_profit_yoy",
        "grossprofit_margin": "gross_margin",
        "netprofit_margin":   "net_margin",
        "debt_to_assets":     "debt_ratio",
        "ocfps":              "oc_ps",
        "fcff":               "free_cash_flow",
        "total_revenue":      "revenue",
        "n_income_attr_p":    "net_profit",
    })

    keep = ["code", "end_date", "ann_date", "eps", "diluted_eps",
            "revenue", "revenue_yoy", "net_profit", "net_profit_yoy",
            "roe", "roa", "gross_margin", "net_margin",
            "debt_ratio", "bps", "oc_ps", "free_cash_flow"]
    fi = fi[[c for c in keep if c in fi.columns]]

    return (
        fi.dropna(subset=["end_date"])
        .sort_values("end_date")
        .drop_duplicates(subset=["code", "end_date"], keep="last")
        .reset_index(drop=True)
    )
