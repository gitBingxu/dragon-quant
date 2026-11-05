"""web_ui 包公共入口（PEP 562 惰性导出，无导入副作用）。

- ``web_ui.start_server``：deprecated 兼容壳；
- ``web_ui.create_app`` / ``web_ui.start_dashboard``：新公共入口。
"""

__all__ = ["start_server", "create_app", "start_dashboard"]


def __getattr__(name: str):
    if name == "start_server":
        from .server import start_server

        return start_server
    if name == "create_app":
        from .backend.app import create_app

        return create_app
    if name == "start_dashboard":
        from .backend.serve import start_dashboard

        return start_dashboard
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
