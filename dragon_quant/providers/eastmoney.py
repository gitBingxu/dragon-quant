"""
东财 Provider — 概念板块排行 / 成分股 / 板块5分K
所有请求 JSONP 格式，带完整反爬 Header
"""

import json, re, sys, threading, time
import urllib.request, urllib.parse
from typing import Optional
from dragon_quant.models.types import Quote, KBar, StockInfo, SectorPerformance
from dragon_quant.providers.base import StockProvider
from dragon_quant.providers.cookie import get_em

BASE = "https://push2.eastmoney.com"
BASE_HIS = "https://push2his.eastmoney.com"

# 东财请求头模板 — 固定值
HEADERS = {
    "Accept": "*/*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "Pragma": "no-cache",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
    "sec-ch-ua": '"Google Chrome";v="147", "Not.A/Brand";v="8", "Chromium";v="147"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"macOS"',
    "Sec-Fetch-Dest": "script",
    "Sec-Fetch-Mode": "no-cors",
    "Sec-Fetch-Site": "same-site",
}

REFERERS = {
    "ranking": "https://quote.eastmoney.com/center/hsbk.html",
    "components": "https://quote.eastmoney.com/center/gridlist.html",
    "kline": "https://quote.eastmoney.com/bk/90.{code}.html",
}


def _safe_float(v, default=0.0):
    """安全转浮点，处理 '-' 和 None"""
    try:
        return float(v)
    except (ValueError, TypeError):
        return default


def _fetch(url: str, referer: str, logger=None, endpoint: str = "") -> Optional[dict]:
    """JSONP GET 请求"""
    cookie = get_em()
    if not cookie:
        print("⚠️ 东财 Cookie 未设置，请先 python -m dragon_quant.providers.cookie fetch --source em", file=sys.stderr)
        return None

    headers = dict(HEADERS)
    headers["Referer"] = referer
    headers["Cookie"] = cookie

    req = urllib.request.Request(url, headers=headers)
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8")
        elapsed = (time.time() - t0) * 1000
        data = _parse_jsonp(raw)
        if logger:
            logger.api("eastmoney", endpoint, ok=True, elapsed_ms=elapsed)
        return data
    except Exception as e:
        elapsed = (time.time() - t0) * 1000
        if logger:
            logger.api("eastmoney", endpoint, ok=False, elapsed_ms=elapsed, error=str(e))
        return _fetch_playwright(url, referer, cookie, logger=logger, endpoint=endpoint)


def _parse_jsonp(raw: str) -> Optional[dict]:
    """解析 JSONP: jQueryxxx({...});"""
    m = re.search(r"\((\{.*\})\)", raw, re.DOTALL)
    if not m:
        return None
    return json.loads(m.group(1))


# ─── Playwright 降级路径（push2 / push2his 有 TLS 指纹检测） ───

_pw_local = threading.local()


def _get_thread_playwright():
    """按线程懒加载 Playwright 单例（避免 greenlet 跨线程错误）"""
    if hasattr(_pw_local, '_browser'):
        return _pw_local
    from playwright.sync_api import sync_playwright
    _pw_local._playwright = sync_playwright().start()
    _pw_local._browser = _pw_local._playwright.chromium.launch(headless=True)
    _pw_local._context = _pw_local._browser.new_context(
        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
        locale="zh-CN",
        timezone_id="Asia/Shanghai",
    )
    return _pw_local


def _fetch_playwright(url: str, referer: str, cookie: str,
                      logger=None, endpoint: str = "") -> Optional[dict]:
    """通过 Playwright 发 JSONP 请求（绕过 TLS 指纹 + 受限端点封锁）"""
    t0 = time.time()
    try:
        cb_name = "dq_cb_" + str(int(t0 * 1000))
        jsonp_url = re.sub(r'cb=[^&]+', f'cb={cb_name}', url)
        if 'cb=' not in jsonp_url:
            sep = '&' if '?' in jsonp_url else '?'
            jsonp_url += f'{sep}cb={cb_name}'

        pw = _get_thread_playwright()
        page = pw._context.new_page()
        try:
            page.goto(referer, wait_until="domcontentloaded", timeout=15000)
            body = page.evaluate(f'''async () => {{
                const r = await fetch("{jsonp_url}", {{
                    credentials: 'include',
                    headers: {{"Referer": "{referer}"}}
                }});
                return await r.text();
            }}''')
        finally:
            page.close()

        elapsed = (time.time() - t0) * 1000
        result = _parse_jsonp(body)
        if logger:
            logger.api("eastmoney", endpoint, ok=result is not None,
                       elapsed_ms=elapsed)
        return result
    except Exception as e:
        elapsed = (time.time() - t0) * 1000
        if logger:
            logger.api("eastmoney", endpoint, ok=False,
                       elapsed_ms=elapsed, error=str(e))
        print(f"  ⚠️ 东财 Playwright 请求失败: {e}", file=sys.stderr)
        return None


