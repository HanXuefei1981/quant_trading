"""研报因子计算

输入：
  data/raw/reports/{code}.parquet — 研报列表（date, code, institution, rating）
  data/raw/eps/{code}.parquet     — EPS 共识快照（snapshot_date, code, eps_cur, eps_next, analyst_count）

输出（新增列）：
  analyst_count     — 覆盖机构数（前向填充）
  report_count_30d  — 近30日研报数（以 available_date = report_date + 1 计算）
  eps_consensus_cur — 当年 EPS 共识均值（快照前向填充）
  eps_revision      — EPS 修订方向（+1 上调 / -1 下调 / 0 不变）
"""
import logging
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

_REPORT_WINDOW = 30  # 近30日研报计数窗口（交易日）


def _load_reports(code: str, base_dir: Path) -> Optional[pd.DataFrame]:
    path = base_dir / "reports" / f"{code}.parquet"
    if not path.exists():
        return None
    try:
        df = pd.read_parquet(path)
        df["date"] = pd.to_datetime(df["date"])
        return df
    except Exception as exc:
        logger.debug(f"读取研报缓存失败 {code}: {exc}")
        return None


def _load_eps(code: str, base_dir: Path) -> Optional[pd.DataFrame]:
    path = base_dir / "eps" / f"{code}.parquet"
    if not path.exists():
        return None
    try:
        df = pd.read_parquet(path)
        df["snapshot_date"] = pd.to_datetime(df["snapshot_date"])
        return df.sort_values("snapshot_date").reset_index(drop=True)
    except Exception as exc:
        logger.debug(f"读取 EPS 缓存失败 {code}: {exc}")
        return None


def add_report_features(
    df: pd.DataFrame,
    code: str,
    base_dir: Optional[Path] = None,
) -> pd.DataFrame:
    """左连接研报因子到单股 K 线，返回新 DataFrame（不修改原始）。

    前视偏差：available_date = report_date + 1 自然日。
    缺失数据：全部填 NaN，由预处理层填均值。
    """
    if base_dir is None:
        from config.settings import DATA_DIR
        base_dir = DATA_DIR / "raw"

    df = df.copy()
    df["date"] = pd.to_datetime(df["date"])

    df = _add_report_counts(df, code, base_dir)
    df = _add_eps_features(df, code, base_dir)
    return df


def _add_report_counts(df: pd.DataFrame, code: str, base: Path) -> pd.DataFrame:
    """添加 analyst_count 和 report_count_30d。"""
    reports = _load_reports(code, base)
    if reports is None or reports.empty:
        df["analyst_count"] = np.nan
        df["report_count_30d"] = np.nan
        return df

    # available_date = report_date + 1 自然日（前视偏差控制）
    reports = reports.copy()
    reports["available_date"] = reports["date"] + pd.Timedelta(days=1)

    dates = df["date"].values

    report_counts = []
    analyst_counts = []
    for d in dates:
        d_ts = pd.Timestamp(d)
        # 近30个交易日窗口（包含当日，取前 _REPORT_WINDOW+1 个交易日的起始日）
        window_start = pd.bdate_range(end=d_ts, periods=_REPORT_WINDOW + 1)[0]
        # 仅使用 available_date <= 当前日期 的研报
        visible = reports[
            (reports["available_date"] <= d_ts)
            & (reports["available_date"] > window_start)
        ]
        report_counts.append(len(visible))
        analyst_counts.append(visible["institution"].nunique() if not visible.empty else 0)

    df["report_count_30d"] = report_counts
    df["analyst_count"] = analyst_counts
    return df


def _add_eps_features(df: pd.DataFrame, code: str, base: Path) -> pd.DataFrame:
    """添加 eps_consensus_cur 和 eps_revision。"""
    eps = _load_eps(code, base)
    if eps is None or eps.empty:
        df["eps_consensus_cur"] = np.nan
        df["eps_revision"] = np.nan
        return df

    # EPS 快照以 snapshot_date 作为 available_date（月频，不需要额外偏移）
    eps = eps.sort_values("snapshot_date").reset_index(drop=True)
    eps["eps_revision"] = np.sign(eps["eps_cur"].diff()).fillna(0).astype(int)

    # merge_asof：对每个 kline.date 取最近的 snapshot_date ≤ date（前向填充）
    df_sorted = df.sort_values("date").reset_index(drop=True)
    merged = pd.merge_asof(
        df_sorted,
        eps[["snapshot_date", "eps_cur", "eps_revision", "analyst_count"]].rename(
            columns={
                "snapshot_date": "date",
                "eps_cur": "_eps_cur",
                "eps_revision": "_eps_revision",
                "analyst_count": "_analyst_count_eps",
            }
        ),
        on="date",
        direction="backward",
    )

    # analyst_count 优先使用研报计数（已在 _add_report_counts 中写入），EPS 的 analyst_count 备用
    if "analyst_count" not in df.columns or df["analyst_count"].isna().all():
        merged["analyst_count"] = merged.pop("_analyst_count_eps")
    else:
        merged = merged.drop(columns=["_analyst_count_eps"], errors="ignore")

    merged["eps_consensus_cur"] = merged.pop("_eps_cur")
    merged["eps_revision"] = merged.pop("_eps_revision")

    # 还原原始行序
    return merged.sort_values("date").reset_index(drop=True)
