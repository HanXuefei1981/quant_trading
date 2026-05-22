"""DuckDB 表结构定义与迁移"""
from __future__ import annotations

import duckdb

_CREATE_KLINE = """
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
)
"""

_CREATE_FUNDAMENTALS = """
CREATE TABLE IF NOT EXISTS fundamentals (
    date              DATE    NOT NULL,
    code              VARCHAR NOT NULL,
    pe_ttm            DOUBLE,
    pe_static         DOUBLE,
    pb                DOUBLE,
    ps                DOUBLE,
    pcf               DOUBLE,
    peg               DOUBLE,
    market_cap        DOUBLE,
    float_market_cap  DOUBLE,
    total_shares      BIGINT,
    float_shares      BIGINT,
    PRIMARY KEY (date, code)
)
"""

_CREATE_FUND_FLOW = """
CREATE TABLE IF NOT EXISTS fund_flow (
    date              DATE    NOT NULL,
    code              VARCHAR NOT NULL,
    major_net_inflow  DOUBLE,
    major_net_pct     DOUBLE,
    PRIMARY KEY (date, code)
)
"""

_CREATE_NORTHBOUND = """
CREATE TABLE IF NOT EXISTS northbound (
    date              DATE   NOT NULL PRIMARY KEY,
    north_net_inflow  DOUBLE,
    hgt_yi            DOUBLE,
    sgt_yi            DOUBLE
)
"""

_CREATE_LHB = """
CREATE TABLE IF NOT EXISTS lhb (
    date             DATE    NOT NULL,
    code             VARCHAR NOT NULL,
    lhb_net_buy      DOUBLE,
    lhb_buy_amount   DOUBLE,
    lhb_sell_amount  DOUBLE,
    PRIMARY KEY (date, code)
)
"""

_CREATE_REPORTS = """
CREATE TABLE IF NOT EXISTS reports (
    date         DATE    NOT NULL,
    code         VARCHAR NOT NULL,
    institution  VARCHAR NOT NULL,
    rating       VARCHAR,
    PRIMARY KEY (date, code, institution)
)
"""

_CREATE_EPS_SNAPSHOT = """
CREATE TABLE IF NOT EXISTS eps_snapshot (
    snapshot_date  DATE    NOT NULL,
    code           VARCHAR NOT NULL,
    eps_cur        DOUBLE,
    eps_next       DOUBLE,
    analyst_count  INTEGER,
    PRIMARY KEY (snapshot_date, code)
)
"""

# features 表仅预建 PK 列；~100 个业务列（ma5_ratio、rsi14、label 等）在
# FeatureRepo.upsert_features() 首次写入时通过 ALTER TABLE ADD COLUMN 动态添加。
# 这样 schema 始终与 assembler 输出同步，新增因子只需更新 get_feature_columns()。
_CREATE_FEATURES = """
CREATE TABLE IF NOT EXISTS features (
    date  DATE    NOT NULL,
    code  VARCHAR NOT NULL,
    PRIMARY KEY (date, code)
)
"""

_CREATE_COLLECT_LOG = """
CREATE TABLE IF NOT EXISTS collect_log (
    table_name  VARCHAR   NOT NULL,
    scope       VARCHAR   NOT NULL,
    last_date   DATE,
    row_count   INTEGER,
    updated_at  TIMESTAMP,
    status      VARCHAR,
    PRIMARY KEY (table_name, scope)
)
"""

_CREATE_FUNDAMENTALS_SNAPSHOT = """
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
)
"""

_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_kline_code ON kline (code)",
    "CREATE INDEX IF NOT EXISTS idx_fundamentals_code ON fundamentals (code)",
    "CREATE INDEX IF NOT EXISTS idx_fund_flow_code ON fund_flow (code)",
    "CREATE INDEX IF NOT EXISTS idx_lhb_code ON lhb (code)",
    "CREATE INDEX IF NOT EXISTS idx_reports_code ON reports (code)",
    "CREATE INDEX IF NOT EXISTS idx_eps_code ON eps_snapshot (code)",
    "CREATE INDEX IF NOT EXISTS idx_features_date ON features (date)",
    "CREATE INDEX IF NOT EXISTS idx_features_code ON features (code)",
    "CREATE INDEX IF NOT EXISTS idx_fundamentals_snapshot_code ON fundamentals_snapshot (code)",
]


def migrate(conn: duckdb.DuckDBPyConnection | None = None) -> None:
    """建表（已存在则跳过），可传入外部连接（用于测试）。"""
    from src.dal.connection import get_db
    db = conn if conn is not None else get_db()
    for sql in [
        _CREATE_KLINE, _CREATE_FUNDAMENTALS, _CREATE_FUND_FLOW,
        _CREATE_NORTHBOUND, _CREATE_LHB, _CREATE_REPORTS,
        _CREATE_EPS_SNAPSHOT, _CREATE_FEATURES, _CREATE_COLLECT_LOG,
        _CREATE_FUNDAMENTALS_SNAPSHOT,
    ]:
        db.execute(sql)
    for idx_sql in _INDEXES:
        db.execute(idx_sql)
