#!/usr/bin/env python3
"""
东方财富公开 API 封装

所有接口均为公开 JSONP 接口，无需登录，免费使用。
详情见 references/api_reference.md
"""

import json
import re
import sys
import time
import urllib.request
import urllib.parse
from typing import Optional, Any
from datetime import datetime, timedelta

BASE_URL = "https://push2.eastmoney.com/api/qt"

_API_CALL_LOG: list[dict] = []


def get_api_calls_and_clear() -> list[dict]:
    """获取并清空本进程内的 API 调用记录。"""
    global _API_CALL_LOG
    calls = _API_CALL_LOG
    _API_CALL_LOG = []
    return calls


def _fetch(url: str, max_retries: int = 3) -> dict:
    """带重试的 HTTP GET，自动去掉 JSONP 包装。每次调用自动记录打点。"""
    t0 = time.time()
    last_err = None
    last_http_status = None
    last_body_snippet = None
    attempts = 0

    for attempt in range(max_retries):
        attempts += 1
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Referer": "https://quote.eastmoney.com/",
            })
            with urllib.request.urlopen(req, timeout=10) as resp:
                last_http_status = resp.status
                raw = resp.read().decode("utf-8")
                last_body_snippet = raw[:300]
            # 去 JSONP 包装
            match = re.search(r"\{.*\}", raw, re.DOTALL)
            if not match:
                raise ValueError(f"no JSON in response: {raw[:200]}")
            data = json.loads(match.group())
            if isinstance(data, dict) and data.get("rc") is not None and data["rc"] != 0:
                raise ValueError(f"API error rc={data.get('rc')} msg={data.get('msg','')}")

            elapsed = round((time.time() - t0) * 1000, 1)
            _API_CALL_LOG.append({
                "url": url[:150],
                "elapsed_ms": elapsed,
                "ok": True,
                "attempts": attempts,
            })
            return data
        except Exception as e:
            last_err = e
            if hasattr(e, "code"):
                last_http_status = e.code
                try:
                    body = e.read().decode("utf-8")
                    last_body_snippet = body[:300]
                except Exception:
                    pass
            if attempt < max_retries - 1:
                time.sleep(0.5 * (attempt + 1))

    elapsed = round((time.time() - t0) * 1000, 1)
    _API_CALL_LOG.append({
        "url": url[:150],
        "elapsed_ms": elapsed,
        "ok": False,
        "attempts": attempts,
        "reason": f"{type(last_err).__name__}: {last_err}",
        "last_http_status": last_http_status,
        "last_body_snippet": last_body_snippet,
    })
    raise RuntimeError(f"{type(last_err).__name__}: {last_err}") from last_err


# ─── 涨停榜 ────────────────────────────────────────────

