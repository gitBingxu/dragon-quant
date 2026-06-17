"""
同花顺(THS) Provider — 概念板块排行 / 成分股 / 板块5分K

接口与反爬要点：
  - 概念排行榜：q.10jqka.com.cn/gn/ 涨跌幅由 JS 动态填充，用 Playwright 渲染读取
  - 概念成分股：q.10jqka.com.cn/gn/detail/.../ 详情页 HTML 表格解析（GBK）
  - 板块分时：d.10jqka.com.cn/v6/time/48_{innerCode}/last.js（JSONP，无 Cookie）
    分时为 1 分钟粒度，provider 内聚合为 5 分 K 对齐东财 get_sector_5min_kline

代码体系（两套）：
  - 6 位 code（URL 用，如 301558）
  - innerCode（行情接口用，如 885611），映射在详情页 <input id="clid">

接口契约对齐东财 EastMoneyProvider，编排器与 scorer 无需改动。
"""

import re
import subprocess
import sys
import time
from typing import Optional

from dragon_quant.models.types import Quote, KBar, StockInfo, SectorPerformance
from dragon_quant.providers.base import StockProvider

Q_BASE = "http://q.10jqka.com.cn"
D_BASE = "https://d.10jqka.com.cn"
DATA_BASE = "http://data.10jqka.com.cn"

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

CURL_TIMEOUT = 12

# 概念资金流排行页：含 概念名+6位code+涨跌幅，按涨跌幅排序，一页 50 条。
# 涨跌幅由 JS 填充，需浏览器渲染。order=desc 涨幅榜 / order=asc 跌幅榜。
RANKING_URL = DATA_BASE + "/funds/gnzjl/field/tradezdf/order/{order}/page/1/"
# 成分股详情页（按涨跌幅降序），page 翻页
COMPONENTS_URL = (
    f"{Q_BASE}/gn/detail/field/264648/order/desc/page/{{page}}/ajax/1/code/{{code}}/"
)
DETAIL_URL = f"{Q_BASE}/gn/detail/code/{{code}}/"
TIME_URL = D_BASE + "/v6/time/48_{inner}/last.js"

# 6 位 code → innerCode 进程内缓存，避免每次 5 分 K 都多一次详情页请求
_INNER_CACHE: dict[str, str] = {}


def _safe_float(v, default=0.0):
    try:
        return float(v)
    except (ValueError, TypeError):
        return default


def _curl(url: str, referer: str = "", gbk: bool = False) -> Optional[str]:
    """curl GET。gbk=True 时按 GBK 解码（同花顺网页页面），否则 UTF-8。"""
    cmd = ["curl", "-s", "--max-time", str(CURL_TIMEOUT), "-A", UA]
    if referer:
        cmd += ["-H", f"Referer: {referer}"]
    cmd.append(url)
    try:
        enc = "gbk" if gbk else "utf-8"
        result = subprocess.run(
            cmd, capture_output=True, timeout=CURL_TIMEOUT + 5,
        )
        if result.returncode == 0 and result.stdout:
            return result.stdout.decode(enc, errors="ignore")
    except FileNotFoundError:
        print("  ⚠️ curl 不可用", file=sys.stderr)
    except Exception as e:
        print(f"  ⚠️ 同花顺 curl 异常: {e}", file=sys.stderr)
    return None


def _parse_jsonp(raw: str) -> Optional[dict]:
    """解析 JSONP: cb({...})"""
    import json
    m = re.search(r"\((\{.*\})\)", raw, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return None


def _parse_components_html(html: str, sector_code: str) -> list[StockInfo]:
    """解析成分股详情页表格。

    列：td[1]=代码 td[2]=名称 td[3]=现价 td[4]=涨跌幅%
    """
    result: list[StockInfo] = []
    m = re.search(r"<tbody[^>]*>(.*?)</tbody>", html, re.DOTALL)
    if not m:
        return result
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", m.group(1), re.DOTALL)
    for row in rows:
        tds = re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL)
        if len(tds) < 5:
            continue

        def _txt(s: str) -> str:
            return re.sub(r"<[^>]+>", "", s).strip()

        code = _txt(tds[1])
        name = _txt(tds[2])
        if not re.fullmatch(r"\d{6}", code):
            continue
        result.append(StockInfo(
            code=code,
            name=name,
            sector_code=sector_code,
            pct=_safe_float(_txt(tds[4])),
            price=_safe_float(_txt(tds[3])),
        ))
    return result


