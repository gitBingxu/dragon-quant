"""
东财 Provider — 概念板块排行 / 成分股 / 板块5分K

请求链路（重构后）：
  curl 子进程为统一主通道（系统 TLS 指纹，支持并发，轻量）
  → 失败按指数退避重试
  → 连续失败降级到 Playwright 浏览器兜底（browser.py）

反爬要点：
  - 严格对齐 east_money.md 的请求头（Chrome 148）与查询参数
  - 动态 ut token（失败回退硬编码）
  - 请求间随机延迟 + 失败指数退避
  - 分域 Cookie：push2（排行/成分股）用 get_em()，push2his（5分K）用 get_em_his()
"""

import json, os, random, re, socket, subprocess, sys, threading, time
import urllib.parse
from typing import Optional
from dragon_quant.models.types import Quote, KBar, StockInfo, SectorPerformance
from dragon_quant.providers.base import StockProvider
from dragon_quant.providers.cookie import get_em, get_em_his

BASE = "https://push2.eastmoney.com"       # 板块排行 / 成分股
BASE_HIS = "https://push2his.eastmoney.com"  # 板块5分K

# 东财请求头模板 — 与 east_money.md 对齐（Chrome 148）
HEADERS = {
    "Accept": "*/*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "Pragma": "no-cache",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
    "sec-ch-ua": '"Chromium";v="148", "Google Chrome";v="148", "Not/A)Brand";v="99"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"macOS"',
    "Sec-Fetch-Dest": "script",
    "Sec-Fetch-Mode": "no-cors",
    "Sec-Fetch-Site": "same-site",
}

REFERERS = {
    "ranking": "https://quote.eastmoney.com/center/gridlist.html",
    "components": "https://data.eastmoney.com/bkzj/{code}.html",
    "kline": "https://quote.eastmoney.com/bk/90.{code}.html",
}

# 前端 ut token（来自 east_money.md）
_UT_DEFAULT = "fa5fd1943c7b386f172d6893dbfba10b"  # 行情/K线（push2 排行、push2his 5分K）
_UT_COMPONENTS = "8dec03ba335b81bf4ebdf7b29ec27d15"  # 板块成分股资金流页

CURL_TIMEOUT = 15


class EastMoneyAllNodesDown(Exception):
    """一次请求把某域名所有 CDN 节点 IP 都试完仍空响应/超时。

    通常意味着本机出口 IP 已被东财风控（换 CDN 节点无效），需切换 IP 后重试。
    """


# host → (ip 列表, 解析时间戳)，进程内短缓存，避免每次请求重复解析
_DNS_CACHE: dict[str, tuple[list[str], float]] = {}
_DNS_TTL = 300  # 秒
_DNS_LOCK = threading.Lock()

# DoH（DNS over HTTPS）多源，用于扩充 CDN 节点池（系统 DNS 单次通常只返回 1 个 IP）
_DOH_ENDPOINTS = [
    "https://dns.alidns.com/resolve",
    "https://doh.pub/dns-query",
    "https://dns.google/resolve",
    "https://cloudflare-dns.com/dns-query",
]


def _host_of(url: str) -> str:
    return urllib.parse.urlparse(url).hostname or ""


def _doh_resolve(host: str) -> list[str]:
    """通过多个 DoH 服务并发查询 host 的 A 记录，合并去重。失败的源忽略。"""
    ips: list[str] = []

    def _query(endpoint: str):
        out: list[str] = []
        try:
            r = subprocess.run(
                ["curl", "-s", "--max-time", "5",
                 "-H", "accept: application/dns-json",
                 f"{endpoint}?name={host}&type=A"],
                capture_output=True, text=True, timeout=8,
            )
            data = json.loads(r.stdout or "{}")
            for ans in data.get("Answer", []):
                if ans.get("type") == 1:  # A 记录
                    ip = ans.get("data", "")
                    if ip:
                        out.append(ip)
        except Exception:
            pass
        return out

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=len(_DOH_ENDPOINTS)) as ex:
        for res in ex.map(_query, _DOH_ENDPOINTS):
            for ip in res:
                if ip not in ips:
                    ips.append(ip)
    return ips


