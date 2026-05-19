"""
共享浏览器会话 — 东财接口 playwright 兜底通道

用法:
  from dragon_quant.providers.browser import get_browser, close_browser
  b = get_browser()           # headless, 懒启动
  text = b.fetch_jsonp(url, referer)  # 返回 JSONP 原始文本
  close_browser()             # 扫描结束释放

设计:
  - 所有 Playwright 操作跑在专用后台线程，避免 greenlet 线程切换问题
  - headless=True 默认静默（数据获取），cookie 获取用 headless=False
  - 首次 fetch 时自动启动浏览器并导航到东财主站建立 session cookie
  - 预热：前 2-3 次 JS fetch 会失败，启动时自动发哑请求预热
"""

import atexit
import queue
import threading
import time
from typing import Optional


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
        browser = playwright.chromium.launch(headless=self._headless)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/147.0.0.0 Safari/537.36"
            ),
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
        )
        self._page = context.new_page()
        self._pw = playwright
        self._page.goto(
            "https://quote.eastmoney.com/center/hsbk.html",
            wait_until="domcontentloaded",
            timeout=30000,
        )

        # 预热：前几次 fetch 会失败，先发哑请求让浏览器进入工作状态
        warm_url = (
            "https://push2.eastmoney.com/api/qt/clist/get?"
            "pn=1&pz=1&po=1&np=1&fltt=2&invt=2&fid=f3"
            "&fs=m:90+t:3&fields=f12,f14"
            "&ut=fa5fd1943c7b386f172d6893dbfba10b&cb=jQuery_dq"
        )
        warm_ref = "https://quote.eastmoney.com/center/hsbk.html"
        for _ in range(3):
            try:
                self._page.evaluate(
                    """([url, referer]) => fetch(url, {
                        headers: { 'Referer': referer },
                        credentials: 'include'
                    }).then(r => r.text())""",
                    arg=[warm_url, warm_ref],
                )
            except Exception:
                pass
            time.sleep(0.1)

        self._started = True

    def _do_fetch(self, url: str, referer: str) -> Optional[str]:
        text = self._page.evaluate(
            """([url, referer]) => fetch(url, {
                headers: { 'Referer': referer },
                credentials: 'include'
            }).then(r => {
                if (!r.ok) throw new Error('HTTP ' + r.status);
                return r.text();
            })""",
            arg=[url, referer],
        )
        return text

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