def get_limit_up_list(date: Optional[str] = None) -> list[dict]:
    """
    获取某日涨停板列表。
    date: "2026-05-03" 或 None（最新交易日）
    返回: [{"code":"002xxx","name":"...","pct":10.0,"board_time":"0932",
            "consecutive":2,"industry_code":"BK0429","industry_name":"半导体",
            "turnover":5.2,"amount":523000000}, ...]
    """
    params = {
        "pn": "1", "pz": "200", "po": "1", "np": "1",
        "fltt": "2", "invt": "2", "fid": "f3",
        "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23",
        "fields": "f12,f14,f3,f8,f6,f100,f26,f184,f186",
    }
    qs = urllib.parse.urlencode(params)
    _load_industry_map()
    data = _fetch(f"{BASE_URL}/clist/get?{qs}")

    results = []
    for diff in data.get("data", {}).get("diff", []):
        pct = diff.get("f3", 0)
        if isinstance(pct, str):
            try: pct = float(pct)
            except ValueError:
                print(f"⚠️ 涨停榜涨幅解析失败 code={diff.get('f12','?')} raw={pct!r}", file=sys.stderr)
                continue
        code = diff.get("f12", "")
        
        # 涨停阈值：主板 9.9%，科创板/创业板 19.8%（取20%的-0.2%避免边界问题）
        if code.startswith(("30", "68")):
            threshold = 19.8
        else:
            threshold = 9.9
            
        if pct < threshold:
            continue
            
        # 日期：使用最新K线日期而非 f26（f26 可能是上市日期）
        raw_date = str(diff.get("f26", ""))
        date_str = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:8]}" if len(raw_date) == 8 else ""
        ind_name = diff.get("f100", "") or ""

        # 忽略明显是历史日期（上市日期）的条目，使用当前交易日
        if date_str and date_str < datetime.now().strftime("%Y-%m-%d"):
            date_str = ""

        # 封板时间处理
        board_time = diff.get("f186")
        if isinstance(board_time, int):
            board_time = f"{board_time:04d}"
        elif board_time is not None:
            board_time = str(board_time)
            
        # 连板数
        consecutive = diff.get("f184", 1)
        if isinstance(consecutive, str):
            try:
                consecutive = int(consecutive)
            except ValueError:
                consecutive = 1
        elif consecutive is None:
            consecutive = 1

        results.append({
            "code": code,
            "name": _clean_name(diff.get("f14", "")),
            "pct": pct,
            "date": date_str,
            "board_time": board_time,
            "consecutive": consecutive,
            "industry_name": ind_name,
            "industry_code": _INDUSTRY_NAME_TO_CODE.get(ind_name, ""),
            "turnover": diff.get("f8", 0) or 0,
            "amount": diff.get("f6", 0) or 0,
        })
    return results


def infer_consecutive_boards(code: str, kline: list[dict]) -> int:
    """从K线数据推算连板数。"""
    if not kline or len(kline) < 2:
        return 1
    cons = 1
    threshold = 19.9 if code.startswith(("30", "68")) else 9.5
    # 从最近一天往回数
    for i in range(len(kline) - 2, 0, -1):
        if kline[i]["pct"] >= threshold:
            cons += 1
        else:
            break
    return cons


# ─── 行业成分股 ─────────────────────────────────────────

# 行业名称 → 编码映射（常用板块，运行时动态扩展）
_INDUSTRY_NAME_TO_CODE: dict[str, str] = {}
_INDUSTRY_CODE_TO_NAME: dict[str, str] = {}
_INDUSTRY_MAP_LOADED = False

# 概念板块映射
_CONCEPT_CODE_TO_NAME: dict[str, str] = {}
_CONCEPT_MAP_LOADED = False

# 技术性板块名称（非题材，过滤用）
_TECH_BOARD_NAMES = {
    "昨日首板", "昨日涨停", "昨日涨停_含一字", "昨日连板", "昨日连板_含一字",
    "昨日触板", "东方财富热股", "历史新高", "百日新高", "最近多板",
    "近期新高", "2026一季报预增", "2026—季报扭亏", "预盈预增", "预亏预减",
    "机构重仓", "基金重仓", "券商重仓", "信托重仓", "保险重仓",
    "QFII重仓", "社保重仓", "融资融券", "深股通", "沪股通",
    "创业板综", "深圳特区", "广东板块", "浙江板块", "江苏板块",
    "北京板块", "上海板块", "山东板块",
}


def _load_industry_map():
    """从东方财富加载全部行业编码→名称映射。"""
    global _INDUSTRY_NAME_TO_CODE, _INDUSTRY_CODE_TO_NAME, _INDUSTRY_MAP_LOADED
    if _INDUSTRY_MAP_LOADED:
        return
    try:
        for pn in [1, 2, 3]:
            params = {
                "pn": str(pn), "pz": "200", "po": "1", "np": "1",
                "fltt": "2", "invt": "2", "fid": "f3",
                "fs": "m:90+t:2",
                "fields": "f12,f14",
            }
            qs = urllib.parse.urlencode(params)
            data = _fetch(f"{BASE_URL}/clist/get?{qs}")
            raw_diffs = data.get("data", {}).get("diff", [])
            if isinstance(raw_diffs, dict):
                raw_diffs = list(raw_diffs.values())
            if not raw_diffs:
                break
            for item in raw_diffs:
                if isinstance(item, dict):
                    code = item.get("f12", "")
                    name = item.get("f14", "")
                    if code and name:
                        _INDUSTRY_NAME_TO_CODE[name] = code
                        _INDUSTRY_CODE_TO_NAME[code] = name
    except Exception as e:
        print(f"⚠️ 行业映射加载失败 ({type(e).__name__}): {e}", file=sys.stderr)
    _INDUSTRY_MAP_LOADED = True


