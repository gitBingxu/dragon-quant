"""infra.assets 公共门面。

- ``run`` / ``SYNC_ACTIONS``：``assets`` 命令的云端同步应用服务入口；
- ``db``：assets.db 连接/仓库模块（全部资产仓储）；
- ``dragons_mirror``：dragons 全量镜像模块（写/读/对账/回填）；
- 资产读写与镜像回填的批准函数出口。

新代码应 import 本模块（或具体子模块），不要再依赖迁移期旧路径 shim。
"""

from __future__ import annotations

from dragon_quant.infra.assets import db, dragons_mirror
from dragon_quant.infra.assets.db import (
    get_auction_snapshot,
    get_prep_assets,
    get_prep_snapshot,
    list_auction_assets,
    list_auction_snapshots,
    replace_auction_snapshots,
    save_auction_asset,
    save_prep_assets,
    save_prep_snapshot,
)
from dragon_quant.infra.assets.dragons_mirror import backfill_dragons, diff_dragons
from dragon_quant.infra.assets.sync.cli_service import SYNC_ACTIONS, run

__all__ = [
    "run",
    "SYNC_ACTIONS",
    "db",
    "dragons_mirror",
    "save_prep_assets",
    "get_prep_assets",
    "get_prep_snapshot",
    "save_prep_snapshot",
    "save_auction_asset",
    "list_auction_assets",
    "replace_auction_snapshots",
    "list_auction_snapshots",
    "get_auction_snapshot",
    "diff_dragons",
    "backfill_dragons",
]
