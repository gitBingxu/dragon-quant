"""premarket.report — 固定行序的 human 摘要（Template Method）。

只渲染 Context 已有字段：缺失写「不可用 / 未知」，不补 0、不写中性。任何单行
生成异常只把该行降级为「不可用（render_error）」，不改变退出码（与 prep /
auction 展示失败同策略）。完整八段报告由 Skill 生成，本模块不输出 JSON。
"""

from __future__ import annotations

from typing import Any, Callable, Mapping

RENDER_ERROR = "不可用（render_error）"


def _text(value: Any, *, unknown: str = "不可用") -> str:
    if value is None:
        return unknown
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _bool(value: Any) -> str:
    return "true" if value else "false"


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _safe(render: Callable[[], str]) -> str:
    try:
        return render()
    except Exception:  # 展示失败不改变退出码
        return RENDER_ERROR


def _created_range(value: Any) -> str:
    source = value if isinstance(value, Mapping) else {}
    low, high = source.get("min"), source.get("max")
    if not low and not high:
        return "不可用"
    if low == high or not high:
        return str(low)
    if not low:
        return str(high)
    return f"{low}..{high}"


def _mainlines(context: Mapping[str, Any]) -> str:
    prep = context.get("prep") if isinstance(context.get("prep"), Mapping) else {}
    result = prep.get("result") if isinstance(prep.get("result"), Mapping) else {}
    review = result.get("review") if isinstance(result.get("review"), Mapping) else {}
    rows = review.get("mainlines") if isinstance(review.get("mainlines"), list) else []
    entries = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        board = row.get("board_name")
        if isinstance(board, str) and board:
            entries.append(f"{board}={_text(row.get('state'))}")
    return "；".join(entries) if entries else "主线不可用"


def _candidate_counts(context: Mapping[str, Any]) -> str:
    candidates = context.get("candidates")
    rows = candidates if isinstance(candidates, list) else []
    eligible = positive = zero = null = 0
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        prep = row.get("prep") if isinstance(row.get("prep"), Mapping) else {}
        if prep.get("eligibility") == "eligible":
            eligible += 1
        value = _number(prep.get("position_pct"))
        if value is None:
            null += 1
        elif value > 0:
            positive += 1
        else:
            zero += 1
    return (f"候选：{len(rows)} 只；eligible={eligible}；position>0={positive}；"
            f"0={zero}；null={null}")


def _ceiling_counts(context: Mapping[str, Any]) -> str:
    candidates = context.get("candidates")
    rows = candidates if isinstance(candidates, list) else []
    counts = {"wait": 0, "observe_only": 0, "no_new_position": 0}
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        ceiling = row.get("action_ceiling")
        status = ceiling.get("status") if isinstance(ceiling, Mapping) else None
        if status in counts:
            counts[status] += 1
    return (f"行动上限：wait={counts['wait']}；observe_only="
            f"{counts['observe_only']}；no_new_position="
            f"{counts['no_new_position']}")


def render_report(context: Mapping[str, Any]) -> list[str]:
    prep = context.get("prep") if isinstance(context.get("prep"), Mapping) else {}
    auction = context.get("auction") if isinstance(context.get("auction"), Mapping) else {}
    quality = (context.get("data_quality")
               if isinstance(context.get("data_quality"), Mapping) else {})
    market = context.get("market") if isinstance(context.get("market"), Mapping) else {}
    config = prep.get("review_config")
    config = config if isinstance(config, Mapping) else {}
    degraded = quality.get("degraded")
    degraded_text = ", ".join(str(code) for code in degraded) \
        if isinstance(degraded, list) and degraded else "无"

    lines = [
        _safe(lambda: f"盘前预案上下文｜{_text(context.get('trade_date'))}"),
        _safe(lambda: (
            f"信息截止：{_text(context.get('info_cutoff'))}"
            f"（run_at={_text(context.get('run_at'))}）；"
            "T0 只读：不写库、不触网、不重算")),
        _safe(lambda: (
            f"T-1 复盘：{_text(prep.get('trade_date'))}；"
            f"frozen_at={_text(prep.get('frozen_at'))}；"
            f"created_at={_text(prep.get('created_at'))}；"
            f"version={_text(prep.get('schema_version'), unknown='未知')}；"
            f"review_config={_text(config.get('config_version'), unknown='缺失')}；"
            f"strictness={_text(prep.get('strictness'))}；"
            f"proven_gap={_text(prep.get('proven_gap'), unknown='null')}")),
        _safe(lambda: (
            f"T 竞价：state={_text(auction.get('state'))}；"
            f"available={_bool(auction.get('available'))}；"
            f"as_of={_text(auction.get('as_of'))}；"
            f"rows={_text(auction.get('row_count'), unknown='0')}；"
            f"created_at={_created_range(auction.get('created_at'))}；"
            "cache=unknown（快照行未持久化 from_cache）")),
        _safe(lambda: (
            f"数据质量：{_text(quality.get('status'))}；"
            f"plan_usable={_bool(quality.get('plan_usable'))}；"
            f"auction_usable={_bool(quality.get('auction_usable'))}；"
            f"degraded={degraded_text}")),
        _safe(lambda: (
            f"市场（T-1）：state={_text(market.get('state'))}；"
            f"score={_text(market.get('score'))}；"
            f"coeff={_text(market.get('coeff'))}")),
        _safe(lambda: f"主线（T-1）：{_mainlines(context)}"),
        _safe(lambda: _candidate_counts(context)),
        _safe(lambda: _ceiling_counts(context)),
        "提示：",
    ]

    notes = quality.get("notes")
    bullets = [str(note) for note in notes] if isinstance(notes, list) else []
    for code in degraded if isinstance(degraded, list) else []:
        bullets.append(f"降级：{code}")
    if not bullets:
        bullets.append("无")
    lines.extend(f"- {bullet}" for bullet in bullets)
    lines.append(_safe(lambda: f"声明：{_text(context.get('disclaimer'))}"))
    return lines


__all__ = ["RENDER_ERROR", "render_report"]
