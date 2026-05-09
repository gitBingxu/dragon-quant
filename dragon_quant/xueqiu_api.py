#!/usr/bin/env python3
"""
雪球 API 客户端 (Xueqiu API Client)

提供个股 5 分钟 K 线、日K线等数据，需浏览器 cookie 认证。
Cookie 存储在 ~/.lsa_xq_cookies，过期时回退到备用数据源。
"""

import json
import os
import sys
import time
import urllib.request
import urllib.parse
from datetime import datetime
from typing import Optional, Any, Dict

COOKIE_FILE = os.path.expanduser("~/.lsa_xq_cookies")
XUEQIU_BASE = "https://stock.xueqiu.com"
_xq_available_cache: Dict[str, Any] = {"ts": 0, "value": None}


def _load_cookies() -> Optional[str]:
    try:
        with open(COOKIE_FILE) as f:
            cookies = f.read().strip()
        return cookies if cookies else None
    except FileNotFoundError:
        return None


def _save_cookies(cookie_str: str) -> None:
    os.makedirs(os.path.dirname(COOKIE_FILE), exist_ok=True)
    with open(COOKIE_FILE, "w") as f:
        f.write(cookie_str.strip())


def is_available() -> bool:
    now = time.time()
    cached = _xq_available_cache
    if now - cached["ts"] < 60 and cached["value"] is not None:
        return bool(cached["value"])
    cookies = _load_cookies()
    if not cookies:
        cached["ts"], cached["value"] = now, False
        return False
    valid = _validate_cookies(cookies)
    cached["ts"], cached["value"] = now, valid
    return valid


def _validate_cookies(cookies: str) -> bool:
    try:
        data = _fetch_xq("/v5/stock/quote.json?symbol=SZ000001&extend=detail", cookies, timeout=5)
        name = data.get("data", {}).get("quote", {}).get("name")
        return bool(name)
    except Exception:
        return False


def _fetch_xq(path: str, cookies: str, timeout: int = 10) -> dict:
    url = f"{XUEQIU_BASE}{path}"
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) AppleWebKit/537.36",
        "Referer": "https://xueqiu.com/",
        "Sec-Fetch-Mode": "cors",
        "Cookie": cookies,
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
    data = json.loads(raw)
    err_code = data.get("error_code")
    if err_code and err_code != 0:
        raise RuntimeError(f"Xueqiu API error {err_code}: {data.get('error_description','')}")
    return data


def get_stock_5min(code: str, bars: int = 48) -> list[dict]:
    """
    获取个股 5 分钟 K 线（雪球）。
    code: "002192" 或 "600519"
    返回归一化后的标准格式 [{"time":"0935","open":...,"close":...,...}, ...]
    """
    cookies = _load_cookies()
    if not cookies:
        return []

    prefix = "SH" if code.startswith(("6", "9")) else "SZ"
    symbol = f"{prefix}{code}"

    end_ts = int(time.time() * 1000)
    begin_ts = end_ts - 14 * 86400 * 1000

    path = (f"/v5/stock/chart/kline.json?symbol={symbol}"
            f"&begin={begin_ts}&period=5m&type=before&count=-{min(bars, 96)}"
            f"&indicator=kline")

    try:
        data = _fetch_xq(path, cookies, timeout=10)
    except Exception as e:
        print(f"  ⚠️ 雪球 5min K线 {symbol} 失败 ({type(e).__name__})", file=sys.stderr)
        return []

    items = data.get("data", {}).get("item", [])
    return _normalize_bars(items, bars)


def get_daily_kline(code: str, days: int = 20) -> list[dict]:
    """
    获取日 K 线（雪球，备用）。
    code: "002192" 或 "600519"
    返回: [{"date":"2026-04-28","open":...,"close":...,...}, ...]
    """
    cookies = _load_cookies()
    if not cookies:
        return []

    prefix = "SH" if code.startswith(("6", "9")) else "SZ"
    symbol = f"{prefix}{code}"

    end_ts = int(time.time() * 1000)
    begin_ts = end_ts - (days + 14) * 86400 * 1000

    path = (f"/v5/stock/chart/kline.json?symbol={symbol}"
            f"&begin={begin_ts}&period=day&type=before&count=-{days}"
            f"&indicator=kline")

    try:
        data = _fetch_xq(path, cookies, timeout=10)
    except Exception as e:
        print(f"  ⚠️ 雪球日K线 {symbol} 失败 ({type(e).__name__})", file=sys.stderr)
        return []

    items = data.get("data", {}).get("item", [])
    results = []
    pre_close = None
    for item in items:
        ts = item[0] / 1000
        date_str = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
        open_p = float(item[2])
        close_p = float(item[5])
        high_p = float(item[3])
        low_p = float(item[4])
        vol = float(item[1])
        amt = float(item[9]) if len(item) > 9 else 0
        pct = (close_p - pre_close) / pre_close * 100 if pre_close else 0
        results.append({
            "date": date_str,
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


def get_cookie_info() -> Optional[dict]:
    cookies = _load_cookies()
    if not cookies:
        return None
    valid = _validate_cookies(cookies)
    expire_days = None
    if valid:
        expire_days = _token_expire_days(cookies)
    return {"valid": valid, "expire_days": expire_days, "file": COOKIE_FILE}


def _token_expire_days(cookies: str) -> Optional[int]:
    import re
    match = re.search(r"xq_id_token=([^\s;]+)", cookies)
    if not match:
        return None
    token = match.group(1)
    try:
        payload = token.split(".")[1]
        payload += "=" * (4 - len(payload) % 4)
        import base64
        decoded = json.loads(base64.urlsafe_b64decode(payload))
        exp_ts = decoded.get("exp", 0)
        if exp_ts:
            return int((exp_ts - time.time()) / 86400)
    except Exception:
        pass
    return None


# ─── 数据归一化 ────────────────────────────────────────

def _normalize_bars(items: list, max_bars: int) -> list[dict]:
    """
    将雪球原始 K 线数据归一化为标准格式。
    雪球原始: [timestamp_ms, volume, open, high, low, close, chg, pct, turnover, amount, ...]
    标准输出: {"time": "0935", "open": ..., "close": ..., "high": ..., "low": ..., "volume": ..., "amount": ...}
    """
    results = []
    for item in items:
        try:
            ts = datetime.fromtimestamp(item[0] / 1000)
            time_str = ts.strftime("%H%M")
            results.append({
                "time": time_str,
                "open": float(item[2]),
                "high": float(item[3]),
                "low": float(item[4]),
                "close": float(item[5]),
                "volume": float(item[1]),
                "amount": float(item[9]) if len(item) > 9 else 0,
            })
        except (IndexError, ValueError, TypeError, OSError):
            continue

    if len(results) > max_bars:
        results = results[-max_bars:]
    return results
