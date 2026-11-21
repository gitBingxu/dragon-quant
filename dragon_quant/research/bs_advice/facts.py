"""公共事实适配：本地 PIT 快照/账户检查点 → G3/G5/G6/G8 typed facts。

Adapter 只读、纯函数；不决策、不撮合、不写库。两个消费者（只读建议与逐事件
回放/研究）共用同一口径，freeze 只负责规范化保存。

- G3：``confirm:<code>`` 结构确认（达到 confirm_level 的合格盘中事实 + 真实
  evidence_id/available_at/snapshot_id/entry_version）。
- G5：``trend`` 采用 ``bs_trend_ma5_v1``（6 个连续完整日收盘 → 最近/前一日
  MA5），账户输入趋势仅作 as_of 检查点留痕，不直接授权。
- G6：主题/压力优先本地事实，缺失时回退账户显式检查点并保留来源。
- G8：``market_regime_verified`` / ``execution_quality_ok`` 具名规则确认。
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from typing import Iterable, Mapping, Optional

TREND_RULE_VERSION = "bs_trend_ma5_v1"
EXECUTION_RULE_VERSION = "bs_execution_quality_v1"
CONFIRM_RULE_PREFIX = "confirm:"
CONFIRM_EVIDENCE_VERSION = "local_confirmation_v1"

CAP_DAILY = "daily_bars"
CAP_INTRADAY = "intraday_bars"
CAP_THEME = "theme_membership"


def _payload(fact) -> Mapping:
    return dict(getattr(fact, "payload", {}) or {})


def _number(value) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if number > 0 else None


def _usable(snapshot, code: str, capability: str) -> list:
    return sorted((fact for fact in snapshot.facts
                   if fact.code == code and fact.capability == capability
                   and fact.usable),
                  key=lambda item: (str(item.interval_end or ""),
                                    str(item.available_at or "")))


def _closes(snapshot, code: str) -> list[tuple[str, float]]:
    points: dict[str, float] = {}
    for fact in _usable(snapshot, code, CAP_DAILY):
        close = _number(_payload(fact).get("close"))
        day = str(fact.trade_date or fact.interval_end or "")
        if close is not None and day:
            points[day] = close
    return sorted(points.items())


def _consecutive(days: list[str], calendar: Iterable[str]) -> bool:
    order = {str(day): pos for pos, day in enumerate(calendar or ())}
    positions = [order.get(day) for day in days]
    if any(pos is None for pos in positions):
        return False
    return all(right == left + 1 for left, right in
               zip(positions, positions[1:]))


def trend_for(snapshot, code: str, price, *, calendar=()) -> tuple[str, dict]:
    """`bs_trend_ma5_v1`：6 连续日收盘 + 当前合格价 vs 最近/前一日 MA5。"""
    points = _closes(snapshot, code)
    audit = {"rule_version": TREND_RULE_VERSION, "code": code,
             "window": 5, "required_closes": 6}
    if len(points) < 6:
        audit["reason"] = "insufficient_daily_closes"
        return "unknown", audit
    window = points[-6:]
    days = [day for day, _value in window]
    if calendar and not _consecutive(days, calendar):
        audit["reason"] = "non_consecutive_trading_days"
        audit["days"] = days
        return "unknown", audit
    closes = [value for _day, value in window]
    ma5 = sum(closes[-5:]) / 5.0
    prev_ma5 = sum(closes[:5]) / 5.0
    audit.update({"days": days, "ma5": ma5, "prev_ma5": prev_ma5})
    current = _number(price)
    if current is None:
        audit["reason"] = "current_price_missing"
        return "unknown", audit
    audit["price"] = current
    if current > ma5 and ma5 > prev_ma5:
        audit["reason"] = "price_above_rising_ma5"
        return "up", audit
    if current < ma5:
        audit["reason"] = "price_below_ma5"
        return "down", audit
    audit["reason"] = "ma5_not_rising"
    return "flat", audit


def themes_for(snapshot, code: str) -> tuple[str, ...]:
    names: list[str] = []
    for fact in snapshot.facts:
        if fact.capability != CAP_THEME or not fact.usable:
            continue
        payload = _payload(fact)
        codes = payload.get("codes")
        if isinstance(codes, (list, tuple)) and code in codes:
            name = payload.get("theme_name") or payload.get("theme")
            if isinstance(name, str) and name and name not in names:
                names.append(name)
    return tuple(names)


def pressure_for(snapshot, code: str) -> Optional[float]:
    closes = [_number(_payload(fact).get("close"))
              for fact in _usable(snapshot, code, CAP_DAILY)[-5:]]
    values = [value for value in closes if value is not None]
    if len(values) < 2:
        return None
    high, low = max(values), min(values)
    return min(1.0, max(0.0, (high - low) / high))


def confirmations_for(snapshot, code: str, *, confirm_level, price,
                      cutoff, entry_version: str):
    """结构确认：达到 confirm_level 的最新合格盘中事实（真实证据 ID/时点）。"""
    from dragon_quant.review_account.strategy.bs_policy import EvidenceConfirmation

    level = _number(confirm_level)
    current = _number(price)
    if level is None or current is None or current < level:
        return ()
    intraday = _usable(snapshot, code, CAP_INTRADAY)
    if not intraday:
        return ()
    fact = intraday[-1]
    if str(fact.available_at) > str(cutoff):
        return ()
    return (EvidenceConfirmation(
        rule_id=f"{CONFIRM_RULE_PREFIX}{code}",
        evidence_ids=(str(fact.evidence_id),),
        available_at=str(fact.available_at),
        version=entry_version or CONFIRM_EVIDENCE_VERSION,
        snapshot_id=str(snapshot.snapshot_id)),)


def _day(value) -> Optional[date]:
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def held_days_for(checkpoint: Mapping, entry_date: str, trade_date: str,
                  calendar: Iterable[str]) -> Optional[int]:
    """真实交易日历持有时长；账户 held_days 仅作 as_of 检查点延展。"""
    calendar = [str(day) for day in calendar or ()]
    entry_day, current_day = _day(entry_date), _day(trade_date)
    if entry_day is None or current_day is None or not calendar:
        if checkpoint.get("held_days") is not None \
                and str(checkpoint.get("as_of") or "")[:10] \
                == str(trade_date)[:10]:
            return int(checkpoint["held_days"])
        return None
    if calendar and entry_date not in calendar:
        return None
    return len([day for day in calendar
                if str(entry_day) < day <= str(current_day)])


def held_days_map(batches, *, trade_date: str, calendar: Iterable[str],
                  checkpoints: Mapping) -> dict:
    result = {}
    for batch in batches or ():
        checkpoint = dict((checkpoints or {}).get(str(batch.batch_id)) or {})
        value = held_days_for(checkpoint, str(batch.entry_date or ""),
                              trade_date, calendar)
        if value is not None:
            result[str(batch.batch_id)] = int(value)
    return result


def checkpoints_from_account(account) -> dict:
    """AccountSnapshot/AccountView 的 as_of 检查点事实（只留痕/回退，不授趋势）。"""

    view = getattr(account, "account", account)
    as_of = str(getattr(account, "as_of", "") or "")
    result = {}
    for position in getattr(view, "positions", ()) or ():
        record = {"trend": str(getattr(position, "trend", "unknown")
                               or "unknown"),
                  "held_days": getattr(position, "held_days", None),
                  "pressure_pct": getattr(position, "pressure_pct", None),
                  "theme_ids": list(getattr(position, "theme_ids", ()) or ()),
                  "as_of": as_of, "source": "account_input_checkpoint"}
        if position.batches:
            for batch in position.batches:
                result[str(batch.batch_id)] = dict(record)
        else:
            result[f"code:{position.code}"] = dict(record)
    return result


def market_facts(snapshot, account, *, trade_date: str, calendar=(),
                 prices: Optional[Mapping] = None,
                 checkpoints: Optional[Mapping] = None,
                 codes: Iterable[str] = ()) -> dict:
    """逐事件/建议的 G5/G6 事实（趋势/主题/压力/持有时长）。

    ``prices`` 必须是**调用方已按当时可得性过滤**的当前价（事件报价或截至
    cutoff 的候选价）。缺口不退回账户 ``mark``/as_of 检查点：没有合格当前价
    时趋势为 ``unknown`` 并记录原因，不能靠旧价延长授权。
    """
    prices = prices or {}
    checkpoints = checkpoints or {}
    trends, pressures, themes = {}, {}, {}
    audit = {}
    positions = list(getattr(account, "positions", ()) or ())
    known = {position.code for position in positions} | set(codes or ())
    for code in sorted(known):
        price = prices.get(code)
        trend, detail = trend_for(snapshot, code, price, calendar=calendar)
        trends[code] = trend
        detail = dict(detail, trend=trend)
        local_theme = themes_for(snapshot, code)
        code_checkpoints = [
            dict(checkpoints.get(f"code:{code}") or {})]
        for item in positions:
            if item.code != code:
                continue
            code_checkpoints.extend(
                dict(checkpoints.get(str(batch.batch_id)) or {})
                for batch in item.batches or ())
        checkpoint_themes: list[str] = []
        for checkpoint in code_checkpoints:
            for name in checkpoint.get("theme_ids") or ():
                if name not in checkpoint_themes:
                    checkpoint_themes.append(name)
        merged_themes = list(local_theme)
        for name in checkpoint_themes:
            if name not in merged_themes:
                merged_themes.append(name)
        if merged_themes:
            themes[code] = tuple(merged_themes)
        local_pressure = pressure_for(snapshot, code)
        pressure = local_pressure
        checkpoint_pressure = None
        for checkpoint in code_checkpoints:
            value = checkpoint.get("pressure_pct")
            if value is None:
                continue
            checkpoint_pressure = (float(value) if checkpoint_pressure is None
                                   else max(checkpoint_pressure, float(value)))
        if checkpoint_pressure is not None:
            pressure = (checkpoint_pressure if pressure is None
                        else max(float(pressure), checkpoint_pressure))
        if pressure is not None:
            pressures[code] = pressure
        audit[code] = dict(detail, themes={
            "local": list(local_theme),
            "account_checkpoint": checkpoint_themes,
            "merged": list(merged_themes),
            "source": ("merged_local_and_account_checkpoint"
                       if local_theme and checkpoint_themes else
                       "local" if local_theme else
                       "account_input_checkpoint" if checkpoint_themes else
                       "missing")},
            pressure={"local": local_pressure,
                      "account_checkpoint": checkpoint_pressure,
                      "effective": pressure,
                      "source": ("max_local_and_account_checkpoint"
                                 if local_pressure is not None
                                 and checkpoint_pressure is not None else
                                 "local" if local_pressure is not None else
                                 "account_input_checkpoint"
                                 if checkpoint_pressure is not None else
                                 "missing")})
    return {"trends": trends, "pressures": pressures, "themes": themes,
            "trend_audit": audit,
            "held_days_by_batch": held_days_map(
                [batch for position in getattr(account, "positions", ()) or ()
                 for batch in (position.batches or ())],
                trade_date=trade_date, calendar=calendar,
                checkpoints=checkpoints)}


def _moment(value):
    from datetime import datetime, timezone, timedelta

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return datetime.fromtimestamp(float(value) / 1000,
                                          tz=timezone(timedelta(hours=8)))
        except (OverflowError, OSError, ValueError):
            return None
    try:
        moment = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else None


def _execution_support_ok(snapshot, rows: Mapping,
                          execution_facts: Mapping):
    if not rows or execution_facts.get("cost_model_present") is not True \
            or execution_facts.get("fee_fields_valid") is not True:
        return False, (), "execution_support_missing", ()
    cutoff = _moment(snapshot.cutoff)
    if cutoff is None:
        return False, (), "execution_cutoff_invalid", ()
    usable = [fact for fact in snapshot.facts if fact.usable
              and fact.capability == CAP_INTRADAY
              and (dict(fact.calibration or {})
                   or bool(dict(getattr(snapshot, "source_manifest", {}) or {})
                           .get("calibrations")))
              and fact.time_semantics == "verified"
              and fact.unit_semantics == "verified"]
    evidence, moments, audit = [], [], []
    for code, row in rows.items():
        price = _number(row.get("execution_price"))
        capacity = row.get("available_qty")
        row_moment = _moment(row.get("available_at"))
        if price is None or isinstance(capacity, bool) \
                or not isinstance(capacity, int) or capacity <= 0 \
                or row_moment is None:
            return False, (), f"execution_row_invalid:{code}", ()
        if row_moment > cutoff:
            return False, (), f"execution_row_future:{code}", ()
        matched = next((fact for fact in usable
                        if str(fact.code) == str(code)
                        and _moment(fact.available_at) == row_moment
                        and abs(float(_payload(fact).get("close") or 0.0)
                                - price) <= 1e-6), None)
        if matched is None:
            return False, (), f"execution_fact_mismatch:{code}", ()
        evidence.append(str(matched.evidence_id))
        moments.append(row_moment)
        audit.append({"code": str(code), "evidence_id":
                      str(matched.evidence_id),
                      "available_at": matched.available_at,
                      "price": price, "capacity": capacity})
    return True, tuple(evidence), None, tuple(audit)


def recovery_confirmations(snapshot, *, market, rows: Mapping,
                           execution_facts: Optional[Mapping] = None,
                           market_meta: Optional[Mapping] = None,
                           market_context: Optional[Mapping] = None):
    """G8 具名条件确认：引用真实 prep market / 当前执行支持事实。"""
    from dragon_quant.review_account.strategy.bs_policy.scenario_contracts import (
        RecoveryConditionConfirmation,
    )

    snapshot_id = str(snapshot.snapshot_id)
    result = []
    meta = dict(market_meta or {})
    context_data = dict(market_context or {})
    created = _moment(meta.get("created_at"))
    trade_day = str(meta.get("trade_date") or "")
    decision_cutoff = _moment(snapshot.cutoff)
    if (getattr(market, "new_risk_allowed", None) is True
            and context_data.get("partial") is False
            and context_data.get("state") not in (None, "", "unknown")
            and trade_day and created is not None
            and decision_cutoff is not None and created <= decision_cutoff
            and trade_day < str(snapshot.cutoff)[:10]):
        from dragon_quant.review_account.strategy.event_inputs import (
            canonical_input, hash_payload,
        )
        identity = "prep_market|" + hash_payload(canonical_input({
            "meta": meta, "context": context_data,
            "market": {"regime": getattr(market, "regime", None),
                       "new_risk_allowed": getattr(
                           market, "new_risk_allowed", None)},
        }))
        result.append(RecoveryConditionConfirmation(
            rule_id="market_regime_verified", evidence_ids=(identity,),
            source="prep.market (strict T-1)",
            available_at=str(meta.get("created_at")),
            snapshot_id=snapshot_id, version="bs_market_regime_v1"))
    ok, evidence, reason, execution_audit = _execution_support_ok(
        snapshot, rows, dict(execution_facts or {}))
    if ok:
        moments = [_moment(row.get("available_at")) for row in rows.values()]
        available_at = max(moments).isoformat()
        _ = execution_audit
        result.append(RecoveryConditionConfirmation(
            rule_id="execution_quality_ok", evidence_ids=evidence,
            source="local_execution_support (simulation inputs)",
            available_at=available_at, snapshot_id=snapshot_id,
            version=EXECUTION_RULE_VERSION))
    return tuple(result)


def merge_recovery(recovery, confirmations):
    if not confirmations:
        return recovery
    if recovery is None:
        return None
    return replace(recovery,
                   confirmations=tuple(getattr(recovery, "confirmations", ()) or ())
                   + tuple(confirmations))


__all__ = [
    "CONFIRM_EVIDENCE_VERSION", "EXECUTION_RULE_VERSION", "TREND_RULE_VERSION",
    "confirmations_for", "held_days_for", "held_days_map", "market_facts",
    "checkpoints_from_account", "merge_recovery", "recovery_confirmations",
    "themes_for", "trend_for",
]
