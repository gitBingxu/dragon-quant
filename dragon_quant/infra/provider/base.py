"""
StockProvider — 数据提供层抽象接口
所有数据源适配器实现此接口，评分器只依赖此接口。
"""

from abc import ABC, abstractmethod
from typing import Optional
from dragon_quant.infra.models import Quote, KBar, StockInfo, SectorPerformance


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
    def get_sector_ranking(self, asc: bool = False,
                           category: str = "hy") -> list[SectorPerformance]:
        """
        获取板块涨跌幅排行。
        asc=False: 涨幅榜（领涨板块）
        asc=True: 跌幅榜（领跌板块）
        category: hy 行业 / gn 概念 / dq 地区；默认 hy 保持历史行为
        """
        ...

    @abstractmethod
    def get_sector_components(self, sector_code: str, page: int = 1,
                              all_pages: bool = False,
                              page_size: int = 50,
                              category: str = "hy") -> list[StockInfo]:
        """获取板块成分股列表（按涨跌幅降序）"""
        ...

    @abstractmethod
    def get_sector_5min_kline(self, sector_code: str, bars: int = 100,
                              category: str = "hy") -> list[KBar]:
        """获取板块 5 分钟 K 线"""
        ...

    # ─── scorers 新增（普通方法，默认未实现；仅 ths 覆写）───

    def get_sector_1min_kline(self, sector_code: str, bars: int = 240,
                              category: str = "hy") -> list[KBar]:
        """获取板块当日 1 分钟分时 K 线（原始 1 分，不聚合）"""
        raise NotImplementedError(f"{self.name} 不提供板块当日 1 分 K")

    def get_sector_5min_kline_history(self, sector_code: str, days: int = 10,
                                      category: str = "hy") -> list[KBar]:
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

    # ─── prep 复盘分新增（普通方法，默认未实现；不得用 @abstractmethod）───
    # 动机：``create_providers()`` 会一次实例化全部 provider，抽象方法会让旧
    # provider 直接崩（AGENTS 硬约束）。错误契约：transport 失败抛异常，
    # 成功但确无数据返回 {} / []。

    def batch_get_daily_klines(self, codes: list[str], days: int = 120,
                               end_date: str | None = None) -> dict[str, list[KBar]]:
        """批量日 K（不复权/前复权由实现声明）；返回 ``{code: [KBar, ...]}``。"""
        raise NotImplementedError(f"{self.name} 不提供批量日 K")

    def get_index_daily(self, code: str, days: int = 120) -> list[KBar]:
        """指数日 K（points）；transport 失败抛异常。"""
        raise NotImplementedError(f"{self.name} 不提供指数日 K")

    def get_auction_snapshot(self, code: str) -> dict | None:
        """当前交易日的 09:25 竞价终态（价/量/未匹配量）；非当日或不可用返回 None。

        返回 ``{"price","amount","matched","unmatched"}``；``amount`` = 成交额（元）。
        T+1 反馈补采只走当日调用，历史不可回补。
        """
        raise NotImplementedError(f"{self.name} 不提供竞价终态")

    def get_5min_kline_for_date(self, code: str, date: str) -> list[KBar]:
        """指定交易日（``YYYY-MM-DD``）的 5 分钟 K 线；不可用返回 ``[]``。"""
        raise NotImplementedError(f"{self.name} 不提供指定日 5 分钟 K")

    # ─── prep 宏观扰动（普通方法，默认未实现；仅部分 provider 覆写）───
    # 错误契约：transport/解析失败抛异常，由 providers.macro.collect_macro 逐源回退。

    def get_macro_quotes(self, specs: list, trade_date: str) -> dict[str, dict]:
        """批量获取宏观日 K/报价，返回 {spec.key: normalized_dict}。

        canonical 取「日 K 中日期 < trade_date 的最后一根」；quote 类回退源必须
        自行校验 session_date < trade_date，否则拒绝该品种。transport 失败抛异常。
        """
        raise NotImplementedError(f"{self.name} 不提供宏观报价")