def _aggregate_5min(ts_data: str, date_str: str, pre_close: float) -> list[KBar]:
    """把 1 分钟分时聚合为 5 分 K。

    分时行格式：HHMM,指数点位,分钟成交额,均价,分钟成交量
    按交易时间每 5 分钟一桶（09:30-09:34 为第 1 根，时间戳取桶末分钟）。
    open=桶首点位 close=桶尾点位 high/low=桶内极值
    amount/volume=桶内各分钟求和 pct=相对昨收
    """
    points = []
    for line in ts_data.split(";"):
        parts = line.split(",")
        if len(parts) < 5:
            continue
        hhmm = parts[0].strip()
        if len(hhmm) != 4 or not hhmm.isdigit():
            continue
        price = _safe_float(parts[1])
        amount = _safe_float(parts[2])
        volume = _safe_float(parts[4])
        points.append((hhmm, price, amount, volume))

    if not points:
        return []

    bars: list[KBar] = []
    bucket: list[tuple] = []

    def _flush(bk: list[tuple]):
        if not bk:
            return
        prices = [p[1] for p in bk]
        last_hhmm = bk[-1][0]
        ts = int(time.mktime(time.strptime(
            f"{date_str} {last_hhmm[:2]}:{last_hhmm[2:]}", "%Y%m%d %H:%M"))) * 1000
        close = prices[-1]
        pct = ((close - pre_close) / pre_close * 100.0) if pre_close else 0.0
        bars.append(KBar(
            timestamp=ts,
            volume=sum(p[3] for p in bk),
            open=prices[0],
            high=max(prices),
            low=min(prices),
            close=close,
            chg=close - pre_close if pre_close else 0.0,
            pct=pct,
            turnover=0.0,
            amount=sum(p[2] for p in bk),
        ))

    # 以分钟序号分桶：每 5 个分钟点一桶
    for i, pt in enumerate(points):
        bucket.append(pt)
        if len(bucket) == 5:
            _flush(bucket)
            bucket = []
    _flush(bucket)  # 残余不足 5 分钟的尾桶
    return bars


