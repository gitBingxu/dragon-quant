"""
量价分析（VPA）数据模型
"""

from __future__ import annotations

from dataclasses import dataclass, field


# 综合信号取值
SIGNAL_BULLISH = "bullish"
SIGNAL_BEARISH = "bearish"
SIGNAL_NEUTRAL = "neutral"


@dataclass
class FactorResult:
    """单个量价因子的输出。

    Attributes:
        name:     因子英文名（如 "vol_amount"）
        title:    因子中文标题（用于展示）
        signal:   bullish / bearish / neutral
        score:    0-100，该因子的量价健康度（非买卖强度）
        note:     一句话中文结论
        evidence: 判断依据（逐条数值/事实，用于详细报告展示）
        details:  计算明细（结构化，用于入库/程序消费）
    """
    name: str
    title: str
    signal: str
    score: float
    note: str
    evidence: list[str] = field(default_factory=list)
    details: dict = field(default_factory=dict)


@dataclass
class VPAReport:
    """一只个股的量价分析综合报告。

    Attributes:
        code:         股票代码
        source:       数据源
        health_score: 0-100，综合量价健康度
        signal:       综合信号 bullish / bearish / neutral
        summary:      一句话综合结论
        factors:      各因子结果
        fallback:     True 表示数据不足等原因未能完成分析
        reason:       fallback 原因
    """
    code: str
    source: str
    health_score: float
    signal: str
    summary: str
    factors: list[FactorResult] = field(default_factory=list)
    fallback: bool = False
    reason: str = ""
