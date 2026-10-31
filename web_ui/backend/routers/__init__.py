"""路由注册表；元组顺序即路由顺序。"""

from __future__ import annotations

from . import account, assets, dragons, factors, meta, scans

ROUTERS = (
    meta.router,
    dragons.router,
    scans.router,
    factors.router,
    assets.router,
    account.router,
)
