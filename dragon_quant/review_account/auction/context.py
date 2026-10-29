"""auction.context — 逐只证据摘要与版本化 ``strategy_context``（``auction_sc_v1``）。

纯函数：不读 wall clock、不读环境、不触 I/O。``as_of`` 由 ``trade_date`` 推导，
``--recompute`` 可逐字节复现（除存储层 ``created_at``）。

覆盖规则（方案 §12.3）：
  - ``collect``：候选池 ∪ 已存资产 ∪ empty ∪ failed；池内缺口用 ``unknown`` 兜底；
  - ``recompute``：只能覆盖已存资产 ∪ skipped_empty ∪ failed。
"""

from __future__ import annotations

from typing import Optional

from . import registry as R
from .serialize import verdict_to_payload

CONTEXT_VERSION = "auction_sc_v1"
DISCLAIMER = "阈值为待验证基线，不构成投资建议，不承诺收益。"
_RAW_SUBSET = (
    "official_open_gap_pct", "auction_turnover_pct", "volume_ratio_5d",
    "final_unmatched_matched_ratio", "post20_price_return_pct",
    "tail_matched_acceleration", "final_unmatched_lots_signed",
)
_STATUS_PRIORITY = {"failed": 0, "empty": 1, "unknown": 2,
                    "unavailable": 3, "vetoed": 4, "scored": 5}


def _compact_date(trade_date: str) -> str:
    return str(trade_date or "").replace("-", "")


def as_of(trade_date: str) -> str:
    """``20261029`` / ``2026-10-29`` → ``2026-10-29T09:25:00+08:00``。"""
    compact = _compact_date(trade_date)
    if len(compact) == 8 and compact.isdigit():
        return (f"{compact[:4]}-{compact[4:6]}-{compact[6:8]}"
                "T09:25:00+08:00")
    return f"{trade_date}T09:25:00+08:00"


def _summary_parts(raw: dict, reason_codes) -> Optional[str]:
    raw = raw if isinstance(raw, dict) else {}
    parts = []
    for key in ("official_open_gap_pct", "auction_turnover_pct",
                "tail_matched_acceleration", "final_unmatched_matched_ratio"):
        value = raw.get(key)
        if key == "final_unmatched_matched_ratio":
            if value is None:
                continue
            try:
                number = float(value)
            except (TypeError, ValueError):
                continue
            parts.append(f"买余{number:.1f}x" if number >= 0
                         else f"卖余{abs(number):.1f}x")
            continue
        if value is None:
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if key == "official_open_gap_pct":
            parts.append(f"开{number:+.1f}%")
        elif key == "auction_turnover_pct":
            parts.append(f"换手{number:.2f}%")
        else:
            parts.append(f"末段匹配×{number:.1f}")
    if not parts:
        codes = list(reason_codes or []) if isinstance(
            reason_codes, (list, tuple)) else []
        return f"数据不足：{codes[0]}" if codes else None
    return " ".join(parts[:3])


def evidence_summary(verdict) -> Optional[str]:
    """一条确定性的关键证据短句（最多 3 项），供 CLI / Skill 直读。"""
    return _summary_parts(verdict.raw_factors or {},
                          verdict.data_quality.reason_codes)


def evidence_summary_from_payload(payload: dict) -> Optional[str]:
    """从已存 ``analysis_json`` 重算同一证据短句（确定性，无额外状态）。"""
    payload = payload if isinstance(payload, dict) else {}
    quality = payload.get("data_quality")
    quality = quality if isinstance(quality, dict) else {}
    return _summary_parts(payload.get("raw_factors") or {},
                          quality.get("reason_codes") or [])


def _status_from_payload(payload: dict) -> str:
    if payload.get("is_weak"):
        return "vetoed"
    quality = payload.get("data_quality")
    if isinstance(quality, dict) and quality.get("status") == "unavailable":
        return "unavailable"
    return "scored"


def _dim_contributions_from_payload(payload: dict) -> dict:
    dims = payload.get("dims")
    if not isinstance(dims, dict):
        return {}
    result = {}
    for dim in R.DIM_ORDER:
        item = dims.get(dim)
        if not isinstance(item, dict) or item.get("score") is None:
            continue
        weight = item.get("weight")
        if weight is None:
            continue
        try:
            contribution = round(weight * item.get("score"), 2)
        except (TypeError, ValueError):
            continue
        result[dim] = {
            "score": item.get("score"),
            "weight": weight,
            "effective_weight": item.get("effective_weight"),
            "contribution": contribution,
        }
    return result


def _raw_subset_from_payload(payload: dict) -> dict:
    raw = payload.get("raw_factors")
    if not isinstance(raw, dict):
        return {}
    return {key: raw.get(key) for key in _RAW_SUBSET if key in raw}


