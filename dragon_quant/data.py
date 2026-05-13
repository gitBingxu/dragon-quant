"""
原子数据查询 API — 暴露底层 Provider 的原子能力

将雪球/东财/腾讯等数据源的查询接口作为顶层函数暴露，
Agent 可以直接调用获取个股K线、实时行情、板块数据等。

用法（Python API）:
  from dragon_quant.data import (
      get_sector_ranking, get_sector_components, get_sector_5min_kline,
      get_kline, get_minute_kline, get_quote, batch_get_quotes,
  )

  # 板块排行
  sectors = get_sector_ranking()   # 涨幅榜
  sectors = get_sector_ranking(asc=True)  # 跌幅榜

  # 板块成分股
  stocks = get_sector_components("BK0487")

  # 个股日K线
  kline = get_kline("600172", source="xueqiu", days=20)

  # 个股1分钟K线（分时）
  mline = get_minute_kline("600172")

  # 实时行情
  quote = get_quote("600172")
  quotes = batch_get_quotes(["600172", "000001", "002409"])

CLI 用法:
  python -m dragon_quant data sector
  python -m dragon_quant data sector --asc
  python -m dragon_quant data components --sector BK0487
  python -m dragon_quant data kline --code 600172 [--source xueqiu] [--days 20]
  python -m dragon_quant data minute --code 600172
  python -m dragon_quant data quote --code 600172
  python -m dragon_quant data batch-quote --codes 600172,000001,002409
"""

import json
import threading
from typing import Optional

from dragon_quant.models.types import Quote, KBar, StockInfo, SectorPerformance
from dragon_quant.providers import create_providers

# 懒加载 — 首次调用时初始化所有 Provider
_lock = threading.Lock()
_providers: Optional[dict] = None


def _get_providers() -> dict:
    global _providers
    if _providers is None:
        with _lock:
            if _providers is None:
                _providers = create_providers()
    return _providers


# ═══ 板块相关 ═══

def get_sector_ranking(asc: bool = False) -> list[SectorPerformance]:
    """获取概念板块涨跌幅排行榜

    Args:
        asc: False=涨幅榜, True=跌幅榜
    Returns:
        list[SectorPerformance] 按涨跌幅排序的板块列表
    """
    providers = _get_providers()
    em = providers["eastmoney"]
    return em.get_sector_ranking(asc=asc)


def get_sector_components(sector_code: str) -> list[StockInfo]:
    """获取概念板块成分股列表（按涨跌幅降序）

    Args:
        sector_code: 板块代码，如 "BK0487"
    Returns:
        list[StockInfo] 成分股列表
    """
    providers = _get_providers()
    em = providers["eastmoney"]
    return em.get_sector_components(sector_code)


def get_sector_5min_kline(sector_code: str, bars: int = 100) -> list[KBar]:
    """获取概念板块 5 分钟 K 线

    Args:
        sector_code: 板块代码
        bars: K 线根数（默认 100）
    """
    providers = _get_providers()
    em = providers["eastmoney"]
    return em.get_sector_5min_kline(sector_code, bars=bars)


# ═══ 个股相关 ═══

def get_kline(code: str, source: str = "xueqiu", days: int = 20) -> list[KBar]:
    """获取个股日 K 线

    Args:
        code: 股票代码，如 "600172"
        source: 数据源，xueqiu=雪球, tencent=腾讯
        days: 天数
    Returns:
        list[KBar] 日K线数据
    """
    providers = _get_providers()
    p = providers.get(source, providers["xueqiu"])
    return p.get_kline(code, days=days)


def get_minute_kline(code: str, source: str = "xueqiu") -> list[KBar]:
    """获取个股 1分钟K线（分时数据）

    Args:
        code: 股票代码
        source: 数据源，xueqiu 或 tencent
    """
    providers = _get_providers()
    p = providers.get(source, providers["xueqiu"])
    if source == "tencent":
        return p.get_5min_kline(code)
    # 雪球直接用 get_minute_kline
    return p.get_minute_kline(code) if hasattr(p, 'get_minute_kline') else p.get_5min_kline(code)


def get_quote(code: str, source: str = "tencent") -> Optional[Quote]:
    """获取个股实时行情

    Args:
        code: 股票代码
        source: 数据源，tencent=腾讯（推荐）, xueqiu=雪球
    Returns:
        Quote 或 None（获取失败时）
    """
    providers = _get_providers()
    p = providers.get(source, providers["tencent"])
    return p.get_quote(code)


def batch_get_quotes(codes: list[str], source: str = "tencent") -> list[Quote]:
    """批量获取实时行情

    Args:
        codes: 股票代码列表
        source: 数据源
    Returns:
        list[Quote]
    """
    providers = _get_providers()
    p = providers.get(source, providers["tencent"])
    if hasattr(p, 'batch_get_quotes'):
        return p.batch_get_quotes(codes)
    # fallback: 逐个请求
    return [p.get_quote(c) for c in codes]
