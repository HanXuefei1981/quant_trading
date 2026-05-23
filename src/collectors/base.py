"""采集器基类与统计数据类"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date


@dataclass
class CollectStats:
    """单次采集统计"""
    ok: int = 0
    fail: int = 0
    cached: int = 0
    skipped: int = 0

    def __str__(self) -> str:
        return (
            f"新拉取={self.ok} 缓存={self.cached} "
            f"跳过={self.skipped} 失败={self.fail}"
        )

    @property
    def total(self) -> int:
        return self.ok + self.fail + self.cached + self.skipped


class BaseCollector(ABC):
    """所有采集器的抽象基类。

    子类必须实现 collect()，将数据写入 DAL（DuckDB）。
    """

    @abstractmethod
    def collect(self, codes: list[str] | None = None, since: date | None = None) -> CollectStats:
        """执行采集并写入 DAL。

        codes: 需要采集的股票代码列表；None 表示空列表；市场级采集器忽略此参数。
        since: 覆盖增量起点；None 时采集器自行从 MetaRepo 查询上次日期。
        """
