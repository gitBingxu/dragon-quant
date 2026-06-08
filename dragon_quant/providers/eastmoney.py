"""
东财 Provider — 概念板块排行 / 成分股 / 板块5分K
所有请求 JSONP 格式，带完整反爬 Header
"""

import json, re, socket, ssl, subprocess, sys, time
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

# 东财部分 CDN 节点对 TLSv1.3 发空响应，强制 TLSv1.2
_SSL_CTX = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
_SSL_CTX.maximum_version = ssl.TLSVersion.TLSv1_2


def _resolve_ips(host: str) -> list[str]:
    """解析 host 的所有 IPv4 地址（去重），失败返回空列表"""
    try:
        addrs = socket.getaddrinfo(host, 443, socket.AF_INET, socket.SOCK_STREAM)
        return list(dict.fromkeys(a[4][0] for a in addrs))
    except Exception:
        return []


def _safe_float(v, default=0.0):
    """安全转浮点，处理 '-' 和 None"""
    try:
        return float(v)
    except (ValueError, TypeError):
        return default


def _curl_request(url: str, headers: dict, resolve_ip: str = "") -> Optional[str]:
    """用 curl 发送 HTTP 请求（绕过 Python TLS 指纹限制）。

    东财 WAF 检测 Python ssl（LibreSSL）的 TLS 指纹并直接断开连接。
    curl 使用系统 libcurl + SecureTransport TLS，指纹与浏览器一致。
    可指定 resolve_ip 绑定到特定 CDN 节点。
    """
    cmd = ["curl", "-s", "--max-time", "15", "--http1.1", "--tlsv1.2", "--tls-max", "1.2", "-X", "GET"]
    if resolve_ip:
        host = urllib.parse.urlparse(url).hostname
        cmd += ["--resolve", f"{host}:443:{resolve_ip}"]
    for k, v in headers.items():
        cmd += ["-H", f"{k}: {v}"]
    cmd.append(url)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        if result.returncode == 0 and result.stdout:
            return result.stdout
        else:
            err = result.stderr.strip()
            reason = err[:200] if err else f"rc={result.returncode}, stdout空"
            print(f"  ⚠️ curl 失败: {reason}", file=sys.stderr)
    except FileNotFoundError:
        print("  ⚠️ curl 不可用", file=sys.stderr)
    except Exception as e:
        print(f"  ⚠️ curl 异常: {e}", file=sys.stderr)
    return None


