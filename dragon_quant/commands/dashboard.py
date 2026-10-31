"""``dashboard`` 命令：自装引导 + 端口策略 + 启动 Web 仪表盘。"""

from __future__ import annotations

import errno
import importlib
import os
import subprocess
import sys

DEFAULT_UI_PORT = 8765
_WINDOWS_ADDR_IN_USE = 10048
_TRUE_VALUES = {"1", "true", "yes", "on"}

MANUAL_HINT = """Web 仪表盘需要可选依赖 [web]（fastapi + uvicorn）：{reason}
请先手动安装后重试：
  python -m pip install "dragon-quant[web]"
pipx 安装的用户：
  pipx inject dragon-quant "dragon-quant[web]"
"""

SUCCESS_TEXT = """✅ 已安装 [web] 可选依赖。
提示：pipx 用户建议使用 pipx inject dragon-quant "dragon-quant[web]"，以保持包元数据一致。
"""


def is_addr_in_use(exc: BaseException) -> bool:
    """``exc`` 是否为端口占用（POSIX ``EADDRINUSE`` 或 Windows ``winerror=10048``）。"""
    return (
        getattr(exc, "errno", None) == errno.EADDRINUSE
        or getattr(exc, "winerror", None) == _WINDOWS_ADDR_IN_USE
    )


def web_extra_status() -> tuple[bool, str | None]:
    """真实 import 检测（同时发现半装/损坏的安装）。"""
    try:
        import fastapi
        import uvicorn  # noqa: F401
    except Exception as exc:  # noqa: BLE001 - 任何导入失败都降级为未安装
        return False, f"{type(exc).__name__}: {exc}"
    if not hasattr(fastapi.FastAPI, "frontend"):
        return False, "fastapi 版本过低（需要 >=0.162.0，app.frontend 缺失）"
    return True, None


def _env_auto_install() -> bool:
    return os.environ.get("DQ_WEB_AUTO_INSTALL", "").strip().lower() in _TRUE_VALUES


def _is_tty() -> bool:
    try:
        return bool(sys.stdin.isatty() and sys.stdout.isatty())
    except (AttributeError, ValueError):
        return False


def _print_manual_hint(reason: str) -> None:
    print(MANUAL_HINT.format(reason=reason or "未安装"), file=sys.stderr)


def _run_pip_install() -> bool:
    """唯一 I/O seam：``sys.executable -m pip install "dragon-quant[web]"``。

    绝不使用 ``--break-system-packages`` / ``--user``；任何异常（含受限环境
    拒绝创建子进程的 ``RuntimeError``）都降级为“安装失败”。
    """
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pip", "install", "dragon-quant[web]"],
            check=False,
        )
    except Exception as exc:  # noqa: BLE001 - 沙箱/策略拒绝也必须降级
        print(f"dashboard: 无法执行 pip：{type(exc).__name__}: {exc}", file=sys.stderr)
        return False
    return proc.returncode == 0


def ensure_web_extra(*, auto: bool = False) -> bool:
    """四态状态机：可用 → 确认 → 安装 → 复查。失败一律 exit 1。"""
    ok, reason = web_extra_status()
    if ok:
        return True

    reason = reason or "未安装"
    if auto or _env_auto_install():
        pass
    elif _is_tty():
        try:
            answer = input(
                "Web 仪表盘需要可选依赖 [web]（fastapi + uvicorn）。"
                "现在安装？[Y/n] "
            )
        except EOFError:
            answer = "n"
        if answer.strip().lower() not in ("", "y", "yes"):
            _print_manual_hint(reason)
            raise SystemExit(1)
    else:
        _print_manual_hint(reason)
        raise SystemExit(1)

    installed = _run_pip_install()
    if installed:
        importlib.invalidate_caches()
        ok, retry_reason = web_extra_status()
        if ok:
            print(SUCCESS_TEXT, end="")
            return True
        reason = retry_reason or "安装后仍无法导入"
        print(
            "dashboard: 已执行安装但当前进程无法导入 [web]；"
            "请重新运行 `dragon-quant dashboard`。",
            file=sys.stderr,
        )

    _print_manual_hint(reason)
    raise SystemExit(1)


def _start_server(port: int, *, open_browser: bool, default_page: str):
    from web_ui.server import start_server

    return start_server(
        port=port,
        open_browser=open_browser,
        default_source="v2",
        default_page=default_page,
    )


def start_ui(args, *, default_page: str = "review") -> None:
    """与 ``review`` 完全一致的端口策略（隐式占用重试一次 port=0）。"""
    requested = getattr(args, "port", None)
    explicit = requested is not None
    port = requested if explicit else DEFAULT_UI_PORT
    open_browser = not getattr(args, "no_browser", False)

    try:
        _start_server(port, open_browser=open_browser, default_page=default_page)
        return
    except OSError as exc:
        if not is_addr_in_use(exc):
            raise
        if explicit:
            print(
                f"dashboard: Web UI 端口 {port} 已被占用。"
                f"请关闭占用该端口的进程，或用 --port <其他端口> 指定端口。",
                file=sys.stderr,
            )
            raise SystemExit(1) from exc

    print(
        f"dashboard: Web UI 端口 {port} 已被占用，改用系统分配端口重试…",
        file=sys.stderr,
    )
    _start_server(0, open_browser=open_browser, default_page=default_page)


def handle(args, services) -> None:
    """``dragon-quant dashboard``：确保 [web] 可用后启动面板。"""
    ensure_web_extra(auto=getattr(args, "yes", False))
    start_ui(args, default_page="review")
