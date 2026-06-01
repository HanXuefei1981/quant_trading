"""Tests for monitor/readers/watermark.py（DuckDB 版水位读取器）。

读取器直接对各表取 MAX(date) 与 COUNT(DISTINCT code)，不再依赖 collect_log——
因为批量采集路径（fetch-fund / fetch-flow）会绕过 collect_log。
"""

import json
from pathlib import Path

import duckdb
import pytest

from src.dal.schema import migrate
from monitor.readers.watermark import (
    UNIVERSE_SIZE,
    SourceStatus,
    WatermarkData,
    get_watermarks,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_watermark(data_dir: Path, payload: dict) -> None:
    (data_dir / "watermark.json").write_text(json.dumps(payload))


def _populate_codes(conn, table: str, n: int, date_str: str) -> None:
    """向按股票表插入 n 个不同 code，日期列均为 date_str。"""
    if table == "reports":
        conn.execute(
            "INSERT INTO reports (date, code, institution) "
            f"SELECT DATE '{date_str}', (i)::VARCHAR, '机构A' FROM range({n}) t(i)"
        )
    elif table == "financial_indicator":
        # 日期列读取的是 ann_date（公告日）
        conn.execute(
            "INSERT INTO financial_indicator (code, end_date, ann_date) "
            f"SELECT (i)::VARCHAR, DATE '2026-03-31', DATE '{date_str}' FROM range({n}) t(i)"
        )
    elif table == "eps_snapshot":
        conn.execute(
            "INSERT INTO eps_snapshot (snapshot_date, code) "
            f"SELECT DATE '{date_str}', (i)::VARCHAR FROM range({n}) t(i)"
        )
    else:  # fundamentals / fund_flow / lhb / features
        datecol = "date"
        conn.execute(
            f"INSERT INTO {table} ({datecol}, code) "
            f"SELECT DATE '{date_str}', (i)::VARCHAR FROM range({n}) t(i)"
        )


@pytest.fixture
def make_db(tmp_path, monkeypatch):
    """构建临时 DuckDB 并通过 QUANT_DB_PATH 指向它；返回 (data_dir, populate_fn)。

    每次写入用独立短连接并立即关闭——DuckDB 不允许同一文件同时存在 read_write 与
    read_only 连接，读取器以 read_only 打开时必须没有其它连接持有该文件。
    """
    db_path = tmp_path / "test.duckdb"
    conn = duckdb.connect(str(db_path))
    migrate(conn)
    conn.close()

    def populate(table: str, n: int, date_str: str) -> None:
        c = duckdb.connect(str(db_path))
        _populate_codes(c, table, n, date_str)
        c.commit()
        c.close()

    monkeypatch.setenv("QUANT_DB_PATH", str(db_path))
    yield tmp_path, populate


# ---------------------------------------------------------------------------
# 结构 & kline
# ---------------------------------------------------------------------------


def test_returns_watermark_data_with_all_fields(make_db):
    data_dir, _ = make_db
    _write_watermark(data_dir, {"kline": "2026-05-26"})

    result = get_watermarks(data_dir)

    assert isinstance(result, WatermarkData)
    for field in ("kline", "features", "northbound", "fundamentals",
                  "fund_flow", "lhb", "reports", "financial_indicator",
                  "eps_snapshot", "models"):
        assert isinstance(getattr(result, field), SourceStatus)


def test_kline_status_ok_from_watermark_json(make_db):
    data_dir, _ = make_db
    _write_watermark(data_dir, {"kline": "2026-05-26"})

    result = get_watermarks(data_dir)

    assert result.kline.status == "ok"
    assert result.kline.date == "2026-05-26"


# ---------------------------------------------------------------------------
# 核心修复：直接读表 MAX(date)（不依赖 collect_log）
# ---------------------------------------------------------------------------


def test_reads_table_max_date_without_collect_log(make_db):
    """批量写入绕过 collect_log，读取器仍应从表本身取到最新日期。"""
    data_dir, populate = make_db
    _write_watermark(data_dir, {"kline": "2026-05-26"})
    populate("fund_flow", n=10, date_str="2026-05-26")

    result = get_watermarks(data_dir)

    assert result.fund_flow.date == "2026-05-26"  # collect_log 为空仍能取到


def test_financial_indicator_uses_ann_date(make_db):
    """财务表的新鲜度取公告日 ann_date，而非报告期 end_date。"""
    data_dir, populate = make_db
    _write_watermark(data_dir, {"kline": "2026-05-26"})
    populate("financial_indicator", n=5000, date_str="2026-05-21")  # ann_date

    result = get_watermarks(data_dir)

    assert result.financial_indicator.date == "2026-05-21"


# ---------------------------------------------------------------------------
# features：5 日标签视界容差（落后 kline ≤10 自然日仍算 ok）
# ---------------------------------------------------------------------------


def test_features_ok_when_within_label_horizon(make_db):
    """features 比 kline 落后 7 天（5 个交易日标签视界）应判 ok，而非告警。"""
    data_dir, populate = make_db
    _write_watermark(data_dir, {"kline": "2026-05-26"})
    populate("features", n=10, date_str="2026-05-19")  # 落后 7 自然日

    result = get_watermarks(data_dir)

    assert result.features.status == "ok"
    assert result.features.date == "2026-05-19"


def test_features_warn_when_too_stale(make_db):
    """features 落后 kline 超过 10 自然日，说明特征工程未跟上，判 warn。"""
    data_dir, populate = make_db
    _write_watermark(data_dir, {"kline": "2026-05-26"})
    populate("features", n=10, date_str="2026-05-01")  # 落后 25 天

    result = get_watermarks(data_dir)

    assert result.features.status == "warn"


# ---------------------------------------------------------------------------
# 覆盖率 & 状态阈值
# ---------------------------------------------------------------------------


def test_fundamentals_coverage_and_ok_status(make_db):
    data_dir, populate = make_db
    _write_watermark(data_dir, {"kline": "2026-05-26"})
    n = int(UNIVERSE_SIZE * 0.90) + 1
    populate("fundamentals", n=n, date_str="2026-05-26")

    result = get_watermarks(data_dir)

    assert result.fundamentals.count == n
    assert result.fundamentals.coverage == pytest.approx(n / UNIVERSE_SIZE, rel=1e-3)
    assert result.fundamentals.status == "ok"


def test_fundamentals_warn_between_70_and_90(make_db):
    data_dir, populate = make_db
    _write_watermark(data_dir, {"kline": "2026-05-26"})
    populate("fundamentals", n=int(UNIVERSE_SIZE * 0.80), date_str="2026-05-26")

    result = get_watermarks(data_dir)

    assert result.fundamentals.status == "warn"


def test_fund_flow_err_when_coverage_below_20_percent(make_db):
    data_dir, populate = make_db
    _write_watermark(data_dir, {"kline": "2026-05-26"})
    populate("fund_flow", n=100, date_str="2026-05-26")  # 100/5641 ≈ 1.8%

    result = get_watermarks(data_dir)

    assert result.fund_flow.status == "err"


def test_eps_snapshot_ok_at_realistic_analyst_coverage(make_db):
    """EPS 共识约覆盖半数市场，~49% 应判 ok（阈值已下调到 40%）。"""
    data_dir, populate = make_db
    _write_watermark(data_dir, {"kline": "2026-05-26"})
    populate("eps_snapshot", n=int(UNIVERSE_SIZE * 0.49), date_str="2026-05-27")

    result = get_watermarks(data_dir)

    assert result.eps_snapshot.status == "ok"


# ---------------------------------------------------------------------------
# models：依据 eval_results.json 是否存在
# ---------------------------------------------------------------------------


def test_models_ok_when_eval_results_exists(make_db):
    data_dir, _ = make_db
    _write_watermark(data_dir, {"kline": "2026-05-26", "features": "2026-05-19"})
    models_dir = data_dir / "models"
    models_dir.mkdir()
    (models_dir / "eval_results.json").write_text("{}")

    result = get_watermarks(data_dir)

    assert result.models.status == "ok"


def test_models_err_when_eval_results_missing(make_db):
    data_dir, _ = make_db
    _write_watermark(data_dir, {"kline": "2026-05-26"})

    result = get_watermarks(data_dir)

    assert result.models.status == "err"