def _industry_name_to_code(name: str) -> str:
    """行业名称 → 编码。"""
    if not name or name == "-":
        return ""
    _load_industry_map()
    return _INDUSTRY_NAME_TO_CODE.get(name, "")


# ─── 概念板块映射 ────────────────────────────────────────

def _load_concept_map():
    """加载概念板块编码→名称映射。"""
    global _CONCEPT_CODE_TO_NAME, _CONCEPT_MAP_LOADED
    if _CONCEPT_MAP_LOADED:
        return
    try:
        for pn in [1, 2]:
            params = {
                "pn": str(pn), "pz": "200", "po": "1", "np": "1",
                "fltt": "2", "invt": "2", "fid": "f3",
                "fs": "m:90+t:3",
                "fields": "f12,f14",
            }
            qs = urllib.parse.urlencode(params)
            data = _fetch(f"{BASE_URL}/clist/get?{qs}")
            raw_diffs = data.get("data", {}).get("diff", [])
            if isinstance(raw_diffs, dict):
                raw_diffs = list(raw_diffs.values())
            if not raw_diffs:
                break
            for item in raw_diffs:
                if isinstance(item, dict):
                    code = item.get("f12", "")
                    name = item.get("f14", "")
                    if code and name and name not in _TECH_BOARD_NAMES:
                        _CONCEPT_CODE_TO_NAME[code] = name
    except Exception as e:
        print(f"⚠️ 概念映射加载失败 ({type(e).__name__}): {e}", file=sys.stderr)
    _CONCEPT_MAP_LOADED = True


# 手动概念覆写映射（已知市场叙事与官方分类不一致的票）
_CONCEPT_OVERRIDE: dict[str, str] = {
    "603095": "算力概念",
    "603629": "算力概念",
}


def get_stock_concept_map(limit_up_list: list[dict],
                          candidate_codes: set) -> dict[str, tuple[str, str]]:
    """
    将股票映射到当日最活跃的概念板块。
    返回: {code: (concept_code, concept_name), ...}
    """
    # 手动覆写
    result = {}
    for code in candidate_codes:
        cn = _CONCEPT_OVERRIDE.get(code)
        if cn:
            result[code] = ("", cn)

    _load_concept_map()
    if not _CONCEPT_CODE_TO_NAME:
        return result

    lu_codes = {lu.get("code", "") for lu in limit_up_list}
    all_codes = lu_codes | candidate_codes

    # 取当日涨幅前 100 的概念板块（扫更多覆盖）
    params = {
        "pn": "1", "pz": "100", "po": "0", "np": "1",
        "fltt": "2", "invt": "2", "fid": "f3",
        "fs": "m:90+t:3",
        "fields": "f12,f14,f3",
    }
    qs = urllib.parse.urlencode(params)
    data = _fetch(f"{BASE_URL}/clist/get?{qs}")
    diffs = data.get("data", {}).get("diff", [])
    if isinstance(diffs, dict):
        diffs = list(diffs.values())

    thematic_codes = [
        d.get("f12", "") for d in diffs
        if d.get("f14", "") not in _TECH_BOARD_NAMES
    ][:50]

    # 逐个概念板块查成分股
    stock_concepts: dict[str, list[tuple[str, str, int]]] = {}
    for cc in thematic_codes:
        cn = _CONCEPT_CODE_TO_NAME.get(cc, "")
        if not cn:
            continue
        try:
            constituents = get_industry_components(cc)
            time.sleep(0.1)
            up_in_concept = [c for c in constituents if c.get("code", "") in lu_codes]
            up_count = len(up_in_concept)
            for c in constituents:
                sc = c.get("code", "")
                if sc in all_codes:
                    stock_concepts.setdefault(sc, []).append((cn, cc, up_count))
        except Exception as e:
            print(f"  ⚠️ 概念 '{cn}' 成分股获取失败 ({type(e).__name__})", file=sys.stderr)
            continue

    # 每只票取涨停股最多的概念（若无涨停概念则取任意匹配的）
    for sc, concepts in stock_concepts.items():
        # 优先选涨停家数多的，平局时保持第一个
        best = max(concepts, key=lambda x: x[2])
        result[sc] = (best[1], best[0])

    return result


