"""premarket.context — ``premarket_sc_v1`` 纯投影构建。

输入只有 snapshot Adapter 输出的 prep / auction 信封；输出固定键序的只读上下文。
本模块不读时钟 / 环境 / 文件 / 数据库，不发请求、不重算评分；``run_at`` 由
Facade 注入，``local_trade_days`` 只用于 ``proven_gap`` 单边证明。

时间语义严格分离：

* ``run_at`` / ``info_cutoff``：本次运行；
* ``prep.frozen_at``：只取 ``result.strategy_context.frozen_at``（缺失即 null）；
* ``prep.created_at``：快照行写入时间；
* ``auction.as_of`` / ``ready_at``：由 ``trade_date`` 推导的 09:25 语义时点；
* ``auction.positions[].created_at``：快照行写入时间；
* ``evidence.last_time``：盘口最后一笔时间。
"""

from __future__ import annotations

from datetime import date
from typing import Any, Mapping, Optional

from .actions import build_ceiling
from .anchors import build_anchors
from .snapshot import AUCTION_SUPPORTED_SCHEMA

CONTEXT_VERSION = "premarket_sc_v1"
DISCLAIMER = "只读盘前上下文，不构成投资建议；阈值为待验证基线，不承诺收益。"
CACHE_NOTE = "cache_status_unknown：auction_snapshots 未持久化 from_cache"
TRADING_DAY_NOTE = "trading_day_unverified：离线无交易日历"
POOL_RULE = "eligibility==eligible AND position_pct>0"
POOL_NOTE = "extra_codes 只说明快照含非 T-1 推荐 code，不据此判定 auction 回退"

STATES = ("confirmed", "weakened", "invalidated", "unknown")
_EVIDENCE_KEYS = ("last_time", "official_open_px", "last_indicative_price",
                  "last_matched_lots", "final_unmatched_lots_signed")
_RAW_KEYS = ("official_open_gap_pct", "auction_turnover_pct",
             "volume_ratio_5d", "final_unmatched_matched_ratio",
             "post20_price_return_pct", "tail_matched_acceleration",
             "final_unmatched_lots_signed")


# ─── 工具 ──────────────────────────────────────────────────────────────────


def _as_dict(value: Any) -> dict:
    return dict(value) if isinstance(value, Mapping) else {}


def _as_list(value: Any) -> list:
    return list(value) if isinstance(value, (list, tuple)) else []


def _as_str(value: Any) -> Optional[str]:
    return value if isinstance(value, str) and value else None


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _as_date(value: Any) -> Optional[date]:
    if not isinstance(value, str):
        return None
    text = value.replace("/", "-")
    if len(text) == 8 and text.isdigit():
        text = f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _iso_date(value: Any, fallback: str) -> str:
    parsed = _as_date(value)
    return parsed.isoformat() if parsed else fallback


def _unique_codes(values) -> list[str]:
    return sorted({value for value in values
                   if isinstance(value, str) and value})


# ─── 候选 auction join 视图 ────────────────────────────────────────────────


def _quality(value: Any) -> dict:
    source = _as_dict(value)
    codes = source.get("reason_codes")
    return {
        "status": source.get("status"),
        "completeness": source.get("completeness"),
        "reason_codes": [str(code) for code in codes]
        if isinstance(codes, list) else [],
    }


def _status(analysis: Mapping[str, Any]) -> str:
    if analysis.get("is_weak"):
        return "vetoed"
    quality = analysis.get("data_quality")
    if not isinstance(quality, Mapping):
        return "unavailable"
    if quality.get("status") == "unavailable":
        return "unavailable"
    return "scored"


def _empty_auction_view(*, present: bool, reason: str) -> dict:
    return {
        "present": present,
        "status": None,
        "strength": None,
        "level": None,
        "direction": None,
        "rank": None,
        "is_weak": False,
        "reject_reason": None,
        "data_quality": {"status": None, "completeness": None,
                         "reason_codes": []},
        "risk_event_codes": [],
        "evidence": {},
        "raw_factors": {},
        "reference_prices": {},
        "windows": {},
        "actions": [],
        "unknown_reason": reason,
        "created_at": None,
    }


