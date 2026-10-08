"""
Cookie 管理 — 雪球 Cookie 存取 + 无头浏览器自动获取。

支持手动设置 & 无头浏览器自动获取。
"""

from dragon_quant.storage.paths import COOKIE_DIR

XQ_FILE = COOKIE_DIR / "xueqiu"

# 浏览器 UA（Chrome 148）
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/148.0.0.0 Safari/537.36"
)


def _ensure():
    COOKIE_DIR.mkdir(parents=True, exist_ok=True)


def _data_dir():
    """向后兼容别名"""
    from dragon_quant.storage.paths import DATA_DIR
    return DATA_DIR


# ─── 读写 ───

def set_xq(c: str):
    _ensure(); XQ_FILE.write_text(c.strip())
    print(f"✅ 雪球 Cookie -> {XQ_FILE}")


def get_xq() -> str:
    return XQ_FILE.read_text().strip() if XQ_FILE.exists() else ""


# ─── 浏览器自动获取 ───

# 隐藏自动化特征（降低被风控触发验证的概率）
_STEALTH_JS = "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"


def _browser_cookies(url: str, headless: bool = True) -> str:
    """打开页面并提取 Cookie。

    headless: True 无界面，False 显示窗口。
    """
    from playwright.sync_api import sync_playwright
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
        page.wait_for_timeout(3000)  # 等页面 JS 写入 Cookie
        raw = ctx.cookies()
        b.close()
    if not raw:
        return ""
    return "; ".join(f"{c['name']}={c['value']}" for c in raw)


def fetch_xq() -> str:
    """获取雪球 Cookie — headless 无界面（首页无需验证）"""
    c = _browser_cookies("https://xueqiu.com/", headless=True)
    if c:
        set_xq(c)
        return c
    print("⚠️ 雪球 Cookie 获取失败")
    return ""


# ─── CLI ───

if __name__ == "__main__":
    import argparse
    a = argparse.ArgumentParser()
    a.add_argument("action", choices=["set", "fetch", "status"])
    a.add_argument("--source", choices=["xq"], default="xq")
    a.add_argument("--cookie", "-c")
    a.add_argument("--show", action="store_true")
    args = a.parse_args()
    if args.action == "status":
        items = [("雪球", get_xq())]
        for k, v in items:
            print(f"{k}: {'✅' if v else '❌'} ({len(v)}字符)")
        if args.show:
            for k, v in items:
                if v:
                    print(f"\n{k}: {v[:200]}...")
    elif args.action == "set" and args.cookie:
        set_xq(args.cookie)
    elif args.action == "fetch":
        fetch_xq()