def get_industry_components(industry_code_or_name: str) -> list[dict]:
    """
    获取某行业全量成分股当日行情。
    industry_code_or_name: "BK0429" 或 "半导体"
    返回: [{"code":"002xxx","name":"...","pct":3.5,"turnover":5.0,"amount":1.2e8}, ...]
    """
    # 如果传入的是行业名，先转编码
    if not industry_code_or_name.startswith("BK"):
        code = _industry_name_to_code(industry_code_or_name)
        if not code:
            import sys
            print(f"⚠️ 行业名 '{industry_code_or_name}' 未找到对应BK编码，返回空", file=sys.stderr)
            return []
        industry_code_or_name = code

    params = {
        "pn": "1", "pz": "500", "po": "1", "np": "1",
        "fltt": "2", "invt": "2", "fid": "f3",
        "fs": f"b:{industry_code_or_name}",
        "fields": "f12,f14,f3,f8,f6",
    }
    qs = urllib.parse.urlencode(params)
    data = _fetch(f"{BASE_URL}/clist/get?{qs}")

    results = []
    raw_diffs = data.get("data", {}).get("diff", [])
    # API 可能返回 dict（有序）或 list
    if isinstance(raw_diffs, dict):
        raw_diffs = list(raw_diffs.values())
    for diff in raw_diffs:
        pct = diff.get("f3", 0)
        if isinstance(pct, str):
            try: pct = float(pct)
            except ValueError: pct = 0
        results.append({
            "code": diff.get("f12", ""),
            "name": _clean_name(diff.get("f14", "")),
            "pct": pct,
            "turnover": diff.get("f8", 0) or 0,
            "amount": diff.get("f6", 0) or 0,
        })
    return results


# ─── 个股日K线 ──────────────────────────────────────────

def get_stock_kline(code: str, days: int = 20) -> list[dict]:
    """
    获取个股日K线（优先使用腾讯 API，东方财富作为备用）。
    code: "600519" 或 "002xxx"
    返回: [{"date":"2026-04-28","open":...,"high":...,"low":...,
            "close":...,"pre_close":...,"pct":...,"volume":...,"amount":...}, ...]
    """
    # 优先尝试腾讯 API（稳定，非交易日也能用）
    results = _get_kline_tencent(code, days)
    if results:
        return results

    # 备用：东方财富 API
    return _get_kline_eastmoney(code, days)


def _get_kline_tencent(code: str, days: int = 20) -> list[dict]:
    """通过腾讯财经 API 获取日K线（内置重试）。"""
    prefix = "sh" if code.startswith(("6", "9")) else "sz"
    url = (f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
           f"?param={prefix}{code},day,,,{days},qfq")
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://gu.qq.com/",
            })
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            key = f"{prefix}{code}"
            klines = data.get("data", {}).get(key, {}).get("qfqday", [])
            if not klines:
                klines = data.get("data", {}).get(key, {}).get("day", [])
            if klines:
                break
        except Exception:
            if attempt < 2:
                time.sleep(0.5 * (attempt + 1))
            else:
                return []
    if not klines:
        return []

    results = []
    pre_close = None
    for line in klines:
        if isinstance(line, dict):
            continue
        if len(line) < 6:
            continue
        open_p  = float(line[1])
        close_p = float(line[2])
        high_p  = float(line[3])
        low_p   = float(line[4])
        vol     = float(line[5])
        amt     = float(line[6]) if len(line) > 6 else 0
        pct = (close_p - pre_close) / pre_close * 100 if pre_close else 0
        results.append({
            "date": line[0],
            "open": open_p,
            "close": close_p,
            "high": high_p,
            "low": low_p,
            "pre_close": pre_close or open_p,
            "pct": pct,
            "volume": vol,
            "amount": amt,
        })
        pre_close = close_p
    return results


