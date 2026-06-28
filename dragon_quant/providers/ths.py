"""
同花顺(THS) Provider — 行业板块排行 / 成分股 / 板块分时与历史K

接口与反爬要点：
  - 行业排行榜：data.10jqka.com.cn/funds/hyzjl/field/zdf/order/desc/page/{p}/
    curl + GBK 直取（无需 Playwright/Cookie），field=zdf 可正确按涨跌幅排序、翻页；
    单页 DOM 非严格有序，需抓多页后本地排序。网关有 403 频控，带退避重试。
  - 行业成分股：q.10jqka.com.cn/thshy/detail/.../ 详情页 HTML 表格解析（GBK）
  - 板块分时：d.10jqka.com.cn/v6/time/48_{innerCode}/last.js（JSONP，无 Cookie）
  - 板块历史5分K：d.10jqka.com.cn/v6/line/48_{innerCode}/30/last1000.js（JSONP）

代码体系：
  - 6 位 code（URL 用，行业为 881xxx）
  - innerCode（行情接口用）：行业板块 clid 即 code 本身（881xxx），
    概念板块为 885xxx 映射，统一在详情页 <input id="clid"> 解析。
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

# 行业板块涨跌幅排行页：field=zdf（涨跌幅）可正确按 order 排序、可翻页。
# 注意：旧 field=tradezdf 是资金流字段，无视 order/page，永远固定返回 50 行资金流入板块。
# curl + GBK 直取（无需 Playwright/Cookie/反爬）；单页 DOM 非严格有序，需抓多页后本地排序。
# hyzjl=行业资金流（约90个行业板块，code 为 881xxx）。
RANKING_URL = DATA_BASE + "/funds/hyzjl/field/zdf/order/desc/page/{page}/"
RANKING_MAX_PAGES = 3  # 行业板块约 90 个，每页 50，翻 3 页足够（末页不足自然停止）
# 成分股详情页（按涨跌幅降序），page 翻页
COMPONENTS_URL = (
    f"{Q_BASE}/thshy/detail/field/264648/order/desc/page/{{page}}/ajax/1/code/{{code}}/"
)
DETAIL_URL = f"{Q_BASE}/thshy/detail/code/{{code}}/"
# 非 ajax 翻页路径（免登录翻页），行业成分股按涨跌幅降序，每页约 20 只
PAGE_URL = f"{Q_BASE}/thshy/detail/order/desc/page/{{page}}/code/{{code}}/"
TIME_URL = D_BASE + "/v6/time/48_{inner}/last.js"
# 历史 K 线路径：周期码 30=5分钟（实测），last1000 取最近 1000 根
LINE_URL = D_BASE + "/v6/line/48_{inner}/30/last1000.js"

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


def _parse_1min(ts_data: str, date_str: str, pre_close: float) -> list[KBar]:
    """把同花顺当日 1 分钟分时逐分钟构造 KBar（不聚合）。

    分时行：HHMM,点位,分钟成交额,均价,分钟成交量
    单点位 → open=high=low=close=点位；pct 相对昨收。
    """
    bars: list[KBar] = []
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
        try:
            ts = int(time.mktime(time.strptime(
                f"{date_str} {hhmm[:2]}:{hhmm[2:]}", "%Y%m%d %H:%M"))) * 1000
        except ValueError:
            continue
        pct = ((price - pre_close) / pre_close * 100.0) if pre_close else 0.0
        bars.append(KBar(
            timestamp=ts, volume=volume,
            open=price, high=price, low=price, close=price,
            chg=price - pre_close if pre_close else 0.0,
            pct=pct, turnover=0.0, amount=amount,
        ))
    return bars


def _parse_line_5min(line_data: str) -> list[KBar]:
    """解析同花顺历史 K 线行（/v6/line，周期码30=5分）。

    行格式：YYYYMMDDHHMM,开,高,低,收,量,额,...（11 段，无昨收）
    pct/chg 用前一根 close 推算（首根无前根 → 0）。
    """
    bars: list[KBar] = []
    prev_close = 0.0
    for line in line_data.split(";"):
        parts = line.split(",")
        if len(parts) < 7:
            continue
        tstr = parts[0].strip()
        if len(tstr) != 12 or not tstr.isdigit():
            continue
        try:
            ts = int(time.mktime(time.strptime(tstr, "%Y%m%d%H%M"))) * 1000
        except ValueError:
            continue
        o = _safe_float(parts[1]); h = _safe_float(parts[2])
        lo = _safe_float(parts[3]); c = _safe_float(parts[4])
        vol = _safe_float(parts[5]); amt = _safe_float(parts[6])
        chg = (c - prev_close) if prev_close else 0.0
        pct = (chg / prev_close * 100.0) if prev_close else 0.0
        bars.append(KBar(
            timestamp=ts, volume=vol,
            open=o, high=h, low=lo, close=c,
            chg=chg, pct=pct, turnover=0.0, amount=amt,
        ))
        prev_close = c
    return bars


class THSProvider(StockProvider):

    @property
    def name(self) -> str:
        return "ths"

    # ─── 行业板块排行（curl field=zdf 排行页，多页+本地排序）───

    def get_sector_ranking(self, asc: bool = False) -> list[SectorPerformance]:
        """行业板块涨跌幅排行。asc=False 涨幅榜 / asc=True 跌幅榜。

        curl 抓取 field=zdf 排行页多页（GBK，无需 Playwright/Cookie），合并去重后
        本地按涨跌幅排序（单页 DOM 非严格有序，必须本地 re-sort）。
        同花顺数据网关有频控（403），单页带退避重试 + 页间小延迟降低触发概率。
        """
        t0 = time.time()
        seen: dict[str, SectorPerformance] = {}
        for p in range(1, RANKING_MAX_PAGES + 1):
            part = None
            for attempt in range(3):  # 退避重试：应对 403 频控
                html = _curl(RANKING_URL.format(page=p),
                             referer=f"{DATA_BASE}/", gbk=True)
                part = self._parse_ranking_html(html) if html else None
                if part:
                    break
                time.sleep(0.8 * (attempt + 1))  # 0.8s / 1.6s 退避
            if not part:  # 重试后仍空 → 末页或持续限流
                continue
            for s in part:
                seen[s.code] = s  # 按 code 去重（多页可能重叠）
            time.sleep(0.5)  # 页间延迟，降低频控触发

        rows = list(seen.values())
        elapsed = (time.time() - t0) * 1000
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

        # 翻页（仅 all_pages 时尝试；非 ajax 路径免登录，前 5 页≈50 只）
        if all_pages and result:
            for p in range(2, 6):
                url = PAGE_URL.format(page=p, code=sector_code)
                ph = _curl(url, referer=DETAIL_URL.format(code=sector_code), gbk=True)
                if not ph:
                    break
                part = _parse_components_html(ph, sector_code)
                if not part:  # 末页或 302 跳登录
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
        """6 位 code → innerCode，解析详情页 <input id="clid">，带缓存。
        行业板块 clid 即 code 本身（881xxx）；概念板块为 885xxx 映射。"""
        if sector_code in _INNER_CACHE:
            return _INNER_CACHE[sector_code]

        # 行业板块（881xxx）的 clid 与板块代码一致，无需依赖详情页解析。
        # 详情页偶发返回异常/空页面时，历史 5 分 K 与当日 1 分 K 会被误判为
        # innerCode 解析失败，进而导致 v2 资金承接降级为“目标板块5分K不足”。
        if re.fullmatch(r"881\d{3}", sector_code):
            _INNER_CACHE[sector_code] = sector_code
            return sector_code

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

    def get_sector_1min_kline(self, sector_code: str, bars: int = 240) -> list[KBar]:
        """概念板块当日 1 分钟分时 K 线（原始 1 分，不聚合）。"""
        t0 = time.time()
        inner = self._get_inner_code(sector_code)
        if not inner:
            if self._logger:
                self._logger.api("ths", "sector_1min_kline", ok=False,
                                 elapsed_ms=(time.time() - t0) * 1000,
                                 error="innerCode 解析失败")
            return []
        raw = _curl(TIME_URL.format(inner=inner), referer=f"{Q_BASE}/")
        data = _parse_jsonp(raw) if raw else None
        elapsed = (time.time() - t0) * 1000
        if not data:
            if self._logger:
                self._logger.api("ths", "sector_1min_kline", ok=False,
                                 elapsed_ms=elapsed, error="分时为空")
            return []
        node = data.get(f"48_{inner}", {})
        kbars = _parse_1min(
            node.get("data", ""), node.get("date", ""),
            _safe_float(node.get("pre")))
        if self._logger:
            self._logger.api("ths", "sector_1min_kline", ok=bool(kbars),
                             elapsed_ms=elapsed, note=f"n={len(kbars)}")
        return kbars[-bars:] if bars else kbars

    def get_sector_5min_kline_history(self, sector_code: str,
                                      days: int = 10) -> list[KBar]:
        """概念板块近 days 个交易日的 5 分钟历史 K 线（真实 OHLC）。

        同花顺 /v6/line/48_{inner}/30/last1000.js（周期码30=5分），_parse_jsonp
        后节点即顶层 dict，data 行 YYYYMMDDHHMM,开,高,低,收,量,额,... 直接 OHLC。
        """
        t0 = time.time()
        inner = self._get_inner_code(sector_code)
        if not inner:
            if self._logger:
                self._logger.api("ths", "sector_5min_history", ok=False,
                                 elapsed_ms=(time.time() - t0) * 1000,
                                 error="innerCode 解析失败")
            return []
        raw = _curl(LINE_URL.format(inner=inner), referer=f"{Q_BASE}/")
        data = _parse_jsonp(raw) if raw else None
        elapsed = (time.time() - t0) * 1000
        if not data:
            if self._logger:
                self._logger.api("ths", "sector_5min_history", ok=False,
                                 elapsed_ms=elapsed, error="历史K线为空")
            return []
        kbars = _parse_line_5min(data.get("data", ""))
        # 截取最近 days 个交易日（按日期分组取末 days 天）
        if kbars and days:
            from collections import OrderedDict
            by_day: "OrderedDict[str, list[KBar]]" = OrderedDict()
            for kb in kbars:
                d = time.strftime("%Y%m%d", time.localtime(kb.timestamp / 1000))
                by_day.setdefault(d, []).append(kb)
            keep_days = list(by_day.keys())[-days:]
            kbars = [kb for d in keep_days for kb in by_day[d]]
        if self._logger:
            self._logger.api("ths", "sector_5min_history", ok=bool(kbars),
                             elapsed_ms=elapsed, note=f"n={len(kbars)}")
        return kbars

    # ─── 未实现（个股相关由雪球/腾讯提供）───

    def get_kline(self, code: str, days: int = 20) -> list[KBar]:
        raise NotImplementedError("同花顺 provider 不提供个股 K 线")

    def get_5min_kline(self, code: str, bars: int = 96) -> list[KBar]:
        raise NotImplementedError("同花顺 provider 不提供个股 5 分 K 线")

    def get_quote(self, code: str) -> Optional[Quote]:
        raise NotImplementedError("同花顺 provider 不提供个股行情")