def _resolve_ips(host: str) -> list[str]:
    """解析 host 的 CDN 节点 IP 池（系统 DNS + DoH 多源合并，带 TTL 缓存）。

    系统 DNS 单次通常只返回 1 个 IP，叠加 DoH 多源可拿到更多东财 CDN 节点，
    供 _curl_request 轮询切换。解析失败返回空列表（上层回退系统 DNS 直连）。
    """
    now = time.time()
    with _DNS_LOCK:
        cached = _DNS_CACHE.get(host)
        if cached and now - cached[1] < _DNS_TTL:
            return list(cached[0])
    ips: list[str] = []
    try:
        for info in socket.getaddrinfo(host, 443, family=socket.AF_INET,
                                       proto=socket.IPPROTO_TCP):
            ip = info[4][0]
            if ip not in ips:
                ips.append(ip)
    except Exception:
        ips = []
    # 叠加 DoH 多源节点
    for ip in _doh_resolve(host):
        if ip not in ips:
            ips.append(ip)
    random.shuffle(ips)  # 打散，分散节点压力
    with _DNS_LOCK:
        _DNS_CACHE[host] = (ips, now)
    return list(ips)


def _safe_float(v, default=0.0):
    """安全转浮点，处理 '-' 和 None"""
    try:
        return float(v)
    except (ValueError, TypeError):
        return default


def _is_valid_response(raw: str) -> bool:
    """判断 curl 响应是否为有效东财数据（排除 502/网关 HTML 等坏节点响应）。

    东财正常返回为 JSONP（jQuery_xxx({...})）或裸 JSON；CDN 坏节点常返回
    502 Bad Gateway 等 HTML 页面，应视为无效并切换下一节点。
    """
    if not raw:
        return False
    head = raw.lstrip()[:512].lower()
    if head.startswith("<") or "<html" in head or "bad gateway" in head:
        return False
    # 含 JSON/JSONP 特征即认为有效
    return ("{" in raw and "}" in raw)


def _curl_once(url: str, headers: dict, resolve_ip: Optional[str] = None) -> Optional[str]:
    """发送单次 curl GET。resolve_ip 不为空时用 --resolve 强制连指定 CDN 节点。"""
    cmd = ["curl", "-s", "--max-time", str(CURL_TIMEOUT), "-X", "GET"]
    if resolve_ip:
        host = _host_of(url)
        if host:
            cmd += ["--resolve", f"{host}:443:{resolve_ip}"]
    for k, v in headers.items():
        cmd += ["-H", f"{k}: {v}"]
    cmd.append(url)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=CURL_TIMEOUT + 5)
        if result.returncode == 0 and result.stdout:
            return result.stdout
    except FileNotFoundError:
        print("  ⚠️ curl 不可用", file=sys.stderr)
    except Exception as e:
        print(f"  ⚠️ curl 异常: {e}", file=sys.stderr)
    return None


def _curl_request(url: str, headers: dict) -> Optional[str]:
    """用 curl 发送 HTTP GET，按 CDN 节点逐个轮询。

    依次尝试该域名解析出的每个 CDN 节点 IP，任一节点返回有效响应即成功；
    某节点空响应/超时/返回 502 等坏响应则立即换下一个节点。所有节点都失败
    时抛 EastMoneyAllNodesDown（上层据此判定本机出口 IP 被风控并退出）。
    """
    host = _host_of(url)
    ips = _resolve_ips(host)
    if not ips:
        # 解析失败 → 回退系统 DNS 直连一次
        raw = _curl_once(url, headers)
        if _is_valid_response(raw):
            return raw
        raise EastMoneyAllNodesDown(host)

    for ip in ips:
        raw = _curl_once(url, headers, resolve_ip=ip)
        if _is_valid_response(raw):
            return raw
        reason = "502/无效响应" if raw else "空响应"
        print(f"  ⚠️ 东财节点 {ip} {reason}，切换下一节点...", file=sys.stderr)
    raise EastMoneyAllNodesDown(host)


def _browser_fallback(url: str, referer: str) -> Optional[str]:
    """curl 连续失败后的浏览器兜底（Playwright 真实 Chromium HTTP 栈）。"""
    try:
        from dragon_quant.providers import browser
        if not browser.is_available():
            return None
        return browser.get_browser().fetch_jsonp(url, referer)
    except Exception as e:
        print(f"  ⚠️ 浏览器兜底失败: {e}", file=sys.stderr)
        return None


