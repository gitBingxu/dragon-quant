"""宏观扰动数据源门面：外围指数 / 美债 10Y / 黄金。

设计（见 docs/solutions/2026-10-20-prep-macro-candidate-verdict-design.md）：

- ``MacroSpec`` 声明每个品种的取值口径与**每品种源优先级**（priority）。
- ``collect_macro`` 按全局源顺序（tdx → tencent → yahoo → xueqiu）批次拉取，
  每个源只接收「priority 含该源且尚未满足」的品种；单源失败记 ``source_errors`` 后继续。
- canonical 取值为「日 K 中日期 < trade_date 的最后一根已完成 bar」；quote 类回退源
  必须自行校验 ``session_date < trade_date``，否则拒绝（防止把盘中值写进 T 日资产）。
- 所有数值归一为 native float；NaN/inf → None，保证 ``allow_nan=False`` 落库不炸。
"""

from __future__ import annotations

import json
import math
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

NEW_YORK = ZoneInfo("America/New_York")

_SOURCE_ORDER = ("tdx", "tencent", "yahoo", "xueqiu")

YAHOO_HOST = "https://query1.finance.yahoo.com"
YAHOO_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
)


@dataclass(frozen=True)
class MacroSpec:
    key: str
    label: str
    kind: str                 # index / yield / commodity
    price_unit: str           # points / pct / usd_per_oz / cny_per_g
    chg_unit: str             # pct / bp
    tdx: tuple[tuple[str, str], ...]
    priority: tuple[str, ...]
    tencent: Optional[str] = None
    xueqiu: Optional[str] = None
    yahoo: Optional[str] = None


MACRO_SPECS: tuple[MacroSpec, ...] = (
    MacroSpec("us_dji", "道指", "index", "points", "pct",
              (("INTL_INDEX", "A_DJI"),), ("tdx", "tencent", "yahoo", "xueqiu"),
              "usDJI", ".DJI", "^DJI"),
    MacroSpec("us_ixic", "纳指", "index", "points", "pct",
              (("INTL_INDEX", "A_IXIC"),), ("tdx", "tencent", "yahoo", "xueqiu"),
              "usIXIC", ".IXIC", "^IXIC"),
    MacroSpec("us_spx", "标普", "index", "points", "pct",
              (("INTL_INDEX", "A_SPX"),), ("tdx", "tencent", "yahoo", "xueqiu"),
              "usINX", ".INX", "^GSPC"),
    MacroSpec("ust10y", "美债10Y", "yield", "pct", "bp",
              (("MACRO_INDICATOR", "8_ATY"),), ("tdx", "yahoo"),
              None, None, "^TNX"),
    MacroSpec("gold", "COMEX金", "commodity", "usd_per_oz", "pct",
              (("COMEX_FUTURES", "GC00W"), ("SH_GOLD", "Au99.99")), ("tdx", "yahoo"),
              None, None, "GC=F"),
)

MACRO_KEYS: tuple[str, ...] = tuple(spec.key for spec in MACRO_SPECS)
SPEC_BY_KEY: dict[str, MacroSpec] = {spec.key: spec for spec in MACRO_SPECS}


def _num(value) -> Optional[float]:
    """float 归一；NaN/inf/异常 → None。"""
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _compact_date(value) -> Optional[str]:
    """'YYYY-MM-DD' → 'YYYYMMDD'。"""
    if not isinstance(value, str) or len(value) < 10:
        return None
    return value[:10].replace("-", "")


def build_macro_record(spec: MacroSpec, *, price, prev_close, source: str, code: str,
            market: Optional[str] = None, session_date: Optional[str] = None,
            raw_date: Optional[str] = None, raw_time: Optional[str] = None,
            chg_bp_approx: Optional[bool] = None,
            precision_note: Optional[str] = None,
            label: Optional[str] = None,
            price_unit: Optional[str] = None) -> Optional[dict]:
    """构造归一后的单品种记录；price<=0 视为不可用。"""
    price = _num(price)
    if price is None or price <= 0:
        return None
    price = round(price, 6)
    prev = _num(prev_close)
    if prev is not None and prev <= 0:
        prev = None
    if prev is not None:
        prev = round(prev, 6)

    if spec.kind == "yield":
        chg = round(price - prev, 6) if prev is not None else None
        pct = None
        chg_bp = round((price - prev) * 100.0, 2) if prev is not None else None
    else:
        chg = round(price - prev, 6) if prev is not None else None
        pct = round((price / prev - 1.0) * 100.0, 4) if prev else None
        chg_bp = None

    return {
        "key": spec.key,
        "label": label or spec.label,
        "kind": spec.kind,
        "price_unit": price_unit or spec.price_unit,
        "chg_unit": spec.chg_unit,
        "price": price,
        "prev_close": prev,
        "chg": chg,
        "pct": pct,
        "chg_bp": chg_bp,
        "chg_bp_approx": chg_bp_approx,
        "precision_note": precision_note,
        "source": source,
        "code": code,
        "market": market,
        "session_date": session_date,
        "raw_date": raw_date or _compact_date(session_date),
        "raw_time": raw_time,
    }


