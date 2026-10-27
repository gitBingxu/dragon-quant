"""``scan.orchestrator`` — 扫描编排（真实实现已迁入，Task 11）。

公共入口是 :mod:`dragon_quant.scan.orchestrator.main`（``scan`` / ``run_scan``）；
本 ``__init__`` 保持零 eager import，避免导入包时加载编排器（B1）。
"""

from __future__ import annotations

__all__: list[str] = []
