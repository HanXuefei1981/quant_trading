"""DuckDB 单例连接管理"""
import duckdb

from config.settings import DB_PATH

_conn: duckdb.DuckDBPyConnection | None = None


def get_db(read_only: bool = False) -> duckdb.DuckDBPyConnection:
    """返回 DuckDB 连接。

    read_only=True：
      - 若本进程已持有读写单例 `_conn`，**复用它**（读写连接亦可读）——避免
        DuckDB「同进程对同一库不能用不同配置再开连接」的冲突（如 daily 链内
        update/Phase1 已开读写连接后，scan 再请求只读）。
      - 否则新开一个全新只读连接：DuckDB 单写多读，只读连接可跨进程并存，
        让独立运行的只读步骤（scan / Phase2/3）与开着的 monitor 并行、不抢写锁。
        调用方负责关闭。

    read_only=False（默认）：返回读写单例，供写入命令（fetch/Phase1/upsert）使用。

    注意（边界）：本函数只解决「读写单例已存在 → 只读请求复用之」。反向路径
    「同进程先开只读连接、之后再请求读写」DuckDB 仍会冲突；当前调用图不会出现
    （进程内写入步骤总在只读步骤之前），故不在修复范围。
    """
    global _conn
    if read_only:
        if _conn is not None:
            return _conn
        return duckdb.connect(str(DB_PATH), read_only=True)
    if _conn is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _conn = duckdb.connect(str(DB_PATH))
    return _conn
