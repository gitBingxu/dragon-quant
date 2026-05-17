"""
复盘报告生成 — 表格 + 文本
"""

from typing import Dict, List, Optional


def build_table(results: List[Dict],
                skipped: Optional[List[Dict]] = None) -> str:
    """
    生成复盘报告文本。

    Args:
        results: 复盘成功的股票列表
        skipped: 被跳过的股票列表（去重）
    """
    lines = []

    if results:
        header = f"{'代码':8s} {'名称':8s} {'扫描分':>6s}  {'买入日':8s}  {'买入价':>8s}  {'买入方式':8s}  {'5日收益':>8s}  {'超额':>8s}"
        sep = "-" * len(header)
        lines.append(header)
        lines.append(sep)

        for r in results:
            entry_type_disp = "分时(炸板)" if r.get("entry_type") == "minute" else "日K(开盘)"
            note = f" ⚠{r['note']}" if r.get("note") else ""
            lines.append(
                f"{r['code']:8s} {r.get('name', ''):8s} "
                f"{r.get('scan_score', 0):6.1f}  "
                f"{r.get('entry_date', '')[-5:]:8s}  "
                f"{r.get('entry_price', 0):8.2f}  "
                f"{entry_type_disp:8s}  "
                f"{r.get('return_pct', 0):+8.2f}% "
                f"{r.get('excess_return', 0):+8.2f}%{note}"
            )

    if skipped:
        lines.append("")
        for s in skipped:
            lines.append(f"  ⚠ {s['code']} {s.get('name', '')} {s.get('reason', '')}")

    return "\n".join(lines)


def build_summary(results: List[Dict]) -> str:
    """生成摘要统计行"""
    if not results:
        return "无复盘数据"

    valid = [r for r in results if r.get("return_pct") is not None]
    if not valid:
        return "无有效收益数据"

    avg_ret = sum(r["return_pct"] for r in valid) / len(valid)
    win_count = sum(1 for r in valid if r["return_pct"] > 0)
    win_rate = win_count / len(valid) * 100

    best = max(valid, key=lambda r: r["return_pct"])
    worst = min(valid, key=lambda r: r["return_pct"])

    parts = [
        f"平均收益: {avg_ret:+.2f}%",
        f"胜率: {win_rate:.1f}% ({win_count}/{len(valid)})",
        f"最佳: {best['code']} {best.get('name', '')} {best['return_pct']:+.2f}%",
        f"最差: {worst['code']} {worst.get('name', '')} {worst['return_pct']:+.2f}%",
    ]
    return "    ".join(parts)