def _fetch(url: str, referer: str, logger=None, endpoint: str = "", extra_headers: dict = None) -> Optional[dict]:
    """JSONP GET 请求

    优先 urllib，curl 兜底。DNS 多 IP 轮询绕过坏掉的 CDN 节点。
    最多重试 2 次，每次尝试不同 IP。
    """
    cookie = get_em()
    if not cookie:
        print("\u26a0\ufe0f 东财 Cookie 未设置，请先 python -m dragon_quant.providers.cookie fetch --source em", file=sys.stderr)
        return None

    headers = dict(HEADERS)
    if extra_headers:
        headers.update(extra_headers)
    headers["Referer"] = referer
    headers["Cookie"] = cookie

    parsed = urllib.parse.urlparse(url)
    host = parsed.hostname
    ips = _resolve_ips(host)
    if not ips:
        ips = [host]  # DNS 失败，用原始 hostname

    MAX_RETRIES = 2
    last_error = None

    # 说明：这里会做 "urllib → curl" 的两级兜底，并且会换 IP 重试。
    # 为了避免“明明最终成功但日志里刷满 error”的错觉：
    # - 只有在全部重试都失败时，才用 logger.api(... ok=False) 记一条 error；
    # - 中间过程的失败，用 logger.warn 记到 api_attempt:* 分类下（不计入 api_stats）。

    for attempt in range(MAX_RETRIES):
        ip = ips[attempt % len(ips)]

        # 1) urllib，绑定到指定 IP
        ip_headers = dict(headers)
        if ip != host:
            ip_headers["Host"] = host
            ip_netloc = f"[{ip}]" if ":" in ip else ip
            if parsed.port:
                ip_netloc += f":{parsed.port}"
            ip_url = parsed._replace(netloc=ip_netloc).geturl()
        else:
            ip_url = url

        req = urllib.request.Request(ip_url, headers=ip_headers)
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=15, context=_SSL_CTX) as resp:
                raw = resp.read().decode("utf-8")
            elapsed = (time.time() - t0) * 1000
            data = _parse_jsonp(raw)
            if logger:
                logger.api("eastmoney", endpoint, ok=data is not None,
                           elapsed_ms=elapsed, note=f"urllib@{ip}", attempt=attempt + 1)
            return data
        except Exception as e:
            elapsed = (time.time() - t0) * 1000
            if logger:
                logger.warn(
                    f"api_attempt:eastmoney:{endpoint}",
                    f"eastmoney/{endpoint} urllib@{ip} 失败，将尝试 curl 兜底 (attempt={attempt + 1}/{MAX_RETRIES})",
                    elapsed_ms=elapsed,
                    error=str(e),
                    attempt=attempt + 1,
                    note=f"urllib@{ip}",
                )
            print(f"  ⚠️ urllib@{ip} 失败: {e}", file=sys.stderr)
            last_error = e

        # 2) 兜底：curl，绑定到同一 IP
        t0 = time.time()
        try:
            raw = _curl_request(url, headers, resolve_ip=ip if ip != host else "")
            if raw:
                elapsed = (time.time() - t0) * 1000
                data = _parse_jsonp(raw)
                if logger:
                    logger.api("eastmoney", endpoint, ok=data is not None,
                               elapsed_ms=elapsed, note=f"curl@{ip}", attempt=attempt + 1)
                return data
            raise RuntimeError("curl 返回空数据")
        except Exception as e:
            elapsed = (time.time() - t0) * 1000
            if logger:
                logger.warn(
                    f"api_attempt:eastmoney:{endpoint}",
                    f"eastmoney/{endpoint} curl@{ip} 失败，将换 IP 重试 (attempt={attempt + 1}/{MAX_RETRIES})",
                    elapsed_ms=elapsed,
                    error=f"curl:{e}",
                    attempt=attempt + 1,
                    note=f"curl@{ip}",
                )
            last_error = e

        # 非最后一次尝试，等待后换 IP
        if attempt < MAX_RETRIES - 1:
            time.sleep(1.0)

    if logger:
        logger.api(
            "eastmoney",
            endpoint,
            ok=False,
            elapsed_ms=0,
            error=str(last_error) if last_error else "unknown",
            attempts=MAX_RETRIES,
        )
    print(f"  ⚠️ 东财请求失败 (重试{MAX_RETRIES}次): {last_error}", file=sys.stderr)
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


_TEMPLATE_CACHE: dict[str, tuple] = {}
_TEMPLATE_SOURCE: dict[str, str] = {}


def _get_template(endpoint: str) -> tuple:
    from dragon_quant.config.api_config import (
        load_templates, hardcoded_templates, save_templates,
    )
    if endpoint in _TEMPLATE_CACHE:
        return _TEMPLATE_CACHE[endpoint]

    local = load_templates("eastmoney")
    if local and endpoint in local:
        _TEMPLATE_CACHE[endpoint] = (local[endpoint], "local")
        _TEMPLATE_SOURCE[endpoint] = "local"
        return _TEMPLATE_CACHE[endpoint]

    hardcoded = hardcoded_templates("eastmoney")
    if endpoint in hardcoded:
        tpl = hardcoded[endpoint]
        if not load_templates("eastmoney"):
            save_templates("eastmoney", hardcoded_templates("eastmoney"))
        _TEMPLATE_CACHE[endpoint] = (tpl, "hardcoded")
        _TEMPLATE_SOURCE[endpoint] = "hardcoded"
        return _TEMPLATE_CACHE[endpoint]

    raise ValueError(f"Unknown endpoint: eastmoney/{endpoint}")


