"""跨路由共享的单飞锁：账户生成与对照实验不得并行跑两个重任务。"""

from __future__ import annotations

import threading

ACCOUNT_HEAVY_LOCK = threading.Lock()

__all__ = ["ACCOUNT_HEAVY_LOCK"]