def _get_kline_eastmoney(code: str, days: int = 20) -> list[dict]:
    """通过东方财富 API 获取日K线（备用）。"""
    market = _get_market(code)
    secid = f"{market}.{code}"
    params = {
        "secid": secid,
        "klt": "101",
        "lmt": str(days),
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        "ut": "fa5fd1943c7b386f172d6893dbfbfdc4",
        "fqt": "1",
    }
    qs = urllib.parse.urlencode(params)
    try:
        data = _fetch(f"https://push2his.eastmoney.com/api/qt/stock/kline/get?{qs}")
    except Exception:
        return []

    results = []
    klines = data.get("data", {}).get("klines", [])
    pre_close = None
    for line in klines:
        if isinstance(line, dict):
            continue
        parts = line.split(",")
        if len(parts) < 11:
            continue
        open_p  = float(parts[1])
        close_p = float(parts[2])
        high_p  = float(parts[3])
        low_p   = float(parts[4])
        vol     = float(parts[5])
        amt     = float(parts[6])
        pct = (close_p - pre_close) / pre_close * 100 if pre_close else 0
        results.append({
            "date": parts[0],
            "open": open_p,
            "close": close_p,
            "high": high_p,
            "low": low_p,
            "pre_close": pre_close or open_p,
            "pct": pct,
            "volume": vol,
            "amount": amt,
        })
        pre_close = close_p
    return results


# ─── 个股实时行情 ────────────────────────────────────────

def get_stock_quote(code: str) -> dict:
    """
    获取个股实时行情（优先东方财富，失败回退腾讯）。
    返回: {"code":"600519","name":"贵州茅台","price":1400,"pct":1.5,
           "open":1380,"high":1405,"low":1375,"volume":50000,"amount":7e9}
    """
    # 主源：东方财富
    try:
        market = _get_market(code)
        secid = f"{market}.{code}"
        params = {
            "secid": secid,
            "fields": "f43,f44,f45,f46,f47,f48,f50,f57,f58,f60,f170",
        }
        qs = urllib.parse.urlencode(params)
        data = _fetch(f"{BASE_URL}/stock/get?{qs}")
        d = data.get("data", {})
        return {
            "code": d.get("f57", code),
            "name": _clean_name(d.get("f58", "")),
            "price": d.get("f43", 0) / 100 if d.get("f43") else 0,
            "pct": d.get("f170", 0) / 100 if d.get("f170") else 0,
            "open": d.get("f44", 0) / 100 if d.get("f44") else 0,
            "high": d.get("f45", 0) / 100 if d.get("f45") else 0,
            "low": d.get("f46", 0) / 100 if d.get("f46") else 0,
            "volume": d.get("f47", 0),
            "amount": d.get("f48", 0),
        }
    except Exception as e:
        print(f"  ⚠️ 东方财富行情失败 ({type(e).__name__})，尝试腾讯备用", file=sys.stderr)

    # 备用：腾讯财经
    return _get_quote_tencent(code)


def _get_quote_tencent(code: str) -> dict:
    """腾讯财经实时行情（备用源）。"""
    prefix = "sh" if code.startswith(("6", "9")) else "sz"
    url = f"http://qt.gtimg.cn/q={prefix}{code}"
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://gu.qq.com/",
            })
            with urllib.request.urlopen(req, timeout=10) as resp:
                raw = resp.read().decode("gbk")
            # 格式: v_sz000790="1~华神科技~000790~4.57~4.55~4.15~4.58~..."
            if '="' not in raw:
                continue
            body = raw.split('="', 1)[1].rstrip('";\n')
            parts = body.split("~")
            if len(parts) < 38:
                continue
            price = float(parts[3]) if parts[3] else 0
            prev_close = float(parts[4]) if parts[4] else 0
            return {
                "code": code,
                "name": parts[1],
                "price": price,
                "pct": round((price - prev_close) / prev_close * 100, 2) if prev_close else 0,
                "open": float(parts[5]) if parts[5] else 0,
                "high": float(parts[33]) if len(parts) > 33 and parts[33] else 0,
                "low": float(parts[34]) if len(parts) > 34 and parts[34] else 0,
                "volume": float(parts[6]) if parts[6] else 0,
                "amount": float(parts[37]) * 10000 if len(parts) > 37 and parts[37] else 0,
            }
        except Exception:
            if attempt < 2:
                time.sleep(0.5 * (attempt + 1))
            else:
                print(f"  ⚠️ 腾讯行情也失败，返回默认值", file=sys.stderr)
                return {"code": code, "name": "", "price": 0, "pct": 0,
                        "open": 0, "high": 0, "low": 0, "volume": 0, "amount": 0}


