"""路由注册表；元组顺序即路由顺序。

``accuracy.router`` 必须先于 ``assets.router``：静态 ``/api/prep/accuracy`` /
``/api/auction/accuracy`` 不能被动态 ``{trade_date}`` 抢先匹配。
"""

from __future__ import annotations

from . import account, accuracy, assets, dragons, factors, meta, scans

ROUTERS = (
    meta.router,
    dragons.router,
    scans.router,
    factors.router,
    accuracy.router,
    assets.router,
    account.router,
)
