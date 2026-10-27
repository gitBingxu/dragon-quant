"""
量价分析（VPA）独立模块。

独立于 scorer 与编排器，基于量价博弈原则对个股做量价健康度验证。

用法：
    from dragon_quant.vpa import analyze
    report = analyze("600519")

公共入口在 :mod:`dragon_quant.vpa.main`（Task 12）；本包保持轻量显式重导出。
"""

from dragon_quant.vpa.main import FactorResult, VPAReport, analyze

__all__ = ["analyze", "VPAReport", "FactorResult"]