# ─── 板块指数 5 分钟 K 线 ────────────────────────────────

def get_sector_5min_kline(industry_code: str, bars: int = 48) -> list[dict]:
    """
    获取板块指数 5 分钟 K 线。
    策略：取板块内成分股的 5 分钟 K 线合成板块走势（成分股合成法），
    解决东财 push2his K 线 API 封杀问题。
    industry_code: "BK0429"
    bars: 默认 48（一天 4h × 12 根/小时）
    返回: [{"time":"0935","open":...,"close":...,"high":...,"low":...,"volume":...,"amount":...}, ...]
    """
    # ① 合成板块 K 线：取成分股 Top 3 的 5 分钟 K 线等权平均
    try:
        components = get_industry_components(industry_code)
        if components:
            components.sort(key=lambda c: c.get("amount", 0) or 0, reverse=True)
            top_codes = [c["code"] for c in components[:3] if c.get("code")]
            if top_codes:
                component_bars = []
                for tc in top_codes:
                    kl = get_stock_5min_kline(tc, bars)
                    if kl and len(kl) >= min(10, bars):
                        component_bars.append(kl)
                if len(component_bars) >= 1:
                    synthesized = _synthesize_sector_bars(component_bars, bars)
                    if synthesized:
                        return synthesized
    except Exception as e:
        print(f"  ⚠️ 板块 {industry_code} 合成K线失败 ({type(e).__name__})", file=sys.stderr)

    # ② 兜底：东财（目前通常返回 rc=102，但保留接口）
    secid = f"90.{industry_code}"
    params = {
        "secid": secid,
        "klt": "5",
        "lmt": str(bars),
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        "ut": "fa5fd1943c7b386f172d6893dbfbfdc4",
        "fqt": "1",
    }
    qs = urllib.parse.urlencode(params)
    try:
        data = _fetch(f"https://push2his.eastmoney.com/api/qt/stock/kline/get?{qs}")
    except Exception:
        return []

    results = []
    klines = data.get("data", {}).get("klines", [])
    for line in klines:
        parts = line.split(",")
        if len(parts) < 11:
            continue
        results.append({
            "time": parts[0].split(" ")[-1][:4] if " " in parts[0] else parts[0],
            "open":  float(parts[1]),
            "close": float(parts[2]),
            "high":  float(parts[3]),
            "low":   float(parts[4]),
            "volume":float(parts[5]),
            "amount":float(parts[6]),
        })
    return results


def _synthesize_sector_bars(component_bars_list: list[list[dict]], max_bars: int) -> list[dict]:
    """
    将多只成分股的 5 分钟 K 线按时间对齐后等权平均，合成板块 K 线。
    component_bars_list: [[{time,open,close,...}, ...], [{...}, ...], ...]
    """
    if not component_bars_list:
        return []

    time_slots = list(dict.fromkeys(
        b["time"] for bars in component_bars_list for b in bars
    ))

    results = []
    for ts in time_slots:
        opens, highs, lows, closes, volumes, amounts = [], [], [], [], [], []
        for bars in component_bars_list:
            matched = [b for b in bars if b["time"] == ts]
            if not matched:
                continue
            b = matched[0]
            opens.append(b["open"])
            highs.append(b["high"])
            lows.append(b["low"])
            closes.append(b["close"])
            volumes.append(b.get("volume", 0) or 0)
            amounts.append(b.get("amount", 0) or 0)

        if not opens:
            continue

        results.append({
            "time": ts,
            "open": sum(opens) / len(opens),
            "high": max(highs),
            "low": min(lows),
            "close": sum(closes) / len(closes),
            "volume": sum(volumes),
            "amount": sum(amounts),
        })

    if len(results) > max_bars:
        results = results[-max_bars:]
    return results


