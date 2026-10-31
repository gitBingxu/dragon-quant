"""B2：``start_dashboard`` / deprecated ``start_server`` 生命周期契约（离线）。

覆盖：``port=0`` 真实端口、显式冲突同步 ``OSError``、``block=False`` 句柄
``port/.stop()`` 释放端口、导入无警告、生命周期不触库。
"""

from __future__ import annotations

import http.client
import inspect
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

import web_ui
from web_ui import server as web_server
from web_ui.backend import serve as backend_serve


def _port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", int(port)))
        except OSError:
            return False
    return True


def _wait_http(port: int, timeout: float = 10.0) -> tuple[int, bytes]:
    deadline = time.monotonic() + timeout
    last: object = None
    while time.monotonic() < deadline:
        try:
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2.0)
            try:
                connection.request("GET", "/")
                response = connection.getresponse()
                body = response.read()
                return response.status, body
            finally:
                connection.close()
        except (OSError, http.client.HTTPException) as exc:
            last = exc
            time.sleep(0.05)
    raise AssertionError(f"dashboard 未就绪: {last!r}")


def test_start_server_signature_is_frozen():
    signature = inspect.signature(web_server.start_server)
    assert signature.parameters["port"].default == 8765
    assert signature.parameters["open_browser"].default is True
    assert signature.parameters["default_source"].default == "v2"
    assert signature.parameters["default_page"].default == "review"
    assert signature.parameters["block"].default is True


def test_block_false_serves_and_stop_releases_port():
    handle = web_server.start_server(port=0, open_browser=False, block=False)
    try:
        assert isinstance(handle, backend_serve.DashboardHandle)
        assert handle.port != 0
        assert handle.server_address == ("127.0.0.1", handle.port)
        status, body = _wait_http(handle.port)
        assert status == 200
        assert body
        assert not _port_is_free(handle.port)
    finally:
        handle.stop()
    assert _port_is_free(handle.port)


def test_occupied_explicit_port_raises_without_leaking_listener():
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


def test_lifecycle_does_not_touch_the_database(monkeypatch):
    from dragon_quant.infra.storage import connection

    def _must_not_connect(*args, **kwargs):
        raise AssertionError("server lifecycle must not touch the database")

    monkeypatch.setattr(connection, "_connect", _must_not_connect)
    handle = web_server.start_server(port=0, open_browser=False, block=False)
    try:
        status, body = _wait_http(handle.port)
        assert status == 200 and body
    finally:
        handle.stop()


def test_package_exports_public_entries():
    assert web_ui.start_server is web_server.start_server
    assert callable(web_ui.create_app)
    assert callable(web_ui.start_dashboard)


def test_module_entrypoint_starts_without_import_warning():
    repo_root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, "-m", "web_ui.server", "--help"],
        cwd=str(repo_root), capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "RuntimeWarning" not in result.stderr
    assert "--port" in result.stdout