def _do_fetch_with_fallback(endpoint: str, dynamic_params: dict, referer: str,
                            logger=None) -> Optional[dict]:
    tpl, source = _get_template(endpoint)
    url = tpl.build_url(**dynamic_params)
    extra_headers = tpl.build_headers(referer)

    data = _fetch(url, referer, logger=logger, endpoint=endpoint,
                  extra_headers=extra_headers)
    if data is not None:
        return data

    if source == "local":
        from dragon_quant.config.api_config import (
            hardcoded_templates, save_single_template,
        )
        hardcoded = hardcoded_templates("eastmoney")
        if endpoint in hardcoded:
            hc_tpl = hardcoded[endpoint]
            hc_url = hc_tpl.build_url(**dynamic_params)
            hc_headers = hc_tpl.build_headers(referer)
            data2 = _fetch(hc_url, referer, logger=logger,
                          endpoint=f"{endpoint}_hc",
                          extra_headers=hc_headers)
            if data2 is not None:
                save_single_template("eastmoney", endpoint, hc_tpl)
                _TEMPLATE_CACHE[endpoint] = (hc_tpl, "hardcoded")
                _TEMPLATE_SOURCE[endpoint] = "hardcoded"
                print(f"  \U0001f4a1 {endpoint}: 本地 config 失效, 已回退至包内硬编码",
                      file=sys.stderr)
                return data2

    print(f"  \u274c eastmoney/{endpoint}: 请求失败，请运行 fix-api 修复:",
          file=sys.stderr)
    print(f"       python -m dragon_quant fix-api --provider eastmoney",
          file=sys.stderr)
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

    COMPONENTS_PAGE_SIZE = 50

    @property
    def name(self) -> str:
        return "eastmoney"

    # ─── 板块涨跌幅排行 ───

    def get_sector_ranking(self, asc: bool = False) -> list[SectorPerformance]:
        """概念板块排行 po=1 涨幅榜 / po=0 跌幅榜"""
        dynamic = {
            "fs": "m:90+t:3",
            "fields": "f12,f14,f3,f4,f8,f104",
            "fid": "f3",
            "pn": "1", "pz": "500",
            "po": "0" if asc else "1",
            "ut": _get_ut_token(),
        }
        data = _do_fetch_with_fallback(
            "sector_ranking", dynamic,
            referer=REFERERS["ranking"],
            logger=self._logger,
        )
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

    def _get_sector_components_page(self, sector_code: str, page: int = 1,
                                    page_size: int = COMPONENTS_PAGE_SIZE) -> list[StockInfo]:
        """获取单页板块成分股，按涨跌幅降序。"""
        dynamic = {
            "np": "1", "fltt": "1", "invt": "2",
            "fs": f"b:{sector_code}+f:!",
            "fields": "f12,f14,f3,f2,f4,f8",
            "fid": "f3",
            "pn": str(page), "pz": str(page_size),
            "po": "1",
            "ut": _get_ut_token(),
        }
        data = _do_fetch_with_fallback(
            "sector_components", dynamic,
            referer=REFERERS["components"],
            logger=self._logger,
        )
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

    def get_sector_components(self, sector_code: str, page: int = 1,
                              all_pages: bool = False,
                              page_size: int = COMPONENTS_PAGE_SIZE) -> list[StockInfo]:
        """板块成分股，按涨跌幅降序。支持单页与全量分页聚合。"""
        if not all_pages:
            return self._get_sector_components_page(sector_code, page=page, page_size=page_size)

        all_items: list[StockInfo] = []
        seen_codes: set[str] = set()
        current_page = max(page, 1)
        max_pages = 20

        for _ in range(max_pages):
            page_items = self._get_sector_components_page(
                sector_code, page=current_page, page_size=page_size
            )
            if not page_items:
                break

            for item in page_items:
                if item.code and item.code not in seen_codes:
                    seen_codes.add(item.code)
                    all_items.append(item)

            if len(page_items) < page_size:
                break
            current_page += 1

        return all_items

    # ─── 板块 5 分钟 K 线 ───

    def get_sector_5min_kline(self, sector_code: str, bars: int = 100) -> list[KBar]:
        """概念板块 5 分钟 K 线"""
        dynamic = {
            "secid": f"90.{sector_code}",
            "klt": "5",
            "lmt": str(bars),
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
            "ut": _get_ut_token(),
        }
        referer = REFERERS["kline"].format(code=sector_code)
        data = _do_fetch_with_fallback(
            "sector_5min_kline", dynamic,
            referer=referer,
            logger=self._logger,
        )
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
