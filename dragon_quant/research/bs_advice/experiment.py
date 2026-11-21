"""BS 冻结实验（W4b）：一次冻结输入包，两个 variant 只读同一包。

冻结包包含完整账户 seed（批次/marks/batch facts/policy states/recovery/订单/预留/
fund credits）、规范化策略/ref、日历、费用/参与率/滞后/新鲜度、逐决策母集快照
（含拒绝/未入选/缺口）、证据事实版本与 received/available/calibration/cutoff、
事件输入与执行实现版本；digest 实算。变体不重新读库，不能因补数/换源/改成员
改变旧对照。生命周期复用 review_account_experiments（running/completed/failed）。
"""

from __future__ import annotations

from typing import Mapping, Optional

from dragon_quant.review_account.strategy.bs_policy import MarketView
from dragon_quant.review_account.strategy.event_inputs import (
    SOURCE_HASH_VERSION, canonical_input, hash_payload,
)
from dragon_quant.review_account.strategy.policy_replay import (
    ReplayInput, replay_account_policy,
)

from .freeze import (
    freeze_experiment_inputs, load_experiment_inputs,
)
from .metrics import (
    compute_metrics, maturity_report, split_samples, time_splits,
)
from .review import BsReviewError, build_review_pack

EXPERIMENT_VERSION = "bs_experiment_v2"
EXECUTION_IMPL_VERSION = "bs_execution/1"
BASELINE_MARKET = MarketView(regime="baseline_no_new_risk",
                             new_risk_allowed=False,
                             reasons=("baseline_no_new_risk",))


class BsExperimentError(ValueError):
    def __init__(self, message: str, *, code: str = "bs_experiment_error",
                 status: int = 422) -> None:
        super().__init__(message)
        self.code = code
        self.status = int(status)


def _cost_payload(cost_model) -> dict:
    return {
        "commission_rate": cost_model.commission_rate,
        "min_commission": cost_model.min_commission,
        "stamp_tax_rate": cost_model.stamp_tax_rate,
        "buy_slippage": cost_model.buy_slippage,
        "sell_slippage": cost_model.sell_slippage,
        "lot_size": cost_model.lot_size,
    }


_EVIDENCE_CONTENT_KEYS = ("events", "evidence_manifest", "decision_universes")


def _evidence_content_digest(payload: Mapping) -> str:
    """只有真实事件/证据清单/母集变化才算能力证据变化；仅 gap 变化不算。"""
    return hash_payload({key: canonical_input(payload.get(key))
                         for key in _EVIDENCE_CONTENT_KEYS})


def _events_payload(events) -> list[dict]:
    return [{
        "timestamp": int(event.timestamp), "trade_date": event.trade_date,
        "rows": canonical_input(dict(event.rows or {})),
        "candidates": [candidate.to_dict()
                       for candidate in tuple(event.candidates or ())],
        "decision_kwargs": canonical_input(dict(event.decision_kwargs or {})),
        "unavailable": canonical_input(dict(event.unavailable or {})),
        "last_closes": canonical_input(dict(event.last_closes or {})),
        "evidence_available": list(event.evidence_available or ()),
        "capacity_override": (None if event.capacity_override is None else
                              canonical_input(dict(event.capacity_override))),
        "decide": bool(event.decide), "phase": event.phase,
    } for event in events]


_HARD_SUPPORT_REASONS = frozenset({
    "freshness_model_missing", "no_price_reference", "evidence_not_available",
    "risk_limit_unknown", "account_risk_unknown", "per_trade_risk_unknown",
    "theme_risk_unknown", "unknown_group_risk_unknown",
    "risk_unknown_holding", "risk_amount_unknown",
})
_FUNDING_REASONS = frozenset({
    "insufficient_cash", "account_risk_exceeded", "per_trade_risk_exceeded",
    "theme_risk_exceeded", "unknown_group_risk_exceeded",
})


def _reference_rows(events) -> dict:
    return {int(event.timestamp): dict(event.rows or {})
            for event in events or ()}


