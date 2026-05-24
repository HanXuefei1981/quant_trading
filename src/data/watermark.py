"""水位管理：记录各数据源最后成功更新的日期，驱动增量采集与特征计算。

水位文件：data/watermark.json
  {
    "kline":        "2026-05-11",   # TDX K线最新日期
    "northbound":   "2026-05-11",   # 北向资金最新日期
    "tencent":      "2026-05-11",   # 腾讯财经快照最新日期
    "fundamentals": "2026-05-10",   # 基本面最新更新日期（月频）
    "fund_flow":    "2026-05-11",   # 个股资金流向最新更新日期
    # "features" key removed — features watermark is now managed by MetaRepo
    # (collect_log table, code="__market__")
  }

get_since(data_type) 返回水位日期（None 表示尚无记录，需全量）。
update(data_type, d)  原子性写入新水位。
"""
import json
import logging
from datetime import date
from pathlib import Path
from typing import Optional

from config.settings import DATA_DIR

logger = logging.getLogger(__name__)

_WATERMARK_PATH = DATA_DIR / "watermark.json"
# "features" key removed — features watermark is now managed by MetaRepo
# (collect_log table, code="__market__")
_VALID_KEYS = {"kline", "northbound", "tencent", "fundamentals", "fund_flow"}


def _read() -> dict:
    if not _WATERMARK_PATH.exists():
        return {}
    try:
        return json.loads(_WATERMARK_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning(f"读取水位文件失败，视为空水位: {exc}")
        return {}


def _write(data: dict) -> None:
    _WATERMARK_PATH.parent.mkdir(parents=True, exist_ok=True)
    _WATERMARK_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def get_all() -> dict[str, Optional[str]]:
    """返回全部水位，缺失 key 的值为 None。"""
    wm = _read()
    return {k: wm.get(k) for k in _VALID_KEYS}


def get_since(data_type: str) -> Optional[date]:
    """返回指定数据源的水位日期，None 表示尚无记录（需全量）。"""
    if data_type not in _VALID_KEYS:
        raise ValueError(f"未知水位类型: {data_type}，可选: {_VALID_KEYS}")
    wm = _read()
    val = wm.get(data_type)
    if val is None:
        return None
    try:
        return date.fromisoformat(val)
    except Exception:
        logger.warning(f"水位值 '{val}' 格式非法，视为空水位")
        return None


def update(data_type: str, d: date) -> None:
    """更新指定数据源的水位日期（只前进，不后退）。"""
    if data_type not in _VALID_KEYS:
        raise ValueError(f"未知水位类型: {data_type}")
    wm = _read()
    existing = wm.get(data_type)
    new_val = d.isoformat()
    if existing and existing >= new_val:
        return  # 水位已是最新或更新，跳过
    wm[data_type] = new_val
    _write(wm)
    logger.info(f"水位更新: {data_type} → {new_val}")


def reset(data_type: str) -> None:
    """清除指定数据源的水位（强制下次全量）。仅供调试使用。"""
    wm = _read()
    if data_type in wm:
        del wm[data_type]
        _write(wm)
        logger.info(f"水位已清除: {data_type}")
