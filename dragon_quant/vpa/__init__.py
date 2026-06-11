"""
量价分析（VPA）独立模块。

独立于 scorer 与编排器，基于量价博弈原则对个股做量价健康度验证。

用法：
    from dragon_quant.vpa import analyze
    report = analyze("600519")
"""

from dragon_quant.vpa.engine import analyze
from dragon_quant.vpa.types import VPAReport, FactorResult

__all__ = ["analyze", "VPAReport", "FactorResult"]
