"""
共享浏览器会话 — 东财接口 curl 失败后的 Playwright 兜底通道

用法:
  from dragon_quant.providers.browser import get_browser, close_browser
  b = get_browser()           # headless, 懒启动
  text = b.fetch_jsonp(url, referer)  # 返回 JSONP 原始文本
  close_browser()             # 扫描结束释放

设计:
  - 所有 Playwright 操作跑在专用后台线程，避免 greenlet 线程切换问题
  - 使用 Playwright APIRequestContext 发请求（浏览器 HTTP 栈，无 CORS 限制）
  - 按目标域名注入对应 Cookie：push2his → get_em_his()，其余 → get_em()
  - 首次 fetch 时自动启动浏览器并导航到东财主站
"""

import atexit
import queue
import threading
import time
import urllib.parse
from typing import Optional

# 与 eastmoney.py / cookie.py 统一的 UA（Chrome 148）
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/148.0.0.0 Safari/537.36"
)


class _WorkItem:
    __slots__ = ("fn", "args", "result_q")

    def __init__(self, fn, args, result_q):
        self.fn = fn
        self.args = args
        self.result_q = result_q


class BrowserSession:
    def __init__(self, headless: bool = True):
        self._headless = headless
        self._started = False
        self._page = None
        self._pw = None

        self._req_q: queue.Queue = queue.Queue()
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def _exec(self, fn, *args):
        """在浏览器线程上同步执行函数"""
        result_q: queue.Queue = queue.Queue()
        self._req_q.put(_WorkItem(fn, args, result_q))
        result = result_q.get(timeout=30)
        if isinstance(result, Exception):
            raise result
        return result

    # ─── 公共 API（线程安全，通过 _exec 派发） ───

    def ensure(self):
        self._exec(self._ensure_impl)

    def fetch_jsonp(self, url: str, referer: str) -> Optional[str]:
        for attempt in range(2):
            self.ensure()
            try:
                result = self._exec(self._do_fetch, url, referer)
                if result is not None:
                    return result
            except Exception as e:
                print(f"  ⚠️ 浏览器请求失败: {e}")
                # 浏览器可能崩溃，标记后重试
                try:
                    self._exec(self._cleanup_impl)
                except Exception:
                    self._started = False
        return None

    def render_text(self, url: str, wait_selector: str = "",
                    timeout_ms: int = 15000) -> Optional[str]:
        """导航到 url，渲染后返回页面 HTML（page.content()）。

        wait_selector 非空时等待该选择器出现（JS 动态内容填充完成）。
        用于同花顺概念排行等涨跌幅由 JS 填充的页面。
        """
        for attempt in range(2):
            self.ensure()
            try:
                result = self._exec(self._do_render, url, wait_selector, timeout_ms)
                if result is not None:
                    return result
            except Exception as e:
                print(f"  ⚠️ 浏览器渲染失败: {e}")
                try:
                    self._exec(self._cleanup_impl)
                except Exception:
                    self._started = False
        return None

    def close(self):
        try:
            self._exec(self._cleanup_impl)
        except Exception:
            pass
        self._req_q.put(None)  # 哨兵，终止 worker 线程
        self._started = False

    @property
    def is_started(self) -> bool:
        return self._started

    # ─── 以下方法仅在浏览器线程执行 ───

    def _worker(self):
        """专用线程：所有 Playwright 操作在此执行"""
        while True:
            item = self._req_q.get()
            if item is None:
                break
            try:
                result = item.fn(*item.args)
                item.result_q.put(result)
            except Exception as e:
                item.result_q.put(e)

    def _ensure_impl(self):
        if self._started:
            return
        from playwright.sync_api import sync_playwright

        playwright = sync_playwright().start()
        try:
            browser = playwright.chromium.launch(headless=self._headless)
        except Exception:
            playwright.stop()
            # 浏览器二进制不可用（段错误/未安装/版本不匹配）
            # 全局标记为不可用，避免后续重复尝试
            global _PLAYWRIGHT_AVAILABLE
            _PLAYWRIGHT_AVAILABLE = False
            raise

        context = browser.new_context(
            user_agent=UA,
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
        )
        self._page = context.new_page()
        self._pw = playwright

        # 注入东财两个域的 Cookie（push2 / push2his 各一份，均落到 .eastmoney.com）
        try:
            from dragon_quant.providers.cookie import get_em, get_em_his
            cookies = []
            for cookie_str in (get_em(), get_em_his()):
                if not cookie_str:
                    continue
                for part in cookie_str.split(";"):
                    part = part.strip()
                    if "=" in part:
                        name, value = part.split("=", 1)
                        cookies.append({
                            "name": name.strip(),
                            "value": value.strip(),
                            "domain": ".eastmoney.com",
                            "path": "/",
                        })
            if cookies:
                context.add_cookies(cookies)
        except Exception:
            pass

        self._started = True

    def _do_fetch(self, url: str, referer: str) -> Optional[str]:
        """用浏览器 HTTP 栈发送请求（不受 CORS 限制）。

        使用 Playwright APIRequestContext 而非 page.evaluate() + JS fetch，
        前者在浏览器进程内使用 Chromium 的 HTTP 栈（TLS 指纹同 Chrome），
        但不受同源策略限制，适用于东财跨域 JSONP 请求。
        """
        api = self._page.context.request
        resp = api.fetch(
            url,
            headers={
                "Referer": referer,
                "User-Agent": UA,
                "Accept": "*/*",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "sec-ch-ua": '"Chromium";v="148", "Google Chrome";v="148", "Not/A)Brand";v="99"',
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": '"macOS"',
                "Sec-Fetch-Dest": "script",
                "Sec-Fetch-Mode": "no-cors",
                "Sec-Fetch-Site": "same-site",
            },
        )
        if resp.ok:
            return resp.text()
        return None

    def _do_render(self, url: str, wait_selector: str, timeout_ms: int) -> Optional[str]:
        """在浏览器线程导航并渲染页面，返回渲染后的 HTML。"""
        self._page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        if wait_selector:
            try:
                self._page.wait_for_selector(wait_selector, timeout=timeout_ms)
            except Exception:
                pass  # 选择器超时也返回当前内容，由上层解析容错
        return self._page.content()

    def _cleanup_impl(self):
        if not self._page:
            return
        try:
            context = self._page.context
            browser = context.browser
            self._page.close()
            context.close()
            browser.close()
        except Exception:
            pass
        try:
            if self._pw:
                self._pw.stop()
        except Exception:
            pass
        self._page = None
        self._pw = None
        self._started = False


# ─── 模块级单例 ───

_browser: Optional[BrowserSession] = None
_lock = threading.Lock()


def get_browser(headless: bool = True) -> BrowserSession:
    """获取全局共享的浏览器会话（懒启动）"""
    global _browser
    if _browser is None:
        with _lock:
            if _browser is None:
                _browser = BrowserSession(headless=headless)
    return _browser


def close_browser():
    """关闭并释放浏览器会话"""
    global _browser
    b, _browser = _browser, None
    if b is not None:
        b.close()


def is_available() -> bool:
    """检查 playwright 是否已安装"""
    return _PLAYWRIGHT_AVAILABLE


# ─── 模块级检查 ───

try:
    import playwright.sync_api  # noqa: F401
    _PLAYWRIGHT_AVAILABLE = True
except ImportError:
    _PLAYWRIGHT_AVAILABLE = False

# 进程退出时自动清理浏览器，防止僵尸进程
atexit.register(close_browser)
