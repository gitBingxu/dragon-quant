"""
东财 Provider — 概念板块排行 / 成分股 / 板块5分K
所有请求 JSONP 格式，带完整反爬 Header
"""

import json, re, random, sys, time
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
    """JSONP GET 请求（DNS 多 IP 自动 fallback）

    如果 playwright 可用，采用快速失败策略（8s 超时 / 1 次重试），
    尽快交棒给 _browser_fetch 兜底。否则保持传统策略（15s / 2 次退避重试）。
    """
    cookie = get_em()
    if not cookie:
        print("\u26a0\ufe0f 东财 Cookie 未设置，请先 python -m dragon_quant.providers.cookie fetch --source em", file=sys.stderr)
        return None

    try:
        from dragon_quant.providers.browser import is_available
        _has_browser = is_available()
    except Exception:
        _has_browser = False

    timeout_s = 8 if _has_browser else 15
    max_retries = 1 if _has_browser else 2

    headers = dict(HEADERS)
    headers["Referer"] = referer
    headers["Cookie"] = cookie

    parsed = urllib.parse.urlparse(url)
    host = parsed.hostname
    path = parsed.path + ("?" + parsed.query if parsed.query else "")

    def _do_request() -> Optional[dict]:
        t0 = time.time()
        try:
            req = urllib.request.Request(f"https://{host}{path}", headers=headers)
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                raw = resp.read().decode("utf-8")
            elapsed = (time.time() - t0) * 1000
            data = _parse_jsonp(raw)
            if logger:
                logger.api("eastmoney", endpoint, ok=data is not None,
                           elapsed_ms=elapsed)
            return data
        except Exception as e:
            elapsed = (time.time() - t0) * 1000
            if logger:
                logger.api("eastmoney", endpoint, ok=False,
                           elapsed_ms=elapsed, error=str(e))
            print(f"  ⚠️ 东财请求失败: {e}", file=sys.stderr)
            return None

    # 1) 首次尝试
    result = _do_request()
    if result is not None:
        return result

    # 2) 重试
    for retry in range(max_retries):
        if _has_browser:
            time.sleep(random.uniform(0.5, 1.0))
        else:
            time.sleep(random.uniform(0.8, 1.5) * (retry + 1))
        result = _do_request()
        if result is not None:
            return result

    return None


def _browser_fetch(url: str, referer: str, logger=None, endpoint: str = "") -> Optional[dict]:
    """东财 JSONP 请求 — playwright 浏览器兜底通道

    当 urllib（TLS 指纹 / HTTP/1.1 / Cookie 管理）失败时，
    用真实 Chrome 浏览器在页面上下文中执行 fetch，绕过反爬检测。
    """
    t0 = time.time()
    try:
        from dragon_quant.providers.browser import get_browser, is_available

        if not is_available():
            return None

        browser = get_browser()
        raw = browser.fetch_jsonp(url, referer)
        if not raw:
            return None

        data = _parse_jsonp(raw)
        elapsed = (time.time() - t0) * 1000
        if logger:
            logger.api("eastmoney", endpoint, ok=data is not None,
                       elapsed_ms=elapsed, note="browser")
        return data
    except Exception as e:
        elapsed = (time.time() - t0) * 1000
        if logger:
            logger.api("eastmoney", endpoint, ok=False,
                       elapsed_ms=elapsed, error=f"browser:{e}")
        return None


def _parse_jsonp(raw: str) -> Optional[dict]:
    """解析 JSONP: jQueryxxx({...});"""
    m = re.search(r"\((\{.*\})\)", raw, re.DOTALL)
    if not m:
        return None
    return json.loads(m.group(1))


_UT_CACHE = ""
_UT_DEFAULT = "fa5fd1943c7b386f172d6893dbfba10b"


def _get_ut_token() -> str:
    """获取东财前端 ut token，优先从页面提取，失败回退硬编码"""
    global _UT_CACHE
    if _UT_CACHE:
        return _UT_CACHE
    try:
        req = urllib.request.Request(
            "https://quote.eastmoney.com/center/gridlist.html",
            headers={"User-Agent": HEADERS["User-Agent"]},
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            html = resp.read().decode("utf-8", errors="replace")
        m = re.search(r'"ut"\s*:\s*"([a-f0-9]{30,50})"', html)
        if m:
            _UT_CACHE = m.group(1)
            return _UT_CACHE
    except Exception:
        pass
    _UT_CACHE = _UT_DEFAULT
    return _UT_CACHE


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
            "ut": _get_ut_token(),
            "dect": "1", "wbp2u": "|0|0|0|web",
            "cb": "jQuery_dq",
        }
        qs = urllib.parse.urlencode(params)
        url = f"{BASE}/api/qt/clist/get?{qs}"
        data = _fetch(url, REFERERS["ranking"],
                      logger=self._logger, endpoint="sector_ranking")
        if not data:
            data = _browser_fetch(url, REFERERS["ranking"],
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
            "ut": _get_ut_token(),
            "wbp2u": "|0|0|0|web",
            "cb": "jQuery_dq",
        }
        qs = urllib.parse.urlencode(params)
        url = f"{BASE}/api/qt/clist/get?{qs}"
        data = _fetch(url, REFERERS["components"],
                      logger=self._logger, endpoint="sector_components")
        if not data:
            data = _browser_fetch(url, REFERERS["components"],
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
            "ut": _get_ut_token(),
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
            data = _browser_fetch(url, referer,
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