def _support_summary(events) -> dict:
    """执行支持：缺能力/硬数据缺口/资金拒绝分别分类；无事件也是受限。"""
    rejected: dict[str, int] = {}
    used, missing = set(), set()
    decided = 0
    for event in events or ():
        execution = event.get("execution_record") or {}
        if event.get("decided"):
            decided += 1
        for item in execution.get("rejected") or ():
            reason = str(item.get("reason") or "unknown")
            rejected[reason] = rejected.get(reason, 0) + 1
        for item in execution.get("matches") or ():
            reason = item.get("reason")
            if reason:
                reason = str(reason)
                rejected[reason] = rejected.get(reason, 0) + 1
        coverage = event.get("coverage") or {}
        used.update(str(item) for item in
                    coverage.get("used_capabilities") or ())
        missing.update(str(item) for item in
                       coverage.get("missing_capabilities") or ())
    hard = sorted(set(rejected) & _HARD_SUPPORT_REASONS)
    funding = sorted(set(rejected) & _FUNDING_REASONS)
    events_n = len(tuple(events or ()))
    if not events_n:
        status = "no_events"
    elif missing:
        status = "capability_missing"
    elif hard:
        status = "data_unsupported"
    else:
        status = "usable"
    return {
        "status": status,
        "decided_events": decided,
        "used_capabilities": sorted(used),
        "missing_capabilities": sorted(missing),
        "rejected": rejected,
        "hard_reasons": hard,
        "funding_reasons": funding,
        "classification": {
            "no_action_feasible": (status == "usable" and not rejected
                                   and not any(
                                       (event.get("execution_record") or {})
                                       .get("orders") for event in events or ())),
            "data_unsupported": status == "data_unsupported",
            "funding_constrained": bool(funding),
            "unfilled_queue": any(
                str(order.get("status")) in ("proposed", "reserved", "pending",
                                             "partially_filled")
                for event in events or ()
                for order in ((event.get("state") or {}).get("orders") or ())),
        },
    }


def _capability_sets(events):
    used, missing = set(), set()
    for event in events or ():
        coverage = event.get("coverage") or {}
        used.update(str(item) for item in
                    coverage.get("used_capabilities") or ())
        missing.update(str(item) for item in
                       coverage.get("missing_capabilities") or ())
    return {"used": sorted(used), "missing": sorted(missing)}


def _resource_contract(payload: Mapping) -> dict:
    return {
        "date_from": payload.get("date_from"),
        "date_to": payload.get("date_to"),
        "cohort_mode": payload.get("cohort_mode"),
        "participation": payload.get("participation"),
        "max_lag_ms": payload.get("max_lag_ms"),
        "max_quote_age_ms": payload.get("max_quote_age_ms"),
        "order_ttl_ms": payload.get("order_ttl_ms"),
        "account_digest": hash_payload(canonical_input(payload.get("account"))),
        "cost_digest": hash_payload(canonical_input(payload.get("cost"))),
        "calendar_digest": hash_payload(
            canonical_input(payload.get("calendar"))),
        "universe_digest": hash_payload(
            canonical_input(payload.get("decision_universes"))),
    }


def _variant_metrics(run, config, *, calendar, date_from, date_to,
                     initial_cash, initial_equity, splits, cutoff,
                     research_start, study_payload, reference_events,
                     alternatives=None) -> dict:
    support = _support_summary(run.events)
    maturity = maturity_report(
        (run.final_state or {}).get("closed_trades") or (), calendar,
        evaluation_cutoff=cutoff, research_start=research_start,
        execution_support=support)
    metric = compute_metrics(
        initial_cash=initial_cash, initial_equity=initial_equity,
        events=run.events, final_state=run.final_state, config=config,
        trade_calendar=calendar, date_from=date_from, date_to=date_to,
        alternatives=alternatives, maturity_report_data=maturity,
        splits=splits, study=study_payload,
        reference_rows=_reference_rows(reference_events),
        execution_support=support)
    metric["splits"] = {**dict(metric.get("splits") or {}),
                        "by_split": split_samples(
                            run.events, splits, initial_equity=initial_equity,
                            calendar=calendar)}
    metric["execution_support"] = support
    return metric



