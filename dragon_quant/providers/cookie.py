"""
Cookie 管理 — ~/.dragon-quant/cookies/{eastmoney,xueqiu}
支持手动设置 & 无头浏览器自动获取
"""

import os
from typing import Optional

from dragon_quant.storage.paths import COOKIE_DIR

EM_FILE = COOKIE_DIR / "eastmoney"
XQ_FILE = COOKIE_DIR / "xueqiu"


def _ensure():
    COOKIE_DIR.mkdir(parents=True, exist_ok=True)


def _data_dir():
    """向后兼容别名"""
    from dragon_quant.storage.paths import DATA_DIR
    return DATA_DIR

# ─── 读写 ───

def set_em(c: str):
    _ensure(); EM_FILE.write_text(c.strip())
    print(f"✅ 东财 Cookie -> {EM_FILE}")

def set_xq(c: str):
    _ensure(); XQ_FILE.write_text(c.strip())
    print(f"✅ 雪球 Cookie -> {XQ_FILE}")

def get_em() -> str:
    return EM_FILE.read_text().strip() if EM_FILE.exists() else ""

def get_xq() -> str:
    return XQ_FILE.read_text().strip() if XQ_FILE.exists() else ""

# ─── 浏览器自动获取 ───

def _browser_cookies(url: str) -> str:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        ctx = b.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
            locale="zh-CN", timezone_id="Asia/Shanghai")
        ctx.new_page().goto(url, wait_until="domcontentloaded")
        ctx.new_page().wait_for_timeout(5000)
        raw = ctx.cookies()
        b.close()
    if not raw:
        return ""
    return "; ".join(f"{c['name']}={c['value']}" for c in raw)

def fetch_em() -> str:
    c = _browser_cookies("https://quote.eastmoney.com/center/hsbk.html")
    if c: set_em(c); return c
    print("⚠️ 东财 Cookie 获取失败"); return ""

def fetch_xq() -> str:
    c = _browser_cookies("https://xueqiu.com/")
    if c: set_xq(c); return c
    print("⚠️ 雪球 Cookie 获取失败"); return ""

def fetch_all():
    fetch_em(); fetch_xq()

# ─── CLI ───

if __name__ == "__main__":
    import argparse
    a = argparse.ArgumentParser()
    a.add_argument("action", choices=["set","fetch","status"])
    a.add_argument("--source", choices=["em","xq","all"], default="all")
    a.add_argument("--cookie", "-c")
    a.add_argument("--show", action="store_true")
    args = a.parse_args()
    if args.action == "status":
        for k, v in [("东财",get_em()),("雪球",get_xq())]:
            print(f"{k}: {'✅' if v else '❌'} ({len(v)}字符)")
        if args.show:
            for k, v in [("东财",get_em()),("雪球",get_xq())]:
                if v: print(f"\n{k}: {v[:200]}...")
    elif args.action == "set" and args.cookie:
        if args.source in ("em","all"): set_em(args.cookie)
        if args.source in ("xq","all"): set_xq(args.cookie)
    elif args.action == "fetch":
        if args.source == "em": fetch_em()
        elif args.source == "xq": fetch_xq()
        else: fetch_all()
