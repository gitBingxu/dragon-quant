"""Socket-level network guard installed in every E2E child process.

External network and DNS fail closed.  ``localhost`` is allowed only when the
runner explicitly enables it for a web E2E case (M1+); by default even
localhost is blocked so a leaked proxy cannot be reached.
"""

from __future__ import annotations

import socket as _socket

INSTALLED = False
ALLOW_LOCALHOST = False
_LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost", "ip6-localhost"}
_ORIGINALS: dict[str, object] = {}


class NetworkBlocked(RuntimeError):
    """Raised when a child process attempts any non-approved network access."""


def _host_of(address: object) -> str:
    if isinstance(address, (tuple, list)) and address:
        return str(address[0])
    return str(address)


def _check_host(host: object) -> None:
    text = str(host)
    if ALLOW_LOCALHOST and text in _LOCAL_HOSTS:
        return
    raise NetworkBlocked(f"E2E 网络已禁用（allow_localhost={ALLOW_LOCALHOST}）: {text}")


def _install_socket_functions() -> None:
    original_connect = _socket.socket.connect
    original_connect_ex = _socket.socket.connect_ex
    original_create_connection = _socket.create_connection
    original_getaddrinfo = _socket.getaddrinfo
    original_gethostbyname = _socket.gethostbyname
    original_gethostbyname_ex = _socket.gethostbyname_ex
    original_sendto = _socket.socket.sendto
    _ORIGINALS.update(
        connect=original_connect,
        connect_ex=original_connect_ex,
        create_connection=original_create_connection,
        getaddrinfo=original_getaddrinfo,
        gethostbyname=original_gethostbyname,
        gethostbyname_ex=original_gethostbyname_ex,
        sendto=original_sendto,
    )

    def connect(self, address):  # type: ignore[no-untyped-def]
        if getattr(self, "family", None) == getattr(_socket, "AF_UNIX", object()):
            return original_connect(self, address)
        _check_host(_host_of(address))
        return original_connect(self, address)

    connect.E2E_NETGUARD = True  # type: ignore[attr-defined]

    def connect_ex(self, address):  # type: ignore[no-untyped-def]
        if getattr(self, "family", None) == getattr(_socket, "AF_UNIX", object()):
            return original_connect_ex(self, address)
        _check_host(_host_of(address))
        return original_connect_ex(self, address)

    connect_ex.E2E_NETGUARD = True  # type: ignore[attr-defined]

    def create_connection(address, *args, **kwargs):  # type: ignore[no-untyped-def]
        _check_host(_host_of(address))
        return original_create_connection(address, *args, **kwargs)

    create_connection.E2E_NETGUARD = True  # type: ignore[attr-defined]

    def getaddrinfo(host, *args, **kwargs):  # type: ignore[no-untyped-def]
        if host not in (None, ""):
            _check_host(host)
        return original_getaddrinfo(host, *args, **kwargs)

    getaddrinfo.E2E_NETGUARD = True  # type: ignore[attr-defined]

    def gethostbyname(host):  # type: ignore[no-untyped-def]
        _check_host(host)
        return original_gethostbyname(host)

    gethostbyname.E2E_NETGUARD = True  # type: ignore[attr-defined]

    def gethostbyname_ex(host):  # type: ignore[no-untyped-def]
        _check_host(host)
        return original_gethostbyname_ex(host)

    gethostbyname_ex.E2E_NETGUARD = True  # type: ignore[attr-defined]

    def sendto(self, data, *args):  # type: ignore[no-untyped-def]
        if getattr(self, "family", None) == getattr(_socket, "AF_UNIX", object()):
            return _ORIGINALS["sendto"](self, data, *args)
        address = args[-1] if args else None
        _check_host(_host_of(address))
        return _ORIGINALS["sendto"](self, data, *args)

    sendto.E2E_NETGUARD = True  # type: ignore[attr-defined]

    _socket.socket.connect = connect  # type: ignore[method-assign]
    _socket.socket.connect_ex = connect_ex  # type: ignore[method-assign]
    _socket.socket.sendto = sendto  # type: ignore[method-assign]
    _socket.create_connection = create_connection  # type: ignore[assignment]
    _socket.getaddrinfo = getaddrinfo  # type: ignore[assignment]
    _socket.gethostbyname = gethostbyname  # type: ignore[assignment]
    _socket.gethostbyname_ex = gethostbyname_ex  # type: ignore[assignment]


def is_patched() -> bool:
    """True only when the actual socket entry points are the guard functions."""

    return bool(
        getattr(_socket.create_connection, "E2E_NETGUARD", False)
        and getattr(_socket.getaddrinfo, "E2E_NETGUARD", False)
        and getattr(_socket.socket.connect, "E2E_NETGUARD", False)
    )


def install(*, allow_localhost: bool = False) -> None:
    """Install the guard once per process."""

    global INSTALLED, ALLOW_LOCALHOST
    if INSTALLED:
        return
    ALLOW_LOCALHOST = bool(allow_localhost)
    _install_socket_functions()
    if not is_patched():
        raise RuntimeError("E2E netguard 安装后自检失败：socket 入口未被接管")
    INSTALLED = True
