"""Tushare Pro 客户端工厂（全项目唯一初始化入口）

使用私有代理服务器，token 和 URL 从 .env 读取（config/settings.py 统一注入）。
**所有 tushare 调用都必须经由本文件的 get_pro_api()，不要在别处自行 ts.pro_api()。**

调用约定（私有代理服务器关键点）：
    import tushare as ts
    pro = ts.pro_api(TUSHARE_TOKEN)
    pro._DataApi__http_url = TUSHARE_HTTP_URL   # ⭐️ 必须有这行，否则会打到官方服务器报「无效的 token」
    df = pro.index_basic(limit=5)
    bar = ts.pro_bar(api=pro, ts_code="000001.SZ", limit=3)   # pro_bar 需显式传 api=pro

凭证配置在 .env（勿提交）：
    TUSHARE_TOKEN=<token>
    TUSHARE_HTTP_URL=http://<host>:<port>/

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
