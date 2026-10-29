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
    raw = raw or {}
    parts = []
    gap = raw.get("official_open_gap_pct")
    if gap is not None:
        parts.append(f"开{gap:+.1f}%")
    turnover = raw.get("auction_turnover_pct")
    if turnover is not None:
        parts.append(f"换手{turnover:.2f}%")
    accel = raw.get("tail_matched_acceleration")
    if accel is not None:
        parts.append(f"末段匹配×{accel:.1f}")
    ratio = raw.get("final_unmatched_matched_ratio")
    if ratio is not None:
        if ratio >= 0:
            parts.append(f"买余{ratio:.1f}x")
        else:
            parts.append(f"卖余{abs(ratio):.1f}x")
    if not parts:
        codes = list(reason_codes or [])
        return f"数据不足：{codes[0]}" if codes else None
    return " ".join(parts[:3])


def evidence_summary(verdict) -> Optional[str]:
    """一条确定性的关键证据短句（最多 3 项），供 CLI / Skill 直读。"""
    return _summary_parts(verdict.raw_factors or {},
                          verdict.data_quality.reason_codes)


def evidence_summary_from_payload(payload: dict) -> Optional[str]:
    """从已存 ``analysis_json`` 重算同一证据短句（确定性，无额外状态）。"""
    quality = payload.get("data_quality") or {}
    return _summary_parts(payload.get("raw_factors") or {},
                          quality.get("reason_codes") or [])


def _status_of(verdict) -> str:
    if verdict.is_weak:
        return "vetoed"
    if verdict.data_quality.status == "unavailable":
        return "unavailable"
    return "scored"


def _dim_contributions(verdict) -> dict:
    result = {}
    for dim in R.DIM_ORDER:
        item = verdict.dims.get(dim)
        if item is None or item.score is None:
            continue
        result[dim] = {
            "score": item.score,
            "weight": item.weight,
            "effective_weight": item.effective_weight,
            "contribution": round(item.weight * item.score, 2),
        }
    return result


def _raw_subset(verdict) -> dict:
    raw = verdict.raw_factors or {}
    return {key: raw.get(key) for key in _RAW_SUBSET if key in raw}


def _action_payload(verdict) -> list:
    return [dict(action) for action in (verdict.actions or [])]


def _position_from_verdict(verdict, name: str) -> dict:
    status = _status_of(verdict)
    return {
        "code": verdict.code,
        "name": name,
        "status": status,
        "strength": verdict.strength,
        "pre_veto_strength": verdict.pre_veto_strength,
        "level": verdict.level,
        "direction": verdict.direction,
        "rank": verdict.rank,
        "is_weak": verdict.is_weak,
        "reject_reason": verdict.reject_reason,
        "data_quality": {
            "status": verdict.data_quality.status,
            "completeness": verdict.data_quality.completeness,
            "reason_codes": verdict.data_quality.reason_codes,
            "reasons": [dict(reason) for reason in verdict.data_quality.reasons],
        },
        "risk_event_codes": [event.event_id.split("@")[0]
                             for event in verdict.risk_events],
        "raw_factors": _raw_subset(verdict),
        "dim_contributions": _dim_contributions(verdict),
        "evidence": {key: (verdict.evidence or {}).get(key) for key in (
            "last_time", "official_open_px", "last_indicative_price",
            "last_matched_lots", "final_unmatched_lots_signed")},
        "reference_prices": {
            "prev_close": (verdict.reference_prices or {}).get("prev_close"),
            "official_open": (verdict.reference_prices or {}).get("official_open"),
            "last_indicative": (verdict.reference_prices or {}).get("last_indicative"),
        },
        "windows": {"core": (verdict.windows or {}).get("core")},
        "actions": _action_payload(verdict),
        "unknown_reason": (verdict.data_quality.reason_codes or [None])[0]
        if status == "unavailable" else None,
    }


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


def build_strategy_context(trade_date: str, *, mode: str,
                           evaluations: list, names: Optional[dict] = None,
                           failed: Optional[list] = None,
                           empty: Optional[list] = None,
                           candidate_codes=None,
                           pool: Optional[dict] = None,
                           empty_reason: Optional[str] = None) -> dict:
    """投影 ``auction_sc_v1`` 上下文。"""
    names = names or {}
    failed = failed or []
    empty = list(empty or [])
    positions: dict[str, dict] = {}

    for verdict in evaluations:
        positions[verdict.code] = _position_from_verdict(
            verdict, names.get(verdict.code) or verdict.name or "")

    for item in failed:
        code = item.get("code")
        if not code:
            continue
        reason = "auction_fetch_failed" if item.get("stage") == "auction" else str(
            item.get("stage") or "failed")
        positions[code] = _simple_position(
            code, "failed", reason,
            [{"kind": "request_data", "text": "竞价明细采集失败，本只不可评价",
              "requires": "auction_items"}],
            name=names.get(code, ""))

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


# 供测试 / 展示复用：状态优先级（同一 code 只出现一次）
def status_priority(status: str) -> int:
    return _STATUS_PRIORITY.get(status, 99)
