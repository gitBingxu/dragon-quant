"""``/prep`` 视图投影：资产三态/合法豁免、derived(high_num)、跨日与成效（方案 §5.1–§5.3）。

全部是纯函数：输入是 assets.db 读出的普通 dict（或 None），输出是 API 视图模型。
不读库、不写库、不触网、不读时钟（``today`` 由调用方注入，测试可固定）。
"""

from __future__ import annotations

import re
from typing import Any, Mapping

from dragon_quant.infra.board_height import (
    board_height_for_display,
    needs_recompute,
    parse_board_height,
)

# 13 类业务资产 + ``collection_report`` 元数据（固定清单，既有契约）。
ASSET_TYPES = [
    "dragons",
    "ths_limit_up_pool",
    "ths_block_top",
    "market_breadth",
    "fg_boards",
    "holdings",
    "fund_flow",
    "cninfo_announcements",
    "macro_global",
    "stock_daily",
    "prev_plan",
    "auction_feedback",
    "t1_intraday",
]

# 资产类别（Strategy 表驱动）：只决定 expected / legal_absence，不再决定
# 「异常是否可忽略」——所有 correct 期望的资产都参与完备判定。
ASSET_CATEGORIES: dict[str, str] = {
    "dragons": "core",
    "ths_limit_up_pool": "core",
    "ths_block_top": "core",
    "macro_global": "core",
    "market_breadth": "same_day_only",
    "fg_boards": "same_day_only",
    "fund_flow": "same_day_only",
    "stock_daily": "review_conditional",
    "holdings": "auxiliary",
    "cninfo_announcements": "auxiliary",
    "prev_plan": "auxiliary",
    "auction_feedback": "auxiliary",
    "t1_intraday": "auxiliary",
    "collection_report": "report",
}

# 真实业务空态：无持仓 / 无公告。其它 expected 资产的 empty 都不是合法空态。
LEGAL_EMPTY = frozenset({"holdings", "cninfo_announcements"})
BAD_STATUS = frozenset({"error", "empty_suspicious", "unavailable"})
# 既有的「展示可用」非空要求（保持旧契约不变）。
REQUIRED_NONEMPTY = frozenset({"dragons", "ths_limit_up_pool"})

STATE_LABELS = {
    "new": "新进主线",
    "strengthening": "走强",
    "stable": "稳定",
    "weakening": "走弱",
    "unknown": "数据不足",
}
UNKNOWN_REASON_LABELS = {
    "prev_snapshot_missing": "前日无快照",
    "prev_snapshot_corrupt": "前日快照损坏",
    "prev_mainlines_missing": "前日无主线",
    "prev_mainline_score_missing": "前日主线缺分",
    "mainline_score_missing": "当日主线缺分",
    "mainline_prev_rank_missing": "前日无该主线排名",
    "mainline_change_below_threshold": "变化未达阈值",
    "board_members_missing": "缺成分名单",
}

_PACKED_EVIDENCE = re.compile(r"最高\s*(\d+)\s*板")

__all__ = [
    "ASSET_CATEGORIES",
    "ASSET_TYPES",
    "LEGAL_EMPTY",
    "STATE_LABELS",
    "UNKNOWN_REASON_LABELS",
    "build_asset_rows",
    "build_calendar",
    "build_review_context",
    "build_snapshot_derived",
    "summarize_expected",
]


# ─── 通用取值 helper（不编造 0/50） ───────────────────────────────────────


def _as_dict(value: Any) -> dict | None:
    return value if isinstance(value, dict) else None


