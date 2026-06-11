"""
量价分析报告文本渲染。

风格对齐 scan 的 report_text：头部 + 每因子一行（图标+分数+结论）+ 缩进的判断依据。
"""

from __future__ import annotations

from dragon_quant.vpa.types import (
    VPAReport, SIGNAL_BULLISH, SIGNAL_BEARISH,
)

_SIGNAL_ICON = {
    SIGNAL_BULLISH: "📈",
    SIGNAL_BEARISH: "📉",
}


def _icon(signal: str) -> str:
    return _SIGNAL_ICON.get(signal, "➖")


def _grade(health: float) -> str:
    """量价健康度分级。"""
    if health >= 70:
        return "量价健康"
    if health >= 55:
        return "偏强"
    if health >= 45:
        return "中性"
    if health >= 30:
        return "偏弱"
    return "量价转弱"


def render(report: VPAReport) -> str:
    """完整多行报告（用于独立 vpa 命令），对齐 scan report_text 风格。"""
    lines = []
    if report.fallback:
        lines.append(f"量价分析 {report.code}（source={report.source}）")
        lines.append("═" * 60)
        lines.append(f"⚠️  {report.summary}")
        return "\n".join(lines)

    # 头部：代码 - 综合健康度 - 分级 - 信号
    header = (f"量价分析 {report.code} — "
              f"{report.health_score:.1f}分-{_grade(report.health_score)}-"
              f"{_icon(report.signal)} {report.summary}")
    lines.append(header)
    lines.append("═" * 60)

    for r in report.factors:
        lines.append(f"- {_icon(r.signal)} {r.title}({r.score:.0f}): {r.note}")
        for ev in r.evidence:
            lines.append(f"    - {ev}")

    return "\n".join(lines)


def render_line(report: VPAReport) -> str:
    """单行结论（用于 review 回测后追加打印）。"""
    if report.fallback:
        return f"   量价: ⚠️ {report.summary}"
    tags = []
    for r in report.factors:
        mark = "✓" if r.signal == SIGNAL_BULLISH else ("✗" if r.signal == SIGNAL_BEARISH else "·")
        tags.append(f"{r.title}{mark}")
    return f"   量价: {_icon(report.signal)} {report.summary}  " + " ".join(tags)


def render_block(report: VPAReport, indent: str = "   ") -> str:
    """多行块（用于 review 回测后追加打印的详细版）。

    带统一缩进，便于嵌在回测每只股票的输出下方。
    """
    if report.fallback:
        return f"{indent}量价: ⚠️ {report.summary}"

    lines = [f"{indent}量价 {report.health_score:.0f}分-{_grade(report.health_score)}-"
             f"{_icon(report.signal)} {report.summary}"]
    for r in report.factors:
        lines.append(f"{indent}  - {_icon(r.signal)} {r.title}({r.score:.0f}): {r.note}")
        for ev in r.evidence:
            lines.append(f"{indent}      · {ev}")
    return "\n".join(lines)
