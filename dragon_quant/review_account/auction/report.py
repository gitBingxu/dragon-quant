"""auction.report — 集合竞价人读摘要（纯函数，无 I/O）。

CLI 默认展示：标题 + 逐只「排名/代码/名称/等级/强度/方向/关键证据/数据状态」
+ 汇总（强/中/弱；否决；不可用；失败）。旧前缀与旧快照兼容：缺少 v2 键时
退化为旧格式，不插入空 token。

约束（方案 §12.4）：不出现买卖词、不写建议/仓位；「未验证」只表示本次评价不可用。
"""

from __future__ import annotations

_DIRECTION_LABELS = {"bull": "偏多", "bear": "偏空", "neutral": "中性"}
_LEVELS = ("强", "中", "弱")
_VALIDATION_NOTE = "ℹ️ validation_status=baseline_unvalidated（阈值为待验证基线，不构成投资建议）"


def _code_of(snapshot: dict) -> str:
    return str(snapshot.get("code") or "")


def _strength_of(snapshot: dict) -> float:
    raw = snapshot.get("strength")
    if raw is None:
        return 0.0
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 0.0


def _strength_text(snapshot: dict) -> str:
    raw = snapshot.get("strength")
    if raw is None or (snapshot.get("is_weak") and _strength_of(snapshot) == 0.0):
        return "—"
    try:
        return f"{float(raw):.1f}"
    except (TypeError, ValueError):
        return "—"


def _sort_key(snapshot: dict):
    """ranked(0) → unverified(1) → 兜底(2) → 否决(3)。"""
    code = _code_of(snapshot)
    rank = snapshot.get("rank")
    if rank is not None:
        try:
            return (0, int(rank), code)
        except (TypeError, ValueError):
            pass
    strength = _strength_of(snapshot)
    if snapshot.get("is_weak"):
        return (3, -strength, code)
    if snapshot.get("unverified"):
        return (1, -strength, code)
    return (2, -strength, code)


def sort_snapshots(snapshots: list[dict]) -> list[dict]:
    """rank 升序 → 未验证组 → 兜底组 → 否决组；组内按 (-strength, code)。"""
    return sorted(snapshots, key=_sort_key)


def _data_status_text(snapshot: dict) -> str | None:
    status = snapshot.get("data_quality_status")
    if status is None:
        return None
    if status == "ok":
        return "数据 ok"
    if status == "degraded":
        return "数据部分缺失"
    evidence = snapshot.get("evidence_summary")
    return evidence if evidence and str(evidence).startswith("数据不足") else "数据不足"


def _evidence_text(snapshot: dict) -> str | None:
    if snapshot.get("data_quality_status") is None:
        return None
    evidence = snapshot.get("evidence_summary")
    if not evidence or str(evidence).startswith("数据不足"):
        return None
    return str(evidence)


def format_verdict_line(snapshot: dict, name: str = "") -> str:
    """单只判定：旧前缀行 + v2 证据 / 数据状态（若存在）。"""
    rank = snapshot.get("rank")
    if rank is not None:
        prefix = f"{rank}."
    elif snapshot.get("is_weak"):
        prefix = "-"
    else:
        prefix = "?"

    tokens = [prefix, _code_of(snapshot)]
    clean_name = (name or "").strip()
    if clean_name:
        tokens.append(clean_name)
    tokens.append(str(snapshot.get("level") or "—"))
    tokens.append(_strength_text(snapshot))
    direction = snapshot.get("direction")
    tokens.append(_DIRECTION_LABELS.get(str(direction), str(direction or "—")))

    line = " ".join(tokens)
    if snapshot.get("is_weak"):
        line += f"｜否决：{snapshot.get('reject_reason') or '未说明原因'}"

    extras = []
    evidence = _evidence_text(snapshot)
    if evidence:
        extras.append(evidence)
    status = _data_status_text(snapshot)
    if status:
        extras.append(status)
    if extras:
        line += "｜" + "｜".join(extras)

    if snapshot.get("unverified"):
        line += "｜未验证"
    return line