def _parse_kline_items(raw_items: list[str]) -> list[KBar]:
    """解析东财 K 线文本行 → KBar 列表"""
    result = []
    for line in raw_items:
        parts = line.split(",")
        if len(parts) < 11:
            continue
        try:
            # 格式: 时间,开,收,高,低,成交量,成交额,振幅,涨跌幅,涨跌额,换手率
            ts = int(time.mktime(time.strptime(parts[0].strip(), "%Y-%m-%d %H:%M"))) * 1000
            result.append(KBar(
                timestamp=ts,
                open=float(parts[1]), close=float(parts[2]),
                high=float(parts[3]), low=float(parts[4]),
                volume=float(parts[5]), amount=float(parts[6]),
                chg=float(parts[9]), pct=float(parts[8]),
                turnover=float(parts[10]),
            ))
        except (ValueError, IndexError):
            continue
    return result


class EastMoneyProvider(StockProvider):

    @property
    def name(self) -> str:
        return "eastmoney"

    # ─── 板块涨跌幅排行 ───

    def get_sector_ranking(self, asc: bool = False) -> list[SectorPerformance]:
        """概念板块排行 po=1 涨幅榜 / po=0 跌幅榜"""
        params = {
            "np": "1", "fltt": "1", "invt": "2",
            "fs": "m:90+t:3",
            "fields": "f12,f14,f3,f4,f8,f104",
            "fid": "f3",
            "pn": "1", "pz": "500",
            "po": "0" if asc else "1",  # 0=跌幅榜 1=涨幅榜
            "ut": "fa5fd1943c7b386f172d6893dbfba10b",
            "dect": "1", "wbp2u": "|0|0|0|web",
            "cb": "jQuery_dq",
        }
        qs = urllib.parse.urlencode(params)
        url = f"{BASE}/api/qt/clist/get?{qs}"
        data = _fetch(url, REFERERS["ranking"],
                      logger=self._logger, endpoint="sector_ranking")
        if not data:
            return []

        diffs = data.get("data", {}).get("diff", []) or []
        if isinstance(diffs, dict):
            diffs = list(diffs.values())

        result = []
        for d in diffs:
            result.append(SectorPerformance(
                code=d.get("f12", ""),
                name=d.get("f14", ""),
                pct=_safe_float(d.get("f3", 0)) / 100.0,
                amplitude=_safe_float(d.get("f8", 0)) / 100.0,
                turnover_rate=_safe_float(d.get("f104", 0)) / 100.0,
            ))
        return result

    # ─── 板块成分股 ───

    def get_sector_components(self, sector_code: str, page: int = 1) -> list[StockInfo]:
        """板块成分股，按涨跌幅降序"""
        params = {
            "np": "1", "fltt": "1", "invt": "2",
            "fs": f"b:{sector_code}+f:!",
            "fields": "f12,f14,f3,f2,f4,f8",
            "fid": "f3",
            "pn": str(page), "pz": "50",
            "po": "1",
            "dect": "1",
            "ut": "fa5fd1943c7b386f172d6893dbfba10b",
            "wbp2u": "|0|0|0|web",
            "cb": "jQuery_dq",
        }
        qs = urllib.parse.urlencode(params)
        url = f"{BASE}/api/qt/clist/get?{qs}"
        data = _fetch(url, REFERERS["components"],
                      logger=self._logger, endpoint="sector_components")
        if not data:
            return []

        diffs = data.get("data", {}).get("diff", []) or []
        if isinstance(diffs, dict):
            diffs = list(diffs.values())

        result = []
        for d in diffs:
            result.append(StockInfo(
                code=d.get("f12", ""),
                name=d.get("f14", ""),
                sector_code=sector_code,
                pct=_safe_float(d.get("f3", 0)) / 100.0,
                price=_safe_float(d.get("f2", 0)),
            ))
        return result

    # ─── 板块 5 分钟 K 线 ───

    def get_sector_5min_kline(self, sector_code: str, bars: int = 100) -> list[KBar]:
        """概念板块 5 分钟 K 线"""
        params = {
            "cb": "jQuery_dq",
            "secid": f"90.{sector_code}",
            "ut": "fa5fd1943c7b386f172d6893dbfba10b",
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
            "klt": "5",
            "fqt": "1",
            "lmt": str(bars),
            "beg": "0",
            "end": "20500101",
        }
        qs = urllib.parse.urlencode(params)
        url = f"{BASE_HIS}/api/qt/stock/kline/get?{qs}"
        referer = REFERERS["kline"].format(code=sector_code)
        data = _fetch(url, referer,
                      logger=self._logger, endpoint="sector_5min_kline")
        if not data:
            return []

        klines = data.get("data", {}).get("klines", []) or []
        return _parse_kline_items(klines)

    # ─── 未实现的方法 ───

    def get_kline(self, code: str, days: int = 20) -> list[KBar]:
        raise NotImplementedError("东财不提供个股 K 线")

    def get_5min_kline(self, code: str, bars: int = 96) -> list[KBar]:
        raise NotImplementedError("东财不提供个股 5 分 K 线")

    def get_quote(self, code: str) -> Optional[Quote]:
        raise NotImplementedError("东财不提供个股行情")