# ─── 个股 5 分钟 K 线 ────────────────────────────────────

def get_stock_5min_kline(code: str, bars: int = 48) -> list[dict]:
    """
    获取个股 5 分钟 K 线（多源 fallback：雪球 → 新浪 → 东财）。
    code: "002192" 或 "600519"
    bars: 默认 48（一天 4h × 12 根/小时）
    返回: [{"time":"0935","open":...,"close":...,"high":...,"low":...,"volume":...,"amount":...}, ...]
    """
    # ① 雪球（需 cookie，路径最优先）
    try:
        from xueqiu_api import get_stock_5min as _xq_5min
        result = _xq_5min(code, bars)
        if result and len(result) >= min(10, bars):
            return result
    except Exception:
        pass

    # ② 新浪财经（无需 login，稳定性高）
    result = _get_stock_5min_sina(code, bars)
    if result and len(result) >= min(10, bars):
        return result

    # ③ 东方财富（目前 push2his 返回 rc=102，但保留兜底）
    market = _get_market(code)
    secid = f"{market}.{code}"
    params = {
        "secid": secid,
        "klt": "5",
        "lmt": str(bars),
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        "ut": "fa5fd1943c7b386f172d6893dbfbfdc4",
        "fqt": "1",
    }
    qs = urllib.parse.urlencode(params)
    try:
        data = _fetch(f"https://push2his.eastmoney.com/api/qt/stock/kline/get?{qs}")
    except Exception:
        return []

    results = []
    klines = data.get("data", {}).get("klines", [])
    for line in klines:
        parts = line.split(",")
        if len(parts) < 11:
            continue
        results.append({
            "time": parts[0].split(" ")[-1][:4] if " " in parts[0] else parts[0],
            "open":  float(parts[1]),
            "close": float(parts[2]),
            "high":  float(parts[3]),
            "low":   float(parts[4]),
            "volume":float(parts[5]),
            "amount":float(parts[6]),
        })
    return results


def _get_stock_5min_sina(code: str, bars: int = 48) -> list[dict]:
    """
    从新浪财经获取个股 5 分钟 K 线。
    code: "002192" 或 "600519"
    """
    prefix = "sh" if code.startswith(("6", "9")) else "sz"
    symbol = f"{prefix}{code}"
    url = (f"http://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
           f"CN_MarketData.getKLineData?symbol={symbol}&scale=5&ma=no&datalen={bars}")
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0",
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode("utf-8")
        data = json.loads(raw)
    except Exception:
        return []

    if not isinstance(data, list):
        return []

    results = []
    for item in data:
        try:
            day = item.get("day", "")
            # Sina format: "2026-05-08 14:01:00" → "1401"
            if " " in day:
                hms = day.split(" ")[1]  # "14:01:00"
                time_str = hms.replace(":", "")[:4]  # "1401"
            else:
                time_str = ""
            results.append({
                "time": time_str,
                "open": float(item.get("open", 0)),
                "high": float(item.get("high", 0)),
                "low": float(item.get("low", 0)),
                "close": float(item.get("close", 0)),
                "volume": float(item.get("volume", 0)),
                "amount": 0,
            })
        except (ValueError, TypeError):
            continue

    if len(results) > bars:
        results = results[-bars:]
    return results


# ─── 全量板块 5 分钟 K 线 ────────────────────────────────