def format_summary(snapshots: list[dict], failed_count: int = 0) -> str:
    """强/中/弱 + 否决/不可用/失败/未验证计数（v2）；旧快照只出旧计数字段。"""
    counts = {level: 0 for level in _LEVELS}
    other = veto = unavailable = unverified = 0
    has_v2 = False
    for snapshot in snapshots:
        level = snapshot.get("level")
        if level in counts:
            counts[level] += 1
        else:
            other += 1
        if snapshot.get("unverified"):
            unverified += 1
        if snapshot.get("is_weak"):
            veto += 1
        if snapshot.get("data_quality_status") == "unavailable":
            unavailable += 1
        if snapshot.get("data_quality_status") is not None:
            has_v2 = True

    parts = [f"强 {counts['强']} / 中 {counts['中']} / 弱 {counts['弱']}"]
    if other and not has_v2:
        parts.append(f"其他 {other}")
    if veto:
        parts.append(f"否决 {veto}")
    if unavailable:
        parts.append(f"不可用 {unavailable}")
    if failed_count:
        parts.append(f"失败 {failed_count}")
    if unverified and not has_v2:
        parts.append(f"未验证 {unverified}")
    return "汇总：" + "；".join(parts)


def _format_empty(
    *,
    trade_date: str,
    pool_source: str | None,
    candidate_count: int | None,
    fallback_reason: str | None,
    failed_count: int,
    empty_count: int,
    saved_count: int,
) -> str:
    n = candidate_count or 0
    if n <= 0:
        bits = [str(trade_date)]
        if pool_source:
            bits.append(f"池来源 {pool_source}")
        if fallback_reason:
            bits.append(f"回退：{fallback_reason}")
        if empty_count:
            bits.append(f"空盘口 {empty_count} 只")
        return f"无参与集合竞价强度判断的标的（{'，'.join(bits)}）；详情加 --json"

    if saved_count > 0:
        return (f"候选 {n} 只均未产出评分结果（原始资产已写入，"
                f"失败 {failed_count} 条）；详情加 --json")
    if failed_count > 0:
        return f"候选 {n} 只均未产出竞价强度判定（采集失败 {failed_count} 条）；详情加 --json"
    return f"候选 {n} 只均无竞价盘口（空盘口 {empty_count} 只）；详情加 --json"


def format_auction_report(
    snapshots: list[dict],
    *,
    trade_date: str,
    mode: str = "collect",
    pool_source: str | None = None,
    candidate_count: int | None = None,
    fallback_reason: str | None = None,
    failed_count: int = 0,
    empty_count: int = 0,
    saved_count: int = 0,
    names: dict[str, str] | None = None,
) -> list[str]:
    """返回待 print 的整段行；空 snapshots 按四种形态返回单行。"""
    snapshots = list(snapshots or [])
    names = names or {}

    if not snapshots:
        return [_format_empty(
            trade_date=trade_date,
            pool_source=pool_source,
            candidate_count=candidate_count,
            fallback_reason=fallback_reason,
            failed_count=failed_count,
            empty_count=empty_count,
            saved_count=saved_count,
        )]

    if mode == "recompute":
        head = ["重算", f"参与 {len(snapshots)} 只"]
    elif mode == "snapshot":
        head = ["快照", "来自缓存", f"参与 {len(snapshots)} 只"]
        if failed_count:
            head.append(f"快照损坏 {failed_count} 行")
    else:
        head = [f"参与 {len(snapshots)} 只"]
        if pool_source:
            head.append(f"池来源 {pool_source}")
        if fallback_reason:
            head.append(f"回退：{fallback_reason}")
        if candidate_count is not None and candidate_count > len(snapshots):
            head.append(f"候选 {candidate_count} 只")
    if empty_count:
        head.append(f"空盘口 {empty_count} 只")

    lines = [f"📊 {trade_date} 集合竞价强度（{'｜'.join(head)}）"]
    lines.extend(
        format_verdict_line(snapshot, names.get(_code_of(snapshot), ""))
        for snapshot in sort_snapshots(snapshots)
    )
    lines.append(format_summary(snapshots, failed_count=failed_count))
    if any(snapshot.get("validation_status") for snapshot in snapshots):
        lines.append(_VALIDATION_NOTE)
    return lines
