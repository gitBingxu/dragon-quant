"""
Cookie 管理 — ~/.dragon-quant/cookies/{eastmoney,eastmoney_his,xueqiu}

东财两个不同域名分开存储 Cookie：
- eastmoney      → push2.eastmoney.com（板块排行 / 板块成分股）
- eastmoney_his  → push2his.eastmoney.com（板块5分K）

支持手动设置 & 无头浏览器自动获取。
"""

import os
import subprocess
from typing import Optional

from dragon_quant.storage.paths import COOKIE_DIR

EM_FILE = COOKIE_DIR / "eastmoney"          # push2 域
EM_HIS_FILE = COOKIE_DIR / "eastmoney_his"  # push2his 域
XQ_FILE = COOKIE_DIR / "xueqiu"

# 与 eastmoney.py / browser.py 统一的 UA（Chrome 148）
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/148.0.0.0 Safari/537.36"
)

# 过码探测必须带的完整反爬头（与 eastmoney.py 正式抓取链路一致）。
# 缺失这些头时东财 WAF 会直接拦截/返回空，导致过码后仍探测不到 rc:0。
_PROBE_HEADERS = {
    "Accept": "*/*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "User-Agent": UA,
    "sec-ch-ua": '"Chromium";v="148", "Google Chrome";v="148", "Not/A)Brand";v="99"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"macOS"',
    "Sec-Fetch-Dest": "script",
    "Sec-Fetch-Mode": "no-cors",
    "Sec-Fetch-Site": "same-site",
}


def _ensure():
    COOKIE_DIR.mkdir(parents=True, exist_ok=True)


def _data_dir():
    """向后兼容别名"""
    from dragon_quant.storage.paths import DATA_DIR
    return DATA_DIR

# ─── 读写 ───

def set_em(c: str):
    _ensure(); EM_FILE.write_text(c.strip())
    print(f"✅ 东财(push2) Cookie -> {EM_FILE}")

def set_em_his(c: str):
    _ensure(); EM_HIS_FILE.write_text(c.strip())
    print(f"✅ 东财(push2his) Cookie -> {EM_HIS_FILE}")

def set_xq(c: str):
    _ensure(); XQ_FILE.write_text(c.strip())
    print(f"✅ 雪球 Cookie -> {XQ_FILE}")

def get_em() -> str:
    return EM_FILE.read_text().strip() if EM_FILE.exists() else ""

def get_em_his() -> str:
    return EM_HIS_FILE.read_text().strip() if EM_HIS_FILE.exists() else ""

def get_xq() -> str:
    return XQ_FILE.read_text().strip() if XQ_FILE.exists() else ""

# ─── 浏览器自动获取 ───

# 隐藏自动化特征（降低被风控触发验证的概率）
_STEALTH_JS = "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"

# 常见滑块手柄选择器（按优先级尝试，覆盖东财/通用验证组件）
_SLIDER_SELECTORS = [
    ".nc_iconfont.btn_slide",      # 阿里云盾
    ".btn_slide",
    ".slider-btn",
    ".slide-btn",
    ".verify-move-block",          # verify.js
    "[class*='slider'][class*='btn']",
    "[class*='handler']",
    "[class*='slide'][class*='button']",
]


