"""
雪球 Provider — 个股日K线、个股5分钟K线
需完整 Cookie（含 xq_a_token, xq_r_token, xq_is_login 等）
"""

import json, sys, time
import urllib.request
from typing import Optional
from dragon_quant.models.types import Quote, KBar, StockInfo, SectorPerformance
from dragon_quant.providers.base import StockProvider
from dragon_quant.providers.cookie import get_xq

BASE = "https://stock.xueqiu.com"

HEADERS = {
    "Accept": "*/*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "Origin": "https://xueqiu.com",
    "Pragma": "no-cache",
    "Priority": "u=1, i",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
    "sec-ch-ua": '"Google Chrome";v="147", "Not.A/Brand";v="8", "Chromium";v="147"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"macOS"',
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-site",
}


def _symbol(code: str) -> str:
    prefix = "SH" if code.startswith(("6", "9")) else "SZ"
    return f"{prefix}{code}"


def _fetch(path: str, logger=None, endpoint: str = "") -> Optional[dict]:
    """雪球 API GET 请求"""
    cookie = get_xq()
    if not cookie:
        print("⚠️ 雪球 Cookie 未设置", file=sys.stderr)
        return None

    url = f"{BASE}{path}"
    headers = dict(HEADERS)
    if "symbol=" in path:
        try:
            symbol_part = path.split("symbol=")[1].split("&")[0]
            headers["Referer"] = f"https://xueqiu.com/S/{symbol_part}"
        except (IndexError, ValueError):
            headers["Referer"] = "https://xueqiu.com/"
    else:
        headers["Referer"] = "https://xueqiu.com/"
    headers["Cookie"] = cookie

    req = urllib.request.Request(url, headers=headers)
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8")
            data = json.loads(raw)
        elapsed = (time.time() - t0) * 1000
    except Exception as e:
        elapsed = (time.time() - t0) * 1000
        if logger:
            logger.api("xueqiu", endpoint, ok=False,
                       elapsed_ms=elapsed, error=str(e))
        print(f"  ⚠️ 雪球请求失败: {e}", file=sys.stderr)
        return None

    if data.get("error_code") and data["error_code"] != 0:
        if logger:
            logger.api("xueqiu", endpoint, ok=False,
                       elapsed_ms=elapsed, error=data.get("error_description", ""))
        print(f"  ⚠️ 雪球 API 错误: {data.get('error_description','')}", file=sys.stderr)
        return None

    if logger:
        logger.api("xueqiu", endpoint, ok=True, elapsed_ms=elapsed)
    return data


def _parse_kline(items: list[list]) -> list[KBar]:
    """雪球 K 线格式: [ts, volume, open, high, low, close, chg, pct, turnover, amount]"""
    result = []
    for item in items:
        try:
            result.append(KBar(
                timestamp=int(item[0]),
                volume=float(item[1]),
                open=float(item[2]), high=float(item[3]),
                low=float(item[4]), close=float(item[5]),
                chg=float(item[6]), pct=float(item[7]),
                turnover=float(item[8]), amount=float(item[9]),
            ))
        except (ValueError, IndexError):
            continue
    return result


class XueqiuProvider(StockProvider):

    @property
    def name(self) -> str:
        return "xueqiu"

    # ─── 日 K 线 ───

    def get_kline(self, code: str, days: int = 20) -> list[KBar]:
        symbol = _symbol(code)
        now_ms = int(time.time() * 1000)
        begin = now_ms - 100 * 86400 * 1000  # 从 100 天前开始取
        path = f"/v5/stock/chart/kline.json?symbol={symbol}&period=day&type=after&count={max(days * 4, 300)}&indicator=kline&begin={begin}"
        data = _fetch(path, logger=self._logger, endpoint="kline")
        if not data:
            return []
        items = data.get("data", {}).get("item", []) or data.get("data", {}).get("items", [])
        return _parse_kline(items)[-days:]

    # ─── 5 分钟 K 线 ───

    def get_5min_kline(self, code: str, bars: int = 96) -> list[KBar]:
        symbol = _symbol(code)
        now_ms = int(time.time() * 1000)
        begin = now_ms - 3 * 86400 * 1000
        path = f"/v5/stock/chart/kline.json?symbol={symbol}&period=5m&type=after&count={max(bars * 4, 500)}&indicator=kline&begin={begin}"
        data = _fetch(path, logger=self._logger, endpoint="5min_kline")
        if not data:
            return []
        items = data.get("data", {}).get("item", []) or data.get("data", {}).get("items", [])
        return _parse_kline(items)

    # ─── 分时 K 线（1 分钟级） ───

    def get_minute_kline(self, code: str) -> list[KBar]:
        """获取当日分时K线（1分钟级），open 取上一分钟的 close"""
        symbol = _symbol(code)
        path = f"/v5/stock/chart/minute.json?symbol={symbol}&period=1d"
        data = _fetch(path, logger=self._logger, endpoint="minute_kline")
        if not data:
            return []
        items = data.get("data", {}).get("items", [])
        result = []
        prev_close = None
        for item in items:
            cur = float(item["current"])
            o = prev_close if prev_close is not None else cur
            hi = item.get("high")
            lo = item.get("low")
            chg_v = item.get("chg")
            pct_v = item.get("percent")
            result.append(KBar(
                timestamp=int(item["timestamp"]),
                open=o, close=cur,
                high=float(hi) if hi is not None else cur,
                low=float(lo) if lo is not None else cur,
                volume=float(item.get("volume", 0)),
                amount=float(item.get("amount", 0)),
                chg=float(chg_v) if chg_v is not None else 0,
                pct=float(pct_v) if pct_v is not None else 0,
                turnover=0,
            ))
            prev_close = cur
        return result

    # ─── 未实现 ───

    def get_sector_ranking(self, asc: bool = False) -> list[SectorPerformance]:
        raise NotImplementedError("雪球不提供板块排行")

    def get_sector_components(self, sector_code: str, page: int = 1) -> list[StockInfo]:
        raise NotImplementedError("雪球不提供板块成分股")

    def get_sector_5min_kline(self, sector_code: str, bars: int = 100) -> list[KBar]:
        raise NotImplementedError("雪球不提供板块 K 线")

    def get_quote(self, code: str) -> Optional[Quote]:
        raise NotImplementedError("用腾讯 Get 行情")
