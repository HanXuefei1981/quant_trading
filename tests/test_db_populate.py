#!/usr/bin/env python3
"""
集成测试: 9 张 DB 表数据填充验证
=================================
验证每个数据源能正确拉取数据并写入 DuckDB。
从沙箱可测: kline(本地.day), fund_flow(本地Parquet), fundamentals_snapshot(腾讯财经)
其余表需用户本地跑 (东财 API 地域限制)。

用法:
  python3 tests/test_db_populate.py           # 全部
  python3 tests/test_db_populate.py --quick    # 只测沙箱可达的 (kline/fund_flow/fundamentals_snapshot)
  python3 tests/test_db_populate.py --table kline  # 只测单表
"""

import sys, os, time, argparse
from pathlib import Path
from datetime import date, datetime, timedelta

# ── 项目路径 ──────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import duckdb
import pandas as pd

# ── 配置 ──────────────────────────────────────────
TEST_CODES = ["000001", "000002", "600519", "000858", "300750"]  # 平安/万科/茅台/五粮液/宁德
TDX_DAY_DIR = Path("/Volumes/Elements/5、投资/tdx_data/2026-05-21")
FUND_FLOW_DIR = Path.home() / "fund_flow_data" / "individual"

# ── 扩展后的完整 Schema ───────────────────────────

SCHEMA_DDL = """
-- 1. K线 (通达信日线, 前复权)
CREATE TABLE IF NOT EXISTS kline (
    date    DATE    NOT NULL,
    code    VARCHAR NOT NULL,
    open    DOUBLE,
    high    DOUBLE,
    low     DOUBLE,
    close   DOUBLE,
    amount  DOUBLE,
    volume  BIGINT,
    PRIMARY KEY (date, code)
);

-- 2. 基本面 (腾讯财经每日快照 — Sub-2 新增)
CREATE TABLE IF NOT EXISTS fundamentals_snapshot (
    date             DATE    NOT NULL,
    code             VARCHAR NOT NULL,
    pe_ttm           DOUBLE,
    pe_static        DOUBLE,
    pb               DOUBLE,
    turnover_pct     DOUBLE,
    mcap_yi          DOUBLE,
    float_mcap_yi    DOUBLE,
    price            DOUBLE,
    PRIMARY KEY (date, code)
);

-- 3. 个股资金流向 (东财 push2his, 13 列 — 扩展)
CREATE TABLE IF NOT EXISTS fund_flow (
    date              DATE    NOT NULL,
    code              VARCHAR NOT NULL,
    close             DOUBLE,
    change_pct        DOUBLE,
    major_net_inflow  DOUBLE,
    major_net_pct     DOUBLE,
    super_net_inflow  DOUBLE,
    super_net_pct     DOUBLE,
    large_net_inflow  DOUBLE,
    large_net_pct     DOUBLE,
    mid_net_inflow    DOUBLE,
    mid_net_pct      DOUBLE,
    small_net_inflow  DOUBLE,
    small_net_pct    DOUBLE,
    PRIMARY KEY (date, code)
);

-- 4. 北向资金 (市场级)
CREATE TABLE IF NOT EXISTS northbound (
    date              DATE   NOT NULL PRIMARY KEY,
    north_net_inflow  DOUBLE,
    hgt_yi            DOUBLE,
    sgt_yi            DOUBLE
);

-- 5. 龙虎榜
CREATE TABLE IF NOT EXISTS lhb (
    date             DATE    NOT NULL,
    code             VARCHAR NOT NULL,
    lhb_net_buy      DOUBLE,
    lhb_buy_amount   DOUBLE,
    lhb_sell_amount  DOUBLE,
    PRIMARY KEY (date, code)
);

-- 6. 研报
CREATE TABLE IF NOT EXISTS reports (
    date         DATE    NOT NULL,
    code         VARCHAR NOT NULL,
    institution  VARCHAR NOT NULL,
    rating       VARCHAR,
    PRIMARY KEY (date, code, institution)
);

-- 7. EPS共识快照
CREATE TABLE IF NOT EXISTS eps_snapshot (
    snapshot_date  DATE    NOT NULL,
    code           VARCHAR NOT NULL,
    eps_cur        DOUBLE,
    eps_next       DOUBLE,
    analyst_count  INTEGER,
    PRIMARY KEY (snapshot_date, code)
);

-- 8. 采集进度
CREATE TABLE IF NOT EXISTS collect_log (
    table_name  VARCHAR   NOT NULL,
    scope       VARCHAR   NOT NULL,
    last_date   DATE,
    row_count   INTEGER,
    updated_at  TIMESTAMP,
    status      VARCHAR,
    PRIMARY KEY (table_name, scope)
);
"""

