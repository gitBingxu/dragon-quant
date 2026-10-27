"""
雪球 Provider — 个股日K线、个股5分钟K线
需完整 Cookie（含 xq_a_token, xq_r_token, xq_is_login 等）
"""

import json
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo
from dragon_quant.infra.models import Quote, KBar, StockInfo, SectorPerformance
from dragon_quant.infra.provider.base import StockProvider
from dragon_quant.infra.provider.cookie import get_xq

BASE = "https://stock.xueqiu.com"

HEADERS = {
    "Accept": "*/*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "Origin": "https://xueqiu.com",
    "Pragma": "no-cache",
    "Priority": "u=1, i",
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"),
    "sec-ch-ua": '"Google Chrome";v="147", "Not.A/Brand";v="8", "Chromium";v="147"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"macOS"',
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-site",
}


def _symbol(code: str) -> str:
    code = code.upper()
    if code.startswith(("SH", "SZ")):
        return code
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


def _fetch_macro_quote_json(url: str, cookie: str) -> dict:
    """雪球宏观 quote JSON；HTTP/解析失败抛 RuntimeError（provider 契约）。"""
    headers = dict(HEADERS)
    headers["Cookie"] = cookie
    headers["Referer"] = "https://xueqiu.com/"
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        raise RuntimeError(f"雪球宏观行情失败: {exc}") from exc


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

    def get_kline(self, code: str, days: int = 20, fq_type: str = "after") -> list[KBar]:
        symbol = _symbol(code)
        now_ms = int(time.time() * 1000)
        lookback_days = max(days * 2, 100)
        begin = now_ms - lookback_days * 86400 * 1000
        path = (f"/v5/stock/chart/kline.json?symbol={symbol}&period=day&type={fq_type}"
                f"&count={max(days * 4, 300)}&indicator=kline&begin={begin}")
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
        path = (f"/v5/stock/chart/kline.json?symbol={symbol}&period=5m&type=after"
                f"&count={max(bars * 4, 500)}&indicator=kline&begin={begin}")
        data = _fetch(path, logger=self._logger, endpoint="5min_kline")
        if not data:
            return []
        items = data.get("data", {}).get("item", []) or data.get("data", {}).get("items", [])
        return _parse_kline(items)

    # ─── 指定日期的 5 分钟 K 线（用于复盘炸板检测）───

    def get_5min_kline_for(self, code: str, target_ts: int,
                           bars_before: int = 48,
                           bars_after: int = 96,
                           fq_type: str = "after") -> list[KBar]:
        """拉取指定日期附近的 5 分钟 K 线。

        雪球 5 分钟线回溯范围约 14 天，target_ts 在此范围内即可取到。
        type=after 从 target_ts 向后取，确保覆盖目标日全天的分钟线。
        """
        symbol = _symbol(code)
        count = bars_before + bars_after + 100
        begin = max(0, target_ts - bars_before * 300 * 1000)
        path = (
            f"/v5/stock/chart/kline.json?symbol={symbol}"
            f"&period=5m&type={fq_type}&count={count}"
            f"&indicator=kline&begin={begin}"
        )
        data = _fetch(path, logger=self._logger, endpoint="5min_kline_for")
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

    def get_sector_ranking(self, asc: bool = False,
                           category: str = "hy") -> list[SectorPerformance]:
        raise NotImplementedError("雪球不提供板块排行")

    def get_sector_components(self, sector_code: str, page: int = 1,
                              all_pages: bool = False,
                              page_size: int = 50,
                              category: str = "hy") -> list[StockInfo]:
        raise NotImplementedError("雪球不提供板块成分股")

    def get_sector_5min_kline(self, sector_code: str, bars: int = 100,
                              category: str = "hy") -> list[KBar]:
        raise NotImplementedError("雪球不提供板块 K 线")

    # ─── 宏观 quote（历史契约；当前 MACRO_SPECS 无该源路由）──────────────────

    def get_macro_quotes(self, specs: list, trade_date: str) -> dict[str, dict]:
        """雪球全球指数 quote（历史契约；当前规格未路由）。

        当前 ``MACRO_SPECS`` 没有任何品种声明 ``xueqiu``，故 prep 不会调用本方法；
        方法保留以维持 provider 契约与测试覆盖。若未来启用：
        无 Cookie → 返回 {}（配置缺失，不算 transport 失败）；HTTP/解析失败 →
        抛 RuntimeError；只接受 ``session_date < trade_date`` 的已完成 session。
        """
        from dragon_quant.infra.provider.macro import build_macro_record

        # 同日口径（<= trade_date）无法用 T-1 quote 表达，跳过以防 caliber 失真
        wanted = [s for s in (specs or [])
                  if getattr(s, "xueqiu", None)
                  and not getattr(s, "same_day_close", False)]
        if not wanted:
            return {}
        cookie = get_xq()
        if not cookie:
            return {}

        out: dict[str, dict] = {}
        errors: list[str] = []
        for spec in wanted:
            symbol = spec.xueqiu
            url = (f"{BASE}/v5/stock/quote.json?symbol="
                   f"{urllib.parse.quote(symbol, safe='')}&extend=detail")
            try:
                data = _fetch_macro_quote_json(url, cookie)
            except Exception as exc:
                errors.append(f"{spec.key}: {type(exc).__name__}: {exc}")
                continue
            q = ((data or {}).get("data") or {}).get("quote") or {}
            try:
                price = float(q.get("current") or 0)
                prev_close = float(q.get("last_close") or 0)
            except (TypeError, ValueError):
                continue
            if price <= 0 or prev_close <= 0:
                continue
            ts = q.get("timestamp")
            session_date = None
            if ts:
                try:
                    session_date = datetime.fromtimestamp(
                        float(ts) / 1000, ZoneInfo("America/New_York")
                    ).strftime("%Y-%m-%d")
                except (TypeError, ValueError, OSError):
                    session_date = None
            if not session_date or session_date >= trade_date:
                continue
            rec = build_macro_record(
                spec, price=price, prev_close=prev_close, source="xueqiu",
                code=symbol, market="xueqiu", session_date=session_date,
            )
            if rec:
                out[spec.key] = rec
        if not out and errors:
            raise RuntimeError("雪球宏观数据不可用：" + "；".join(errors))
        return out

    def get_quote(self, code: str) -> Optional[Quote]:
        """获取实时行情（通过雪球 quote.json?extend=detail）"""
        symbol = _symbol(code)
        path = f"/v5/stock/quote.json?symbol={symbol}&extend=detail"
        data = _fetch(path, logger=self._logger, endpoint="quote")
        if not data:
            return None
        q = data.get("data", {}).get("quote", {})
        if not q:
            return None
        try:
            return Quote(
                code=str(q.get("code", code)),
                name=str(q.get("name", "")),
                price=float(q.get("current", 0)),
                prev_close=float(q.get("last_close", 0)),
                open_px=float(q.get("open", 0)),
                high=float(q.get("high", 0)),
                low=float(q.get("low", 0)),
                pct=float(q.get("percent", 0)),
                chg=float(q.get("chg", 0)),
                turnover_rate=float(q.get("turnover_rate") or 0),
                amplitude=float(q.get("amplitude") or 0),
                volume=float(q.get("volume", 0)),
                amount=float(q.get("amount", 0)),
                market_cap=float(q.get("market_capital", 0)),
                float_market_cap=float(q.get("float_market_capital", 0)),
                volume_ratio=float(q.get("volume_ratio") or 0),
                pe=float(q.get("pe_ttm") or 0),
                limit_up=float(q.get("limit_up", 0)),
                limit_down=float(q.get("limit_down", 0)),
                avg_price=float(q.get("avg_price") or 0),
            )
        except (ValueError, TypeError):
            return None