def build_experiment_pack(*, account, policy, date_from: str, date_to: str,
                          db_path=None, cohort_mode: str = "rolling",
                          calendar=None, dragons_reader=None,
                          prep_reader=None, calendar_reader=None,
                          plan_reader=None,
                          max_quote_age_ms: int = 120_000,
                          order_ttl_ms: int = 86_400_000,
                          study: Optional[Mapping] = None,
                          enhanced: Optional[Mapping] = None) -> dict:
    for name, value in (("study", study), ("enhanced", enhanced)):
        if value is not None and not isinstance(value, Mapping):
            raise BsExperimentError(f"{name} 必须是 JSON 对象",
                                    code="invalid_request", status=400)
    pack = build_review_pack(
        account=account, policy=policy, date_from=date_from, date_to=date_to,
        db_path=db_path, cohort_mode=cohort_mode, calendar=calendar,
        dragons_reader=dragons_reader, prep_reader=prep_reader,
        calendar_reader=calendar_reader, plan_reader=plan_reader)
    intraday = pack["intraday"]
    payload = {
        "schema_version": EXPERIMENT_VERSION,
        "execution_impl": EXECUTION_IMPL_VERSION,
        "source_hash_version": SOURCE_HASH_VERSION,
        "account": canonical_input(account.to_dict()),
        "policy": canonical_input(policy.to_dict()),
        "ref": canonical_input(dict(pack["ref"])),
        "calendar": list(pack["calendar"]),
        "cost": _cost_payload(pack["cost_model"]),
        "participation": pack["participation"],
        "max_lag_ms": pack["max_lag_ms"],
        "max_quote_age_ms": max_quote_age_ms,
        "order_ttl_ms": order_ttl_ms,
        "cohort_mode": cohort_mode,
        "evidence_manifest": canonical_input(
            pack["base"].snapshot.to_dict()),
        "universe_snapshots": [canonical_input(dict(item))
                               for item in intraday.universe_snapshots],
        "decision_universes": canonical_input(
            list(pack.get("decision_universes") or ())),
        "execution_coverage": canonical_input(dict(intraday.coverage)),
        "execution_gaps": [canonical_input(dict(item)) for item in intraday.gaps],
        "events": _events_payload(intraday.events),
        "study": dict(study or {}),
        "enhanced": dict(enhanced or {}),
    }
    digest = hash_payload(payload)
    frozen_pack = {
        "inputs": payload, "digest": digest, "pack": pack,
        "events": intraday.events,
        "calendar": tuple(pack["calendar"]),
        "date_from": date_from, "date_to": date_to,
        "cohort_mode": cohort_mode,
        "decision_universes": pack.get("decision_universes") or (),
        "study": dict(payload.get("study") or {}),
        "enhanced": dict(payload.get("enhanced") or {}),
    }
    frozen = freeze_experiment_inputs(
        account=account, policy=policy, pack=frozen_pack)
    frozen_pack["freeze"] = frozen
    frozen_pack["digest"] = frozen["digest"]
    return frozen_pack


def _run_frozen_variant(frozen, *, market_override=None) -> dict:
    events = frozen.events
    if market_override is not None:
        from dataclasses import replace

        events = tuple(
            replace(event, decision_kwargs={
                **(event.decision_kwargs or {}), "market": market_override})
            for event in events)
    return replay_account_policy(ReplayInput(
        ledger_id=f"bs-exp:{frozen.digest[:16]}",
        account=frozen.account.account, config=frozen.policy.config,
        strategy_ref=dict(frozen.ref), events=tuple(events),
        calendar=frozen.calendar,
        max_quote_age_ms=frozen.max_quote_age_ms,
        cost_model=frozen.cost_model, recovery=frozen.account.recovery,
        policy_states=frozen.account.policy_states,
        pending_orders=frozen.account.pending_orders,
        reservations=frozen.account.core_reservations,
        mark_facts=frozen.account.mark_facts,
        batch_facts=frozen.account.batch_facts,
        fund_credits=frozen.account.fund_credits,
        order_ttl_ms=frozen.order_ttl_ms))


def _run_policy_variant(frozen, config) -> dict:
    from dragon_quant.review_account.strategy.bs_policy import (
        BS_ACCOUNT_STRATEGY_ID,
    )
    from dragon_quant.workspace import snapshots

    ref = snapshots.builtin_ref(
        strategy_id=BS_ACCOUNT_STRATEGY_ID,
        params={"bs_policy": config.to_dict()})
    return replay_account_policy(ReplayInput(
        ledger_id=f"bs-exp:{frozen.digest[:16]}:{config.content_hash[:8]}",
        account=frozen.account.account, config=config, strategy_ref=ref,
        events=frozen.events, calendar=frozen.calendar,
        max_quote_age_ms=frozen.max_quote_age_ms,
        cost_model=frozen.cost_model, recovery=frozen.account.recovery,
        policy_states=frozen.account.policy_states,
        pending_orders=frozen.account.pending_orders,
        reservations=frozen.account.core_reservations,
        mark_facts=frozen.account.mark_facts,
        batch_facts=frozen.account.batch_facts,
        fund_credits=frozen.account.fund_credits,
        order_ttl_ms=frozen.order_ttl_ms))


