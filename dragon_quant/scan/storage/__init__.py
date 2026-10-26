"""``scan.storage`` — 扫描 / 龙头 / 日志 / 黑名单持久化（Task 9 迁入）。

包根零 eager import（B1）：公共入口是 :mod:`dragon_quant.scan.storage.main`，
子模块 ``scans`` / ``dragons`` / ``logs`` / ``blacklist`` / ``results`` 按需导入。
"""

from __future__ import annotations

__all__ = ["main", "scans", "dragons", "logs", "blacklist", "results"]
