"""uvicorn 进程生命周期：预绑定 socket + 编程式 ``Server.serve``。

预绑定让 ``EADDRINUSE`` 在本项目代码内同步抛出（保持既有 ``OSError`` 语义），
``port=0`` 在启动前就能拿到真实端口；信号由本项目安装的处理器转成
``server.should_exit``，SIGTERM 仍以非零退出码结束（E2E W8 冻结语义）。
"""

from __future__ import annotations

import asyncio
import socket
import threading
import webbrowser
from dataclasses import dataclass, field
from typing import Any

from .settings import DashboardSettings

STARTUP_TIMEOUT_S = 10.0


@dataclass
class DashboardHandle:
    """已绑定端口的仪表盘句柄（``block=False`` 时由调用方 stop）。"""

    port: int
    _server: Any = field(default=None, repr=False)
    _thread: threading.Thread | None = field(default=None, repr=False)
    _sock: socket.socket | None = field(default=None, repr=False)

    @property
    def server_address(self) -> tuple[str, int]:
        return ("127.0.0.1", self.port)

    def stop(self, timeout: float = 5.0) -> None:
        server = self._server
        if server is not None:
            server.should_exit = True
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)
        self._close_socket()

    def _close_socket(self) -> None:
        sock = self._sock
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
            self._sock = None


def _bind_socket(port: int) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(("127.0.0.1", int(port)))
        sock.listen(128)
    except OSError:
        try:
            sock.close()
        finally:
            pass
        raise
    return sock


async def _serve(handle: DashboardHandle, settings: DashboardSettings, url: str,
                  open_browser: bool) -> int | None:
    import contextlib
    import signal

    import uvicorn

    from .app import create_app

    class _Server(uvicorn.Server):
        """信号由本项目处理：保留 SIGTERM 的非零退出码契约。"""

        @contextlib.contextmanager
        def capture_signals(self):  # type: ignore[override]
            yield

    app = create_app(settings)
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=handle.port,
        log_level="warning",
        access_log=False,
        lifespan="off",
    )
    server = _Server(config)
    handle._server = server

    loop = asyncio.get_running_loop()
    received: dict[str, int | None] = {"signal": None}
    installed: list[int] = []
    if threading.current_thread() is threading.main_thread():
        def _handler(signum: int) -> None:
            received["signal"] = signum
            server.should_exit = True

        for signum in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(signum, _handler, signum)
                installed.append(signum)
            except (NotImplementedError, RuntimeError, ValueError):
                pass

    task = asyncio.create_task(server.serve(sockets=[handle._sock]))
    try:
        deadline = loop.time() + STARTUP_TIMEOUT_S
        while not server.started:
            if task.done():
                task.result()
                raise RuntimeError("uvicorn 未进入 started 状态")
            if loop.time() > deadline:
                server.should_exit = True
                raise RuntimeError("uvicorn 启动超时（10s）")
            await asyncio.sleep(0.02)
        print(f"🐉 Review Web UI 已启动 → {url}", flush=True)
        print("   按 Ctrl+C 停止服务", flush=True)
        if open_browser:
            try:
                await asyncio.to_thread(webbrowser.open, url)
            except Exception:  # noqa: BLE001 - 打开浏览器失败不影响服务
                pass
        try:
            await task
        except asyncio.CancelledError:
            pass
    finally:
        for signum in installed:
            try:
                loop.remove_signal_handler(signum)
            except (NotImplementedError, RuntimeError, ValueError):
                pass
    return received["signal"]


def _run(handle: DashboardHandle, settings: DashboardSettings, url: str,
         open_browser: bool) -> None:
    try:
        received = asyncio.run(_serve(handle, settings, url, open_browser))
    except KeyboardInterrupt:
        received = None
    finally:
        handle._close_socket()
    print("\n👋 服务器已停止", flush=True)
    import signal

    if received == signal.SIGTERM:
        raise SystemExit(128 + int(signal.SIGTERM))


def start_dashboard(port: int = 8765, open_browser: bool = True,
                    default_source: str | None = None,
                    default_page: str | None = None, *,
                    settings: DashboardSettings | None = None,
                    block: bool = True) -> DashboardHandle:
    """启动 127.0.0.1 仪表盘。

    ``port=0`` 由 OS 分配，URL 永远不会出现 ``:0``；显式端口被占用时
    ``bind()`` 同步抛 ``OSError``（由调用方决定是否重试 / 退出）。
    ``block=False`` 在线程中运行，**不接管进程信号**，调用方负责 ``stop()``。
    """
    if settings is None:
        settings = DashboardSettings(
            default_source=default_source or "v2",
            default_page=default_page or "review",
        )

    sock = _bind_socket(port)
    actual_port = sock.getsockname()[1]
    handle = DashboardHandle(port=int(actual_port), _sock=sock)
    path = settings.landing_path()
    url = f"http://localhost:{actual_port}{path}?source={settings.normalized_source()}"

    if not block:
        thread = threading.Thread(
            target=_run,
            args=(handle, settings, url, open_browser),
            name="dragon-quant-dashboard",
            daemon=True,
        )
        handle._thread = thread
        thread.start()
        return handle

    _run(handle, settings, url, open_browser)
    return handle


def is_addr_in_use(exc: BaseException) -> bool:
    """端口占用判定（POSIX ``EADDRINUSE`` / Windows ``winerror=10048``）。"""
    import errno

    return (
        getattr(exc, "errno", None) == errno.EADDRINUSE
        or getattr(exc, "winerror", None) == 10048
    )