class THSProvider(StockProvider):

    @property
    def name(self) -> str:
        return "ths"

    # ─── 概念板块排行（Playwright 渲染 gn 页读涨跌幅榜）───

    def get_sector_ranking(self, asc: bool = False) -> list[SectorPerformance]:
        """概念板块涨跌幅排行。asc=False 涨幅榜 / asc=True 跌幅榜。

        涨跌幅由 JS 动态填充，用共享 BrowserSession 渲染资金流排行页后解析表格。
        页面已按 order 排好序（desc/asc），一页 50 条足够编排器取 Top。
        """
        t0 = time.time()
        url = RANKING_URL.format(order="asc" if asc else "desc")
        try:
            from dragon_quant.providers import browser
            if not browser.is_available():
                print("  ⚠️ 同花顺排行需 playwright，未安装", file=sys.stderr)
                return []
            html = browser.get_browser().render_text(
                url, wait_selector="table tbody tr")
        except Exception as e:
            html = None
            print(f"  ⚠️ 同花顺排行渲染失败: {e}", file=sys.stderr)

        elapsed = (time.time() - t0) * 1000
        if not html:
            if self._logger:
                self._logger.api("ths", "sector_ranking", ok=False,
                                 elapsed_ms=elapsed, error="渲染为空")
            return []

        rows = self._parse_ranking_html(html)
        if self._logger:
            self._logger.api("ths", "sector_ranking", ok=bool(rows),
                             elapsed_ms=elapsed, note=f"n={len(rows)}")
        rows.sort(key=lambda s: s.pct, reverse=not asc)
        return rows

    @staticmethod
    def _parse_ranking_html(html: str) -> list[SectorPerformance]:
        """解析概念资金流排行表格。

        列：td[1]=概念名(含 detail/code 链接) td[3]=涨跌幅%
        """
        result: list[SectorPerformance] = []
        m = re.search(r"<tbody[^>]*>(.*?)</tbody>", html, re.DOTALL)
        if not m:
            return result
        rows = re.findall(r"<tr[^>]*>(.*?)</tr>", m.group(1), re.DOTALL)
        for row in rows:
            tds = re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL)
            if len(tds) < 4:
                continue
            code_m = re.search(r"detail/code/(\d+)/", tds[1])
            name = re.sub(r"<[^>]+>", "", tds[1]).strip()
            if not code_m or not name:
                continue
            pct_txt = re.sub(r"<[^>]+>", "", tds[3]).strip().rstrip("%")
            result.append(SectorPerformance(
                code=code_m.group(1),
                name=name,
                pct=_safe_float(pct_txt),
                amplitude=0.0,
                turnover_rate=0.0,
            ))
        return result

    # ─── 概念成分股（详情页 HTML，按涨跌幅降序）───

    def get_sector_components(self, sector_code: str, page: int = 1,
                              all_pages: bool = False,
                              page_size: int = 50) -> list[StockInfo]:
        """概念成分股（按涨跌幅降序）。

        第 1 页用详情页 DETAIL_URL（完整 HTML，无反爬）；后续页用 ajax 翻页接口
        （可能触发 hexin-v 反爬，被拦截则止于第 1 页）。第 1 页约 10 只，
        已能满足编排器 pre_n（candidates_n*2≈10）需求。
        """
        t0 = time.time()
        result: list[StockInfo] = []

        # 第 1 页：详情页完整 HTML
        html = _curl(DETAIL_URL.format(code=sector_code), gbk=True)
        if html:
            result.extend(_parse_components_html(html, sector_code))

        # 翻页（仅 all_pages 时尝试；ajax 接口可能被反爬）
        if all_pages and result:
            for p in range(2, 6):
                url = COMPONENTS_URL.format(page=p, code=sector_code)
                ph = _curl(url, referer=DETAIL_URL.format(code=sector_code), gbk=True)
                if not ph:
                    break
                part = _parse_components_html(ph, sector_code)
                if not part:  # 反爬挑战页或末页
                    break
                result.extend(part)
                if len(part) < 10:
                    break

        elapsed = (time.time() - t0) * 1000
        if self._logger:
            self._logger.api("ths", "sector_components", ok=bool(result),
                             elapsed_ms=elapsed, note=f"n={len(result)}")
        return result

    # ─── 板块 5 分钟 K 线（1 分钟分时聚合）───

    def _get_inner_code(self, sector_code: str) -> str:
        """6 位 code → innerCode（885xxx），解析详情页 <input id="clid">，带缓存。"""
        if sector_code in _INNER_CACHE:
            return _INNER_CACHE[sector_code]
        html = _curl(DETAIL_URL.format(code=sector_code), gbk=True)
        inner = ""
        if html:
            m = re.search(r'id=["\']clid["\']\s+value=["\'](\d+)["\']', html)
            if m:
                inner = m.group(1)
        if inner:
            _INNER_CACHE[sector_code] = inner
        return inner

    def get_sector_5min_kline(self, sector_code: str, bars: int = 100) -> list[KBar]:
        """概念板块 5 分钟 K 线（同花顺 1 分钟分时聚合而来）。"""
        t0 = time.time()
        inner = self._get_inner_code(sector_code)
        if not inner:
            if self._logger:
                self._logger.api("ths", "sector_5min_kline", ok=False,
                                 elapsed_ms=(time.time() - t0) * 1000,
                                 error="innerCode 解析失败")
            return []
        raw = _curl(TIME_URL.format(inner=inner), referer=f"{Q_BASE}/")
        data = _parse_jsonp(raw) if raw else None
        elapsed = (time.time() - t0) * 1000
        if not data:
            if self._logger:
                self._logger.api("ths", "sector_5min_kline", ok=False,
                                 elapsed_ms=elapsed, error="分时为空")
            return []
        node = data.get(f"48_{inner}", {})
        kbars = _aggregate_5min(
            node.get("data", ""), node.get("date", ""),
            _safe_float(node.get("pre")))
        if self._logger:
            self._logger.api("ths", "sector_5min_kline", ok=bool(kbars),
                             elapsed_ms=elapsed, note=f"n={len(kbars)}")
        return kbars[-bars:] if bars else kbars

    # ─── 未实现（个股相关由雪球/腾讯提供）───

    def get_kline(self, code: str, days: int = 20) -> list[KBar]:
        raise NotImplementedError("同花顺 provider 不提供个股 K 线")

    def get_5min_kline(self, code: str, bars: int = 96) -> list[KBar]:
        raise NotImplementedError("同花顺 provider 不提供个股 5 分 K 线")

    def get_quote(self, code: str) -> Optional[Quote]:
        raise NotImplementedError("同花顺 provider 不提供个股行情")