def _auction_view(position: Optional[Mapping[str, Any]]) -> dict:
    if not isinstance(position, Mapping):
        return _empty_auction_view(present=False, reason="no_snapshot_row")
    created_at = position.get("created_at")
    error = position.get("error")
    analysis = position.get("analysis")
    if error or not isinstance(analysis, Mapping):
        view = _empty_auction_view(present=True, reason="snapshot_corrupt")
        view["status"] = "unavailable"
        view["data_quality"] = {"status": "unavailable", "completeness": None,
                                "reason_codes": ["snapshot_corrupt"]}
        view["created_at"] = created_at
        return view

    quality = _as_dict(analysis.get("data_quality"))
    status = _status(analysis)
    reason_codes = _quality(quality)["reason_codes"]
    unknown_reason = None
    if not quality:
        unknown_reason = "snapshot_legacy_schema"
    elif status == "unavailable":
        unknown_reason = reason_codes[0] if reason_codes else "data_unavailable"

    evidence = _as_dict(analysis.get("evidence"))
    raw = _as_dict(analysis.get("raw_factors"))
    reference = _as_dict(analysis.get("reference_prices"))
    windows = _as_dict(analysis.get("windows"))
    risk_events = _as_list(analysis.get("risk_events"))
    return {
        "present": True,
        "status": status,
        "strength": analysis.get("strength"),
        "level": analysis.get("level"),
        "direction": analysis.get("direction") or "neutral",
        "rank": analysis.get("rank"),
        "is_weak": bool(analysis.get("is_weak")),
        "reject_reason": analysis.get("reject_reason"),
        "data_quality": _quality(quality),
        "risk_event_codes": [
            str(event.get("event_id") or "").split("@")[0]
            for event in risk_events if isinstance(event, Mapping)],
        "evidence": {key: evidence.get(key) for key in _EVIDENCE_KEYS},
        "raw_factors": {key: raw.get(key) for key in _RAW_KEYS},
        "reference_prices": {
            "prev_close": reference.get("prev_close"),
            "official_open": reference.get("official_open"),
            "last_indicative": reference.get("last_indicative"),
        },
        "windows": {"core": windows.get("core")},
        "actions": [dict(action) for action in _as_list(analysis.get("actions"))
                    if isinstance(action, Mapping)],
        "unknown_reason": unknown_reason,
        "created_at": created_at,
    }


# ─── 冻结动作映射 ──────────────────────────────────────────────────────────


def _feedback_actions(result: Mapping[str, Any]) -> tuple[dict, dict]:
    context = _as_dict(result.get("strategy_context"))
    plans = _as_list(context.get("feedback_plans"))
    by_code: dict[str, dict] = {}
    defaults: dict[str, list[str]] = {}
    for plan in plans:
        if not isinstance(plan, Mapping):
            continue
        code = _as_str(plan.get("code"))
        if not code:
            continue
        mapping: dict[str, list[str]] = {}
        for condition in _as_list(plan.get("conditions")):
            if not isinstance(condition, Mapping):
                continue
            state = condition.get("state")
            actions = condition.get("allowed_actions")
            if state in STATES and isinstance(actions, list):
                mapping[state] = [action for action in actions
                                  if isinstance(action, str)]
        by_code[code] = mapping
        action_list = plan.get("allowed_actions")
        defaults[code] = [action for action in action_list
                          if isinstance(action, str)] \
            if isinstance(action_list, list) else []
    return by_code, defaults


def _normalized_actions(mapping: Mapping[str, Any]) -> dict[str, list[str]]:
    from .actions import normalize_after_verification

    return normalize_after_verification(mapping)


# ─── 池 / 数据质量 / 候选 ──────────────────────────────────────────────────


def _snapshot_codes(auction: Mapping[str, Any]) -> list[str]:
    return _unique_codes(position.get("code")
                         for position in _as_list(auction.get("positions"))
                         if isinstance(position, Mapping))


