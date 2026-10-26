"""M10A B2：``web_ui.server.start_server`` 生命周期契约。

覆盖范围（全部离线，仅绑定 127.0.0.1）：

* 默认 ``block=True`` 保持既有 CLI 阻塞行为，并在返回前关闭 server；
* ``block=False`` 返回已绑定的 ``HTTPServer``，调用方自行 serve/shutdown/close；
* ``port=0`` 时日志与浏览器 URL 使用 OS 实际分配的端口，绝不出现 0；
* 启动失败（端口被占用）、键盘中断、serve 循环异常都释放端口；
* 包根 ``web_ui.start_server`` 是同一公共入口；
* 生命周期不触碰数据库（``_get_db`` 零调用）。
"""

from __future__ import annotations

import http.client
import inspect
import socket
import subprocess
import sys
import threading
from pathlib import Path

import pytest

import web_ui
from web_ui import server as web_server

_ASSIGNED_PORT = 54321


# ─── fakes / helpers ────────────────────────────────────────────────────────


class FakeHTTPServer:
    """最小 HTTPServer 替身：只记录调用，不绑定端口。"""

    instances: list["FakeHTTPServer"] = []
    on_serve = None

    def __init__(self, address, handler):
        self.address = address
        self.handler = handler
        self.server_address = ("127.0.0.1", _ASSIGNED_PORT)
        self.default_source = None
        self.serve_forever_calls = 0
        self.server_close_calls = 0
        FakeHTTPServer.instances.append(self)

    def serve_forever(self):
        self.serve_forever_calls += 1
        if FakeHTTPServer.on_serve is not None:
            FakeHTTPServer.on_serve()

    def server_close(self):
        self.server_close_calls += 1


@pytest.fixture
def fake_server(monkeypatch):
    FakeHTTPServer.instances = []
    FakeHTTPServer.on_serve = None
    monkeypatch.setattr(web_server, "HTTPServer", FakeHTTPServer)
    return FakeHTTPServer


def _interrupt():
    raise KeyboardInterrupt


def _boom():
    raise RuntimeError("serve failure")


def _port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", int(port)))
        except OSError:
            return False
    return True


# ─── blocking semantics ─────────────────────────────────────────────────────


def test_start_server_defaults_to_blocking_and_returns_http_server(fake_server, capsys):
    signature = inspect.signature(web_server.start_server)
    assert signature.parameters["block"].default is True
    fake_server.on_serve = _interrupt

    result = web_server.start_server(port=0, open_browser=False)

    assert isinstance(result, FakeHTTPServer)
    assert result.serve_forever_calls == 1
    assert result.server_close_calls == 1
    assert result.address == ("127.0.0.1", 0)
    assert result.default_source == "v2"

    out = capsys.readouterr().out
    assert f"http://localhost:{_ASSIGNED_PORT}/" in out
    assert "http://localhost:0" not in out
    assert "服务器已停止" in out


def test_serve_loop_failure_closes_the_server_and_propagates(fake_server):
    fake_server.on_serve = _boom

    with pytest.raises(RuntimeError, match="serve failure"):
        web_server.start_server(port=8765, open_browser=False)

    (server,) = FakeHTTPServer.instances
    assert server.serve_forever_calls == 1
    assert server.server_close_calls == 1


# ─── browser opening ────────────────────────────────────────────────────────


def test_browser_opens_the_assigned_url_only_when_enabled(fake_server, monkeypatch):
    opened: list[str] = []
    monkeypatch.setattr(web_server.webbrowser, "open", lambda url: opened.append(url))
    fake_server.on_serve = _interrupt

    web_server.start_server(port=0, open_browser=True)
    assert opened == [f"http://localhost:{_ASSIGNED_PORT}/?source=v2"]

    opened.clear()
    web_server.start_server(port=0, open_browser=False)
    assert opened == []


# ─── real server lifecycle ──────────────────────────────────────────────────


def test_block_false_serves_in_a_thread_and_graceful_shutdown_releases_port(monkeypatch):
    def _db_must_not_be_touched():
        raise AssertionError("server lifecycle must not touch the database")

    monkeypatch.setattr(web_server, "_get_db", _db_must_not_be_touched)

    server = web_server.start_server(port=0, open_browser=False, block=False)
    port = server.server_address[1]
    assert port != 0

    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5.0)
        try:
            connection.request("GET", "/")
            response = connection.getresponse()
            body = response.read()
        finally:
            connection.close()
        assert response.status == 200
        assert response.getheader("Content-Type", "").startswith("text/html")
        assert body
    finally:
        server.shutdown()
        worker.join(timeout=5.0)
        server.server_close()

    assert not worker.is_alive()
    assert _port_is_free(port)


def test_occupied_port_raises_without_leaking_a_listener():
    blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    blocker.bind(("127.0.0.1", 0))
    blocker.listen(1)
    port = blocker.getsockname()[1]
    try:
        with pytest.raises(OSError):
            web_server.start_server(port=port, open_browser=False, block=False)
    finally:
        blocker.close()

    assert _port_is_free(port)


# ─── public entry point ─────────────────────────────────────────────────────


def test_package_exports_start_server_as_public_entry():
    assert web_ui.start_server is web_server.start_server


def test_module_entrypoint_starts_without_import_warning():
    """``python -m web_ui.server`` must not emit a runpy double-import warning."""

    repo_root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, "-m", "web_ui.server", "--help"],
        cwd=str(repo_root), capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "RuntimeWarning" not in result.stderr
    assert "--port" in result.stdout