def _legacy_position(code: str, payload: dict, name: str) -> dict:
    """旧 schema（缺 ``data_quality``）只读降级：不补默认分、不伪造字段。"""
    reason = "snapshot_legacy_schema"
    return {
        "code": code,
        "name": name,
        "status": "unavailable",
        "strength": payload.get("strength"),
        "pre_veto_strength": payload.get("pre_veto_strength"),
        "level": payload.get("level"),
        "direction": payload.get("direction") or "neutral",
        "rank": payload.get("rank"),
        "is_weak": bool(payload.get("is_weak")),
        "reject_reason": payload.get("reject_reason"),
        "data_quality": {"status": "unavailable", "completeness": 0.0,
                         "reason_codes": [reason], "reasons": []},
        "risk_event_codes": [], "raw_factors": {}, "dim_contributions": {},
        "evidence": {}, "reference_prices": {}, "windows": {},
        "actions": [{"kind": "request_data",
                     "text": "已存快照为旧版 schema，未投影强度明细",
                     "requires": "auction_snapshot"}],
        "unknown_reason": reason,
    }


def position_from_payload(payload: dict, name: str = "") -> dict:
    """已存 ``analysis_json`` → ``strategy_context.positions[]``（纯投影）。

    与裁决对象路径共用同一输出形状；缺 ``data_quality`` 的旧 schema 显式降级。
    """
    payload = payload if isinstance(payload, dict) else {}
    code = str(payload.get("code") or "")
    quality = payload.get("data_quality")
    if not isinstance(quality, dict):
        return _legacy_position(code, payload, name)

    status = _status_from_payload(payload)
    raw_codes = quality.get("reason_codes")
    reason_codes = list(raw_codes) if isinstance(raw_codes, (list, tuple)) else []
    evidence = payload.get("evidence")
    evidence = evidence if isinstance(evidence, dict) else {}
    reference = payload.get("reference_prices")
    reference = reference if isinstance(reference, dict) else {}
    windows = payload.get("windows")
    windows = windows if isinstance(windows, dict) else {}
    actions = payload.get("actions")
    actions = actions if isinstance(actions, list) else []
    return {
        "code": code,
        "name": name,
        "status": status,
        "strength": payload.get("strength"),
        "pre_veto_strength": payload.get("pre_veto_strength"),
        "level": payload.get("level"),
        "direction": payload.get("direction") or "neutral",
        "rank": payload.get("rank"),
        "is_weak": bool(payload.get("is_weak")),
        "reject_reason": payload.get("reject_reason"),
        "data_quality": {
            "status": quality.get("status"),
            "completeness": quality.get("completeness"),
            "reason_codes": reason_codes,
            "reasons": [dict(reason) for reason in (quality.get("reasons") or [])
                        if isinstance(reason, dict)],
        },
        "risk_event_codes": [str(event.get("event_id") or "").split("@")[0]
                             for event in (payload.get("risk_events") or [])
                             if isinstance(event, dict)],
        "raw_factors": _raw_subset_from_payload(payload),
        "dim_contributions": _dim_contributions_from_payload(payload),
        "evidence": {key: evidence.get(key) for key in (
            "last_time", "official_open_px", "last_indicative_price",
            "last_matched_lots", "final_unmatched_lots_signed")},
        "reference_prices": {
            "prev_close": reference.get("prev_close"),
            "official_open": reference.get("official_open"),
            "last_indicative": reference.get("last_indicative"),
        },
        "windows": {"core": windows.get("core")},
        "actions": [dict(action) for action in actions
                    if isinstance(action, dict)],
        "unknown_reason": (reason_codes or [None])[0]
        if status == "unavailable" else None,
    }


def _position_from_verdict(verdict, name: str) -> dict:
    return position_from_payload(verdict_to_payload(verdict), name)


def _simple_position(code: str, status: str, unknown_reason: str,
                     actions: list, name: str = "") -> dict:
    return {
        "code": code, "name": name, "status": status,
        "strength": None, "pre_veto_strength": None, "level": None,
        "direction": "neutral", "rank": None, "is_weak": False,
        "reject_reason": None,
        "data_quality": {"status": "unavailable", "completeness": 0.0,
                         "reason_codes": [unknown_reason], "reasons": []},
        "risk_event_codes": [], "raw_factors": {}, "dim_contributions": {},
        "evidence": {}, "reference_prices": {}, "windows": {},
        "actions": actions, "unknown_reason": unknown_reason,
    }