# 申万二级行业编码列表（精选 50+ 个活跃板块，避免全量 130+ 个请求）
ACTIVE_SECTORS = [
    "BK0429", "BK0431", "BK0433", "BK0435", "BK0437", "BK0439",
    "BK0441", "BK0443", "BK0445", "BK0447", "BK0449", "BK0451",
    "BK0453", "BK0455", "BK0457", "BK0459", "BK0461", "BK0463",
    "BK0465", "BK0467", "BK0469", "BK0471", "BK0473", "BK0475",
    "BK0477", "BK0479", "BK0481", "BK0483", "BK0485", "BK0487",
    "BK0489", "BK0491", "BK0493", "BK0495", "BK0497", "BK0499",
    "BK0501", "BK0503", "BK0505", "BK0507", "BK0509", "BK0511",
    "BK0513", "BK0515", "BK0517", "BK0519", "BK0521", "BK0523",
    "BK0525", "BK0527",
]


def get_all_active_sector_5min() -> dict[str, list[dict]]:
    """
    获取所有活跃板块的 5 分钟 K 线。
    返回: {"BK0429": [{...}, ...], "BK0431": [{...}, ...], ...}
    """
    result = {}
    for code in ACTIVE_SECTORS:
        try:
            kline = get_sector_5min_kline(code, bars=48)
            if kline:
                result[code] = kline
            time.sleep(0.05)  # 限速
        except Exception as e:
            print(f"  ⚠️ 板块 {code} 5分钟K线获取失败 ({type(e).__name__})", file=sys.stderr)
            continue
    return result


# ─── 工具函数 ────────────────────────────────────────────

def _clean_name(name: str) -> str:
    """清洗股票名字中的空格（东方财富 API 对三字股名会插入空格）。"""
    return name.replace(" ", "")


def _get_market(code: str) -> str:
    """根据代码前缀判断市场。"""
    c = str(code)[:3]
    if c in ("600", "601", "603", "605"):
        return "1"   # 上海
    elif c in ("000", "001", "002", "003"):
        return "0"   # 深圳
    elif c.startswith("30"):
        return "0"   # 创业板，深圳
    elif c.startswith("68"):
        return "1"   # 科创板，上海
    else:
        return "0"


def parse_board_minutes(board_time: Optional[str]) -> Optional[int]:
    """
    将 HHMM 封板时间转为当日分钟数（从 9:30 起算）。
    0932 → 2, None → None
    """
    if board_time is None:
        return None
    try:
        h = int(board_time[:2])
        m = int(board_time[2:4])
        return h * 60 + m
    except (ValueError, IndexError):
        return None


def get_market_index_kline(index_code: str = "1.000001", days: int = 20) -> list[dict]:
    """
    获取大盘指数日K线（优先腾讯，失败时回退东方财富）。
    index_code: "1.000001"（上证）, "0.399001"（深成指）, "0.399006"（创业板指）
    """
    # 腾讯 API: sh000001, sz399001, sz399006
    if index_code == "1.000001":
        qq_code = "sh000001"
    elif index_code == "0.399001":
        qq_code = "sz399001"
    elif index_code == "0.399006":
        qq_code = "sz399006"
    elif index_code == "1.000688":
        qq_code = "sh000688"
    else:
        qq_code = f"sh{index_code.split('.')[-1]}"

    # 优先腾讯
    results = _try_tencent_index_kline(qq_code, days)
    if results:
        return results

    # 备用：东方财富
    return _get_kline_eastmoney(index_code, days)


def _try_tencent_index_kline(qq_code: str, days: int) -> list[dict]:
    url = (f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
           f"?param={qq_code},day,,,{days},qfq")
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://gu.qq.com/",
            })
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            klines = data.get("data", {}).get(qq_code, {}).get("qfqday", [])
            if not klines:
                klines = data.get("data", {}).get(qq_code, {}).get("day", [])
            if klines:
                break
        except Exception:
            if attempt < 2:
                time.sleep(0.5 * (attempt + 1))
            else:
                return []

    results = []
    pre_close = None
    for line in klines:
        if len(line) < 6:
            continue
        open_p  = float(line[1])
        close_p = float(line[2])
        high_p  = float(line[3])
        low_p   = float(line[4])
        pct = (close_p - pre_close) / pre_close * 100 if pre_close else 0
        results.append({
            "date": line[0],
            "open": open_p,
            "close": close_p,
            "high": high_p,
            "low": low_p,
            "pre_close": pre_close or open_p,
            "pct": pct,
        })
        pre_close = close_p
    return results
