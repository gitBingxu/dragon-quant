"""web_ui 包公共入口。

Web 服务器实现位于 ``web_ui.server``；包根以 PEP 562 惰性导出
``start_server``，避免 ``python -m web_ui.server`` 因包导入先加载子模块而
触发 runpy 重复导入告警，也不引入任何导入副作用。
"""

__all__ = ["start_server"]


def __getattr__(name: str):
    if name == "start_server":
        from .server import start_server

        return start_server
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