def get_db(db_path: str = ":memory:") -> duckdb.DuckDBPyConnection:
    """获取 DuckDB 连接并初始化 Schema"""
    conn = duckdb.connect(db_path)
    conn.execute(SCHEMA_DDL)
    return conn

# ══════════════════════════════════════════════════════
# 数据源适配器 (每表一个)
# ══════════════════════════════════════════════════════

def fetch_kline_from_tdx(code: str, max_days: int = 30) -> pd.DataFrame | None:
    """从通达信 .day 文件读取日线 (hsjday.zip 解压后)"""
    import struct, zipfile

    # 确定市场前缀
    if code.startswith(("6", "9")):
        market, prefix = "sh", "sh"
    elif code.startswith(("8", "4")):
        market, prefix = "bj", "bj"
    else:
        market, prefix = "sz", "sz"

    zip_path = TDX_DAY_DIR / "hsjday.zip"
    if not zip_path.exists():
        print(f"    ⚠ hsjday.zip 不存在: {zip_path}")
        return None

    day_path = f"{market}\\lday\\{prefix}{code}.day"
    try:
        with zipfile.ZipFile(zip_path, 'r') as zf:
            data = zf.read(day_path)
    except KeyError:
        # 尝试不区分大小写
        try:
            day_path = f"{market}\\lday\\{prefix}{code}.day"
            with zipfile.ZipFile(zip_path, 'r') as zf:
                data = zf.read(day_path)
        except:
            print(f"    ⚠ {code}: .day 文件不存在于 zip 中")
            return None

    REC_SIZE = 32
    total = len(data) // REC_SIZE
    if total == 0:
        return None

    rows = []
    for i in range(max(total - max_days, 0), total):
        off = i * REC_SIZE
        did = struct.unpack_from("<I", data, off)[0]
        yr, rest = did // 10000, did % 10000
        mon, day = rest // 100, rest % 100
        rows.append({
            "date": date(yr, mon, day),
            "code": code,
            "open": struct.unpack_from("<I", data, off + 4)[0] / 100.0,
            "high": struct.unpack_from("<I", data, off + 8)[0] / 100.0,
            "low": struct.unpack_from("<I", data, off + 12)[0] / 100.0,
            "close": struct.unpack_from("<I", data, off + 16)[0] / 100.0,
            "amount": struct.unpack_from("<f", data, off + 20)[0],
            "volume": struct.unpack_from("<I", data, off + 24)[0],
        })

    return pd.DataFrame(rows)


def fetch_fundamentals_snapshot(codes: list[str]) -> pd.DataFrame | None:
    """从腾讯财经拉 PE/PB/市值快照"""
    import urllib.request

    prefixed = []
    for c in codes:
        if c.startswith(("6", "9")):  prefixed.append(f"sh{c}")
        elif c.startswith("8"):       prefixed.append(f"bj{c}")
        else:                         prefixed.append(f"sz{c}")

    url = "https://qt.gtimg.cn/q=" + ",".join(prefixed)
    req = urllib.request.Request(url)
    req.add_header("User-Agent", "Mozilla/5.0")
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        data = resp.read().decode("gbk")
    except Exception as e:
        print(f"    ⚠ 腾讯财经请求失败: {e}")
        return None

    today = date.today()
    rows = []
    for line in data.strip().split(";"):
        if not line.strip() or "=" not in line or '"' not in line:
            continue
        key = line.split("=")[0].split("_")[-1]
        vals = line.split('"')[1].split("~")
        if len(vals) < 53:
            continue
        code = key[2:]
        rows.append({
            "date": today,
            "code": code,
            "pe_ttm":        float(vals[39]) if vals[39] else None,
            "pe_static":     float(vals[52]) if vals[52] else None,
            "pb":            float(vals[46]) if vals[46] else None,
            "turnover_pct":  float(vals[38]) if vals[38] else None,
            "mcap_yi":       float(vals[44]) if vals[44] else None,
            "float_mcap_yi": float(vals[45]) if vals[45] else None,
            "price":         float(vals[3]) if vals[3] else None,
        })

    return pd.DataFrame(rows) if rows else None