def _as_list(value: Any) -> list[dict] | None:
    if not isinstance(value, list):
        return None
    return [item for item in value if isinstance(item, dict)]


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _payload_empty(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return True
    for key in ("rows", "items"):
        value = payload.get(key)
        if isinstance(value, (list, dict)) and value:
            return False
    for key in ("count", "total"):
        value = payload.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value:
            return False
    return True


# ─── 资产期望 / 豁免（唯一规则表） ────────────────────────────────────────


def classify_asset(
    asset_type: str,
    *,
    present: bool,
    has_report_entry: bool,
    is_current_day: bool,
    review_assets_enabled: bool,
    has_prior_snapshot: bool,
    prev_plan_present: bool,
    dragons_present: bool = False,
) -> tuple[bool, str | None]:
    """返回 ``(expected, legal_absence)``；见方案 §5.1 唯一表。"""

    category = ASSET_CATEGORIES.get(asset_type, "auxiliary")
    if category == "report":
        return False, None
    if category == "core":
        return True, None

    has_record = present or has_report_entry
    if category == "same_day_only":
        if is_current_day or has_record:
            return True, None
        return False, "same_day_only_history"

    if category == "review_conditional":      # stock_daily
        if has_record:
            return True, None
        if is_current_day and review_assets_enabled and dragons_present:
            return True, None
        if not review_assets_enabled:
            return False, "review_assets_disabled"
        return False, "not_attempted"

    # auxiliary
    if has_record:
        return True, None
    if asset_type == "prev_plan":
        return (True, None) if has_prior_snapshot else (False, "first_day_no_plan")
    if asset_type in ("auction_feedback", "t1_intraday"):
        if is_current_day and review_assets_enabled and prev_plan_present:
            return True, None
        return False, "no_prev_plan"
    if asset_type in ("holdings", "cninfo_announcements"):
        if is_current_day and review_assets_enabled:
            return True, None
        return False, "not_attempted"
    return False, "not_attempted"


def _status_ok(status: str | None, asset_type: str) -> bool:
    if status in BAD_STATUS:
        return False
    if status == "empty" and asset_type not in LEGAL_EMPTY:
        return False
    return True


def _legal_absence_reason(
    asset_type: str, expected: bool, legal: str | None, status: str | None,
    present: bool,
) -> str | None:
    if expected and status == "empty" and asset_type in LEGAL_EMPTY:
        return "legal_empty"
    if not present and legal is not None:
        return legal
    return None


# ─── 单日资产表（``GET /api/prep/{date}``） ───────────────────────────────


def build_asset_rows(
    *,
    payloads: Mapping[str, Any] | None,
    trade_date: str,
    today: str,
    review_assets_enabled: bool,
    has_prior_snapshot: bool,
) -> list[dict]:
    """逐资产投影 ``category/expected/legal_absence/asset_complete``。

    完全缺失日（``payloads == {}``）同样返回 13 行、全部 ``present=False``，
    期望资产全部计为缺失——端点不 404、不建库。
    """

    data = dict(payloads or {})
    report = _as_dict(data.get("collection_report")) or {}
    prev_plan_present = "prev_plan" in data
    dragons_present = "dragons" in data
    is_current_day = trade_date == today

    rows: list[dict] = []
    for asset_type in ASSET_TYPES:
        present = asset_type in data
        entry = _as_dict(report.get(asset_type)) or {}
        payload = data.get(asset_type)
        status = _text(entry.get("status"))
        as_of = payload.get("as_of") if isinstance(payload, dict) else None
        if not isinstance(as_of, str):
            as_of = None

        expected, legal = classify_asset(
            asset_type,
            present=present,
            has_report_entry=bool(entry),
            is_current_day=is_current_day,
            review_assets_enabled=review_assets_enabled,
            has_prior_snapshot=has_prior_snapshot,
            prev_plan_present=prev_plan_present,
            dragons_present=dragons_present,
        )
        complete = bool(
            expected
            and present
            and _status_ok(status, asset_type)
            and (as_of is None or as_of == trade_date)
        )

        usable = present and status not in BAD_STATUS
        if usable and asset_type in REQUIRED_NONEMPTY and _payload_empty(payload):
            usable = False
        if usable and as_of and as_of != trade_date:
            usable = False

        rows.append({
            "asset_type": asset_type,
            "present": present,
            "status": status,
            "note": entry.get("note"),
            "error": entry.get("error"),
            "as_of": as_of,
            "usable_display": bool(usable),
            "category": ASSET_CATEGORIES[asset_type],
            "expected": bool(expected),
            "legal_absence": _legal_absence_reason(
                asset_type, expected, legal, status, present),
            "asset_complete": complete,
        })
    return rows


def summarize_expected(rows: list[dict]) -> dict[str, int]:
    """``expected_ok/expected_total``：完备判定的唯一基座。"""

    expected = [row for row in rows if row.get("expected")]
    return {
        "expected_ok": sum(1 for row in expected if row.get("asset_complete")),
        "expected_total": len(expected),
    }


# ─── 日历（``GET /api/prep/calendar``） ───────────────────────────────────


def build_calendar(
    *,
    rows: Mapping[str, dict],
    start_date: str,
    end_date: str,
    today: str,
    review_assets_enabled: bool,
    first_snapshot_date: str | None,
) -> dict:
    """把批量投影行转成三态日历（``days`` 只含有记录日期）。"""

    # 方案 §5.1：same_day_only 的「当日」= 日历 end_date（显式 end 时以 end 为准，
    # 保证回放可重现）；路由缺省 end 时已由时钟取今天。
    reference_date = end_date or today
    days: list[dict] = []
    latest: str | None = None

    for trade_date in sorted(rows):
        raw = rows[trade_date] or {}
        asset_types = [str(item) for item in (raw.get("asset_types") or [])]
        report = _as_dict(raw.get("report")) or {}
        has_snapshot = bool(raw.get("has_snapshot"))
        has_real_assets = any(item != "collection_report" for item in asset_types)
        has_prior_snapshot = bool(
            first_snapshot_date and first_snapshot_date < trade_date)

        expected_total = 0
        expected_ok = 0
        core_total = 0
        core_ok = 0
        expected_missing: list[str] = []
        expected_degraded: list[str] = []
        exempt: list[dict] = []

        for asset_type in ASSET_TYPES:
            present = asset_type in asset_types
            entry = _as_dict(report.get(asset_type)) or {}
            status = _text(entry.get("status"))
            expected, legal = classify_asset(
                asset_type,
                present=present,
                has_report_entry=bool(entry),
                is_current_day=trade_date == reference_date,
                review_assets_enabled=review_assets_enabled,
                has_prior_snapshot=has_prior_snapshot,
                prev_plan_present="prev_plan" in asset_types,
                dragons_present="dragons" in asset_types,
            )
            reason = _legal_absence_reason(
                asset_type, expected, legal, status, present)
            if reason is not None:
                exempt.append({"asset_type": asset_type, "reason": reason})
            if not expected:
                continue
            expected_total += 1
            ok = present and _status_ok(status, asset_type)
            if ASSET_CATEGORIES[asset_type] == "core":
                core_total += 1
                if ok:
                    core_ok += 1
            if not present:
                expected_missing.append(asset_type)
            elif not ok:
                expected_degraded.append(asset_type)
            if ok:
                expected_ok += 1

        if not has_snapshot and not has_real_assets:
            status = "missing"
        elif has_snapshot and expected_total > 0 and expected_ok == expected_total:
            status = "complete"
        else:
            status = "incomplete"

        days.append({
            "trade_date": trade_date,
            "status": status,
            "has_snapshot": has_snapshot,
            "has_assets": has_real_assets,
            "asset_count": sum(1 for item in asset_types
                               if item != "collection_report"),
            "expected_ok": expected_ok,
            "expected_total": expected_total,
            "expected_missing": expected_missing,
            "expected_degraded": expected_degraded,
            "core_ok": core_ok,
            "core_total": core_total,
            "exempt": exempt,
            "report_error": raw.get("report_error"),
        })

        if has_snapshot or has_real_assets:
            latest = trade_date

    return {
        "start_date": start_date,
        "end_date": end_date,
        "latest_record_date": latest,
        "days": days,
    }


# ─── derived（high_num 解码 / 需重算） ───────────────────────────────────


def _rewrite_height_evidence(evidence: list[str], stored: Any) -> list[str]:
    decoded = board_height_for_display(stored)
    if decoded is None:
        return list(evidence)

    def replace(match: re.Match[str]) -> str:
        # 只改写与已存打包值一致的那一段，避免误改真实板数文案。
        if int(match.group(1)) == int(stored):
            return f"最高 {decoded} 板"
        return match.group(0)

    return [_PACKED_EVIDENCE.sub(replace, line) for line in evidence]


def _review_mainlines(snapshot: Mapping[str, Any]) -> tuple[list[dict], str]:
    review = _as_dict(snapshot.get("review")) or {}
    reviewed = _as_list(review.get("mainlines"))
    if reviewed is not None:
        return reviewed, "review"
    legacy = _as_list(snapshot.get("main_boards")) or []
    return legacy, "legacy"


def _sentiment_evidence(sentiment: Mapping[str, Any]) -> list[str]:
    evidence: list[str] = []
    up = _num(sentiment.get("limit_up_cnt"))
    down = _num(sentiment.get("limit_down_cnt"))
    if up is not None:
        evidence.append(f"涨停 {int(up)} 家" + (
            f" / 跌停 {int(down)} 家" if down is not None else ""))
    broken = _num(sentiment.get("broken_rate"))
    if broken is not None:
        evidence.append(f"炸板率 {broken * 100:.1f}%")
    height = _num(sentiment.get("max_board_height"))
    if height is not None:
        evidence.append(f"最高 {int(height) if height == int(height) else height} 板")
    premium = _num(sentiment.get("yesterday_premium_pct"))
    if premium is not None:
        evidence.append(f"昨日涨停溢价 {'+' if premium > 0 else ''}{premium}%")
    promotion = _num(sentiment.get("promotion_rate"))
    if promotion is not None:
        evidence.append(f"晋级率 {promotion * 100:.1f}%")
    return evidence


def build_snapshot_derived(snapshot: Mapping[str, Any] | None) -> dict:
    """快照 → 展示用 derived（快照本体一字不改）。"""

    data = dict(snapshot or {})
    mainlines, source = _review_mainlines(data)
    derived_mainlines: list[dict] = []
    any_recompute = False
    for index, item in enumerate(mainlines):
        stored = item.get("high_num")
        height = board_height_for_display(stored)
        if height is None:
            height = parse_board_height(
                {"high": item.get("high"), "high_days": item.get("high_days")})
        recompute = needs_recompute(stored)
        any_recompute = any_recompute or recompute
        derived_mainlines.append({
            "source": source,
            "index": index,
            "board_name": _text(item.get("board_name")),
            "stored_high_num": stored if isinstance(stored, (int, float)) else None,
            "board_height": height,
            "needs_recompute": recompute,
            "evidence": _rewrite_height_evidence(
                [str(line) for line in (item.get("evidence") or [])
                 if isinstance(line, str)], stored),
        })

    review = _as_dict(data.get("review")) or {}
    review_market = _as_dict(review.get("market"))
    inputs = _as_dict(review_market.get("inputs")) if review_market else None
    sentiment = _as_dict(data.get("sentiment"))
    if inputs is not None:
        market_source = "review"
        stored_height = inputs.get("max_board_height")
    elif isinstance(sentiment, dict) and "max_board_height" in sentiment:
        market_source = "sentiment"
        stored_height = sentiment.get("max_board_height")
    elif review_market is not None:
        market_source = "review"
        stored_height = None
    else:
        market_source = "sentiment"
        stored_height = (sentiment or {}).get("max_board_height")

    market_height = board_height_for_display(stored_height)
    market_recompute = needs_recompute(stored_height)
    if market_source == "review":
        market_evidence = [str(line) for line in
                           ((review_market or {}).get("evidence") or [])
                           if isinstance(line, str)]
    else:
        market_evidence = _sentiment_evidence(sentiment or {})
    market_evidence = _rewrite_height_evidence(market_evidence, stored_height)

    return {
        "mainlines": derived_mainlines,
        "market": {
            "source": market_source,
            "stored_max_board_height": (
                stored_height if isinstance(stored_height, (int, float)) else None),
            "max_board_height": market_height,
            "needs_recompute": market_recompute,
            "evidence": market_evidence,
        },
        "needs_recompute": any_recompute or market_recompute,
    }


# ─── review-context（跨日 delta / 补名 / 成效） ──────────────────────────


def _mainline_entries(snapshot: Mapping[str, Any] | None) -> tuple[list[dict], str | None]:
    if not isinstance(snapshot, dict):
        return [], None
    return _review_mainlines(snapshot)


def _valid_rank(value: Any, index: int, watchlist_n: int) -> int:
    number = _num(value)
    if number is not None and number == int(number) and 1 <= int(number) <= watchlist_n:
        return int(number)
    return index + 1


def _build_mainline_deltas(
    current: Mapping[str, Any] | None,
    prev: Mapping[str, Any] | None,
    prev_meta: Mapping[str, Any],
) -> list[dict]:
    current_items, _ = _mainline_entries(current)
    prev_items, _ = _mainline_entries(prev)
    watchlist_n = len(current_items) or 1
    prev_n = len(prev_items) or watchlist_n
    prev_by_name = {
        str(item.get("board_name")): item
        for item in prev_items if item.get("board_name")
    }
    prev_missing = prev is None

    deltas: list[dict] = []
    for index, item in enumerate(current_items):
        name = _text(item.get("board_name"))
        current_rank = _valid_rank(item.get("rank"), index, watchlist_n)
        if name:
            prev_item = prev_by_name.get(name)
        else:
            prev_item = prev_items[index] if index < len(prev_items) else None
        prev_rank = None
        prev_score = None
        if prev_item is not None:
            prev_rank = _valid_rank(prev_item.get("rank"), index, prev_n)
            prev_score = _num(prev_item.get("mainline_score"))
        current_score = _num(item.get("mainline_score"))
        rank_delta = (prev_rank - current_rank) if prev_rank is not None else None
        score_delta = (current_score - prev_score
                       if current_score is not None and prev_score is not None
                       else None)

        reason: str | None = None
        if rank_delta is None or score_delta is None:
            if prev_missing:
                reason = ("prev_snapshot_corrupt" if prev_meta.get("error")
                          else "prev_snapshot_missing")
            elif not prev_items:
                reason = "prev_mainlines_missing"
            elif prev_item is None:
                reason = "mainline_prev_rank_missing"
            elif current_score is None:
                reason = "mainline_score_missing"
            elif prev_score is None:
                reason = "prev_mainline_score_missing"

        deltas.append({
            "index": index,
            "board_name": name,
            "rank": current_rank,
            "prev_rank": prev_rank,
            "prev_score": prev_score,
            "rank_delta": rank_delta,
            "score_delta": score_delta,
            "state": _text(item.get("state")),
            "unknown_reason": reason,
        })
    return deltas


def _plan_name_sources(
    *, prev_plan: Mapping[str, Any] | None, prev: Mapping[str, Any] | None,
    current: Mapping[str, Any] | None,
) -> dict[str, tuple[str, str]]:
    """code → (name, source)，按方案 §5.3 补名优先级。"""

    found: dict[str, tuple[str, str]] = {}

    def take(positions: Any, source: str) -> None:
        for item in _as_list(positions) or []:
            code = _text(item.get("code"))
            name = _text(item.get("name"))
            if code and name and code not in found:
                found[code] = (name, source)

    take((prev_plan or {}).get("positions"), "prev_plan_asset")
    take((_as_dict((prev or {}).get("strategy_context")) or {}).get("positions"),
         "prev_snapshot_context")
    take((current or {}).get("review_candidates"), "current_snapshot")
    take((current or {}).get("candidates"), "current_snapshot")
    return found


def _market_side(snapshot: Mapping[str, Any] | None, trade_date: str | None) -> dict:
    if not isinstance(snapshot, dict):
        return {
            "available": False, "trade_date": trade_date, "state": None,
            "score": None, "coeff": None, "position_advice": None,
            "position_pct": None,
        }
    review = _as_dict(snapshot.get("review")) or {}
    market = _as_dict(review.get("market")) or {}
    sentiment = _as_dict(snapshot.get("sentiment")) or {}
    score = _num(market.get("score"))
    if score is None:
        score = _num(sentiment.get("temperature_score"))
    return {
        "available": bool(market or sentiment),
        "trade_date": trade_date,
        "state": _text(market.get("state")),
        "score": score,
        "coeff": _num(market.get("coeff")),
        "position_advice": (_text(market.get("position_advice"))
                            or _text(sentiment.get("position_advice"))),
        # sentiment 原值 0~1；前端统一 ×100 展示。
        "position_pct": _num(sentiment.get("position_pct")),
    }


def _plan_groups(items: list[dict]) -> list[dict]:
    buckets = {
        "recommended": [item for item in items
                        if item.get("planned_position") is not None
                        and item["planned_position"] > 0],
        "not_recommended": [item for item in items
                            if item.get("planned_position") == 0],
        "unknown": [item for item in items
                    if item.get("planned_position") is None],
    }
    groups: list[dict] = []
    for key in ("recommended", "not_recommended", "unknown"):
        rows = buckets[key]
        outcomes = [item["outcome_pct"] for item in rows
                    if item.get("outcome_pct") is not None]
        groups.append({
            "key": key,
            "count": len(rows),
            "with_outcome": len(outcomes),
            "mean_outcome_pct": (round(sum(outcomes) / len(outcomes), 4)
                                 if outcomes else None),
        })
    return groups


def _build_plan_effect(
    *,
    trade_date: str,
    current: Mapping[str, Any] | None,
    prev: Mapping[str, Any] | None,
    prev_meta: Mapping[str, Any],
    names: dict[str, tuple[str, str]],
) -> dict:
    prev_side = _market_side(prev, prev_meta.get("trade_date"))
    current_side = _market_side(current, trade_date)
    review = _as_dict((current or {}).get("review")) or {}
    market = _as_dict(review.get("market")) or {}
    market_comparison = {
        "prev": prev_side,
        "current": current_side,
        "score_change": _num(market.get("score_change")),
    }
    empty = {
        "available": False,
        "reason": "plan_review_missing",
        "plan_date": None,
        "eval_date": None,
        "checkpoint": None,
        "items": [],
        "no_trade": [],
        "groups": [],
        "market": market_comparison,
        "degraded": [],
    }
    if not isinstance(current, dict):
        return empty
    plan_review = _as_dict(current.get("plan_review"))
    if not plan_review:
        return empty

    feedback = _as_dict(current.get("feedback")) or {}
    feedback_items = {
        _text(item.get("code")): item
        for item in (_as_list(feedback.get("items")) or [])
        if _text(item.get("code"))
    }
    degraded = sorted(set(
        [str(item) for item in (plan_review.get("degraded") or [])]
        + [str(item) for item in (feedback.get("degraded") or [])]
    ))

    def name_of(code: str) -> str | None:
        return names[code][0] if code in names else None

    items: list[dict] = []
    outcomes: dict[str, float | None] = {}
    for raw in _as_list(plan_review.get("items")) or []:
        code = _text(raw.get("code")) or ""
        outcome = _num(raw.get("outcome_pct"))
        outcomes[code] = outcome
        source_item = feedback_items.get(code) or {}
        items.append({
            "code": code,
            "name": name_of(code),
            "planned_action": _text(raw.get("planned_action")),
            "planned_position": _num(raw.get("planned_position")),
            "feedback_state": _text(raw.get("feedback_state")) or "unknown",
            "outcome_pct": outcome,
            "actual_position": _num(raw.get("actual_position")),
            "execution_known": bool(raw.get("execution_known")),
            "unknown_reason": _text(source_item.get("unknown_reason")),
        })

    no_trade: list[dict] = []
    for raw in _as_list(plan_review.get("no_trade")) or []:
        code = _text(raw.get("code")) or ""
        no_trade.append({
            "code": code,
            "name": name_of(code),
            "judgement": _text(raw.get("judgement")) or "data_gap",
            "outcome_pct": outcomes.get(code),
        })

    return {
        "available": True,
        "reason": None,
        "plan_date": _text(plan_review.get("plan_date")),
        "eval_date": _text(plan_review.get("eval_date")),
        "checkpoint": _text(feedback.get("checkpoint")),
        "items": items,
        "no_trade": no_trade,
        "groups": _plan_groups(items),
        "market": market_comparison,
        "degraded": degraded,
    }


def build_review_context(
    *,
    trade_date: str,
    current: Mapping[str, Any] | None,
    prev: Mapping[str, Any] | None,
    prev_meta: Mapping[str, Any],
    prev_plan: Mapping[str, Any] | None,
) -> dict:
    """跨日上下文：主线 delta + 补名 + 计划成效 + 前日/今日市场并排。"""

    meta = dict(prev_meta or {})
    names = _plan_name_sources(
        prev_plan=prev_plan, prev=prev, current=current)
    plan_effect = _build_plan_effect(
        trade_date=trade_date, current=current, prev=prev,
        prev_meta=meta, names=names)

    plan_codes: list[str] = []
    for item in plan_effect["items"] + plan_effect["no_trade"]:
        code = item.get("code")
        if code and code not in plan_codes:
            plan_codes.append(code)
    plan_names = [
        {
            "code": code,
            "name": names[code][0] if code in names else None,
            "source": names[code][1] if code in names else "missing",
        }
        for code in plan_codes
    ]

    degraded: list[str] = []
    if prev is None and meta.get("error"):
        degraded.append("prev_snapshot_corrupt")
    degraded.extend(item for item in plan_effect["degraded"]
                    if item not in degraded)

    available = isinstance(current, dict)
    return {
        "trade_date": trade_date,
        "available": available,
        "reason": None if available else "snapshot_missing",
        "prev_trade_date": meta.get("trade_date"),
        "prev_meta_error": meta.get("error"),
        "mainlines": _build_mainline_deltas(current, prev, meta),
        "plan_names": plan_names,
        "plan_effect": plan_effect,
        "degraded": degraded,
    }