def _build_pool(prep: Mapping[str, Any], auction: Mapping[str, Any],
                review_candidates: list[dict], review_usable: bool) -> dict:
    snapshot_codes = _snapshot_codes(auction)
    pool = {
        "available": False,
        "source": "unavailable",
        "rule": POOL_RULE,
        "prep_trade_date": prep.get("trade_date"),
        "eligible_codes": [],
        "snapshot_codes": snapshot_codes,
        "missing_codes": [],
        "extra_codes": [],
        "reconstructed": False,
        "fallback_observed": None,
        "note": POOL_NOTE,
    }
    if not review_usable:
        return pool

    eligible = []
    review_codes = set()
    for candidate in review_candidates:
        code = _as_str(candidate.get("code"))
        if not code:
            continue
        review_codes.add(code)
        position = _number(candidate.get("position_pct"))
        if candidate.get("eligibility") == "eligible" and position is not None \
                and position > 0:
            eligible.append(code)
    eligible = _unique_codes(eligible)
    missing = sorted(set(eligible) - set(snapshot_codes))
    extra = sorted(set(snapshot_codes) - review_codes)
    pool.update({
        "available": True,
        "source": "derived_from_prep_review_candidates",
        "eligible_codes": eligible,
        "missing_codes": missing,
        "extra_codes": extra,
        "reconstructed": True,
    })
    return pool


def _prep_degraded(prep: Mapping[str, Any], *, proven_gap: bool) -> list[str]:
    reasons: list[str] = []
    state = prep.get("state")
    if state == "missing":
        reasons.append("prep_missing")
    elif state == "corrupt":
        reasons.append("prep_corrupt")
    elif state == "read_error":
        reasons.append("prep_read_error")
    elif state == "unsupported_schema":
        reasons.append("prep_schema_unsupported")

    if prep.get("available"):
        if not prep.get("review_available"):
            review = _as_dict(_as_dict(prep.get("result")).get("review"))
            if review and review.get("enabled") is False:
                reasons.append("prep_review_disabled")
            else:
                reasons.append("prep_review_unavailable")
        config = prep.get("review_config")
        if isinstance(config, Mapping) and config.get("config_version") is None:
            reasons.append("prep_config_missing")
        if prep.get("frozen_at") is None:
            reasons.append("prep_frozen_at_missing")
    if proven_gap:
        reasons.append("prep_not_strict_t_minus_1")
    return reasons


def _auction_degraded(auction: Mapping[str, Any],
                      pool: Mapping[str, Any]) -> list[str]:
    reasons: list[str] = []
    state = auction.get("state")
    weekend = bool(auction.get("weekend"))
    if state == "not_ready":
        reasons.append("auction_not_ready_before_0925")
    elif weekend:
        reasons.append("auction_weekend")
    elif state == "missing":
        reasons.append("auction_missing")
    elif state == "corrupt":
        reasons.append("auction_all_rows_corrupt")
    elif state == "read_error":
        reasons.append("auction_read_error")
    if state == "partial":
        if _as_list(auction.get("corrupt_codes")):
            reasons.append("auction_corrupt_rows")
        if _as_list(auction.get("legacy_schema_codes")):
            reasons.append("auction_legacy_schema_rows")

    config = _as_dict(auction.get("config"))
    if (int(auction.get("row_count") or 0) > 0
            and state not in ("corrupt", "read_error")
            and not _as_list(config.get("config_versions"))):
        reasons.append("auction_config_missing")
    if config.get("mixed"):
        reasons.append("auction_config_mixed")

    if not pool.get("available"):
        reasons.append("auction_pool_unavailable")
    if _as_list(pool.get("missing_codes")):
        reasons.append("auction_pool_coverage_gap")
    return reasons


def _prep_unusable_reason(prep: Mapping[str, Any]) -> str:
    if not prep.get("available"):
        state = prep.get("state")
        if state == "corrupt":
            return "prep_corrupt"
        if state == "read_error":
            return "prep_read_error"
        return "prep_unavailable"
    if not prep.get("schema_supported"):
        return "prep_schema_unsupported"
    review = _as_dict(_as_dict(prep.get("result")).get("review"))
    if review and review.get("enabled") is False:
        return "prep_review_disabled"
    return "prep_review_unavailable"


def _mainline(value: Any) -> Optional[dict]:
    source = _as_dict(value)
    if not source:
        return None
    return {
        "board_name": source.get("board_name"),
        "rank": source.get("rank"),
        "state": source.get("state"),
    }


