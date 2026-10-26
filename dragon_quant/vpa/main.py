"""``vpa`` 公共入口（Task 12）：量价分析用例。

实现在 :mod:`dragon_quant.vpa.engine` / :mod:`dragon_quant.vpa.types`；包
``dragon_quant.vpa`` 从本模块轻量重导出，旧公共 import 路径不变。
"""

from __future__ import annotations

from dragon_quant.vpa.engine import analyze
from dragon_quant.vpa.report import render
from dragon_quant.vpa.storage import upsert_vpa
from dragon_quant.vpa.types import FactorResult, VPAReport

__all__ = ["analyze", "VPAReport", "FactorResult", "render", "upsert_vpa"]
