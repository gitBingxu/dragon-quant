"""
Cookie 管理 — 雪球 Cookie 存取 + 无头浏览器自动获取。

支持手动设置 & 无头浏览器自动获取。
"""

import os
import subprocess
import sys

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
    _ensure()
    XQ_FILE.write_text(c.strip())
    print(f"✅ 雪球 Cookie -> {XQ_FILE}")


def get_xq() -> str:
    return XQ_FILE.read_text().strip() if XQ_FILE.exists() else ""


# ─── 浏览器自动获取 ───

# 隐藏自动化特征（降低被风控触发验证的概率）
_STEALTH_JS = "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"

# 国内镜像，加速 chromium 下载（阿里 npmmirror）
_PLAYWRIGHT_MIRROR = "https://npmmirror.com/mirrors/playwright/"


def _launch_chromium(p, headless: bool):
    """启动 chromium；内核缺失/版本不匹配时自动下载后重试一次。"""
    args = ["--disable-blink-features=AutomationControlled"]
    try:
        return p.chromium.launch(headless=headless, args=args)
    except Exception as e:
        msg = str(e)
        if "Executable doesn't exist" not in msg and "playwright install" not in msg:
            raise  # 非内核缺失问题，原样抛出
        print("🔧 首次使用，正在下载 Chromium 内核（约 150MB，仅此一次）…", file=sys.stderr)
        env = dict(os.environ)
        env.setdefault("PLAYWRIGHT_DOWNLOAD_HOST", _PLAYWRIGHT_MIRROR)
        r = subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            env=env,
        )
        if r.returncode != 0:
            raise RuntimeError(
                "Chromium 内核自动下载失败。\n"
                "  请手动执行：playwright install chromium\n"
                "  国内网络先设镜像：PLAYWRIGHT_DOWNLOAD_HOST="
                "https://npmmirror.com/mirrors/playwright/ playwright install chromium"
            )
        try:
            return p.chromium.launch(headless=headless, args=args)
        except Exception as e2:
            raise RuntimeError(
                f"Chromium 启动失败：{e2}\n"
                "  请确认已正确安装：playwright install chromium"
            )


def _browser_cookies(url: str, headless: bool = True) -> str:
    """打开页面并提取 Cookie。

    headless: True 无界面，False 显示窗口。
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise RuntimeError(
            "未安装 playwright（自动获取 Cookie 依赖无头浏览器）。\n"
            "  请先执行：pip install playwright && playwright install chromium\n"
            "  或手动设置 Cookie：dragon-quant data cookie-set --cookie \"xq_a_token=...; u=...\""
        )
    with sync_playwright() as p:
        b = _launch_chromium(p, headless)
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
    return "; ".join(f"{c['name']}={c['value']}" for c in raw)  # type: ignore[index]


def fetch_xq() -> str:
    """获取雪球 Cookie — headless 无界面（首页无需验证）"""
    try:
        c = _browser_cookies("https://xueqiu.com/", headless=True)
    except RuntimeError as e:
        print(f"❌ {e}", file=sys.stderr)
        return ""
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