def _candidate(candidate: Mapping[str, Any], *, order: int,
               prep: Mapping[str, Any], auction_view: dict,
               actions_mapping: dict, actions_default: list[str],
               facts: Optional[Mapping[str, Any]], plan_usable: bool,
               liquidatable_auction: bool) -> dict:
    prep_block = {
        "source_trade_date": prep.get("trade_date"),
        "eligibility": candidate.get("eligibility"),
        "position_pct": candidate.get("position_pct"),
        "position_basis": _as_list(candidate.get("position_basis")),
        "reason_code": candidate.get("reason_code"),
        "review_score": candidate.get("review_score"),
        "review_grade": candidate.get("review_grade"),
        "confidence": candidate.get("confidence"),
        "risk_level": candidate.get("risk_level"),
        "gate_trace": _as_list(candidate.get("gate_trace")),
        "warnings": _as_list(candidate.get("warnings")),
        "facts": dict(facts) if isinstance(facts, Mapping) else None,
        "expectations": candidate.get("expectations"),
        "allowed_actions_by_state": _normalized_actions(actions_mapping),
        "default_allowed_actions": list(actions_default) or ["observe_only"],
    }
    ceiling = build_ceiling(
        prep_usable=plan_usable,
        prep_reason=_prep_unusable_reason(prep) if not plan_usable else None,
        eligibility=candidate.get("eligibility"),
        position_pct=candidate.get("position_pct"),
        expectations_present=isinstance(candidate.get("expectations"), Mapping),
        auction_present=auction_view["present"],
        auction_usable=bool(liquidatable_auction),
        auction_status=auction_view["status"],
        is_weak=auction_view["is_weak"],
        reject_reason=auction_view["reject_reason"],
        auction_quality=auction_view["data_quality"]["status"],
        after_verification=actions_mapping,
    )
    facts_source = facts if isinstance(facts, Mapping) else {}
    limit_up_type = facts_source.get("limit_up_type")
    prev_close = auction_view["reference_prices"].get("prev_close")
    official_open = auction_view["reference_prices"].get("official_open")
    return {
        "code": candidate.get("code"),
        "name": candidate.get("name"),
        "order": order,
        "mainline": _mainline(candidate.get("mainline")),
        "role": candidate.get("role"),
        "prep": prep_block,
        "auction": auction_view,
        "action_ceiling": ceiling,
        "execution_constraints": {
            "limit_up_type": limit_up_type,
            "one_line_board": (
                "一字" in limit_up_type if isinstance(limit_up_type, str)
                else None),
            "open_num": facts_source.get("open_num"),
            "prev_close": prev_close,
            "official_open": official_open,
            "gap_pct": auction_view["raw_factors"].get(
                "official_open_gap_pct"),
            "slippage_estimate": None,
            "t_plus_1": True,
        },
    }


# ─── 对外入口 ──────────────────────────────────────────────────────────────