def _fetch(url: str, referer: str, cookie: str,
           logger=None, endpoint: str = "") -> Optional[dict]:
    """JSONP GET 请求

    curl 按 CDN 节点逐个轮询（见 _curl_request）；所有 CDN 节点都空响应
    时判定本机出口 IP 已被东财风控，打印提示并立即终止整个脚本。
    cookie 由调用方按域名传入（push2 / push2his 分开）。
    """
    if not cookie:
        print("⚠️ 东财 Cookie 未设置，请先 python -m dragon_quant.providers.cookie fetch", file=sys.stderr)
        return None

    headers = dict(HEADERS)
    headers["Referer"] = referer
    headers["Cookie"] = cookie

    t0 = time.time()
    try:
        raw = _curl_request(url, headers)
    except EastMoneyAllNodesDown as e:
        elapsed = (time.time() - t0) * 1000
        if logger:
            logger.api("eastmoney", endpoint, ok=False,
                       elapsed_ms=elapsed, error="all_cdn_nodes_down")
        print(
            "\n⛔ 东财所有 CDN 节点均空响应，当前出口 IP 大概率已被风控/封禁。\n"
            "   请切换 IP（VPN/代理/重拨）后重新运行 scan。\n"
            f"   （故障域名: {e}）",
            file=sys.stderr,
        )
        # 在 RateLimiter 子线程中，sys.exit 无法终止主进程，用 os._exit 强制退出
        os._exit(2)

    elapsed = (time.time() - t0) * 1000
    if raw:
        data = _parse_jsonp(raw)
        if data is not None:
            if logger:
                logger.api("eastmoney", endpoint, ok=True,
                           elapsed_ms=elapsed, note="curl")
            return data
        last_error = "JSONP 解析失败"
    else:
        last_error = "curl 空响应"
    if logger:
        logger.api("eastmoney", endpoint, ok=False,
                   elapsed_ms=elapsed, error=last_error)

    # 解析失败（非节点全挂）→ 浏览器兜底
    t0 = time.time()
    raw = _browser_fallback(url, referer)
    elapsed = (time.time() - t0) * 1000
    if raw:
        data = _parse_jsonp(raw)
        if logger:
            logger.api("eastmoney", endpoint, ok=data is not None,
                       elapsed_ms=elapsed, note="browser")
        if data is not None:
            return data

    print(f"  ⚠️ 东财请求失败 (CDN 轮询+浏览器兜底): {last_error}", file=sys.stderr)
    return None


def _parse_jsonp(raw: str) -> Optional[dict]:
    """解析 JSONP: jQueryxxx({...});"""
    m = re.search(r"\((\{.*\})\)", raw, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return None


_UT_CACHE = ""


def _get_ut_token() -> str:
    """获取东财前端 ut token，优先从页面提取，失败回退硬编码"""
    global _UT_CACHE
    if _UT_CACHE:
        return _UT_CACHE
    try:
        raw = _curl_request(
            "https://quote.eastmoney.com/center/gridlist.html",
            headers={"User-Agent": HEADERS["User-Agent"]},
        )
        html = raw or ""
        m = re.search(r'"ut"\s*:\s*"([a-f0-9]{30,50})"', html)
        if m:
            _UT_CACHE = m.group(1)
            return _UT_CACHE
    except Exception:
        pass
    _UT_CACHE = _UT_DEFAULT
    return _UT_CACHE


def _parse_kline_items(raw_items: list[str]) -> list[KBar]:
    """解析东财 K 线文本行 → KBar 列表

    格式: 时间,开,收,高,低,成交量,成交额,振幅,涨跌幅,涨跌额,换手率
    """
    result = []
    for line in raw_items:
        parts = line.split(",")
        if len(parts) < 11:
            continue
        try:
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

    # ─── 板块涨跌幅排行（push2，fltt=1，f3=406→4.06%）───

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
        data = _fetch(url, REFERERS["ranking"], get_em(),
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

    # ─── 板块成分股（push2，fltt=2，f3=19.99 已是百分数）───

    def get_sector_components(self, sector_code: str, page: int = 1) -> list[StockInfo]:
        """板块成分股，按涨跌幅降序"""
        params = {
            "cb": "jQuery_dq",
            "fid": "f3",
            "po": "1",
            "pz": "50",
            "pn": str(page),
            "np": "1",
            "fltt": "2",
            "invt": "2",
            "ut": _UT_COMPONENTS,
            "fs": f"b:{sector_code}",
            "fields": "f12,f14,f2,f3,f1,f13",
        }
        qs = urllib.parse.urlencode(params)
        url = f"{BASE}/api/qt/clist/get?{qs}"
        referer = REFERERS["components"].format(code=sector_code)
        data = _fetch(url, referer, get_em(),
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
                pct=_safe_float(d.get("f3", 0)),
                price=_safe_float(d.get("f2", 0)),
            ))
        return result

    # ─── 板块 5 分钟 K 线（push2his）───

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
            "end": "20500101",
            "lmt": str(bars),
        }
        qs = urllib.parse.urlencode(params)
        url = f"{BASE_HIS}/api/qt/stock/kline/get?{qs}"
        referer = REFERERS["kline"].format(code=sector_code)
        data = _fetch(url, referer, get_em_his(),
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
