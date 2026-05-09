#!/usr/bin/env python3
"""
雪球 Cookie 刷新工具

用法:
    python3 scripts/xq_cookie_refresh.py --manual "<cookie_string>"
    python3 scripts/xq_cookie_refresh.py --playwright
    python3 scripts/xq_cookie_refresh.py --status

--manual:   将用户提供的 cookie 字符串写入 ~/.lsa_xq_cookies
--playwright: 启动 headless Chromium，自动获取 cookie（需先安装 playwright）
--status:   检查当前 cookie 有效性
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime
from typing import Optional

COOKIE_FILE = os.path.expanduser("~/.lsa_xq_cookies")


def _save_cookies(cookie_str: str) -> None:
    os.makedirs(os.path.dirname(COOKIE_FILE), exist_ok=True)
    with open(COOKIE_FILE, "w") as f:
        f.write(cookie_str.strip())
    print(f"✅ Cookie 已写入 {COOKIE_FILE}", file=sys.stderr)


def _validate_cookies(cookies: str) -> bool:
    import urllib.request
    try:
        url = "https://stock.xueqiu.com/v5/stock/quote.json?symbol=SZ000001&extend=detail"
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) AppleWebKit/537.36",
            "Referer": "https://xueqiu.com/",
            "Sec-Fetch-Mode": "cors",
            "Cookie": cookies,
        })
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        name = data.get("data", {}).get("quote", {}).get("name")
        return bool(name)
    except Exception:
        return False


def _token_expire_days(cookies: str) -> Optional[int]:
    import re
    import base64
    match = re.search(r"xq_id_token=([^\s;]+)", cookies)
    if not match:
        return None
    token = match.group(1)
    try:
        payload = token.split(".")[1]
        payload += "=" * (4 - len(payload) % 4)
        decoded = json.loads(base64.urlsafe_b64decode(payload))
        exp_ts = decoded.get("exp", 0)
        if exp_ts:
            return int((exp_ts - time.time()) / 86400)
    except Exception:
        pass
    return None


def cmd_manual(cookie_str: str) -> None:
    _save_cookies(cookie_str)
    if _validate_cookies(cookie_str):
        days = _token_expire_days(cookie_str)
        info = f"（{days}天后过期）" if days else ""
        print(f"✅ Cookie 验证通过{info}", file=sys.stderr)
    else:
        print("⚠️ Cookie 写入成功但验证失败，可能格式不对或已过期", file=sys.stderr)


def cmd_playwright() -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("❌ 未安装 playwright。请执行：pip install playwright && playwright install chromium",
              file=sys.stderr)
        sys.exit(1)

    print("🚀 启动 headless Chromium...", file=sys.stderr)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            page.goto("https://xueqiu.com/", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(5000)
            all_cookies = page.context.cookies()
        finally:
            browser.close()

    relevant = [c for c in all_cookies if c["name"] in (
        "xq_a_token", "xq_r_token", "xq_id_token", "xq_is_login",
        "xqat", "u", "cookiesu", "device_id", "s", "is_overseas",
    )]

    cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in all_cookies
                           if c["name"] in [r["name"] for r in relevant])

    if not cookie_str:
        print("❌ 未获取到有效 cookie（页面可能被反爬拦截）", file=sys.stderr)
        sys.exit(1)

    _save_cookies(cookie_str)
    if _validate_cookies(cookie_str):
        days = _token_expire_days(cookie_str)
        info = f"（{days}天后过期）" if days else ""
        print(f"✅ Cookie 自动获取成功并验证通过{info}", file=sys.stderr)
    else:
        print("⚠️ Cookie 已获取但 API 验证失败（可能雪球限制了未登录访客）", file=sys.stderr)
        print("  提示：请先在浏览器中登录雪球，再用 --manual 方式提供 cookie", file=sys.stderr)


def cmd_status() -> None:
    try:
        with open(COOKIE_FILE) as f:
            cookies = f.read().strip()
    except FileNotFoundError:
        print("❌ 未找到 cookie 文件 (~/.lsa_xq_cookies)", file=sys.stderr)
        sys.exit(1)

    if not cookies:
        print("❌ Cookie 文件为空", file=sys.stderr)
        sys.exit(1)

    if _validate_cookies(cookies):
        days = _token_expire_days(cookies)
        expire_info = f"（{days}天后过期）" if days else "（过期时间未知）"
        print(f"✅ Cookie 有效{expire_info}", file=sys.stderr)
    else:
        print("❌ Cookie 已失效，需刷新", file=sys.stderr)
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="雪球 Cookie 刷新工具")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--manual", type=str, metavar="COOKIE_STR",
                       help="手动提供 cookie 字符串")
    group.add_argument("--playwright", action="store_true",
                       help="自动通过浏览器获取 cookie")
    group.add_argument("--status", action="store_true",
                       help="检查当前 cookie 状态")
    args = parser.parse_args()

    if args.manual:
        cmd_manual(args.manual)
    elif args.playwright:
        cmd_playwright()
    elif args.status:
        cmd_status()


if __name__ == "__main__":
    main()
