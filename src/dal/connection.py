"""DuckDB 单例连接管理"""
import duckdb

from config.settings import DB_PATH

_conn: duckdb.DuckDBPyConnection | None = None


def get_db(read_only: bool = False) -> duckdb.DuckDBPyConnection:
    """返回 DuckDB 连接。

    read_only=True 时返回一个**全新的只读连接**（不走单例）：DuckDB 单写多读，
    只读连接之间可跨进程并存。仅读取的流水线步骤（Phase 2/3 只读 features，模型存盘）
    用它即可与开着的 monitor（同样只读）并行运行，互不抢写锁。调用方负责关闭。

    read_only=False（默认）返回读写单例，供写入命令（fetch/Phase1/upsert）使用。
    """
    if read_only:
        return duckdb.connect(str(DB_PATH), read_only=True)
    global _conn
    if _conn is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _conn = duckdb.connect(str(DB_PATH))
    return _conn