def collect_macro(sources: dict, trade_date: str,
                  specs: tuple[MacroSpec, ...] = MACRO_SPECS,
                  as_of: Optional[str] = None) -> dict:
    """按源顺序批次拉取；每个源只接收 priority 含该源且尚未满足的品种。

    ``sources`` 为 ``{source_name: source_obj}``；source_obj 需实现
    ``get_macro_quotes(specs, trade_date) -> {key: record}``。
    全部源都失败且无任何品种 → raise RuntimeError；部分缺失 → degraded 列表。
    """
    if not isinstance(sources, dict):
        sources = {}
    remaining: dict[str, MacroSpec] = {spec.key: spec for spec in specs}
    instruments: dict[str, dict] = {}
    chosen: dict[str, str] = {}
    errors: dict[str, str] = {}

    for name in _SOURCE_ORDER:
        provider = sources.get(name)
        if provider is None or not remaining:
            continue
        wanted = [spec for spec in specs
                  if spec.key in remaining and name in spec.priority]
        if not wanted:
            continue
        try:
            result = provider.get_macro_quotes(wanted, trade_date)
        except (NotImplementedError, ImportError) as exc:
            errors[name] = f"{type(exc).__name__}: {exc}"
            continue
        except Exception as exc:  # 单源失败不中断：记错误继续下一源
            errors[name] = f"{type(exc).__name__}: {exc}"
            continue
        if not isinstance(result, dict):
            continue
        for spec in wanted:
            rec = result.get(spec.key)
            if isinstance(rec, dict) and _num(rec.get("price")) is not None:
                instruments[spec.key] = rec
                chosen[spec.key] = name
                remaining.pop(spec.key, None)

    degraded = sorted(remaining)
    if not instruments:
        detail = "；".join(f"{k}={v}" for k, v in sorted(errors.items())) or "无有效数据"
        raise RuntimeError(f"宏观数据全部不可用：{detail}")

    return {
        "trade_date": trade_date,
        # canonical 日 K 的归属日；不写采集时刻，保证 --recompute 可重放
        "as_of": as_of or trade_date,
        "caliber": "last_completed_daily_bar_before_trade_date",
        "instruments": instruments,
        "degraded": degraded,
        "sources": chosen,
        "source_errors": errors,
    }


# ─── Yahoo chart API（best-effort 最后回退；可能 429）───────────────────────────


class YahooMacroSource:
    """Yahoo chart API 日 K 回退（无 Cookie；单品种 5s 超时，失败即抛）。"""

    name = "yahoo"

    def get_macro_quotes(self, specs, trade_date: str) -> dict[str, dict]:
        wanted = [spec for spec in specs if spec.yahoo]
        if not wanted:
            return {}
        out: dict[str, dict] = {}
        errors: list[str] = []
        for spec in wanted:
            try:
                data = _fetch_yahoo_chart(spec.yahoo)
                rec = _record_from_yahoo(spec, data, trade_date)
            except Exception as exc:
                errors.append(f"{spec.key}: {type(exc).__name__}: {exc}")
                continue
            if rec:
                out[spec.key] = rec
        if not out and errors:
            raise RuntimeError("Yahoo 宏观数据不可用：" + "；".join(errors))
        return out


def _fetch_yahoo_chart(symbol: str) -> dict:
    url = (YAHOO_HOST + "/v8/finance/chart/"
           + urllib.parse.quote(symbol, safe="") + "?range=10d&interval=1d")
    req = urllib.request.Request(url, headers={
        "User-Agent": YAHOO_UA,
        "Accept": "application/json,text/plain,*/*",
    })
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        raise RuntimeError(f"Yahoo {symbol} 请求失败: {exc}") from exc


def _record_from_yahoo(spec: MacroSpec, data: dict,
                       trade_date: str) -> Optional[dict]:
    result = ((data or {}).get("chart") or {}).get("result")
    if not isinstance(result, list) or not result:
        return None
    node = result[0] if isinstance(result[0], dict) else {}
    timestamps = node.get("timestamp") or []
    quotes = ((node.get("indicators") or {}).get("quote") or [])
    quote = quotes[0] if quotes and isinstance(quotes[0], dict) else {}
    closes = quote.get("close") or []
    rows: list[tuple[str, float]] = []
    for i, ts in enumerate(timestamps):
        if i >= len(closes):
            break
        close = _num(closes[i])
        if close is None or close <= 0:
            continue
        try:
            day = datetime.fromtimestamp(float(ts), NEW_YORK).strftime("%Y-%m-%d")
        except (TypeError, ValueError, OSError):
            continue
        if day >= trade_date:      # 排除盘中 bar
            continue
        rows.append((day, close))
    if not rows:
        return None
    rows.sort(key=lambda item: item[0])
    session_date, price = rows[-1]
    prev_close = rows[-2][1] if len(rows) >= 2 else None
    return build_macro_record(
        spec, price=price, prev_close=prev_close, source="yahoo",
        code=spec.yahoo or "", market="yahoo", session_date=session_date,
        chg_bp_approx=False if spec.kind == "yield" else None,
    )
