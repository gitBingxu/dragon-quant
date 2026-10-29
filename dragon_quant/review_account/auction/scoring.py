"""auction.scoring — 唯一评分入口（Template Method + 批量编排）。

骨架固定为 ``normalize → features → events → dimensions → aggregate → project``，
任何股票都不可绕过数据质量、veto 与去重。采集与重算必须共用
:func:`evaluate_batch`（只负责取数 / 落库，不复制评分循环）。
"""

from __future__ import annotations

from typing import Optional

from . import registry as R
from .dimensions import score_dimensions
from .events import detect, first_veto_reason, veto_events
from .features import compute_features
from .normalize import normalize
from .types import AuctionVerdict, DataQuality, DimensionScore


def _unavailable(code: str, reason_code: str, *, reject_reason=None,
                 field_name=None, items=None, quote=None) -> AuctionVerdict:
    quality = DataQuality(status="unavailable", completeness=0.0,
                          reasons=[{"code": reason_code, "severity": "unavailable",
                                    "field": field_name}])
    n = len(items or [])
    return AuctionVerdict(
        code=code, is_weak=False, strength=None, direction="neutral", level=None,
        dims={}, reject_reason=reject_reason, unverified=True, rank=None,
        rank_reason="unavailable",
        validation_status=R.VALIDATION_STATUS,
        config_version=R.CONFIG_VERSION, config_hash=R.CONFIG_HASH,
        semantics=dict(R.SEMANTICS), data_quality=quality, risk_events=[],
        raw_factors={}, evidence={"sample_count": n},
        reference_prices={}, windows={}, actions=[],
    )


def _resolve_direction(raw: dict, flags: dict, available: bool) -> str:
    if not available:
        return "neutral"
    gap = raw.get("effective_open_gap_pct")
    base = 0 if gap is None or gap == 0 else (1 if gap > 0 else -1)
    if flags.get("weak_to_strong"):
        return "bull"
    if flags.get("strong_to_weak"):
        return "bear"
    tail_change = raw.get("tail_unmatched_change")
    last_matched = raw.get("last_matched_lots")
    if (tail_change is not None and base != 0 and last_matched is not None
            and last_matched > 0
            and (1 if tail_change > 0 else (-1 if tail_change < 0 else 0)) == -base
            and abs(tail_change) >= R.CONVERSION_MIN_RATIO * last_matched):
        return "neutral"
    return "bull" if base > 0 else ("bear" if base < 0 else "neutral")


def _level_of(strength: Optional[float]) -> Optional[str]:
    if strength is None:
        return None
    for threshold, name in R.STRENGTH_LEVELS:
        if strength >= threshold:
            return name
    return "弱"


def _windows(norm: dict) -> dict:
    def _fmt(seconds):
        return f"{seconds // 3600:02d}:{(seconds % 3600) // 60:02d}:{seconds % 60:02d}"

    def _slice(idx):
        if not idx:
            return {"start": None, "end": None, "n": 0}
        return {"start": _fmt(norm["times"][idx[0]]),
                "end": _fmt(norm["times"][idx[-1]]), "n": len(idx)}

    pre = _slice(norm["pre_idx"])
    core = _slice(norm["core_idx"])
    tail = _slice(norm["tail_idx"])
    tail["extended"] = bool(norm["tail_extended"])
    return {"pre": pre, "core": core, "tail": tail}


def _actions(verdict: AuctionVerdict) -> list[dict]:
    if verdict.data_quality.status == "unavailable":
        reason = (verdict.data_quality.reason_codes or ["unavailable"])[0]
        return [{"kind": "request_data",
                 "text": f"数据不足：{reason}",
                 "requires": "auction_items"}]
    if verdict.is_weak:
        event = next((e for e in verdict.risk_events if e.veto), None)
        if event is not None:
            return [{"kind": "flag_risk",
                     "text": f"标记{first_veto_reason([event])}风险（{event.event_id.split('@')[-1]}）",
                     "event_id": event.event_id}]
    actions = []
    reference = verdict.reference_prices or {}
    effective = reference.get("effective_open")
    if effective is not None:
        actions.append({
            "kind": "observe",
            "text": f"观察开盘后能否维持在 {effective} 上方",
            "requires": "post_open",
            "condition": "post_open_price >= official_open_px",
        })
    if verdict.data_quality.status == "degraded":
        actions.append({
            "kind": "await_confirmation",
            "text": "数据不完整，等待完整行情后再评估",
            "requires": "quote",
        })
    return actions[:4]


