"""交易权限过滤：获取并缓存 ST / *ST 股票代码集合"""
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

from config.settings import PROCESSED_DIR, ST_CACHE_DAYS

logger = logging.getLogger(__name__)

_CACHE_FILE = PROCESSED_DIR / "st_codes.json"


def get_st_codes() -> set[str]:
    """返回当前 ST / *ST 股票代码集合。

    优先读取本地缓存（有效期 ST_CACHE_DAYS 天），
    缓存缺失或过期时调用 akshare 刷新并重新缓存。
    akshare 调用失败时返回空集合并记录警告，不影响主流程。
    """
    if _CACHE_FILE.exists():
        try:
            data = json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
            cached_at = datetime.fromisoformat(data["cached_at"])
            if datetime.now() - cached_at < timedelta(days=ST_CACHE_DAYS):
                codes = set(data["codes"])
                logger.info(f"ST 列表来自缓存（{cached_at.date()}），共 {len(codes)} 只")
                return codes
        except Exception:
            pass

    logger.info("正在从 akshare 获取最新 ST 股票列表...")
    try:
        import akshare as ak
        df = ak.stock_info_a_code_name()
        st_mask = df["name"].str.contains(r"\bST\b|^\*?ST", case=False, na=False, regex=True)
        codes = set(df.loc[st_mask, "code"].tolist())

        _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        cache = {"codes": sorted(codes), "cached_at": datetime.now().isoformat(timespec="seconds")}
        _CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info(f"ST 列表已更新并缓存，共 {len(codes)} 只 ST/ST 股")
        return codes
    except Exception as exc:
        logger.warning(f"获取 ST 列表失败（{exc}），跳过 ST 过滤")
        return set()