def run_bs_experiment(*, account, policy, date_from: str, date_to: str,
                      db_path=None, cohort_mode: str = "rolling",
                      maturity: Optional[bool] = None, calendar=None,
                      dragons_reader=None, prep_reader=None,
                      calendar_reader=None, plan_reader=None,
                      parameter_attempts: Optional[list] = None,
                      enhanced_pack: Optional[dict] = None,
                      study: Optional[Mapping] = None,
                      enhanced: Optional[Mapping] = None,
                      pack: Optional[dict] = None) -> dict:
    """同一冻结包运行 BS 默认策略与无新增风险替代政策，输出 KR6 对照。"""
    try:
        pack = pack or build_experiment_pack(
            account=account, policy=policy, date_from=date_from,
            date_to=date_to, db_path=db_path, cohort_mode=cohort_mode,
            calendar=calendar, dragons_reader=dragons_reader,
            prep_reader=prep_reader, calendar_reader=calendar_reader,
            plan_reader=plan_reader, study=study, enhanced=enhanced)
        frozen_payload = dict(pack["freeze"])
        frozen = load_experiment_inputs(frozen_payload)
        if account.to_dict() != frozen.account.to_dict() \
                or policy.content_hash != frozen.policy.content_hash:
            raise BsExperimentError(
                "外部 account/policy 与冻结输入不一致，拒绝覆盖",
                code="frozen_input_mismatch", status=422)
        from .study import parse_study

        study_spec = parse_study(frozen.study) if frozen.study else None
        variant_runs = {}
        if study_spec is not None:
            for spec in study_spec.variants:
                if spec.kind == "baseline_no_new_risk":
                    run = _run_frozen_variant(
                        frozen, market_override=BASELINE_MARKET)
                else:
                    run = _run_policy_variant(frozen, spec.config)
                variant_runs[spec.name] = (spec, run)
            primary_name, primary_spec = next(
                (name, spec) for name, (spec, _run) in variant_runs.items()
                if spec.kind == "policy")
            variant = variant_runs[primary_name][1]
            metric_config = primary_spec.config
            baseline_pair = next(
                ((spec, run) for spec, run in variant_runs.values()
                 if spec.kind == "baseline_no_new_risk"), None)
            baseline = (baseline_pair[1] if baseline_pair is not None else
                        _run_frozen_variant(frozen,
                                            market_override=BASELINE_MARKET))
        else:
            variant = _run_frozen_variant(frozen)
            baseline = _run_frozen_variant(frozen,
                                           market_override=BASELINE_MARKET)
            metric_config = frozen.policy.config
        enhanced_source = (frozen.enhanced or
                           (enhanced_pack.get("freeze")
                            if isinstance(enhanced_pack, Mapping) else None))
        if enhanced_source is not None and not isinstance(enhanced_source,
                                                         Mapping):
            raise BsExperimentError("增强来源必须是冻结 JSON 对象",
                                    code="invalid_request", status=400)
        enhanced_frozen = (load_experiment_inputs(enhanced_source)
                           if enhanced_source else None)
        study_payload = (study_spec.to_dict()
                         if study_spec is not None else None)
    except BsReviewError as exc:
        return {
            "schema_version": EXPERIMENT_VERSION, "status": "failed",
            "reason": exc.code, "message": str(exc),
            "cohort_mode": cohort_mode, "expected_events": 0,
            "completed_events": 0, "metrics": None,
            "pack_digest": None,
        }
    initial_equity = frozen.account.account.marked_equity()
    initial_cash = frozen.account.account.cash
    research_dates = [day for day in frozen.calendar
                      if frozen.date_from <= day <= frozen.date_to]
    evaluation_cutoff = (study_spec.evaluation_cutoff
                         if study_spec is not None and study_spec.evaluation_cutoff
                         else frozen.date_to)
    split_spec = dict(study_spec.splits or {}) if study_spec is not None else {}

    def _splits_for(horizon):
        explicit = split_spec.get("horizon_days")
        return time_splits(
            research_dates,
            train=float(split_spec.get("train", 0.6)),
            valid=float(split_spec.get("valid", 0.2)),
            purge_days=split_spec.get("purge_days"),
            horizon_days=(explicit if explicit is not None else horizon))

    if study_spec is not None:
        run_specs = [(name, spec.config or frozen.policy.config, run,
                      spec.kind) for name, (spec, run) in variant_runs.items()]
        if baseline_pair is None:
            run_specs.append(("baseline_no_new_risk",
                              frozen.policy.config, baseline,
                              "baseline_no_new_risk"))
        policy_specs = [(name, spec.config) for name, (spec, _run)
                        in variant_runs.items() if spec.kind == "policy"]
    else:
        run_specs = [("bs", frozen.policy.config, variant, "policy"),
                     ("baseline_no_new_risk", frozen.policy.config, baseline,
                      "baseline_no_new_risk")]
        policy_specs = [("bs", frozen.policy.config)]
    run_by_name = {name: run for name, _cfg, run, _kind in run_specs}
    run_support = {name: _support_summary(run.events)
                   for name, _cfg, run, _kind in run_specs}
    primary_name = primary_name if study_spec is not None else "bs"
    alternatives = {}
    for name, _cfg, run, _kind in run_specs:
        if name == primary_name:
            continue
        support = run_support[name]
        alternatives[name] = {
            "final_equity": (run.final_state or {}).get("equity"),
            "status": ("evaluated" if support["status"] == "usable"
                       else "insufficient_data"),
            "executable": support["status"] == "usable",
            "comparable": True,
        }
    run_metrics = {}
    for name, cfg, run, _kind in run_specs:
        run_metrics[name] = _variant_metrics(
            run, cfg, calendar=frozen.calendar, date_from=frozen.date_from,
            date_to=frozen.date_to, initial_cash=initial_cash,
            initial_equity=initial_equity, splits=_splits_for(
                cfg.scenario.horizon_days),
            cutoff=evaluation_cutoff, research_start=frozen.date_from,
            study_payload=study_payload, reference_events=frozen.events,
            alternatives=(alternatives if name == primary_name else None))
    variant_metrics = run_metrics[primary_name]
    baseline_name = (baseline_pair[0].name if study_spec is not None
                     and baseline_pair is not None
                     else "baseline_no_new_risk")
    baseline_metrics = run_metrics[baseline_name]
    local_contract = _resource_contract(frozen_payload)
    enhanced_contract = (_resource_contract(enhanced_source)
                         if enhanced_source else None)
    evidence_distinct = bool(
        enhanced_source is not None
        and _evidence_content_digest(enhanced_source)
        != _evidence_content_digest(frozen_payload))
    resource_checks = {
        "same_seed": bool(enhanced_contract and
                          enhanced_contract["account_digest"]
                          == local_contract["account_digest"]),
        "same_cost": bool(enhanced_contract and
                          enhanced_contract["cost_digest"]
                          == local_contract["cost_digest"]),
        "same_calendar": bool(enhanced_contract and
                              enhanced_contract["calendar_digest"]
                              == local_contract["calendar_digest"]),
        "same_research_window": bool(
            enhanced_contract and
            (enhanced_contract["date_from"], enhanced_contract["date_to"])
            == (local_contract["date_from"], local_contract["date_to"])),
        "same_resources": bool(
            enhanced_contract and all(
                enhanced_contract[key] == local_contract[key]
                for key in ("participation", "max_lag_ms",
                            "max_quote_age_ms", "order_ttl_ms",
                            "cohort_mode"))),
        "same_universe": bool(enhanced_contract and
                              enhanced_contract["universe_digest"]
                              == local_contract["universe_digest"]),
    }
    resource_ok = bool(enhanced_frozen is not None
                       and all(resource_checks.values()))
    enhanced_runs = {}
    enhanced_metrics_by_policy = {}
    capability_by_policy = {}
    if resource_ok and evidence_distinct:
        for name, cfg in policy_specs:
            local_caps = _capability_sets(run_by_name[name].events)
            enhanced_run = _run_policy_variant(enhanced_frozen, cfg)
            enhanced_runs[name] = enhanced_run
            enhanced_metric = _variant_metrics(
                enhanced_run, cfg, calendar=frozen.calendar,
                date_from=frozen.date_from, date_to=frozen.date_to,
                initial_cash=initial_cash, initial_equity=initial_equity,
                splits=_splits_for(cfg.scenario.horizon_days),
                cutoff=evaluation_cutoff, research_start=frozen.date_from,
                study_payload=study_payload,
                reference_events=enhanced_frozen.events)
            enhanced_metrics_by_policy[name] = enhanced_metric
            caps = _capability_sets(enhanced_run.events)
            capability_by_policy[name] = {
                "status": ("evaluated"
                           if enhanced_metric["effect_status"] == "evaluated"
                           and run_metrics[name]["effect_status"] == "evaluated"
                           else "insufficient_data"),
                "policy_hash": cfg.content_hash,
                "same_policy": True,
                **resource_checks,
                "evidence_distinct": True,
                "used_added": sorted(set(caps["used"])
                                     - set(local_caps["used"])),
                "missing_removed": sorted(set(local_caps["missing"])
                                          - set(caps["missing"])),
                "used_common": sorted(set(caps["used"])
                                      & set(local_caps["used"])),
                "event_count_delta": (len(enhanced_run.events)
                                      - len(variant.events)),
                "delta_net_return_pct": (
                    None if enhanced_metric["net_return_pct"] is None
                    or run_metrics[name]["net_return_pct"] is None
                    or enhanced_metric["effect_status"] != "evaluated"
                    or run_metrics[name]["effect_status"] != "evaluated"
                    else enhanced_metric["net_return_pct"]
                    - run_metrics[name]["net_return_pct"]),
            }
            if name == primary_name:
                enhanced_metrics_by_policy[name]["capability"] = (
                    capability_by_policy[name])
    enhanced_metrics = enhanced_metrics_by_policy.get(primary_name)
    if enhanced_frozen is None:
        enhanced_reason = "enhanced_evidence_not_provided"
    elif not resource_ok:
        enhanced_reason = "enhanced_resources_not_comparable"
    elif not evidence_distinct:
        enhanced_reason = "enhanced_evidence_not_distinct"
    else:
        enhanced_reason = None
    enhanced = enhanced_runs.get(primary_name)
    by_variant = {}
    study_meta = None
    if study_spec is not None:
        primary_equity = (variant.final_state or {}).get("equity")
        primary_qualified = variant_metrics["effect_status"] == "evaluated"
        for name, (spec, run) in variant_runs.items():
            metric = run_metrics[name]
            support = run_support[name]
            equity = (run.final_state or {}).get("equity")
            executable = support["status"] == "usable"
            qualified = bool(executable and primary_qualified
                             and metric["effect_status"] == "evaluated"
                             and equity is not None
                             and primary_equity is not None)
            by_variant[name] = {
                "kind": spec.kind,
                "config_hash": (spec.config.content_hash
                                if spec.config else None),
                "engineering_status": metric["engineering_status"],
                "effect_status": metric["effect_status"],
                "final_equity": equity,
                "fills": metric["denominator"]["fills"],
                "funding": metric["funding"],
                "unfilled_orders": metric["unfilled_orders"],
                "unfilled_ratio": metric["unfilled_ratio"],
                "gates": metric["gates"],
                "execution_support": support,
                "splits": metric["splits"],
                "maturity": metric["maturity"],
                "executable": executable,
                "comparable": True,
                "qualified": qualified,
                "qualified_missed_amount": (
                    None if not qualified
                    else float(primary_equity) - float(equity)),
                "descriptive_equity_diff": (
                    None if equity is None or primary_equity is None
                    else float(equity) - float(primary_equity)),
                "capability": capability_by_policy.get(name),
                "metrics": metric,
            }
        study_meta = {
            "study_id": study_spec.study_id,
            "study_digest": study_spec.digest,
            "spec": study_spec.to_dict(),
            "by_variant": by_variant,
        }
    status = ("completed" if variant_metrics["engineering_status"] == "completed"
              else "insufficient_data")
    attempts = list(parameter_attempts or ())
    return {
        "schema_version": EXPERIMENT_VERSION,
        "status": status,
        "reason": None if status == "completed" else "insufficient_data",
        "pack_digest": frozen.digest,
        "cohort_mode": cohort_mode,
        "metrics_version": variant_metrics["metrics_version"],
        "study_digest": (study_spec.digest if study_spec is not None else None),
        "date_from": date_from, "date_to": date_to,
        "policy_hash": metric_config.content_hash,
        "policy_profile": metric_config.profile_id,
        "data_modes": {
            "local_only": {"status": status,
                           "reason": None if status == "completed"
                           else "insufficient_data"},
            "enhanced_frozen": {
                "status": ("evaluated" if enhanced_metrics is not None
                           else "insufficient_data"),
                "reason": (None if enhanced_metrics is not None
                           else enhanced_reason),
                "pack_digest": (enhanced_frozen.digest
                                if enhanced_frozen is not None else None)},
        },
        "expected_events": len(frozen.events),
        "completed_events": len(variant.events),
        "watermark": len(variant.events),
        "parameter_attempts": attempts,
        "metrics": {
            "bs": variant_metrics,
            "baseline_no_new_risk": baseline_metrics,
            "enhanced_frozen": enhanced_metrics,
            "capability_effect": (
                capability_by_policy.get(primary_name)
                or {"status": "insufficient_data",
                    "reason": enhanced_reason}),
            "comparison": {
                "alternatives": variant_metrics.get("alternatives"),
                "same_frozen_pack": True,
                "pack_digest": frozen.digest,
                "study": study_meta,
            },
        },
        "variants": {
            "bs": {"events": variant.events,
                   "final_state": variant.final_state,
                   "fills": [dict(fill) for record in variant.events
                             for fill in (record.get("fills") or ())]},
            "baseline_no_new_risk": {
                "events": baseline.events,
                "final_state": baseline.final_state,
                "fills": [dict(fill) for record in baseline.events
                          for fill in (record.get("fills") or ())]},
            "enhanced_frozen": ({
                "events": enhanced.events,
                "final_state": enhanced.final_state}
                if enhanced is not None else None),
        },
        "coverage": {
            "events": len(frozen.events),
            "calendar": list(frozen.calendar),
            "universe": frozen_payload["universe_snapshots"],
            "decision_universes": frozen_payload["decision_universes"],
            "execution_coverage": frozen_payload["execution_coverage"],
            "execution_gaps": frozen_payload["execution_gaps"],
        },
        "notes": [
            "fixed_params_split_is_not_training_walk_forward",
            "enhanced_data_not_fabricated",
            "future_high_not_realizable",
        ],
    }


