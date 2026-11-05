"""web_ui.backend 公共入口（PEP 562 惰性导出）。

导入本包**不得** import fastapi / uvicorn，不得触库或创建目录。
"""

from __future__ import annotations

__all__ = ["DashboardSettings", "DashboardHandle", "create_app", "start_dashboard"]


def __getattr__(name: str):
    if name == "DashboardSettings":
        from .settings import DashboardSettings

        return DashboardSettings
    if name == "DashboardHandle":
        from .serve import DashboardHandle

        return DashboardHandle
    if name == "create_app":
        from .app import create_app

        return create_app
    if name == "start_dashboard":
        from .serve import start_dashboard

        return start_dashboard
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(__all__)