def evaluate(code: str, items: list[dict], quote=None, *,
             history=None, name: str = "") -> AuctionVerdict:
    """单只评价（纯函数）。任何异常都由调用方按股票隔离。"""
    if not items:
        verdict = _unavailable(code, "items_empty", reject_reason=R.AUCTION_ITEMS_MISSING,
                               field_name="items", items=items)
        verdict.name = name
        return verdict

    norm = normalize(items)
    if norm["n"] == 0:
        verdict = _unavailable(code, "items_empty", reject_reason=R.AUCTION_ITEMS_MISSING,
                               field_name="items", items=items)
        verdict.name = name
        return verdict

    prev_close = getattr(quote, "prev_close", None) if quote is not None else None
    try:
        prev_close = float(prev_close or 0.0)
    except (TypeError, ValueError):
        prev_close = 0.0
    if prev_close <= 0:
        verdict = _unavailable(code, "prev_close_missing",
                               field_name="quote.prev_close", items=items, quote=quote)
        verdict.name = name
        return verdict
    if len(norm["core_idx"]) < R.CORE_MIN_BARS:
        verdict = _unavailable(code, "core_window_short", field_name="items",
                               items=items, quote=quote)
        verdict.data_quality.add("core_window_short", "unavailable", "items")
        verdict.name = name
        return verdict
    last_idx = norm["last_idx"]
    if (last_idx is None or norm["prices"][last_idx] <= 0
            or norm["matched"][last_idx] is None):
        verdict = _unavailable(code, "last_point_invalid", field_name="items",
                               items=items, quote=quote)
        verdict.name = name
        return verdict

    feats = compute_features(norm, quote, history, code=code)
    raw = feats["raw"]
    flags = feats["flags"]
    quality = DataQuality(status="ok", completeness=0.0)
    for reason in feats["reasons"]:
        quality.add(reason["code"], reason["severity"], reason.get("field"))

    risk_events = detect(code, norm, feats, quote)
    dims = score_dimensions(raw, flags, risk_events)

    scored = [dim for dim in R.DIM_ORDER
              if dim in dims and dims[dim].score is not None]
    covered_weight = sum(R.WEIGHTS[dim] for dim in scored)
    quality.completeness = round(covered_weight, 4)
    required_ok = set(R.REQUIRED_DIMS) <= set(scored)
    strength = None
    if required_ok and len(scored) >= R.MIN_SCORED_DIMS and covered_weight > 0:
        strength = round(sum(R.WEIGHTS[dim] / covered_weight * dims[dim].score
                             for dim in scored), 2)
    if strength is None:
        quality.add("insufficient_dimensions", "unavailable", "dims")

    if strength is None:
        quality.status = "unavailable"
    elif quality.has_severity("degraded") or any(
            dim not in dims or dims[dim].score is None for dim in R.DIM_ORDER):
        quality.status = "degraded"
    else:
        quality.status = "ok"

    vetoes = veto_events(risk_events)
    is_weak = bool(vetoes) and quality.status != "unavailable"
    pre_veto_strength = strength
    reject_reason = None
    if is_weak:
        strength = 0.0
        level = "弱"
        reject_reason = first_veto_reason(vetoes)
    elif quality.status == "unavailable":
        level = None
    else:
        level = _level_of(strength)

    for dim in R.DIM_ORDER:
        item = dims.get(dim)
        if item is None:
            continue
        item.effective_weight = (
            round(R.WEIGHTS[dim] / covered_weight, 4) if covered_weight > 0
            and item.score is not None else None)

    available = quality.status != "unavailable"
    direction = _resolve_direction(raw, flags, available)
    unverified = not available

    evidence = {
        "market": feats["market"],
        "last_time": raw.get("last_time"),
        "last_indicative_price": raw.get("last_indicative_price"),
        "official_open_px": raw.get("official_open_px"),
        "prev_close": prev_close,
        "last_matched_lots": raw.get("last_matched_lots"),
        "final_unmatched_lots_signed": raw.get("final_unmatched_lots_signed"),
        "sample_count": norm["n"],
        "core_sample_count": len(norm["core_idx"]),
        "float_shares": raw.get("float_shares"),
        "float_shares_source": raw.get("float_shares_source"),
        "quote_fields_used": list(feats["quote_fields_used"]),
        "event_density_ratio": raw.get("event_density_ratio"),
    }
    reference_prices = {
        "prev_close": prev_close,
        "limit_up": float(getattr(quote, "limit_up", 0.0) or 0.0) if quote is not None else None,
        "limit_down": float(getattr(quote, "limit_down", 0.0) or 0.0) if quote is not None else None,
        "official_open": raw.get("official_open_px"),
        "last_indicative": raw.get("last_indicative_price"),
        "effective_open": (raw.get("official_open_px")
                           if raw.get("official_open_px") is not None
                           else raw.get("last_indicative_price")),
        "effective_open_source": raw.get("effective_open_source"),
    }

    verdict = AuctionVerdict(
        code=code, is_weak=is_weak, strength=strength, direction=direction,
        level=level, dims=dims, reject_reason=reject_reason,
        unverified=unverified, rank=None,
        rank_reason=("vetoed" if is_weak else
                     ("unavailable" if quality.status == "unavailable" else
                      ("partial_data" if quality.status == "degraded" else None))),
        pre_veto_strength=pre_veto_strength,
        validation_status=R.VALIDATION_STATUS,
        config_version=R.CONFIG_VERSION, config_hash=R.CONFIG_HASH,
        semantics=dict(R.SEMANTICS), data_quality=quality,
        risk_events=risk_events, raw_factors=raw, evidence=evidence,
        reference_prices=reference_prices, windows=_windows(norm), actions=[],
        name=name,
    )
    verdict.actions = _actions(verdict)
    return verdict