def run_frozen_experiment(payload: Mapping) -> dict:
    """只用已验证冻结 payload 重算完整实验（无源读取）。"""
    from .freeze import load_experiment_inputs

    frozen = load_experiment_inputs(payload)
    return run_bs_experiment(
        account=frozen.account, policy=frozen.policy,
        date_from=frozen.date_from, date_to=frozen.date_to,
        cohort_mode=frozen.cohort_mode, pack={"freeze": dict(payload)})


def resume_bs_experiment(experiment_id: int) -> dict:
    """按旧 ID 恢复完整实验：读 frozen→无源重算所有 variant/指标→同 ID 落状态。"""
    from dragon_quant.review_account import storage

    row = storage.get_review_account_experiment(
        experiment_id, experiment_version=EXPERIMENT_VERSION)
    if row is None:
        raise BsExperimentError("实验不存在", code="experiment_not_found",
                                status=404)
    frozen = (row.get("request") or {}).get("frozen")
    if not frozen:
        raise BsExperimentError(
            "缺少冻结输入，legacy_non_replayable",
            code="legacy_non_replayable", status=422)
    report = dict(row.get("report") or {})
    expected = len(frozen.get("events") or ())
    completed = int(report.get("completed_events") or 0)
    if row.get("status") == "completed" and completed == expected:
        return {"id": int(experiment_id), **report}
    previous = {"status": row.get("status"), "reason": report.get("reason")}
    fresh = run_frozen_experiment(frozen)
    fresh["resume"] = previous
    if int(fresh.get("completed_events") or 0) != len(frozen.get("events") or ()):
        raise BsExperimentError(
            "恢复后完成事件数与冻结 expected 不一致，不能标 completed",
            code="resume_incomplete", status=422)
    finish_experiment_run(experiment_id, status="completed", report=fresh)
    return {"id": int(experiment_id), **fresh}


