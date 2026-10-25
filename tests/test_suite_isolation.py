"""单元测试套件隔离的聚焦回归。

`tests/conftest.py` 为整个单元测试套件提供三层安全网：

1. ``DQ_DATA_DIR`` 重定向到一次性临时目录（漏 patch 的写只污染临时区）；
2. Python 网络 fail-closed（只允许本机回环，外部 socket/DNS 直接抛错）；
3. Playwright 内核下载隔离（临时目录 + 禁用下载 + 关闭端口下载源）。

本文件只验证这三层安全网确实生效，不改变任何业务语义；真实数据目录只做
``os.stat`` 元数据观察，绝不读取内容。
"""

from __future__ import annotations

import os
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

# Diagnostic mode (DQ_TEST_ALLOW_REAL_DATA=1) intentionally keeps the real data
# directory and permits real access, so these isolation assertions do not apply.
pytestmark = pytest.mark.skipif(
    not os.environ.get("DQ_TEST_ISOLATED_DATA_DIR"),
    reason="未启用单元测试隔离（DQ_TEST_ALLOW_REAL_DATA=1 诊断模式）",
)


def _isolated_root() -> Path:
    value = os.environ.get("DQ_TEST_ISOLATED_DATA_DIR")
    assert value, "未启用单元测试隔离（需经 tests/conftest.py 运行 pytest）"
    return Path(value).resolve()


class _PingHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - stdlib handler contract
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"pong")

    def log_message(self, *args, **kwargs):  # silence the test server
        pass


def test_data_dir_is_redirected_into_the_isolated_temp_root():
    isolated = _isolated_root()
    data_dir = Path(os.environ["DQ_DATA_DIR"]).resolve()

    assert data_dir == isolated
    assert isolated.parent == Path(isolated.parent).resolve()
    assert isolated.name.startswith("dragon-quant-tests-")

    # 业务流程 import 后创建的所有目录都必须落在隔离根内。
    from dragon_quant.storage import assets_db, paths

    for path in (
        paths.DATA_DIR,
        paths.DB_PATH,
        paths.ASSETS_DB_PATH,
        assets_db.ASSETS_DB_PATH,
    ):
        resolved = Path(path).resolve()
        assert resolved == isolated or isolated in resolved.parents, (path, isolated)


def test_external_network_and_dns_fail_closed():
    from tests.e2e.injection import netguard

    assert netguard.is_patched(), "socket 入口未被 netguard 接管"

    with pytest.raises(netguard.NetworkBlocked):
        socket.create_connection(("example.invalid", 80), 0.1)
    with pytest.raises(netguard.NetworkBlocked):
        socket.getaddrinfo("example.invalid", 80)
    with pytest.raises(netguard.NetworkBlocked):
        socket.gethostbyname("example.invalid")


def test_loopback_remains_available_for_local_servers():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _PingHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        with socket.create_connection(("127.0.0.1", port), timeout=2) as connection:
            connection.sendall(b"GET / HTTP/1.0\r\n\r\n")
            with connection.makefile("rb") as response:
                assert b"pong" in response.read()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_playwright_download_is_isolated_from_the_user_profile():
    isolated = _isolated_root()

    browsers = Path(os.environ["PLAYWRIGHT_BROWSERS_PATH"]).resolve()
    assert isolated in browsers.parents, browsers
    assert os.environ["PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD"] == "1"
    assert os.environ["PLAYWRIGHT_DOWNLOAD_HOST"].startswith("http://127.0.0.1:")


def test_real_data_directory_is_never_the_isolated_directory():
    """真实数据目录与临时根必须物理分离；只做 metadata 观察。"""

    home = Path(os.environ.get("HOME") or Path.home()).resolve()
    if sys.platform == "win32":
        appdata = Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming")
        real = appdata / "dragon-quant"
    elif sys.platform == "darwin":
        real = home / "Library" / "Application Support" / "dragon-quant"
    else:
        xdg = Path(os.environ.get("XDG_DATA_HOME") or home / ".local" / "share")
        real = xdg / "dragon-quant"
    isolated = _isolated_root()

    assert real.resolve() != isolated
    assert real.resolve() not in isolated.parents
    assert isolated not in real.resolve().parents
