"""
原子数据查询 API — 暴露底层 Provider 的原子能力

将雪球/东财/腾讯等数据源的查询接口作为顶层函数暴露，
Agent 可以直接调用获取个股K线、实时行情、板块数据等。

用法（Python API）:
  from dragon_quant.data import (
      get_sector_ranking, get_sector_components, get_sector_5min_kline,
      get_kline, get_minute_kline, get_quote, batch_get_quotes,
      cookie_status, fetch_cookies,
  )

  # 板块排行
  sectors = get_sector_ranking()   # 涨幅榜
  sectors = get_sector_ranking(asc=True)  # 跌幅榜

  # 板块成分股
  stocks = get_sector_components("301558")

  # 个股日K线
  kline = get_kline("600172", source="xueqiu", days=20)

  # 个股1分钟K线（分时）
  mline = get_minute_kline("600172")

  # 实时行情
  quote = get_quote("600172")
  quotes = batch_get_quotes(["600172", "000001", "002409"])

  # Cookie 管理
  cookie_status()     # 查看 Cookie 状态
  fetch_cookies()     # 自动刷新所有 Cookie
  fetch_cookies(source="xueqiu")  # 只刷新雪球

CLI 用法:
  python -m dragon_quant data sector
  python -m dragon_quant data sector --asc
  python -m dragon_quant data components --sector 301558
  python -m dragon_quant data kline --code 600172 [--source xueqiu] [--days 20]
  python -m dragon_quant data minute --code 600172
  python -m dragon_quant data quote --code 600172
  python -m dragon_quant data batch-quote --codes 600172,000001,002409
  python -m dragon_quant data cookie-status
  python -m dragon_quant data cookie-fetch [--source xueqiu]
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
    """获取概念板块涨跌幅排行榜（同花顺）

    Args:
        asc: False=涨幅榜, True=跌幅榜
    Returns:
        list[SectorPerformance] 按涨跌幅排序的板块列表
    """
    providers = _get_providers()
    ths = providers["ths"]
    return ths.get_sector_ranking(asc=asc)


def get_sector_components(sector_code: str, page: int = 1,
                          all_pages: bool = False,
                          page_size: int = 50) -> list[StockInfo]:
    """获取概念板块成分股列表（同花顺，按涨跌幅降序）

    Args:
        sector_code: 同花顺概念板块 6 位代码，如 "301558"
        page: 页码（默认第一页）
        all_pages: 是否自动拉取全量分页
        page_size: 每页大小
    Returns:
        list[StockInfo] 成分股列表
    """
    providers = _get_providers()
    ths = providers["ths"]
    return ths.get_sector_components(
        sector_code,
        page=page,
        all_pages=all_pages,
        page_size=page_size,
    )


def get_sector_5min_kline(sector_code: str, bars: int = 100) -> list[KBar]:
    """获取概念板块 5 分钟 K 线（同花顺，1 分钟分时聚合）

    Args:
        sector_code: 同花顺概念板块 6 位代码，如 "301558"
        bars: K 线根数（默认 100）
    """
    providers = _get_providers()
    ths = providers["ths"]
    return ths.get_sector_5min_kline(sector_code, bars=bars)


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


# ═══ Cookie 管理 ═══

def cookie_status() -> dict:
    """查看当前 Cookie 状态

    返回每个数据源的 Cookie 是否存在及长度。
    如果返回空字符串，说明该数据源的 Cookie 缺失或过期，需要刷新。

    Returns:
        {
            "eastmoney": {"ok": True, "length": 1234},
            "xueqiu": {"ok": False, "length": 0},
        }
    """
    from dragon_quant.providers.cookie import get_em, get_xq

    em = get_em()
    xq = get_xq()
    return {
        "eastmoney": {"ok": bool(em), "length": len(em)},
        "xueqiu": {"ok": bool(xq), "length": len(xq)},
    }


def fetch_cookies(source: str = "all") -> dict:
    """自动刷新 Cookie（使用无头浏览器 Playwright）

    Cookie 过期会导致 API 返回 400 / 401 / 空数据。
    遇到接口异常时，优先尝试此方法刷新 Cookie，然后重试业务请求。

    注意：默认（source="all"）只刷新雪球，不再刷新东财
    （主流程已改用同花顺，同花顺无需 Cookie）。如需东财 Cookie，
    显式传入 source="eastmoney"。

    Args:
        source: "all" 刷新雪球（默认）, "eastmoney" 刷新东财, "xueqiu" 刷新雪球
    Returns:
        {
            "eastmoney": {"ok": True, "length": 1234},
            "xueqiu": {"ok": True, "length": 567},
        }
    """
    from dragon_quant.providers.cookie import fetch_em, fetch_em_his, fetch_xq, get_em, get_xq

    if source == "eastmoney":
        fetch_em()      # push2 域（板块排行 / 成分股）
        fetch_em_his()  # push2his 域（板块5分K）
    if source in ("all", "xueqiu"):
        fetch_xq()

    # 重置 Provider 缓存，下次调用时重新初始化
    global _providers
    with _lock:
        _providers = None

    return cookie_status()