def save_experiment_run(*, request: Mapping, expected_events: int,
                        frozen: Optional[Mapping] = None,
                        run_digest: Optional[str] = None) -> int:
    from dragon_quant.review_account import storage

    payload = dict(request)
    if frozen is not None:
        payload["frozen"] = dict(frozen)
    return storage.save_review_account_experiment(
        0, experiment_version=EXPERIMENT_VERSION, status="running",
        request=payload, run_digest=run_digest or (
            str(frozen.get("digest")) if frozen else None),
        report={"expected_events": int(expected_events), "completed_events": 0,
                "watermark": 0, "status": "running"})


def finish_experiment_run(experiment_id: int, *, status: str,
                          report: Mapping) -> bool:
    from dragon_quant.review_account import storage

    return storage.update_review_account_experiment(
        experiment_id, status=status, report=dict(report))


def _project(row: Optional[dict], *, include_frozen: bool = False,
             include_details: bool = False) -> Optional[dict]:
    if row is None:
        return None
    result = dict(row)
    request = dict(result.get("request") or {})
    frozen = request.pop("frozen", None)
    result["request"] = request
    report = dict(result.get("report") or {})
    if not include_details:
        report.pop("variants", None)
    result["report"] = report
    result["replayable"] = bool(
        frozen and frozen.get("schema_version")
        and frozen.get("impl_version"))
    result["frozen_digest"] = (frozen.get("digest") if frozen else None)
    if include_frozen and frozen is not None:
        result["frozen"] = frozen
    return result