def fetch_fund_flow_from_parquet(codes: list[str]) -> pd.DataFrame | None:
    """从本地 Parquet 读取已有资金流向数据"""
    if not FUND_FLOW_DIR.exists():
        print(f"    ⚠ fund_flow Parquet 目录不存在: {FUND_FLOW_DIR}")
        return None

    files = sorted(FUND_FLOW_DIR.glob("*.parquet"))
    if not files:
        print(f"    ⚠ 无 Parquet 文件")
        return None

    # 读最新一个分区
    latest_file = files[-1]
    df = pd.read_parquet(latest_file)
    df = df[df["代码"].isin(codes)]

    if df.empty:
        return None

    # 映射到 DB 列名
    df["date"] = pd.to_datetime(df["日期"]).dt.date
    result = pd.DataFrame({
        "date":             df["date"],
        "code":             df["代码"],
        "close":            df.get("收盘价"),
        "change_pct":       df.get("涨跌幅"),
        "major_net_inflow": df.get("主力净流入-净额"),
        "major_net_pct":    df.get("主力净流入-净占比"),
        "super_net_inflow": df.get("超大单净流入-净额"),
        "super_net_pct":    df.get("超大单净流入-净占比"),
        "large_net_inflow": df.get("大单净流入-净额"),
        "large_net_pct":    df.get("大单净流入-净占比"),
        "mid_net_inflow":   df.get("中单净流入-净额"),
        "mid_net_pct":      df.get("中单净流入-净占比"),
        "small_net_inflow": df.get("小单净流入-净额"),
        "small_net_pct":    df.get("小单净流入-净占比"),
    })
    return result


def fetch_lhb_from_eastmoney(codes: list[str], lookback: int = 7) -> pd.DataFrame | None:
    """从东财 datacenter 拉龙虎榜 (需国内IP)"""
    import requests
    import json

    UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
    DATACENTER_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"

    end = date.today()
    start = end - timedelta(days=lookback)
    rows = []

    for code in codes:
        filter_str = (
            f"(TRADE_DATE>='{start.strftime('%Y-%m-%d')}')"
            f"(TRADE_DATE<='{end.strftime('%Y-%m-%d')}')"
            f'(SECURITY_CODE="{code}")'
        )
        params = {
            "reportName": "RPT_DAILYBILLBOARD_DETAILSNEW",
            "columns": "ALL",
            "filter": filter_str,
            "pageNumber": "1",
            "pageSize": "50",
            "sortColumns": "TRADE_DATE",
            "sortTypes": "-1",
            "source": "WEB",
            "client": "WEB",
        }
        try:
            r = requests.get(DATACENTER_URL, params=params,
                           headers={"User-Agent": UA}, timeout=15)
            d = r.json()
            data = (d.get("result") or {}).get("data", [])
            for row in data:
                rows.append({
                    "date": str(row.get("TRADE_DATE", ""))[:10],
                    "code": code,
                    "lhb_net_buy": (row.get("BILLBOARD_NET_AMT") or 0) / 10000,
                    "lhb_buy_amount": (row.get("BILLBOARD_BUY_AMT") or 0) / 10000,
                    "lhb_sell_amount": (row.get("BILLBOARD_SELL_AMT") or 0) / 10000,
                })
        except Exception as e:
            print(f"    ⚠ 龙虎榜 {code} 失败: {e}")

    return pd.DataFrame(rows) if rows else None


def fetch_northbound_from_hexin(retries: int = 2) -> pd.DataFrame | None:
    """从同花顺 hsgtApi 拉北向资金"""
    import requests

    url = "https://data.hexin.cn/market/hsgtApi/method/dayChart/"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/117.0.0.0",
        "Host": "data.hexin.cn",
        "Referer": "https://data.hexin.cn/",
    }
    last_err = None
    for attempt in range(retries + 1):
        try:
            r = requests.get(url, headers=headers, timeout=10)
            d = r.json()
            times = d.get("time", []) or []
            hgt = d.get("hgt", []) or []
            sgt = d.get("sgt", []) or []

            if not times or not hgt or not sgt:
                return None

            today = date.today()
            try:
                hgt_last = float(hgt[-1])
            except (TypeError, ValueError, IndexError):
                hgt_last = 0.0
            try:
                sgt_last = float(sgt[-1])
            except (TypeError, ValueError, IndexError):
                sgt_last = 0.0

            return pd.DataFrame([{
                "date": today,
                "north_net_inflow": hgt_last + sgt_last,
                "hgt_yi": hgt_last,
                "sgt_yi": sgt_last,
            }])
        except Exception as e:
            last_err = e
            if attempt < retries:
                time.sleep(1.0)

    print(f"    ⚠ 北向资金失败(重试{retries}次): {last_err}")
    return None


