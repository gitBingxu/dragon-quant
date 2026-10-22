"""auction.report — 集合竞价人读摘要（纯函数，无 I/O）。

CLI 展示层的默认策略：把 ``_snapshot_summary()`` 产出的 dict 渲染为
「标题 + 逐只强弱 + 汇总」。``--json`` 模式完全不经过本模块，JSON 契约
由 ``_cmd_auction`` / ``_cmd_auction_recompute`` 原样构造。

约定：
  - 本模块不 print、不 exit、不读环境；
  - 名称只用于展示，缺失时不插入空 token；
  - 空 snapshots 区分「无候选 / 采集层全失败 / 评分层全失败 / 全空盘口」。
"""

from __future__ import annotations

_DIRECTION_LABELS = {"bull": "偏多", "bear": "偏空", "neutral": "中性"}
_LEVELS = ("强", "中", "弱")


def _code_of(snapshot: dict) -> str:
    return str(snapshot.get("code") or "")


def _strength_of(snapshot: dict) -> float:
    try:
        return float(snapshot.get("strength"))
    except (TypeError, ValueError):
        return 0.0


def _strength_text(snapshot: dict) -> str:
    try:
        return f"{float(snapshot.get('strength')):.1f}"
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


def format_verdict_line(snapshot: dict, name: str = "") -> str:
    """单只判定：`1. 600001 甲 强 78.3 偏多[｜否决：…][｜未验证]`。"""
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
    if snapshot.get("unverified"):
        line += "｜未验证"
    return line


def format_summary(snapshots: list[dict], failed_count: int = 0) -> str:
    """强/中/弱/其他计数 + 全量未验证计数；failed_count>0 时附「详情加 --json」。"""
    counts = {level: 0 for level in _LEVELS}
    other = 0
    unverified = 0
    for snapshot in snapshots:
        level = snapshot.get("level")
        if level in counts:
            counts[level] += 1
        else:
            other += 1
        if snapshot.get("unverified"):
            unverified += 1

    parts = [f"强 {counts['强']} / 中 {counts['中']} / 弱 {counts['弱']}"]
    if other:
        parts.append(f"其他 {other}")
    if unverified:
        parts.append(f"未验证 {unverified}")
    if failed_count:
        parts.append(f"采集失败 {failed_count}（详情加 --json）")
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
    return lines