def _try_auto_slider(page) -> bool:
    """模拟人类拖拽轨迹自动过滑块。

    检测到滑块手柄后用加速-减速 + y 抖动 + 随机间隔拖到轨道末端。
    任何异常都吞掉返回 False，由人工兜底。返回是否检测到并尝试拖动过滑块。
    """
    import random, time
    try:
        handle = None
        for sel in _SLIDER_SELECTORS:
            try:
                el = page.query_selector(sel)
                if el and el.is_visible():
                    handle = el
                    break
            except Exception:
                continue
        if handle is None:
            return False  # 未出现滑块

        box = handle.bounding_box()
        if not box:
            return False

        # 估算拖拽距离：优先用轨道宽度，否则用一个较大默认值
        distance = 300
        for track_sel in ("[class*='track']", "[class*='slide']", "[class*='verify']"):
            try:
                track = page.query_selector(track_sel)
                if track:
                    tb = track.bounding_box()
                    if tb and tb["width"] > box["width"]:
                        distance = tb["width"] - box["width"] - 2
                        break
            except Exception:
                continue

        start_x = box["x"] + box["width"] / 2
        start_y = box["y"] + box["height"] / 2

        print("🤖 检测到滑块，尝试自动拖拽...")
        page.mouse.move(start_x, start_y)
        page.mouse.down()
        time.sleep(random.uniform(0.1, 0.25))

        # 加速-减速轨迹：前 70% 加速，后 30% 减速
        steps = random.randint(25, 35)
        moved = 0.0
        for i in range(steps):
            ratio = (i + 1) / steps
            if ratio < 0.7:
                seg = distance * (ratio ** 2) - moved
            else:
                seg = distance * (1 - (1 - ratio) ** 2) - moved
            moved += seg
            jitter_y = random.uniform(-2, 2)
            page.mouse.move(start_x + moved, start_y + jitter_y)
            time.sleep(random.uniform(0.01, 0.04))

        # 拖到末端并轻微回拉，更像真人
        page.mouse.move(start_x + distance, start_y)
        time.sleep(random.uniform(0.1, 0.2))
        page.mouse.up()
        time.sleep(random.uniform(1.0, 1.8))
        return True
    except Exception as e:
        print(f"⚠️ 自动滑块失败，请手动完成: {e}")
        return False


def _slider_gone(page) -> bool:
    """所有已知滑块手柄都不存在/不可见时视为过码（兜底判定）。"""
    try:
        for sel in _SLIDER_SELECTORS:
            try:
                el = page.query_selector(sel)
                if el and el.is_visible():
                    return False
            except Exception:
                continue
        return True
    except Exception:
        return False


def _browser_cookies(url: str, probe_url: str,
                     headless: bool = True, auto_slider: bool = False) -> str:
    """打开页面，（可选自动过滑块/人工过滑块），提取 Cookie。

    probe_url 用于检测验证是否通过：用浏览器 HTTP 栈请求该接口（不受 CORS 限制，
        与页面共享 Cookie），返回含有效数据（"data" 且 rc=0）视为通过。
    headless: True 无界面，False 显示窗口。
    auto_slider: True 时先尝试模拟拖拽自动过滑块，失败回退人工。
    """
    from playwright.sync_api import sync_playwright
    import time
    with sync_playwright() as p:
        b = p.chromium.launch(
            headless=headless,
            args=["--disable-blink-features=AutomationControlled"],
        )
        ctx = b.new_context(
            user_agent=UA,
            locale="zh-CN", timezone_id="Asia/Shanghai")
        ctx.add_init_script(_STEALTH_JS)
        page = ctx.new_page()
        page.goto(url, wait_until="domcontentloaded")

        if auto_slider:
            page.wait_for_timeout(1500)  # 等滑块渲染
            _try_auto_slider(page)

        if not probe_url:
            # 无需 probe 验证（如雪球）：加载后短暂等待直接取 Cookie
            page.wait_for_timeout(2000)
        else:
            if headless:
                print(f"\n⏳ 正在检测页面状态... (最长等待 120 秒)")
            else:
                print(f"\n⏳ 正在检测页面状态，如自动过码失败请手动完成拼图... (最长等待 120 秒)")

            start_time = time.time()
            success = False
            while time.time() - start_time < 120:
                # 用 curl + 当前浏览器 Cookie 探测（与正式抓取链路一致，
                # 不像 ctx.request 会被 push2his WAF 直接 socket hang up）。
                # 未过码时东财返回空响应/被拦截，过码后返回 rc:0 + data。
                try:
                    cur_cookie = "; ".join(
                        f"{c['name']}={c['value']}" for c in ctx.cookies()
                    )
                    cmd = ["curl", "-s", "--max-time", "8", "-b", cur_cookie,
                           "-H", f"Referer: {url}"]
                    for hk, hv in _PROBE_HEADERS.items():
                        cmd += ["-H", f"{hk}: {hv}"]
                    cmd.append(probe_url)
                    r = subprocess.run(
                        cmd, capture_output=True, text=True, timeout=12,
                    )
                    text = r.stdout or ""
                    if '"data":' in text and '"rc":0' in text:
                        success = True
                        break
                except Exception:
                    pass
                # 兜底：接口探测偶发失败时，若滑块手柄已消失也判定过码
                if auto_slider and _slider_gone(page):
                    success = True
                    break
                time.sleep(1.5)

            if success:
                print("✅ 智能检测到验证已通过！自动提取 Cookie...")
                if auto_slider:
                    # 东财：过滑块后等 3 秒刷新页面，让服务端下发完整鉴权 Cookie
                    page.wait_for_timeout(3000)
                    try:
                        page.reload(wait_until="domcontentloaded")
                        page.wait_for_timeout(1000)
                    except Exception:
                        pass
            else:
                print("⚠️ 等待超时（120秒内未检测到验证通过），尝试提取现有 Cookie...")

        page.wait_for_timeout(1000)
        raw = ctx.cookies()
        b.close()
    if not raw:
        return ""
    return "; ".join(f"{c['name']}={c['value']}" for c in raw)