def fetch_reports_from_eastmoney(codes: list[str]) -> pd.DataFrame | None:
    """从东财 reportapi 拉研报列表"""
    import requests

    UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
    REPORT_API = "https://reportapi.eastmoney.com/report/list"
    session = requests.Session()
    session.headers.update({"User-Agent": UA, "Referer": "https://data.eastmoney.com/"})

    rows = []
    for code in codes:
        params = {
            "industryCode": "*", "pageSize": "20", "industry": "*",
            "rating": "*", "ratingChange": "*",
            "beginTime": "2026-04-01", "endTime": "2026-12-31",
            "pageNo": "1", "fields": "", "qType": "0",
            "orgCode": "", "code": code, "rcode": "",
            "p": "1", "pageNum": "1",
        }
        try:
            r = session.get(REPORT_API, params=params, timeout=15)
            d = r.json()
            for item in d.get("data", []) or []:
                rows.append({
                    "date": (item.get("publishDate") or "")[:10],
                    "code": code,
                    "institution": item.get("orgSName", "未知"),
                    "rating": item.get("emRatingName", ""),
                })
        except Exception as e:
            print(f"    ⚠ 研报 {code} 失败: {e}")

    return pd.DataFrame(rows) if rows else None


def fetch_eps_from_eastmoney(codes: list[str]) -> pd.DataFrame | None:
    """从东财 reportapi 拉一致预期EPS (JSON API, 非网页爬虫)"""
    import requests

    UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
    REPORT_API = "https://reportapi.eastmoney.com/report/list"
    session = requests.Session()
    session.headers.update({"User-Agent": UA, "Referer": "https://data.eastmoney.com/"})

    today = date.today()
    rows = []

    for code in codes:
        params = {
            "industryCode": "*", "pageSize": "10", "industry": "*",
            "rating": "*", "ratingChange": "*",
            "beginTime": "2026-01-01", "endTime": "2026-12-31",
            "pageNo": "1", "fields": "", "qType": "0",
            "orgCode": "", "code": code, "rcode": "",
            "p": "1", "pageNum": "1",
        }
        try:
            r = session.get(REPORT_API, params=params, timeout=15)
            d = r.json()
            items = d.get("data", []) or []
            if not items:
                continue

            # 取最新一份研报的 EPS 预测
            latest = items[0]
            eps_cur  = latest.get("predictThisYearEps")
            eps_next = latest.get("predictNextYearEps")

            # 统计覆盖机构数
            analyst_count = len(items)

            if eps_cur is not None or eps_next is not None:
                rows.append({
                    "snapshot_date": today,
                    "code": code,
                    "eps_cur": float(eps_cur) if eps_cur else None,
                    "eps_next": float(eps_next) if eps_next else None,
                    "analyst_count": analyst_count,
                })
        except Exception as e:
            print(f"    ⚠ EPS {code} 失败: {e}")

    return pd.DataFrame(rows) if rows else None


# ══════════════════════════════════════════════════════
# 插入逻辑
# ══════════════════════════════════════════════════════

def to_date(val):
    """安全转 date 类型"""
    if isinstance(val, date):
        return val
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, str):
        return datetime.strptime(val[:10], "%Y-%m-%d").date()
    return val

def upsert(conn, table: str, df: pd.DataFrame, pk_cols: list[str]):
    """通用 upsert: 主键冲突时覆盖"""
    if df is None or df.empty:
        return 0

    # 统一 date 类型
    for col in df.columns:
        if "date" in col.lower() or "snapshot" in col.lower():
            df[col] = df[col].apply(to_date)

    conn.register("_tmp_upsert", df)
    set_clause = ", ".join(
        f"{col} = EXCLUDED.{col}"
        for col in df.columns if col not in pk_cols
    )
    sql = f"""
        INSERT INTO {table} SELECT * FROM _tmp_upsert
        ON CONFLICT ({', '.join(pk_cols)}) DO UPDATE SET {set_clause}
    """
    conn.execute(sql)
    conn.unregister("_tmp_upsert")
    return len(df)


# ══════════════════════════════════════════════════════
# 测试运行器
# ══════════════════════════════════════════════════════

