"""
StockProvider — 数据提供层抽象接口
所有数据源适配器实现此接口，评分器只依赖此接口。
"""

from abc import ABC, abstractmethod
from typing import Optional
from dragon_quant.models.types import Quote, KBar, StockInfo, SectorPerformance


class StockProvider(ABC):
    """数据提供者抽象基类"""

    @property
    @abstractmethod
    def name(self) -> str:
        """数据源名称，如 'xueqiu', 'eastmoney', 'tencent'"""
        ...

    # ─── 板块相关 ───

    @abstractmethod
    def get_sector_ranking(self, asc: bool = False) -> list[SectorPerformance]:
        """
        获取概念板块涨跌幅排行。
        asc=False: 涨幅榜（领涨板块）
        asc=True: 跌幅榜（领跌板块）
        """
        ...

    @abstractmethod
    def get_sector_components(self, sector_code: str, page: int = 1) -> list[StockInfo]:
        """获取概念板块成分股列表（按涨跌幅降序）"""
        ...

    @abstractmethod
    def get_sector_5min_kline(self, sector_code: str, bars: int = 100) -> list[KBar]:
        """获取概念板块 5 分钟 K 线"""
        ...

    # ─── 个股相关 ───

    @abstractmethod
    def get_kline(self, code: str, days: int = 20) -> list[KBar]:
        """获取个股日 K 线"""
        ...

    @abstractmethod
    def get_5min_kline(self, code: str, bars: int = 96) -> list[KBar]:
        """获取个股 5 分钟 K 线"""
        ...

    @abstractmethod
    def get_quote(self, code: str) -> Optional[Quote]:
        """获取个股实时行情"""
        ...
