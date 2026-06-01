"""测试 scan 信号富信息拼装：join 名称/行业 + 估值 + 最新财务"""
import sys
from datetime import date
from pathlib import Path

import duckdb
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.dal.schema import migrate
from src.features.scan_enrich import enrich_signals


@pytest.fixture
def conn():
    c = duckdb.connect(":memory:")
    migrate(c)
    # stock_basic：名称/行业
    c.execute("INSERT INTO stock_basic VALUES "
              "('000001','平安银行','银行','深圳','主板','19910403'),"
              "('600035','楚天高速','路桥','湖北','主板','20040227')")
    # fundamentals：05-26 估值（market_cap 单位元）
    c.execute("INSERT INTO fundamentals (date, code, pe_ttm, pb, ps, market_cap) VALUES "
              "(DATE '2026-05-26','000001', 4.8, 0.5, 1.4, 6.2e11),"
              "(DATE '2026-05-26','600035', 12.2, 0.7, 0.9, 6.2e9)")
    # financial_indicator：每股两期，取最新 end_date
    c.execute("INSERT INTO financial_indicator (code, end_date, roe, net_profit_yoy) VALUES "
              "('000001', DATE '2025-09-30', 11.0, 5.0),"
              "('000001', DATE '2025-12-31', 12.5, 6.0),"
              "('600035', DATE '2025-12-31', 2.7, -13.5)")
    yield c
    c.close()


def test_enrich_joins_name_industry_valuation_financials(conn):
    signal_df = pd.DataFrame({
        "code": ["000001", "600035"],
        "close": [3.09, 3.82],
        "signal": [1.44, 1.48],
        "rank": [1, 2],
    })

    out = enrich_signals(signal_df, conn, date(2026, 5, 26))

    row = out.set_index("code").loc["000001"]
    assert row["name"] == "平安银行"
    assert row["industry"] == "银行"
    assert row["pe_ttm"] == pytest.approx(4.8)
    assert row["pb"] == pytest.approx(0.5)
    assert row["market_cap_yi"] == pytest.approx(6200.0)   # 6.2e11 元 → 6200 亿
    assert row["roe"] == pytest.approx(12.5)               # 取最新 end_date 2025-12-31
    assert row["net_profit_yoy"] == pytest.approx(6.0)
    # 原始列保留
    assert row["signal"] == pytest.approx(1.44)


def test_enrich_missing_data_yields_nan(conn):
    """股票不在任何辅助表中 → 名称/估值/财务为空，不抛错。"""
    signal_df = pd.DataFrame({
        "code": ["999999"],
        "close": [10.0],
        "signal": [1.0],
        "rank": [1],
    })

    out = enrich_signals(signal_df, conn, date(2026, 5, 26))

    row = out.set_index("code").loc["999999"]
    assert pd.isna(row["name"])
    assert pd.isna(row["pe_ttm"])
    assert pd.isna(row["market_cap_yi"])
    assert pd.isna(row["roe"])
    assert len(out) == 1