def run_test(conn, table: str, fetch_fn, fn_args, pk_cols: list[str],
             label: str, requires_china_ip: bool = False):
    """运行单表测试"""
    print(f"\n{'─'*55}")
    print(f"📋 {label}")
    if requires_china_ip:
        print(f"   (需国内IP, 沙箱可能失败)")
    print(f"{'─'*55}")

    try:
        if fn_args is None:
            df = fetch_fn()
        elif callable(fn_args):
            df = fetch_fn(fn_args())
        else:
            df = fetch_fn(fn_args)

        if df is None or df.empty:
            print(f"  ⚠ 无数据返回")
            return {"table": table, "status": "no_data", "rows": 0}

        n = upsert(conn, table, df, pk_cols)

        # 验证
        count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        date_range = conn.execute(
            f"SELECT MIN(date), MAX(date) FROM {table}"
        ).fetchone() if "date" in [c.lower() for c in df.columns] else (None, None)

        print(f"  ✅ 写入 {n} 行, 表总计 {count} 行")
        if date_range[0]:
            print(f"     日期范围: {date_range[0]} ~ {date_range[1]}")
        if "code" in df.columns:
            codes_in = df["code"].nunique()
            print(f"     覆盖股票: {codes_in} 只")

        return {"table": table, "status": "ok", "rows": n}

    except Exception as e:
        print(f"  ❌ 失败: {e}")
        return {"table": table, "status": "failed", "rows": 0, "error": str(e)[:100]}


def main():
    parser = argparse.ArgumentParser(description="DB 表数据填充验证")
    parser.add_argument("--quick", action="store_true",
                       help="只测沙箱可达的 (kline/fund_flow/fundamentals_snapshot)")
    parser.add_argument("--table", type=str, help="只测指定表")
    parser.add_argument("--db", type=str, default=":memory:",
                       help="DB 路径 (默认内存库)")
    args = parser.parse_args()

    conn = get_db(args.db)
    results = []

    tests = [
        # (table, fetch_fn, pk_cols, label, requires_china_ip)
        ("kline", fetch_kline_from_tdx, ["date", "code"],
         "K线 (通达信 .day → hsjday.zip)", False),

        ("fundamentals_snapshot", fetch_fundamentals_snapshot, ["date", "code"],
         "基本面快照 (腾讯财经 PE/PB/市值)", False),

        ("fund_flow", fetch_fund_flow_from_parquet, ["date", "code"],
         "资金流向 (本地 Parquet → fund_flow_data)", False),

        ("lhb", fetch_lhb_from_eastmoney, ["date", "code"],
         "龙虎榜 (东财 datacenter)", True),

        ("northbound", fetch_northbound_from_hexin, ["date"],
         "北向资金 (同花顺 hsgtApi)", True),

        ("reports", fetch_reports_from_eastmoney, ["date", "code", "institution"],
         "研报列表 (东财 reportapi)", True),

        ("eps_snapshot", fetch_eps_from_eastmoney, ["snapshot_date", "code"],
         "EPS共识 (东财 reportapi)", True),
    ]

    if args.table:
        tests = [t for t in tests if t[0] == args.table]
        if not tests:
            print(f"未知表: {args.table}")
            sys.exit(1)

    for table, fetch_fn, pk_cols, label, req_ip in tests:
        if args.quick and req_ip:
            print(f"\n⏭ 跳过 {label} (需国内IP, --quick 模式)")
            continue
        # 确定参数: 市场级采集器不需要 codes
        if table in ("northbound",):
            r = run_test(conn, table, fetch_fn, None, pk_cols, label, req_ip)
        elif table == "kline":
            for code in TEST_CODES:
                r = run_test(conn, table, fetch_fn, code, pk_cols,
                            f"{label} [{code}]", req_ip)
        else:
            r = run_test(conn, table, fetch_fn, TEST_CODES, pk_cols,
                        label, req_ip)

        results.append(r)

    # ── 汇总 ──────────────────────────────────────
    print(f"\n\n{'='*55}")
    print(f"  汇总")
    print(f"{'='*55}")
    ok = sum(1 for r in results if r["status"] == "ok")
    no_data = sum(1 for r in results if r["status"] == "no_data")
    failed = sum(1 for r in results if r["status"] == "failed")
    total_rows = sum(r["rows"] for r in results)

    print(f"  ✅ 成功: {ok}  |  ⚠ 无数据: {no_data}  |  ❌ 失败: {failed}")
    print(f"  总写入: {total_rows} 行")
    print(f"  DB 路径: {args.db}")

    for r in results:
        icon = "✅" if r["status"] == "ok" else ("⚠" if r["status"] == "no_data" else "❌")
        print(f"  {icon} {r['table']}: {r['status']} ({r['rows']}行)")

    if args.db != ":memory:":
        print(f"\n💾 持久化 DB: {args.db}")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