def fetch_em() -> str:
    """获取 push2 域 Cookie（板块排行 / 成分股）— 有界面 + 自动滑块"""
    c = _browser_cookies(
        "https://quote.eastmoney.com/center/gridlist.html",
        "https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=10&po=1&np=1&fltt=2&invt=2&fid=f3&fs=m:90+t:3&fields=f12,f14",
        headless=False, auto_slider=True,
    )
    if c: set_em(c); return c
    print("⚠️ 东财(push2) Cookie 获取失败"); return ""

def fetch_em_his() -> str:
    """获取 push2his 域 Cookie（板块5分K）— 有界面 + 自动滑块"""
    c = _browser_cookies(
        "https://quote.eastmoney.com/bk/90.BK1011.html",
        "https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=90.BK1011&fields1=f1&fields2=f51,f53&klt=5&fqt=1&end=20500101&lmt=10",
        headless=False, auto_slider=True,
    )
    if c: set_em_his(c); return c
    print("⚠️ 东财(push2his) Cookie 获取失败"); return ""

def fetch_xq() -> str:
    """获取雪球 Cookie — headless 无界面（首页无需 probe 验证）"""
    c = _browser_cookies("https://xueqiu.com/", "", headless=True)
    if c: set_xq(c); return c
    print("⚠️ 雪球 Cookie 获取失败"); return ""

def fetch_all():
    fetch_em(); fetch_em_his(); fetch_xq()

# ─── CLI ───

if __name__ == "__main__":
    import argparse
    a = argparse.ArgumentParser()
    a.add_argument("action", choices=["set","fetch","status"])
    a.add_argument("--source", choices=["em","em_his","xq","all"], default="all")
    a.add_argument("--cookie", "-c")
    a.add_argument("--show", action="store_true")
    args = a.parse_args()
    if args.action == "status":
        items = [("东财(push2)",get_em()),("东财(push2his)",get_em_his()),("雪球",get_xq())]
        for k, v in items:
            print(f"{k}: {'✅' if v else '❌'} ({len(v)}字符)")
        if args.show:
            for k, v in items:
                if v: print(f"\n{k}: {v[:200]}...")
    elif args.action == "set" and args.cookie:
        if args.source in ("em","all"): set_em(args.cookie)
        if args.source in ("em_his","all"): set_em_his(args.cookie)
        if args.source in ("xq","all"): set_xq(args.cookie)
    elif args.action == "fetch":
        if args.source == "em": fetch_em()
        elif args.source == "em_his": fetch_em_his()
        elif args.source == "xq": fetch_xq()
        else: fetch_all()
