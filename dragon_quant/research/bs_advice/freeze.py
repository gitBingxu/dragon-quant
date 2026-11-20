"""持久冻结输入（C1）：canonical JSON、严格解码与跨重启重放。

Adapter/Repository/UseCase 分层：

- :func:`freeze_experiment_inputs` 把一次冻结 pack 转成**可读回**的纯 JSON
  （完整账户 seed、策略/ref、事件/候选/decision_kwargs、日历、费用/容量/时效、
  证据 manifest、逐决策母集），digest 实算；
- :func:`load_experiment_inputs` 用 allowlist 构造器严格解码回既有领域 DTO，
  重算 digest、校验版本/有限值/时区/未知字段，坏版本拒绝；
- :func:`replay_frozen` 直接用既有 ``replay_account_policy`` 重放，不重建规则。

禁止 pickle/动态类/脚本反序列化；冻结内容只存本地受控库。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Optional

from dragon_quant.review_account.execution_core.matching import ExecutionCostModel
from dragon_quant.review_account.evidence import EvidenceSnapshot
from dragon_quant.review_account.strategy.bs_policy import (
    AccountView, BatchView, BSPolicyConfig, CandidateView, MarketView,
    PositionView, Reservation,
)
from dragon_quant.review_account.strategy.bs_policy.scenario_contracts import (
    PositionPolicyState, RecoveryState,
)
from dragon_quant.review_account.strategy.event_inputs import hash_payload
from dragon_quant.review_account.strategy.policy_replay import (
    ReplayEvent, ReplayInput, replay_account_policy,
)

from .account_file import AccountSnapshot
from .policy import LoadedPolicy

FREEZE_SCHEMA_VERSION = "bs_freeze/1"
EXECUTION_IMPL_VERSION = "bs_execution/1"

_ALLOWED_TOP = frozenset({
    "schema_version", "impl_version", "account", "policy", "ref", "calendar",
    "cost", "participation", "max_lag_ms", "max_quote_age_ms", "order_ttl_ms",
    "cohort_mode", "date_from", "date_to", "evidence_manifest",
    "universe_snapshots", "decision_universes", "execution_coverage",
    "execution_gaps", "events", "digest", "study", "enhanced",
})


class FreezeError(ValueError):
    def __init__(self, message: str, *, code: str = "freeze_invalid") -> None:
        super().__init__(message)
        self.code = code


def _without_digest(payload: Mapping) -> dict:
    return {key: value for key, value in payload.items() if key != "digest"}


def freeze_experiment_inputs(*, account: AccountSnapshot,
                             policy: LoadedPolicy, pack: Mapping) -> dict:
    inputs = pack["inputs"]
    payload = {
        "schema_version": FREEZE_SCHEMA_VERSION,
        "impl_version": EXECUTION_IMPL_VERSION,
        "account": account.to_dict(),
        "policy": policy.config.to_dict(),
        "ref": dict(pack["pack"]["ref"]),
        "calendar": list(pack["calendar"]),
        "cost": dict(inputs["cost"]),
        "participation": inputs["participation"],
        "max_lag_ms": inputs["max_lag_ms"],
        "max_quote_age_ms": inputs["max_quote_age_ms"],
        "order_ttl_ms": inputs["order_ttl_ms"],
        "cohort_mode": inputs["cohort_mode"],
        "date_from": pack["date_from"], "date_to": pack["date_to"],
        "evidence_manifest": inputs["evidence_manifest"],
        "universe_snapshots": inputs["universe_snapshots"],
        "decision_universes": list(pack.get("decision_universes") or ()),
        "execution_coverage": inputs["execution_coverage"],
        "execution_gaps": inputs["execution_gaps"],
        "events": inputs["events"],
        "study": dict(pack.get("study") or {}),
        "enhanced": dict(pack["enhanced"]) if pack.get("enhanced") else {},
    }
    payload["digest"] = hash_payload(_without_digest(payload))
    return payload


def _fail(message: str, *, code: str = "freeze_decode") -> None:
    raise FreezeError(message, code=code)


def _keys(raw: Mapping, allowed, what: str) -> None:
    unknown = sorted(set(map(str, raw)) - set(allowed))
    if unknown:
        _fail(f"{what} 含未知字段: {unknown}")


def _bool(value, what: str) -> bool:
    if not isinstance(value, bool):
        _fail(f"{what} 必须是布尔值")
    return value


def _int(value, what: str, *, minimum: Optional[int] = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        _fail(f"{what} 必须是整数")
    if minimum is not None and value < minimum:
        _fail(f"{what} 不能小于 {minimum}")
    return value


def _number(value, what: str, *, minimum: Optional[float] = None,
            maximum: Optional[float] = None, optional: bool = False):
    if value is None and optional:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(f"{what} 必须是数值")
    number = float(value)
    if number != number or abs(number) == float("inf"):
        _fail(f"{what} 必须是有限数值")
    if minimum is not None and number < minimum:
        _fail(f"{what} 不能小于 {minimum}")
    if maximum is not None and number > maximum:
        _fail(f"{what} 不能大于 {maximum}")
    return number


def _date(value, what: str) -> str:
    from datetime import date

    text = str(value or "")
    try:
        date.fromisoformat(text)
    except ValueError:
        _fail(f"{what} 不是真实日期: {value!r}")
    return text


def _checks(raw) -> tuple:
    from dragon_quant.review_account.evidence import CapabilityCheck

    allowed = {"rule_id", "capability", "status", "protects",
               "matched_evidence", "missing_scopes", "missing_intervals",
               "reasons", "affected_actions", "inputs_missing",
               "schema_version"}
    result = []
    for item in raw or ():
        data = dict(item)
        _keys(data, allowed, "evidence_check")
        result.append(CapabilityCheck(
            rule_id=str(data["rule_id"]), capability=str(data["capability"]),
            status=str(data["status"]), protects=str(data["protects"]),
            matched_evidence=tuple(str(value) for value in
                                   data.get("matched_evidence") or ()),
            missing_scopes=tuple(str(value) for value in
                                 data.get("missing_scopes") or ()),
            missing_intervals=tuple(str(value) for value in
                                    data.get("missing_intervals") or ()),
            reasons=tuple(data.get("reasons") or ()),
            affected_actions=tuple(str(value) for value in
                                   data.get("affected_actions") or ()),
            inputs_missing=tuple(str(value) for value in
                                 data.get("inputs_missing") or ()),
            schema_version=str(data.get("schema_version") or "1")))
    return tuple(result)


def _confirmation(raw: Mapping):
    from dragon_quant.review_account.strategy.bs_policy import (
        EvidenceConfirmation,
    )

    data = dict(raw)
    _keys(data, {"rule_id", "evidence_ids", "available_at", "version",
                 "snapshot_id", "independent_of"}, "confirmation")
    return EvidenceConfirmation(
        rule_id=str(data["rule_id"]),
        evidence_ids=tuple(str(value) for value in data.get("evidence_ids") or ()),
        available_at=str(data.get("available_at") or ""),
        version=str(data.get("version") or ""),
        snapshot_id=str(data.get("snapshot_id") or ""),
        independent_of=tuple(str(value) for value in
                             data.get("independent_of") or ()))


def _candidate(raw: Mapping) -> CandidateView:
    allowed = {"code", "name", "theme_ids", "rank", "eligible",
               "eligibility_reasons", "tradable", "tradability_reasons",
               "price", "pressure_pct", "structure", "evidence_checks",
               "confirmations", "entry_version"}
    data = dict(raw)
    _keys(data, allowed, "candidate")
    rank = data.get("rank")
    if rank is not None:
        rank = _int(rank, "candidate.rank", minimum=1)
    pressure = _number(data.get("pressure_pct"), "candidate.pressure_pct",
                       minimum=0, maximum=1, optional=True)
    price = _number(data.get("price"), "candidate.price", minimum=0,
                    optional=True)
    return CandidateView(
        code=str(data["code"]), name=str(data.get("name") or ""),
        theme_ids=tuple(str(value) for value in data.get("theme_ids") or ()),
        rank=rank,
        eligible=_bool(data.get("eligible"), "candidate.eligible"),
        eligibility_reasons=tuple(str(value) for value in
                                  data.get("eligibility_reasons") or ()),
        tradable=_bool(data.get("tradable"), "candidate.tradable"),
        tradability_reasons=tuple(str(value) for value in
                                  data.get("tradability_reasons") or ()),
        price=price, pressure_pct=pressure,
        structure=dict(data.get("structure") or {}),
        evidence_checks=_checks(data.get("evidence_checks")),
        confirmations=tuple(_confirmation(item)
                            for item in data.get("confirmations") or ()),
        entry_version=str(data.get("entry_version") or ""))


def _account(raw: Mapping) -> AccountSnapshot:
    from dragon_quant.review_account.execution_core.models import (
        Order, Reservation as CoreReservation,
    )
    from dragon_quant.review_account.execution_core.runtime_facts import (
        BatchFacts, MarkFact,
    )

    _keys(raw, {"as_of", "account", "recovery", "policy_states",
                "runtime_facts", "evidence", "watch_codes", "policy_hash",
                "restrictions", "notes", "pending_orders", "reservations",
                "mark_facts", "batch_facts", "fund_credits"}, "account_snapshot")
    view = dict(raw["account"])
    _keys(view, {"cash", "positions", "reservations", "peak_equity",
                 "drawdown_pct", "consecutive_losses", "pending_orders",
                 "marked_equity"},
          "account_view")
    positions = []
    for position in view.get("positions") or ():
        data = dict(position)
        _keys(data, {"code", "name", "qty", "sellable_qty", "cost_price",
                     "entry_date", "mark", "theme_ids", "pressure_pct",
                     "trend", "held_days", "strategy_ref", "batches"},
              "position")
        batches = tuple(BatchView(**dict(batch))
                        for batch in data.pop("batches", ()) or ())
        positions.append(PositionView(batches=batches, **data))
    reservations = tuple(Reservation(**dict(item))
                         for item in view.get("reservations") or ())
    account = AccountView(
        cash=_number(view.get("cash"), "account.cash", minimum=0,
                     optional=True),
        positions=tuple(positions), reservations=reservations,
        peak_equity=_number(view.get("peak_equity"), "account.peak_equity",
                            minimum=0, optional=True),
        drawdown_pct=_number(view.get("drawdown_pct"), "account.drawdown_pct",
                             minimum=0, optional=True),
        consecutive_losses=_int(view.get("consecutive_losses") or 0,
                                "account.consecutive_losses", minimum=0))
    recovery = (RecoveryState.from_row(dict(raw["recovery"]))
                if raw.get("recovery") else None)
    states = tuple(PositionPolicyState.from_row(dict(item))
                   for item in raw.get("policy_states") or ())
    mark_facts = {item["code"]: MarkFact.from_row(dict(item))
                  for item in raw.get("mark_facts") or ()}
    batch_facts = {item["batch_id"]: BatchFacts.from_row(dict(item))
                   for item in raw.get("batch_facts") or ()}
    as_of = str(raw.get("as_of") or "")
    if as_of:
        from datetime import datetime

        moment = datetime.fromisoformat(as_of)
        if moment.tzinfo is None:
            _fail("account.as_of 必须带时区")
    return AccountSnapshot(
        account=account, as_of=as_of, recovery=recovery,
        policy_states=states,
        runtime_facts=dict(raw.get("runtime_facts") or {}),
        evidence=dict(raw.get("evidence") or {}),
        watch_codes=tuple(str(value) for value in raw.get("watch_codes") or ()),
        policy_hash=raw.get("policy_hash"),
        restrictions=tuple(dict(item) for item in raw.get("restrictions") or ()),
        notes=str(raw.get("notes") or ""),
        pending_orders=tuple(Order(**dict(item))
                             for item in raw.get("pending_orders") or ()),
        core_reservations=tuple(CoreReservation(**dict(item))
                                for item in raw.get("reservations") or ()),
        mark_facts=mark_facts, batch_facts=batch_facts,
        fund_credits=dict(raw.get("fund_credits") or {}))


def _scenario_set(raw: Mapping, config: BSPolicyConfig):
    from dragon_quant.review_account.strategy.bs_policy.scenario_contracts import (
        ScenarioPath, ScenarioSet, ScenarioStep,
    )

    data = dict(raw)
    _keys(data, {"paths", "insufficiency"}, "scenarios")
    paths = []
    for item in data.get("paths") or ():
        path = dict(item)
        _keys(path, {"scenario_id", "kind", "basis", "mature", "provenance",
                     "fixture", "calendar", "steps"}, "scenario_path")
        steps = []
        for step in path.get("steps") or ():
            step_data = dict(step)
            _keys(step_data, {"index", "at", "trade_date", "prices", "tradable",
                              "evidence_available", "capacity", "available_at",
                              "bar_highs", "bar_complete", "interval_start",
                              "interval_end"}, "scenario_step")
            step_data["evidence_available"] = tuple(
                str(value) for value in step_data.get("evidence_available") or ())
            steps.append(ScenarioStep(**step_data))
        paths.append(ScenarioPath(
            scenario_id=str(path["scenario_id"]), kind=str(path["kind"]),
            steps=tuple(steps), basis=str(path.get("basis") or ""),
            mature=_bool(path.get("mature", True), "scenario.mature"),
            provenance=dict(path.get("provenance") or {}),
            fixture=_bool(path.get("fixture", False), "scenario.fixture"),
            calendar=tuple(str(day) for day in path.get("calendar") or ())))
    return ScenarioSet(paths=tuple(paths),
                       insufficiency=tuple(str(value) for value in
                                           data.get("insufficiency") or ()))


def _decoded_kwargs(original: Mapping, **decoded) -> dict:
    result = {}
    for key, value in decoded.items():
        if key in original:
            result[key] = value
    return result


def _event(raw: Mapping, config: BSPolicyConfig) -> ReplayEvent:
    data = dict(raw)
    _keys(data, {"timestamp", "trade_date", "rows", "candidates",
                 "decision_kwargs", "capacity_override", "decide", "phase",
                 "unavailable", "last_closes", "evidence_available"},
          "event")
    kwargs = dict(data.get("decision_kwargs") or {})
    _keys(kwargs, {"snapshot", "market", "scenarios", "pressures", "themes",
                   "trends", "held_days", "held_days_by_batch",
                   "recovery_confirmations", "trend_audit", "recovery",
                   "policy_states", "decision_marks",
                   "current_valuation_known", "strict", "probability",
                   "risk_envelope"}, "decision_kwargs")
    snapshot = (EvidenceSnapshot.from_dict(dict(kwargs["snapshot"]))
                if kwargs.get("snapshot") else None)
    market = None
    if kwargs.get("market"):
        market_data = dict(kwargs["market"])
        _keys(market_data, {"regime", "new_risk_allowed", "breadth", "reasons"},
              "market")
        allowed = market_data.get("new_risk_allowed")
        if allowed is not None and not isinstance(allowed, bool):
            _fail("market.new_risk_allowed 必须是布尔值或 null")
        market = MarketView(
            regime=str(market_data.get("regime") or "unknown"),
            new_risk_allowed=allowed,
            breadth=_number(market_data.get("breadth"), "market.breadth",
                            minimum=0, maximum=1, optional=True),
            reasons=tuple(str(value) for value in market_data.get("reasons") or ()))
    scenarios = (_scenario_set(dict(kwargs["scenarios"]), config)
                 if kwargs.get("scenarios") is not None else None)
    pressures = ({str(code): _number(value, "decision.pressures", minimum=0,
                                     maximum=1)
                  for code, value in dict(kwargs.get("pressures") or {}).items()}
                 if kwargs.get("pressures") is not None else None)
    themes = ({str(code): tuple(str(item) for item in value)
               for code, value in dict(kwargs.get("themes") or {}).items()}
              if kwargs.get("themes") is not None else None)
    trends = ({str(code): str(value)
               for code, value in dict(kwargs.get("trends") or {}).items()}
              if kwargs.get("trends") is not None else None)
    held_days = ({str(code): _int(value, "decision.held_days", minimum=0)
                  for code, value in dict(kwargs.get("held_days") or {}).items()}
                 if kwargs.get("held_days") is not None else None)
    recovery = (RecoveryState.from_row(dict(kwargs["recovery"]))
                if isinstance(kwargs.get("recovery"), Mapping)
                else kwargs.get("recovery"))
    policy_states = tuple(kwargs.get("policy_states") or ())
    held_days_by_batch = (
        {str(code): _int(value, "decision.held_days_by_batch", minimum=0)
         for code, value in dict(kwargs.get("held_days_by_batch") or {}).items()}
        if kwargs.get("held_days_by_batch") is not None else None)
    from dragon_quant.review_account.strategy.bs_policy.scenario_contracts import (
        RecoveryConditionConfirmation,
    )
    confirmations = tuple(
        (item if isinstance(item, RecoveryConditionConfirmation)
         else RecoveryConditionConfirmation.from_row(dict(item)))
        for item in kwargs.get("recovery_confirmations") or ())
    trend_audit = (dict(kwargs.get("trend_audit") or {})
                   if kwargs.get("trend_audit") is not None else None)
    decision_marks = ({str(code): _number(value, "decision.mark", minimum=0)
                       for code, value in dict(kwargs.get("decision_marks")
                                               or {}).items()}
                      if kwargs.get("decision_marks") is not None else None)
    cval = kwargs.get("current_valuation_known")
    if cval is not None and not isinstance(cval, bool):
        _fail("current_valuation_known 必须是布尔值或 null")
    strict_flag = kwargs.get("strict")
    if strict_flag is not None:
        strict_flag = _bool(strict_flag, "decision.strict")
    capacity = data.get("capacity_override")
    if capacity is not None:
        capacity = {str(code): _int(value, "capacity", minimum=0)
                    for code, value in dict(capacity).items()}
    trade_date = _date(data.get("trade_date"), "event.trade_date")
    phase = str(data.get("phase") or "bar")
    if phase not in ("bar", "open", "late", "auction"):
        _fail(f"未知 event.phase: {phase!r}")
    return ReplayEvent(
        timestamp=_int(data.get("timestamp"), "event.timestamp", minimum=0),
        trade_date=trade_date, rows=dict(data.get("rows") or {}),
        candidates=tuple(_candidate(item) for item in
                         data.get("candidates") or ()),
        decision_kwargs=_decoded_kwargs(
            kwargs, snapshot=snapshot, market=market, scenarios=scenarios,
            pressures=pressures, themes=themes, trends=trends,
            held_days=held_days, recovery=recovery,
            policy_states=policy_states, decision_marks=decision_marks,
            current_valuation_known=cval, strict=strict_flag,
            probability=kwargs.get("probability"),
            risk_envelope=kwargs.get("risk_envelope"),
            held_days_by_batch=held_days_by_batch,
            recovery_confirmations=confirmations,
            trend_audit=trend_audit),
        decide=_bool(data.get("decide"), "event.decide"),
        capacity_override=capacity, phase=phase,
        unavailable=dict(data.get("unavailable") or {}),
        last_closes=dict(data.get("last_closes") or {}),
        evidence_available=tuple(str(value) for value in
                                 data.get("evidence_available") or ()))


@dataclass(frozen=True)
class FrozenInputs:
    account: AccountSnapshot
    policy: LoadedPolicy
    ref: Mapping
    events: tuple
    calendar: tuple
    cost_model: ExecutionCostModel
    max_quote_age_ms: Optional[int]
    order_ttl_ms: Optional[int]
    digest: str
    date_from: str
    date_to: str
    cohort_mode: str
    study: Mapping = field(default_factory=dict)
    enhanced: Mapping = field(default_factory=dict)

    def replay_input(self, *, ledger_id: str) -> ReplayInput:
        return ReplayInput(
            ledger_id=ledger_id, account=self.account.account,
            config=self.policy.config, strategy_ref=dict(self.ref),
            events=self.events, calendar=self.calendar,
            max_quote_age_ms=self.max_quote_age_ms,
            cost_model=self.cost_model,
            recovery=self.account.recovery,
            policy_states=self.account.policy_states,
            pending_orders=self.account.pending_orders,
            reservations=self.account.core_reservations,
            mark_facts=self.account.mark_facts,
            batch_facts=self.account.batch_facts,
            fund_credits=self.account.fund_credits,
            order_ttl_ms=self.order_ttl_ms)


def load_experiment_inputs(payload: Mapping) -> FrozenInputs:
    if not isinstance(payload, Mapping):
        _fail("冻结输入必须是 JSON 对象")
    _keys(payload, _ALLOWED_TOP, "freeze_input")
    if payload.get("schema_version") != FREEZE_SCHEMA_VERSION:
        _fail(f"不支持的冻结 schema_version: {payload.get('schema_version')!r}",
              code="freeze_schema")
    if payload.get("impl_version") != EXECUTION_IMPL_VERSION:
        _fail(f"执行实现版本不兼容（冻结 {payload.get('impl_version')!r}，当前 "
              f"{EXECUTION_IMPL_VERSION}）", code="freeze_impl_version")
    digest = str(payload.get("digest") or "")
    expected = hash_payload(_without_digest(payload))
    if digest != expected:
        _fail("冻结输入 digest 与内容不一致（拒绝被修改的 pack）",
              code="freeze_digest")
    date_from = _date(payload.get("date_from"), "date_from")
    date_to = _date(payload.get("date_to"), "date_to")
    if date_from > date_to:
        _fail("date_from 不能晚于 date_to")
    calendar = tuple(_date(day, "calendar") for day in payload.get("calendar") or ())
    if not calendar:
        _fail("calendar 不能为空", code="freeze_calendar")
    if list(calendar) != sorted(set(calendar)):
        _fail("calendar 必须严格递增且无重复", code="freeze_calendar")
    try:
        config = BSPolicyConfig.from_dict(dict(payload["policy"]))
        account = _account(dict(payload["account"]))
        events = tuple(_event(dict(item), config)
                       for item in payload.get("events") or ())
        cost_data = dict(payload["cost"])
        _keys(cost_data, {"commission_rate", "min_commission",
                          "stamp_tax_rate", "buy_slippage", "sell_slippage",
                          "lot_size"}, "cost")
        cost = ExecutionCostModel(
            commission_rate=_number(cost_data["commission_rate"],
                                    "cost.commission_rate", minimum=0,
                                    maximum=0.999),
            min_commission=_number(cost_data["min_commission"],
                                   "cost.min_commission", minimum=0),
            stamp_tax_rate=_number(cost_data["stamp_tax_rate"],
                                   "cost.stamp_tax_rate", minimum=0,
                                   maximum=0.999),
            buy_slippage=_number(cost_data["buy_slippage"],
                                 "cost.buy_slippage", minimum=0,
                                 maximum=0.999),
            sell_slippage=_number(cost_data["sell_slippage"],
                                  "cost.sell_slippage", minimum=0,
                                  maximum=0.999),
            lot_size=_int(cost_data["lot_size"], "cost.lot_size", minimum=1))
    except FreezeError:
        raise
    except Exception as exc:  # noqa: BLE001
        _fail(f"冻结输入解码失败：{type(exc).__name__}: {exc}")
    ref = dict(payload["ref"])
    _keys(ref, {"schema_version", "snapshot_id", "workspace_id", "strategy_id",
                "sdk_version", "params", "params_hash", "files",
                "python_version", "toolkit_version", "reproducibility",
                "required_factors", "factor_versions"}, "ref")
    if str(ref.get("strategy_id") or "") not in (
            "builtin:bs_account_decision", "bs_account_decision"):
        _fail("ref.strategy_id 不是 BS 账户决策", code="freeze_ref")
    params = dict(ref.get("params") or {})
    if params.get("bs_policy") != config.to_dict():
        _fail("ref.params.bs_policy 与 policy 不一致", code="freeze_ref")
    previous = None
    for event in events:
        if previous is not None and event.timestamp <= previous:
            _fail("冻结事件时间戳必须严格递增", code="freeze_event_order")
        previous = event.timestamp
        if event.trade_date not in (date_from, date_to) and not (
                date_from <= event.trade_date <= date_to):
            _fail("event.trade_date 不在研究区间", code="freeze_event_order")
    _number(payload.get("participation"), "participation",
            minimum=0.000001, maximum=1)
    _int(payload.get("max_lag_ms"), "max_lag_ms", minimum=0)
    max_age = payload.get("max_quote_age_ms")
    if max_age is not None:
        max_age = _int(max_age, "max_quote_age_ms", minimum=0)
    ttl = payload.get("order_ttl_ms")
    if ttl is not None:
        ttl = _int(ttl, "order_ttl_ms", minimum=0)
    policy = LoadedPolicy(config=config, normalized=config.to_dict(),
                          content_hash=config.content_hash,
                          fixture=bool(config.fixture), source="<frozen>")
    study = dict(payload.get("study") or {})
    if study:
        from .study import StudyError, parse_study

        try:
            parse_study(study)
        except StudyError as exc:
            _fail(f"研究规范非法：{exc}", code="freeze_study")
    enhanced = dict(payload.get("enhanced") or {})
    if enhanced:
        load_experiment_inputs(enhanced)
    return FrozenInputs(
        account=account, policy=policy, ref=ref, events=events,
        calendar=calendar, cost_model=cost, max_quote_age_ms=max_age,
        order_ttl_ms=ttl, digest=digest, date_from=date_from,
        date_to=date_to, cohort_mode=str(payload.get("cohort_mode") or "rolling"),
        study=study, enhanced=enhanced)


def replay_frozen(payload: Mapping, *, ledger_id: str = "bs-frozen-replay"
                  ) -> dict:
    frozen = load_experiment_inputs(payload)
    result = replay_account_policy(frozen.replay_input(ledger_id=ledger_id))
    fills = [dict(fill) for record in result.events
             for fill in (record.get("fills") or ())]
    return {
        "schema_version": "bs_frozen_replay/1",
        "digest": frozen.digest,
        "impl_version": EXECUTION_IMPL_VERSION,
        "events": result.events, "final_state": result.final_state,
        "fills": fills,
        "fees": sum(float(fill.get("fee") or 0.0) for fill in fills),
        "persisted": False, "orders_placed": False,
    }


__all__ = [
    "FREEZE_SCHEMA_VERSION", "EXECUTION_IMPL_VERSION", "FreezeError",
    "FrozenInputs", "freeze_experiment_inputs", "load_experiment_inputs",
    "replay_frozen",
]
