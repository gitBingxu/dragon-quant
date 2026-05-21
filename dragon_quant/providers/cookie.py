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
    from playwright.sync_api import sync_playwright, TimeoutError
    import time
    with sync_playwright() as p:
        b = p.chromium.launch(headless=False)
        ctx = b.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
            locale="zh-CN", timezone_id="Asia/Shanghai")
        page = ctx.new_page()
        page.goto(url, wait_until="domcontentloaded")
        
        print(f"\n⏳ 正在检测页面状态，如遇滑块请手动完成拼图... (最长等待 60 秒)")
        
        # 轮询检测：我们通过判断页面是否能正常发起 JSONP 数据请求来确认验证是否通过
        # 如果能正常拿到数据（或者特定 DOM 恢复），说明滑块过了
        start_time = time.time()
        success = False
        
        while time.time() - start_time < 60:
            # 东财中心页如果正常，通常会有 class 为 listview 的表格或者 id 为 table_wrapper 的元素
            # 但最直接的是，如果滑块存在，页面会被遮挡。
            # 我们直接向页面注入 JS，尝试获取一个东财的 API，如果返回正常 JSON/JSONP，说明 Cookie 已通
            try:
                # 尝试通过 fetch 请求一个必须带有有效 cookie 才能访问的接口
                # 比如板块排行的接口
                is_valid = page.evaluate("""() => {
                    return new Promise((resolve) => {
                        const ctrl = new AbortController();
                        const tid = setTimeout(() => { ctrl.abort(); resolve(false); }, 3000);
                        fetch('https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=10&po=1&np=1&fltt=2&invt=2&fid=f3&fs=m:90+t:2&fields=f12,f14', { signal: ctrl.signal })
                        .then(r => r.text())
                        .then(t => { clearTimeout(tid); resolve(t.includes('"data":')); })
                        .catch(() => { clearTimeout(tid); resolve(false); });
                    });
                }""")
                
                if is_valid:
                    success = True
                    break
            except Exception:
                pass
            
            time.sleep(1) # 每秒检查一次
            
        if success:
            print("✅ 智能检测到验证已通过！自动提取 Cookie...")
        else:
            print("⚠️ 等待超时或未检测到验证通过信号，尝试提取现有 Cookie...")
            
        page.wait_for_timeout(1000) # 稍微等一下让 Cookie 稳固
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