def list_bs_experiments(limit: int = 20, *, cursor: Optional[int] = None,
                        total_only: bool = False, include_details: bool = False):
    from dragon_quant.review_account import storage

    page = storage.list_review_account_experiments_by_version(
        EXPERIMENT_VERSION, limit=limit, cursor=cursor,
        total_only=total_only)
    if total_only:
        return page
    page = dict(page)
    page["items"] = [_project(row, include_details=include_details)
                     for row in page["items"]]
    return page


def show_bs_experiment(experiment_id: int, *, include_frozen: bool = False,
                       include_details: bool = False) -> Optional[dict]:
    from dragon_quant.review_account import storage

    row = storage.get_review_account_experiment(
        experiment_id, experiment_version=EXPERIMENT_VERSION)
    return _project(row, include_frozen=include_frozen,
                    include_details=include_details)


def find_bs_experiment_by_digest(run_digest: str) -> Optional[dict]:
    from dragon_quant.review_account import storage

    row = storage.find_review_account_experiment_by_digest(
        run_digest, experiment_version=EXPERIMENT_VERSION)
    return _project(row)


def replay_bs_experiment(experiment_id: int) -> dict:
    """读 run → 校验冻结包 → 完整变体/研究规范重算（不落库）；旧记录明确不可重放。"""
    from dragon_quant.review_account import storage

    row = storage.get_review_account_experiment(
        experiment_id, experiment_version=EXPERIMENT_VERSION)
    if row is None:
        raise BsExperimentError("实验不存在", code="experiment_not_found",
                                status=404)
    frozen = (row.get("request") or {}).get("frozen")
    if not frozen:
        raise BsExperimentError(
            "该实验只保存指标/摘要，缺少冻结输入，legacy_non_replayable",
            code="legacy_non_replayable", status=422)
    from .freeze import FreezeError

    try:
        result = run_frozen_experiment(frozen)
    except FreezeError as exc:
        raise BsExperimentError(str(exc), code=exc.code, status=422) from exc
    result["experiment_id"] = int(experiment_id)
    result["expected_events"] = len(frozen.get("events") or ())
    result["persisted"] = False
    result["digest"] = result.get("pack_digest")
    result["impl_version"] = EXECUTION_IMPL_VERSION
    result["fills"] = [dict(fill) for record
                       in (result.get("variants", {}).get("bs", {})
                           .get("events") or ())
                       for fill in (record.get("fills") or ())]
    return result


__all__ = [
    "EXPERIMENT_VERSION", "EXECUTION_IMPL_VERSION", "BsExperimentError",
    "build_experiment_pack", "run_bs_experiment", "save_experiment_run",
    "finish_experiment_run", "list_bs_experiments", "show_bs_experiment",
    "find_bs_experiment_by_digest", "replay_bs_experiment",
]
