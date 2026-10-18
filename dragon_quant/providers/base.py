"""
StockProvider — 数据提供层抽象接口
所有数据源适配器实现此接口，评分器只依赖此接口。
"""

from abc import ABC, abstractmethod
from typing import Optional
from dragon_quant.models.types import Quote, KBar, StockInfo, SectorPerformance


class StockProvider(ABC):
    """数据提供者抽象基类"""

    def __init__(self):
        self._logger = None

    def set_logger(self, logger):
        self._logger = logger

    @property
    @abstractmethod
    def name(self) -> str:
        """数据源名称，如 'xueqiu', 'tencent', 'ths'"""
        ...

    # ─── 板块相关 ───

    @abstractmethod
    def get_sector_ranking(self, asc: bool = False) -> list[SectorPerformance]:
        """
        获取板块涨跌幅排行。
        asc=False: 涨幅榜（领涨板块）
        asc=True: 跌幅榜（领跌板块）
        """
        ...

    @abstractmethod
    def get_sector_components(self, sector_code: str, page: int = 1,
                              all_pages: bool = False,
                              page_size: int = 50) -> list[StockInfo]:
        """获取板块成分股列表（按涨跌幅降序）"""
        ...

    @abstractmethod
    def get_sector_5min_kline(self, sector_code: str, bars: int = 100) -> list[KBar]:
        """获取板块 5 分钟 K 线"""
        ...

    # ─── scorers 新增（普通方法，默认未实现；仅 ths 覆写）───

    def get_sector_1min_kline(self, sector_code: str, bars: int = 240) -> list[KBar]:
        """获取板块当日 1 分钟分时 K 线（原始 1 分，不聚合）"""
        raise NotImplementedError(f"{self.name} 不提供板块当日 1 分 K")

    def get_sector_5min_kline_history(self, sector_code: str, days: int = 10) -> list[KBar]:
        """获取板块近 days 个交易日的 5 分钟历史 K 线（真实 OHLC）"""
        raise NotImplementedError(f"{self.name} 不提供板块历史 5 分 K")

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

    # ─── 个股可选方法（普通方法，默认未实现；部分 provider 覆写）───

    def get_minute_kline(self, code: str) -> list[KBar]:
        """获取当日 1 分钟分时 K 线（真实 OHLC）"""
        raise NotImplementedError(f"{self.name} 不提供个股 1 分 K")

    def get_5min_kline_for(self, code: str, target_ts: int,
                           bars_before: int = 48, bars_after: int = 96,
                           fq_type: str = "after") -> list[KBar]:
        """获取指定日期附近的 5 分钟 K 线（复盘用）"""
        raise NotImplementedError(f"{self.name} 不提供指定日 5 分 K")

    def batch_get_quotes(self, codes: list[str]) -> list[Quote]:
        """批量获取实时行情快照"""
        raise NotImplementedError(f"{self.name} 不提供批量行情")

    # ─── prep 盘后复盘采集方法（普通方法，默认未实现；仅 tdx/ths 覆写）───
    # 错误契约（与存量「异常吞掉返回 []」不同）：transport 层失败（超时/403/连接错误）
    # 抛异常，由 prep 采集器捕获记 status=error；仅「请求成功但确无数据」返回空。

    def get_market_breadth_rows(self, count: int = 6000) -> list[dict]:
        """全 A 实时行情行（已归一化为 native 类型，NaN→None）。

        行含 code/name/close/pre_close/high/low/vol/amount/buy_price_limit/sell_price_limit。
        """
        raise NotImplementedError(f"{self.name} 不提供全 A 市场宽度")

    def get_fg_boards(self, names: list[str]) -> dict[str, dict]:
        """FG(风格)板块批量成员：一次板块列表解析 + 逐板块成员。

        返回 {name: {'rows': [...], 'status': 'ok'|'missing'}}；transport 失败抛异常。
        """
        raise NotImplementedError(f"{self.name} 不提供 FG 板块批量成员")

    def get_limit_up_pool(self, date: str) -> dict:
        """THS 涨停池 dataapi，date=YYYYMMDD；返回 {'total': int, 'rows': [...]}"""
        raise NotImplementedError(f"{self.name} 不提供涨停池")

    def get_block_top(self, date: str) -> dict:
        """THS 题材主线榜 dataapi，date=YYYYMMDD；返回 {'rows': [...]}"""
        raise NotImplementedError(f"{self.name} 不提供题材主线榜")

    def get_announcements(self, code: str, count: int = 30, page: int = 1) -> list[dict]:
        """cninfo 个股公告单页（title/type/date/url），最新在前；失败抛异常"""
        raise NotImplementedError(f"{self.name} 不提供公告")