def build_context(prep: Mapping[str, Any], auction: Mapping[str, Any], *,
                  trade_date: str, run_at: str,
                  local_trade_days: Optional[list[str]] = None) -> dict:
    """组装 ``premarket_sc_v1``（纯函数，键序固定）。"""

    prep = prep if isinstance(prep, Mapping) else {}
    auction = auction if isinstance(auction, Mapping) else {}
    iso_trade_date = _iso_date(trade_date, str(trade_date))
    trade_day = _as_date(iso_trade_date)

    result = _as_dict(prep.get("result"))
    review = _as_dict(result.get("review"))
    review_available = bool(prep.get("available")) and bool(
        prep.get("review_available")) and bool(review)
    plan_usable = bool(prep.get("available")) and bool(
        prep.get("schema_supported")) and review_available
    auction_usable = bool(auction.get("available")) and auction.get(
        "state") in ("ready", "partial")

    review_candidates = [candidate for candidate
                         in _as_list(result.get("review_candidates"))
                         if isinstance(candidate, Mapping) and _as_str(
                             candidate.get("code"))]
    facts_by_code = {
        row.get("code"): row.get("facts")
        for row in _as_list(result.get("candidates"))
        if isinstance(row, Mapping) and isinstance(row.get("code"), str)
    }
    actions_by_code, actions_defaults = _feedback_actions(result)

    prep_date = _as_date(prep.get("trade_date"))
    age_days = None
    proven_gap = None
    if prep.get("available") and prep_date and trade_day:
        age_days = (trade_day - prep_date).days
        gap_days = [day for day in (local_trade_days or [])
                    if _as_date(day) and prep_date < _as_date(day) < trade_day]
        proven_gap = True if gap_days else None

    pool = _build_pool(prep, auction, review_candidates,
                       review_usable=review_available)
    degraded = (_prep_degraded(prep, proven_gap=bool(proven_gap))
                + _auction_degraded(auction, pool))
    # 稳定去重（保持首次出现顺序）。
    degraded = list(dict.fromkeys(degraded))

    auction_ok = auction.get("state") == "ready"
    if not prep.get("available") and not auction.get("available"):
        quality_status = "unavailable"
    elif plan_usable and auction_ok and not degraded:
        quality_status = "ok"
    else:
        quality_status = "degraded"

    positions_by_code = {
        position.get("code"): position
        for position in _as_list(auction.get("positions"))
        if isinstance(position, Mapping) and _as_str(position.get("code"))
    }
    names = {candidate["code"]: candidate.get("name")
             for candidate in review_candidates}

    candidates = []
    for index, candidate in enumerate(review_candidates, start=1):
        code = candidate["code"]
        position = positions_by_code.get(code)
        row_usable = bool(position.get("usable")) \
            if isinstance(position, Mapping) else False
        candidates.append(_candidate(
            candidate,
            order=index,
            prep=prep,
            auction_view=_auction_view(position),
            actions_mapping=actions_by_code.get(code, {}),
            actions_default=actions_defaults.get(code, []),
            facts=facts_by_code.get(code),
            plan_usable=plan_usable,
            liquidatable_auction=auction_usable and row_usable,
        ))

    compat_versions = sorted({
        str(version)
        for position in _as_list(auction.get("positions"))
        if isinstance(position, Mapping)
        for analysis in [position.get("analysis")]
        if isinstance(analysis, Mapping)
        for version in [analysis.get("version")]
        if isinstance(version, str) and version
    })
    auction_supported = bool(compat_versions) and all(
        version == AUCTION_SUPPORTED_SCHEMA for version in compat_versions)

    market_source = _as_dict(review.get("market"))
    if prep.get("available") and review_available:
        market = {
            "state": market_source.get("state"),
            "score": market_source.get("score"),
            "coeff": market_source.get("coeff"),
            "anchors": _as_dict(_as_dict(review.get("anchors")).get("risk")),
            "evidence": _as_list(market_source.get("evidence")),
            "unknown_reason": (market_source.get("unknown_reason")
                               or ("market_unavailable"
                                   if not market_source else None)),
        }
    else:
        market = {
            "state": None,
            "score": None,
            "coeff": None,
            "anchors": {},
            "evidence": [],
            "unknown_reason": ("prep_unavailable"
                               if not prep.get("available")
                               else "review_unavailable"),
        }

    portfolio_source = _as_dict(review.get("portfolio"))
    same_mainline: dict[str, int] = {}
    for candidate in candidates:
        mainline = candidate.get("mainline")
        board = mainline.get("board_name") if isinstance(mainline, Mapping) \
            else None
        if isinstance(board, str) and board:
            same_mainline[board] = same_mainline.get(board, 0) + 1
    portfolio = {
        "sum_pct": portfolio_source.get("sum_pct"),
        "warning": portfolio_source.get("warning"),
        "same_mainline_counts": {
            board: same_mainline[board] for board in sorted(same_mainline)},
    }

    prep_known = [
        {
            "code": candidate.get("code"),
            "name": candidate.get("name"),
            "risk_level": candidate.get("risk_level"),
            "risk_announcement": _as_dict(
                facts_by_code.get(candidate.get("code"))).get(
                    "risk_announcement"),
            "warnings": _as_list(candidate.get("warnings")),
        }
        for candidate in review_candidates
    ] if prep.get("available") else []

    auction_out = {
        "available": bool(auction.get("available")),
        "state": auction.get("state") or "missing",
        "trade_date": auction.get("trade_date") or iso_trade_date,
        "as_of": auction.get("as_of"),
        "ready_at": auction.get("ready_at"),
        "row_count": int(auction.get("row_count") or 0),
        "pre_ready_rows_present": bool(auction.get("pre_ready_rows_present")),
        "corrupt_codes": _as_list(auction.get("corrupt_codes")),
        "legacy_schema_codes": _as_list(auction.get("legacy_schema_codes")),
        "created_at": _as_dict(auction.get("created_at")) or {
            "min": None, "max": None},
        "config": _as_dict(auction.get("config")) or {
            "config_versions": [], "config_hashes": [],
            "semantics_versions": [], "validation_status": [],
            "mixed": False},
        "cache": _as_dict(auction.get("cache")) or {
            "status": "unknown", "reason": "not_persisted_in_snapshot"},
        "pool": pool,
        "positions": [
            {**dict(position),
             "name": names.get(position.get("code"))
             or position.get("name") or "",
             "planned": (position.get("code") in {
                 candidate["code"] for candidate in review_candidates})
             if review_available else bool(position.get("planned"))}
            for position in _as_list(auction.get("positions"))
            if isinstance(position, Mapping)
        ],
        "unplanned_codes": list(pool["extra_codes"])
        if review_available else [],
    }

    prep_out = {
        "available": bool(prep.get("available")),
        "state": prep.get("state") or "missing",
        "trade_date": prep.get("trade_date"),
        "strictness": (
            None if not prep.get("trade_date")
            else "proven_not_t_minus_1" if proven_gap
            else "latest_strictly_before"),
        "proven_gap": proven_gap,
        "age_calendar_days": age_days,
        "created_at": prep.get("created_at"),
        "frozen_at": prep.get("frozen_at"),
        "frozen_at_reason": prep.get("frozen_at_reason"),
        "schema_version": prep.get("schema_version"),
        "schema_supported": bool(prep.get("schema_supported")),
        "review_available": review_available,
        "review_config": prep.get("review_config"),
        "result": result if prep.get("available") else None,
    }

    return {
        "ok": True,
        "context_version": CONTEXT_VERSION,
        "generated_by": "premarket",
        "source": "v2",
        "trade_date": iso_trade_date,
        "run_at": run_at,
        "info_cutoff": run_at,
        "trading_day_verified": False,
        "compatibility": {
            "premarket": CONTEXT_VERSION,
            "prep_schema": prep.get("schema_version"),
            "prep_supported": bool(prep.get("schema_supported")),
            "auction_schemas": compat_versions,
            "auction_supported": auction_supported,
        },
        "as_of": {
            "prep_frozen_at": prep.get("frozen_at"),
            "auction_as_of": auction.get("as_of"),
            "auction_ready_at": auction.get("ready_at"),
        },
        "prep": prep_out,
        "auction": auction_out,
        "gates": {
            "data": {"status": quality_status, "reasons": list(degraded)},
            "hard_risk": {
                "status": "external_required",
                "prep_known": prep_known,
                "overnight_external": "agent_evidence_required",
            },
            "account": {
                "status": "unknown",
                "reason": "not_provided_by_cli",
                "requires": ["existing_positions", "risk_budget",
                             "max_position"],
            },
        },
        "market": market,
        "anchors": build_anchors(prep),
        "portfolio": portfolio,
        "candidates": candidates,
        "data_quality": {
            "status": quality_status,
            "plan_usable": plan_usable,
            "auction_usable": auction_usable,
            "degraded": list(degraded),
            "coverage": {
                "prep_rows": 1 if prep.get("available") else 0,
                "auction_rows": int(auction.get("row_count") or 0),
                "expected_pool_codes": len(pool["eligible_codes"]),
                "snapshot_codes": len(pool["snapshot_codes"]),
                "candidates": len(candidates),
            },
            "notes": [CACHE_NOTE, TRADING_DAY_NOTE],
        },
        "disclaimer": DISCLAIMER,
    }


__all__ = [
    "CACHE_NOTE",
    "CONTEXT_VERSION",
    "DISCLAIMER",
    "TRADING_DAY_NOTE",
    "build_context",
]
