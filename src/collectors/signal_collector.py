"""龙虎榜全市场日报采集器

数据源：akshare.stock_lhb_detail_em(start_date, end_date)（东财，需关 VPN）
落地路径：data/raw/lhb/YYYY-MM-DD.parquet

列格式：date(datetime64), code(str), lhb_net_buy(float), lhb_buy_amount(float), lhb_sell_amount(float)
"""
import logging
from datetime import date as date_type
from datetime import datetime
from pathlib import Path
from typing import Optional

import akshare as ak
import pandas as pd

from src.collectors.base import BaseCollector, CollectStats

logger = logging.getLogger(__name__)

_COL_MAP = {
    "代码": "code",
    "股票代码": "code",
    "上榜日期": "date",
    "龙虎榜净买额": "lhb_net_buy",
    "买入额合计": "lhb_buy_amount",
    "卖出额合计": "lhb_sell_amount",
}


class SignalCollector(BaseCollector):
    """龙虎榜全市场日报采集器（按日期粒度，非按股票粒度）。

    ⚠️ akshare.stock_lhb_detail_em 在 VPN 开启时有 SSL 错误，需关 VPN 后运行。
    """

    def __init__(self, base_dir: Optional[Path] = None) -> None:
        from config.settings import DATA_DIR
        self._base = base_dir or (DATA_DIR / "raw")
        self._lhb_dir = self._base / "lhb"
        self._lhb_dir.mkdir(parents=True, exist_ok=True)

    def _fetch_daily(self, target_date: date_type) -> Optional[pd.DataFrame]:
        """拉取单日全市场龙虎榜，返回标准化 DataFrame 或 None。"""
        date_str = target_date.strftime("%Y%m%d")
        try:
            raw = ak.stock_lhb_detail_em(start_date=date_str, end_date=date_str)
        except Exception as exc:
            logger.warning(f"龙虎榜拉取失败 {date_str}: {exc}")
            return None

        if raw is None or raw.empty:
            return None

        cols_to_rename = {k: v for k, v in _COL_MAP.items() if k in raw.columns}
        df = raw.rename(columns=cols_to_rename)

        required = {"code", "lhb_net_buy"}
        if not required.issubset(df.columns):
            logger.warning(f"龙虎榜字段不全 {date_str}，已有列: {list(df.columns)}")
            return None

        df["date"] = pd.to_datetime(target_date)
        df["code"] = df["code"].astype(str).str.zfill(6)
        df["lhb_net_buy"] = pd.to_numeric(df["lhb_net_buy"], errors="coerce")
        df["lhb_buy_amount"] = pd.to_numeric(df.get("lhb_buy_amount", 0), errors="coerce")
        df["lhb_sell_amount"] = pd.to_numeric(df.get("lhb_sell_amount", 0), errors="coerce")

        keep = ["date", "code", "lhb_net_buy", "lhb_buy_amount", "lhb_sell_amount"]
        df = df[[c for c in keep if c in df.columns]]
        df = df.dropna(subset=["code"]).drop_duplicates(subset=["code"])
        return df if not df.empty else None

    def fetch_all(
        self,
        codes: list[str],
        date: Optional[date_type] = None,
        incremental: bool = True,
        max_errors: int = 10,
    ) -> CollectStats:
        """拉取指定日期（默认今日）的全市场龙虎榜并落盘。codes 参数忽略。

        注意：date 参数遮蔽了内置 date 类型，方法内部使用 date_type 别名引用类型。
        """
        stats = CollectStats()
        target = date if date is not None else datetime.today().date()
        out_path = self._lhb_dir / f"{target.strftime('%Y-%m-%d')}.parquet"

        if incremental and out_path.exists():
            stats.cached += 1
            return stats

        df = self._fetch_daily(target)
        if df is None:
            stats.fail += 1
            return stats

        if out_path.exists():
            existing = pd.read_parquet(out_path)
            existing["date"] = pd.to_datetime(existing["date"])
            df = pd.concat([existing, df], ignore_index=True)
            df = df.drop_duplicates(subset=["date", "code"]).sort_values("date")

        df.to_parquet(out_path, index=False)
        stats.ok += 1
        logger.info(f"龙虎榜 {target} 已保存：{len(df)} 只股票")
        return stats

    def fetch_one(self, code: str, since: Optional[date_type] = None) -> Optional[pd.DataFrame]:
        """为满足 BaseCollector 接口，等同于拉取今日并返回该股记录。"""
        target = datetime.today().date()
        self.fetch_all(codes=[], date=target, incremental=True)
        return self.load(code)

    def load(self, code: str) -> Optional[pd.DataFrame]:
        """从所有历史日期文件中聚合该股上榜记录。"""
        parts = []
        for p in sorted(self._lhb_dir.glob("*.parquet")):
            try:
                df = pd.read_parquet(p)
                df["date"] = pd.to_datetime(df["date"])
                sub = df[df["code"] == code]
                if not sub.empty:
                    parts.append(sub)
            except Exception:
                continue
        if not parts:
            return None
        result = pd.concat(parts, ignore_index=True).sort_values("date").reset_index(drop=True)
        return result if not result.empty else None

    def load_market(self, date: Optional[date_type] = None) -> Optional[pd.DataFrame]:
        """读取单日全市场龙虎榜文件。"""
        target = date if date is not None else datetime.today().date()
        path = self._lhb_dir / f"{target.strftime('%Y-%m-%d')}.parquet"
        if not path.exists():
            return None
        try:
            df = pd.read_parquet(path)
            df["date"] = pd.to_datetime(df["date"])
            return df
        except Exception as exc:
            logger.warning(f"读取龙虎榜 {target} 失败: {exc}")
            return None
