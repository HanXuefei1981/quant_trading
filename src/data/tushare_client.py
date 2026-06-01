"""Tushare Pro 客户端工厂

使用私有代理服务器，token 和 URL 从环境变量读取（config/settings.py 统一注入）。

用法：
    from src.data.tushare_client import get_pro_api

    pro = get_pro_api()
    df = pro.daily(ts_code='000001.SZ', start_date='20240101', end_date='20240131')
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_pro_instance = None


def get_pro_api():
    """返回已配置好代理 URL 的 tushare Pro 实例（单例）。"""
    global _pro_instance
    if _pro_instance is not None:
        return _pro_instance

    from config.settings import TUSHARE_HTTP_URL, TUSHARE_TOKEN

    if not TUSHARE_TOKEN:
        raise RuntimeError(
            "TUSHARE_TOKEN 未配置。请在项目根目录的 .env 文件中设置：\n"
            "  TUSHARE_TOKEN=<your_token>"
        )

    try:
        import tushare as ts
    except ImportError as exc:
        raise ImportError("请先安装 tushare：pip install tushare>=1.4.24") from exc

    pro = ts.pro_api(TUSHARE_TOKEN)
    pro._DataApi__http_url = TUSHARE_HTTP_URL
    logger.info("Tushare Pro 已初始化，代理: %s", TUSHARE_HTTP_URL)

    _pro_instance = pro
    return pro