def rank_auctions(verdicts: list[AuctionVerdict]) -> list[AuctionVerdict]:
    """只在 ``strength != null ∧ not is_weak ∧ data_quality == ok`` 内排名。"""
    for verdict in verdicts:
        verdict.rank = None
        if verdict.is_weak:
            verdict.rank_reason = "vetoed"
        elif verdict.data_quality.status != "ok" or verdict.strength is None:
            verdict.rank_reason = ("unavailable"
                                   if verdict.data_quality.status == "unavailable"
                                   else "partial_data")
        else:
            verdict.rank_reason = None
    usable = [v for v in verdicts
              if v.strength is not None and not v.is_weak
              and v.data_quality.status == "ok"]
    usable.sort(key=lambda v: (-v.strength, v.code))
    for position, verdict in enumerate(usable, 1):
        verdict.rank = position
    return verdicts


def _snapshot_summary(verdict: AuctionVerdict) -> dict:
    from .context import evidence_summary

    return {
        "code": verdict.code,
        "is_weak": verdict.is_weak,
        "strength": verdict.strength,
        "direction": verdict.direction,
        "level": verdict.level,
        "unverified": verdict.unverified,
        "rank": verdict.rank,
        "reject_reason": verdict.reject_reason,
        "data_quality_status": verdict.data_quality.status,
        "risk_event_codes": [event.event_id.split("@")[0]
                             for event in verdict.risk_events],
        "rank_reason": verdict.rank_reason,
        "validation_status": verdict.validation_status,
        "config_version": verdict.config_version,
        "evidence_summary": evidence_summary(verdict),
    }


def evaluate_batch(assets: list[dict], *, history=None, names=None) -> tuple:
    """唯一批量入口：evaluate → rank → serialize。返回 4 元组。

    ``assets``: ``[{"code", "items", "quote"}]``；``history``:
    ``{code: {"dates": [...], "last_matched_lots": [...]}}``。
    单股异常隔离，不中断整批。
    """
    from .serialize import verdict_to_payload

    names = names or {}
    history = history or {}
    verdicts, failures, summaries = [], [], {}
    for asset in assets:
        code = asset["code"]
        try:
            verdicts.append(evaluate(
                code, asset.get("items") or [], asset.get("quote"),
                history=history.get(code),
                name=names.get(code) or asset.get("name") or ""))
        except Exception as exc:                      # 单股失败隔离
            failures.append({"code": code, "stage": "score",
                             "error": f"{type(exc).__name__}: {exc}"})

    rank_auctions(verdicts)                            # 必须先 rank 再序列化

    payloads = {}
    for verdict in verdicts:
        try:
            payloads[verdict.code] = verdict_to_payload(verdict)
            summaries[verdict.code] = _snapshot_summary(verdict)
        except Exception as exc:
            failures.append({"code": verdict.code, "stage": "serialize",
                             "error": f"{type(exc).__name__}: {exc}"})
    return payloads, failures, summaries, verdicts


def _score_auction_assets(assets: list[dict]) -> tuple:
    """兼容旧签名（无历史注入）的批量入口，内部委托 :func:`evaluate_batch`。"""
    payloads, failures, summaries, _verdicts = evaluate_batch(assets)
    return payloads, failures, summaries
