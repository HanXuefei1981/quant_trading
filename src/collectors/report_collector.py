"""研报 + EPS 共识采集器

数据落地路径：
  data/raw/reports/{code}.parquet  — 研报列表（date, code, institution, rating）
  data/raw/eps/{code}.parquet      — EPS 共识快照（snapshot_date, code, eps_cur, eps_next, analyst_count）
"""
import logging
import time
from datetime import date
from pathlib import Path
from typing import Optional

import akshare as ak
import pandas as pd
from tqdm import tqdm

from src.collectors.base import BaseCollector, CollectStats

logger = logging.getLogger(__name__)

_REPORT_COL_MAP = {
    "日期": "date",
    "机构": "institution",
    "东财评级": "rating",
}


class ReportCollector(BaseCollector):
    """研报列表 + EPS 共识采集器。

    mode='report': 东财研报列表 → data/raw/reports/{code}.parquet
    mode='eps':    同花顺 EPS 共识快照 → data/raw/eps/{code}.parquet
    """

    def __init__(self, base_dir: Optional[Path] = None, delay: float = 0.5) -> None:
        if base_dir is not None:
            self._base = base_dir
        else:
            from config.settings import DATA_DIR
            self._base = DATA_DIR / "raw"
        self._reports_dir = self._base / "reports"
        self._eps_dir = self._base / "eps"
        self._reports_dir.mkdir(parents=True, exist_ok=True)
        self._eps_dir.mkdir(parents=True, exist_ok=True)
        self.delay = delay

    # ── 研报 ──────────────────────────────────────────────────────────────────

    def _fetch_report(self, code: str) -> Optional[pd.DataFrame]:
        """拉取研报列表，返回标准化 DataFrame 或 None。"""
        try:
            raw = ak.stock_research_report_em(symbol=code)
        except Exception as exc:
            logger.debug(f"研报拉取失败 {code}: {exc}")
            return None

        if raw is None or raw.empty:
            return None

        cols_needed = [c for c in _REPORT_COL_MAP if c in raw.columns]
        df = raw[cols_needed].rename(columns=_REPORT_COL_MAP).copy()
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df["code"] = code
        df = df.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)
        return df if not df.empty else None

    def _save_report(self, code: str, df: pd.DataFrame) -> None:
        path = self._reports_dir / f"{code}.parquet"
        if path.exists():
            existing = pd.read_parquet(path)
            existing["date"] = pd.to_datetime(existing["date"])
            df = pd.concat([existing, df], ignore_index=True)
            df = df.drop_duplicates(subset=["date", "institution"]).sort_values("date")
        df.to_parquet(path, index=False)

    # ── EPS 共识 ──────────────────────────────────────────────────────────────

    def _fetch_eps(self, code: str) -> Optional[pd.DataFrame]:
        """拉取同花顺 EPS 共识快照，返回标准化 DataFrame 或 None。"""
        try:
            raw = ak.stock_profit_forecast_ths(
                symbol=code, indicator="预测年报每股收益"
            )
        except Exception as exc:
            logger.debug(f"EPS 共识拉取失败 {code}: {exc}")
            return None

        if raw is None or raw.empty:
            return None

        today = pd.Timestamp.today().normalize()
        rows = raw.sort_values("年度").reset_index(drop=True)

        eps_cur = rows["均值"].iloc[0] if len(rows) >= 1 else float("nan")
        eps_next = rows["均值"].iloc[1] if len(rows) >= 2 else float("nan")
        analyst_count = int(rows["预测机构数"].iloc[0]) if len(rows) >= 1 else 0

        return pd.DataFrame([{
            "snapshot_date": today,
            "code": code,
            "eps_cur": eps_cur,
            "eps_next": eps_next,
            "analyst_count": analyst_count,
        }])

    def _save_eps(self, code: str, df: pd.DataFrame) -> None:
        path = self._eps_dir / f"{code}.parquet"
        if path.exists():
            existing = pd.read_parquet(path)
            existing["snapshot_date"] = pd.to_datetime(existing["snapshot_date"])
            df = pd.concat([existing, df], ignore_index=True)
            df = df.drop_duplicates(subset=["snapshot_date"]).sort_values("snapshot_date")
        df.to_parquet(path, index=False)

    # ── BaseCollector 接口 ────────────────────────────────────────────────────

    def fetch_one(
        self,
        code: str,
        mode: str = "report",
        since: Optional[date] = None,
    ) -> Optional[pd.DataFrame]:
        """拉取单只股票研报或 EPS 共识并落盘。"""
        if mode == "report":
            df = self._fetch_report(code)
            if df is not None:
                self._save_report(code, df)
            return df
        elif mode == "eps":
            df = self._fetch_eps(code)
            if df is not None:
                self._save_eps(code, df)
            return df
        else:
            raise ValueError(f"mode 必须是 'report' 或 'eps'，收到: {mode!r}")

    def fetch_all(
        self,
        codes: list[str],
        mode: str = "report",
        incremental: bool = True,
        max_errors: int = 50,
    ) -> CollectStats:
        """批量拉取，tqdm 进度，连续失败熔断。"""
        stats = CollectStats()
        consecutive_errors = 0
        cache_dir = self._reports_dir if mode == "report" else self._eps_dir

        for code in tqdm(codes, desc=f"研报采集[{mode}]"):
            cache_path = cache_dir / f"{code}.parquet"
            if incremental and cache_path.exists():
                stats.cached += 1
                continue

            df = self.fetch_one(code, mode=mode)
            if df is not None:
                stats.ok += 1
                consecutive_errors = 0
            else:
                stats.fail += 1
                consecutive_errors += 1
                if consecutive_errors >= max_errors:
                    logger.error(f"连续 {max_errors} 次失败，终止采集")
                    break

            time.sleep(self.delay)

        logger.info(f"研报采集[{mode}]完成：{stats}")
        return stats

    def load(
        self,
        code: str,
        mode: str = "report",
    ) -> Optional[pd.DataFrame]:
        """从本地缓存加载，不触发网络请求。"""
        cache_dir = self._reports_dir if mode == "report" else self._eps_dir
        path = cache_dir / f"{code}.parquet"
        if not path.exists():
            return None
        try:
            return pd.read_parquet(path)
        except Exception as exc:
            logger.warning(f"读取缓存失败 {code} [{mode}]: {exc}")
            return None
