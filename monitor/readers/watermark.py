"""Watermark reader — reports freshness and coverage of each data source.

Data sources:
  kline      — watermark.json (old file-based watermark)
  all others — DuckDB collect_log table (MetaRepo)
"""

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

UNIVERSE_SIZE = 5641


@dataclass
class SourceStatus:
    name: str
    date: Optional[str]       # latest date (None if unknown)
    count: Optional[int]      # distinct code count (None if N/A)
    coverage: Optional[float]  # 0.0–1.0 (None if N/A)
    status: str               # "ok" | "warn" | "err"


@dataclass
class WatermarkData:
    kline: SourceStatus
    features: SourceStatus
    northbound: SourceStatus
    fundamentals: SourceStatus
    fund_flow: SourceStatus
    lhb: SourceStatus
    reports: SourceStatus
    financial_indicator: SourceStatus
    eps_snapshot: SourceStatus
    models: SourceStatus


def _load_watermark_json(data_dir: Path) -> dict:
    wm_path = data_dir / "watermark.json"
    if not wm_path.exists():
        return {}
    try:
        return json.loads(wm_path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


# 各表的日期列。按日批量写入路径（fetch-fund / fetch-flow 等）直接 upsert，绕过
# collect_log/MetaRepo，故直接查表 MAX(date) 才能反映真实水位，而非读 collect_log。
_TABLE_DATE_COL: dict[str, str] = {
    "northbound": "date",
    "fundamentals": "date",
    "fund_flow": "date",
    "lhb": "date",
    "reports": "date",
    "financial_indicator": "ann_date",   # 公告日（比报告期 end_date 更能反映新鲜度）
    "eps_snapshot": "snapshot_date",
    "features": "date",
}

_CODE_COUNT_TABLES = ("fundamentals", "fund_flow", "reports", "financial_indicator", "eps_snapshot")


def _query_duckdb(data_dir: Path) -> dict:
    """直接从各表读取最新日期与（按股票）覆盖数。

    不再依赖 collect_log——批量采集路径会绕过它，导致水位长期显示为空。
    直接对各表取 MAX(date)，无论数据由哪条代码路径写入都能如实反映。

    Returns:
        {table_name: {"date": str|None, "code_count": int|None}}
    """
    # DB 路径优先级：QUANT_DB_PATH 环境变量（调用时读取，便于测试指向临时库）→
    # config.settings.DB_PATH（项目唯一来源，默认在 Elements 外置盘）。
    # 不再用 data_dir 相对路径猜测——那会连到不存在的本地文件，导致面板全部显示空。
    db_path = os.getenv("QUANT_DB_PATH")
    if not db_path:
        try:
            from config.settings import DB_PATH
            db_path = str(DB_PATH)
        except Exception:
            db_path = str(data_dir.parent / "quant.duckdb")
    conn = None
    try:
        import duckdb
        conn = duckdb.connect(str(db_path), read_only=True)

        result: dict = {}
        for tbl, datecol in _TABLE_DATE_COL.items():
            try:
                d = conn.execute(
                    f"SELECT MAX(CAST({datecol} AS DATE)) FROM {tbl}"
                ).fetchone()[0]
                result[tbl] = {"date": d.isoformat() if d else None, "code_count": None}
            except Exception:
                result[tbl] = {"date": None, "code_count": None}

        # 按股票表统计去重股票数（覆盖率）
        for tbl in _CODE_COUNT_TABLES:
            try:
                cnt = conn.execute(
                    f"SELECT COUNT(DISTINCT code) FROM {tbl}"
                ).fetchone()[0]
                if tbl in result:
                    result[tbl]["code_count"] = cnt
            except Exception:
                pass

        return result
    except Exception as exc:
        # 写入命令独占写锁时只读连接会失败——返回空让面板显示上次状态，不崩。
        logger.warning("DuckDB 读取失败: %s", exc)
        return {}
    finally:
        # 始终关闭：避免只读连接泄漏长期持锁，阻塞后续写入命令（Phase1/fetch）。
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _cov_status(cov: Optional[float], ok: float, warn: float) -> str:
    if cov is None:
        return "err"
    return "ok" if cov >= ok else "warn" if cov >= warn else "err"


def _make(name: str, info: dict, ok: float = None, warn: float = None) -> SourceStatus:
    d = info.get("date")
    cnt = info.get("code_count")
    cov = cnt / UNIVERSE_SIZE if cnt is not None else None
    if ok is not None:
        st = _cov_status(cov, ok, warn)
    else:
        st = "ok" if d else "err"
    return SourceStatus(name=name, date=d, count=cnt, coverage=cov, status=st)


def get_watermarks(data_dir: Path) -> WatermarkData:
    wm_json = _load_watermark_json(data_dir)
    db = _query_duckdb(data_dir)

    # ── kline: watermark.json (managed by TDXCollector / update step)
    kline_date = wm_json.get("kline")
    kline = SourceStatus(
        name="kline", date=kline_date,
        count=None, coverage=None,
        status="ok" if kline_date else "err",
    )

    # ── features: 直接取 features 表 MAX(date)
    # 特征表因 5 日前向标签视界，天然比 kline 落后约 5 个交易日（~7 自然日）——这是
    # 健康状态而非告警。故用容差判断：features 落后 kline 不超过 10 个自然日即视为 ok。
    feat_info = db.get("features", {})
    feat_date = feat_info.get("date")
    feat_ok = bool(feat_date)
    if feat_date and kline_date:
        try:
            from datetime import date as _date
            kd = _date.fromisoformat(kline_date)
            fd = _date.fromisoformat(feat_date)
            feat_ok = (kd - fd).days <= 10
        except (ValueError, TypeError):
            feat_ok = bool(feat_date)
    features = SourceStatus(
        name="features", date=feat_date,
        count=None, coverage=None,
        status="ok" if feat_ok else "warn",
    )

    # ── market-level tables (date only, no coverage)
    northbound = _make("northbound", db.get("northbound", {}))
    lhb = _make("lhb", db.get("lhb", {}))

    # ── per-stock tables (date + coverage)
    fundamentals       = _make("fundamentals",        db.get("fundamentals",        {}), ok=0.90, warn=0.70)
    fund_flow          = _make("fund_flow",            db.get("fund_flow",            {}), ok=0.50, warn=0.20)
    reports            = _make("reports",              db.get("reports",              {}), ok=0.50, warn=0.20)
    financial_indicator= _make("financial_indicator",  db.get("financial_indicator",  {}), ok=0.80, warn=0.50)
    # EPS 共识仅覆盖被分析师跟踪的股票（约半数市场），40%+ 已是正常上限，不应判红。
    eps_snapshot       = _make("eps_snapshot",         db.get("eps_snapshot",         {}), ok=0.40, warn=0.20)

    # ── models: presence of eval_results.json
    eval_path = data_dir / "models" / "eval_results.json"
    models = SourceStatus(
        name="models", date=wm_json.get("features"),
        count=None, coverage=None,
        status="ok" if eval_path.exists() else "err",
    )

    return WatermarkData(
        kline=kline,
        features=features,
        northbound=northbound,
        fundamentals=fundamentals,
        fund_flow=fund_flow,
        lhb=lhb,
        reports=reports,
        financial_indicator=financial_indicator,
        eps_snapshot=eps_snapshot,
        models=models,
    )