def _finalize_context(trade_date: str, positions: dict, *, mode: str,
                      names: Optional[dict] = None,
                      failed: Optional[list] = None,
                      empty: Optional[list] = None,
                      candidate_codes=None,
                      pool: Optional[dict] = None,
                      empty_reason: Optional[str] = None) -> dict:
    """合并 failed/empty/unknown 并输出稳定的 ``auction_sc_v1`` 结构。"""
    names = names or {}
    failed = failed or []
    empty = list(empty or [])

    for item in failed:
        code = item.get("code")
        if not code:
            continue
        stage = item.get("stage")
        if stage == "snapshot":
            reason = "snapshot_corrupt"
            actions = [{"kind": "request_data",
                        "text": "已存竞价快照损坏，无法投影",
                        "requires": "auction_snapshot"}]
        else:
            reason = "auction_fetch_failed" if stage == "auction" else str(
                stage or "failed")
            actions = [{"kind": "request_data",
                        "text": "竞价明细采集失败，本只不可评价",
                        "requires": "auction_items"}]
        positions[code] = _simple_position(
            code, "failed", reason, actions, name=names.get(code, ""))

    for code in empty:
        positions[code] = _simple_position(
            code, "empty", "no_auction_items",
            [{"kind": "request_data", "text": "当日无竞价盘口，不可评价",
              "requires": "auction_items"}],
            name=names.get(code, ""))

    if mode == "collect":
        for code in sorted(candidate_codes or ()):
            if code in positions:
                continue
            positions[code] = _simple_position(
                code, "unknown", "no_asset",
                [{"kind": "request_data", "text": "未采集到竞价资产，本只不可评价",
                  "requires": "auction_items"}],
                name=names.get(code, ""))

    ordered = [positions[code] for code in sorted(positions)]

    summary = {"known": len(ordered), "scored": 0, "vetoed": 0,
               "unavailable": 0, "empty": 0, "failed": 0, "unknown": 0}
    for position in ordered:
        status = position["status"]
        if status in summary:
            summary[status] += 1

    if pool is None:
        pool = ({"source": "recompute", "candidate_codes": len(ordered),
                 "coverage": "assets_only"} if mode == "recompute"
                else {"source": None, "fallback_reason": None,
                      "snapshot_date": None,
                      "candidate_codes": len(candidate_codes or ())})

    return {
        "context_version": CONTEXT_VERSION,
        "trade_date": _compact_date(trade_date),
        "source": "v2",
        "generated_by": "auction",
        "as_of": as_of(trade_date),
        "config_version": R.CONFIG_VERSION,
        "config_hash": R.CONFIG_HASH,
        "validation_status": R.VALIDATION_STATUS,
        "semantics": {"semantics_version": R.SEMANTICS_VERSION,
                      "core_window": list(R.SEMANTICS["core_window"])},
        "pool": dict(pool),
        "empty_reason": empty_reason,
        "positions": ordered,
        "summary": summary,
        "disclaimer": DISCLAIMER,
    }


def build_strategy_context(trade_date: str, *, mode: str,
                           evaluations: list, names: Optional[dict] = None,
                           failed: Optional[list] = None,
                           empty: Optional[list] = None,
                           candidate_codes=None,
                           pool: Optional[dict] = None,
                           empty_reason: Optional[str] = None) -> dict:
    """投影 ``auction_sc_v1`` 上下文。"""
    names = names or {}
    positions: dict[str, dict] = {}

    for verdict in evaluations:
        positions[verdict.code] = _position_from_verdict(
            verdict, names.get(verdict.code) or verdict.name or "")

    return _finalize_context(
        trade_date, positions, mode=mode, names=names, failed=failed,
        empty=empty, candidate_codes=candidate_codes, pool=pool,
        empty_reason=empty_reason)


def build_strategy_context_from_snapshots(trade_date: str, payloads: dict, *,
                                          failed: Optional[list] = None,
                                          names: Optional[dict] = None) -> dict:
    """从已存 ``analysis_json`` 只读投影上下文（不重算强度、不读候选池）。"""
    names = names or {}
    payloads = payloads or {}
    positions: dict[str, dict] = {}
    for code in sorted(payloads):
        positions[code] = position_from_payload(
            payloads[code], names.get(code) or "")

    failed = list(failed or [])
    involved = set(payloads)
    involved.update(item.get("code") for item in failed if item.get("code"))
    versions = sorted({payload.get("config_version")
                       for payload in payloads.values()
                       if isinstance(payload, dict)
                       and payload.get("config_version")})
    pool = {
        "source": "snapshot",
        "coverage": "assets_only",
        "candidate_codes": len(involved),
        "snapshot_config_versions": versions,
    }
    return _finalize_context(
        trade_date, positions, mode="snapshot", names=names, failed=failed,
        empty=[], candidate_codes=None, pool=pool, empty_reason=None)


# 供测试 / 展示复用：状态优先级（同一 code 只出现一次）
def status_priority(status: str) -> int:
    return _STATUS_PRIORITY.get(status, 99)
