"""``research bs-advice`` 用例（W4a/A1–A3）：只读账户政策建议，不下单/不写账本。

复用 W1 证据、W0/W1 事前母集（dragons+prep+holdings+tracked）、W2 纯政策核与
共享账户检查点适配；缺完整配置时进入**观察模式**：按各持仓冻结引用解析可确定
保护并列出缺项，不构造 dummy 配置、不取消已知保护、不猜空仓/现金。严格模式拒绝
不可解释配置或无合格证据的运行。
"""

from __future__ import annotations

from datetime import datetime
from typing import Callable, Mapping, Optional

from dragon_quant.review_account.models import StrategyConfig
from dragon_quant.review_account.strategy.account_runtime import (
    AccountPolicyRuntime,
)
from dragon_quant.review_account.strategy.bs_policy import (
    BS_ACCOUNT_STRATEGY_ID, AccountView,
)
from dragon_quant.review_account.strategy.bs_policy.scenario_execution import (
    seed_ledger_state,
)
from dragon_quant.review_account.strategy.owner_protection import (
    OwnerProtectionResolver,
)
from dragon_quant.workspace import snapshots

from .account_file import AccountSnapshot, parse_account_file
from .assembly import (
    assemble_snapshot, capabilities_from_snapshot, coverage_from_snapshot,
)
from .candidates import build_candidates
from .dto import Advice, Capabilities, Coverage
from .market import market_from_universe
from .policy import LoadedPolicy, load_policy_payload
from .scenarios import build_historical_scenarios
from .universe import ExAnteUniverse, assemble_universe

#: 公共 replay 的种子批次会追加 ``:sellable`` / ``:locked`` 后缀；此函数是
#: 可逆映射，供用户看到可追溯的原始批次身份（不丢 snapshot 引用）。
_SEED_SUFFIXES = (":sellable", ":locked")


class BsAdviceError(ValueError):
    def __init__(self, message: str, *, code: str = "bs_advice_error",
                 exit_code: int = 1) -> None:
        super().__init__(message)
        self.code = code
        self.exit_code = int(exit_code)


def origin_batch_id(batch_id: str) -> str:
    """把 ``<orig>:sellable`` / ``<orig>:locked`` 还原为原始 batch_id。"""
    for suffix in _SEED_SUFFIXES:
        if batch_id.endswith(suffix):
            return batch_id[: -len(suffix)]
    return batch_id


def _normalize_cutoff(value: str) -> tuple[str, str]:
    if not isinstance(value, str) or not value.strip():
        raise BsAdviceError("需要显式 --cutoff（ISO 时点）",
                            code="invalid_request", exit_code=2)
    try:
        moment = datetime.fromisoformat(value.strip())
    except ValueError as exc:
        raise BsAdviceError(f"--cutoff 不是合法 ISO 时点: {value!r}",
                            code="invalid_request", exit_code=2) from exc
    if moment.tzinfo is None:
        raise BsAdviceError("--cutoff 必须带时区（如 +08:00），拒绝本地时间歧义",
                            code="invalid_request", exit_code=2)
    return moment.isoformat(), moment.date().isoformat()


def _batches_view(account: AccountView) -> tuple[Mapping, ...]:
    rows = []
    for position in account.positions:
        for batch in position.batches:
            row = dict(batch.to_dict())
            row["orig_batch_id"] = origin_batch_id(batch.batch_id)
            rows.append(row)
    return tuple(rows)


def _protection_rows(obligations) -> tuple[Mapping, ...]:
    rows = []
    for item in obligations or ():
        rows.append(dict(item.to_dict()) if hasattr(item, "to_dict")
                    else dict(item))
    return tuple(rows)


def _observation_protections(state, *, trade_date: str,
                             controls: StrategyConfig):
    resolver = OwnerProtectionResolver.for_observation(controls=controls)
    try:
        return resolver.resolve(state, trade_date=trade_date,
                                marks=state.marks)
    finally:
        resolver.close()


def _seed(account: AccountSnapshot, *, trade_date: str):
    return seed_ledger_state(
        account.account, asof=trade_date, recovery=account.recovery,
        policy_states=account.policy_states,
        pending_orders=account.pending_orders,
        reservations=account.core_reservations,
        mark_facts=account.mark_facts, batch_facts=account.batch_facts,
        fund_credits=account.fund_credits)


def build_advice(*, account_payload: Mapping,
                 policy_payload: Optional[Mapping] = None,
                 cutoff: str, db_path=None, strict: bool = False,
                 controls: Optional[StrategyConfig] = None,
                 policy_source: str = "<inline>",
                 dragons_reader: Optional[Callable] = None,
                 prep_reader: Optional[Callable] = None,
                 calendar_reader: Optional[Callable] = None,
                 plan_reader: Optional[Callable] = None) -> dict:
    """构造只读建议 DTO；返回 ``Advice.to_dict()``（JSON 可序列化）。"""
    cutoff_text, trade_date = _normalize_cutoff(cutoff)
    loaded: Optional[LoadedPolicy] = None
    if policy_payload is not None:
        loaded = load_policy_payload(policy_payload, source=policy_source)
    account = parse_account_file(
        account_payload, cutoff=cutoff_text,
        policy_hash=loaded.content_hash if loaded else None)
    controls = controls or StrategyConfig()
    universe = assemble_universe(
        as_of=trade_date, db_path=db_path, dragons_reader=dragons_reader,
        prep_reader=prep_reader, calendar_reader=calendar_reader,
        plan_reader=plan_reader, holdings=account.codes,
        watch_codes=account.watch_codes)
    codes = tuple(sorted(set(universe.codes) | set(account.codes)))
    bundle = assemble_snapshot(cutoff=cutoff_text,
                               codes=codes or ("__none__",), db_path=db_path)
    capabilities = capabilities_from_snapshot(bundle.snapshot)
    coverage = coverage_from_snapshot(bundle.snapshot)
    market = market_from_universe(universe, cutoff=cutoff_text,
                                  snapshot=bundle.snapshot)
    candidates = build_candidates(bundle.snapshot, universe)
    restrictions = list(account.restrictions)
    notes: list[str] = []
    if loaded is not None and loaded.fixture:
        notes.append("配置为教学 fixture（非推荐），真实研究必须显式提供参数")
    policy_meta = (loaded.to_dict() if loaded is not None
                   else {"provided": False})
    common = dict(
        cutoff=cutoff_text, strategy_id=BS_ACCOUNT_STRATEGY_ID,
        account=account.account.to_dict(), batches=_batches_view(account.account),
        restrictions=tuple(restrictions), policy=policy_meta,
        snapshot_id=bundle.snapshot.snapshot_id,
        capabilities=Capabilities(
            usable=tuple(capabilities.get("usable_capabilities") or ()),
            missing=tuple(capabilities.get("missing_capabilities") or ()),
            detail=tuple(capabilities.get("by_capability") or ()),
            calibration_version=str(
                capabilities.get("calibration_version") or "")),
        coverage=Coverage(coverage),
        calibration=bundle.calibration,
        runtime_facts=dict(account.runtime_facts or {}),
        evidence_refs=dict(account.evidence or {}),
        universe=universe.to_dict(),
        candidates=tuple(candidate.to_dict() for candidate in candidates),
        market=market.to_dict(), notes=tuple(notes))
    if loaded is None or account.account.cash is None:
        return _observe_advice(
            account, state_kwargs=dict(trade_date=trade_date,
                                       controls=controls),
            strict=strict,
            missing_reasons=((["policy_config_missing"] if loaded is None else [])
                             + (["account_cash_unknown"]
                                if account.account.cash is None else [])),
            **common)
    scenarios = build_historical_scenarios(
        bundle.snapshot, universe, decision_time=cutoff_text,
        spec=loaded.config.scenario) if loaded is not None else None
    return _decide_advice(
        account, loaded, snapshot=bundle.snapshot, candidate_views=candidates,
        market_view=market, scenario_view=scenarios, universe_view=universe,
        trade_date=trade_date, controls=controls, strict=strict, **common)


def _observe_advice(acct: AccountSnapshot, *, state_kwargs,
                    strict: bool, missing_reasons, **common) -> dict:
    state = _seed(acct, trade_date=state_kwargs["trade_date"])
    obligations, resolver_restrictions = _observation_protections(
        state, trade_date=state_kwargs["trade_date"],
        controls=state_kwargs["controls"])
    restrictions = list(common.pop("restrictions"))
    restrictions.extend(dict(item) for item in resolver_restrictions)
    if strict:
        raise BsAdviceError(
            "严格研究需要完整、可解释的 BS 配置与账户现金；缺失："
            + "、".join(missing_reasons),
            code="policy_config_missing", exit_code=2)
    advice = Advice(
        status="restricted", decision=None, intents=(),
        protections=_protection_rows(obligations),
        missing_capabilities=tuple(missing_reasons),
        restrictions=tuple(restrictions),
        recovery=(acct.recovery.to_dict() if acct.recovery else {}),
        **common)
    return advice.to_dict()


def _decide_advice(acct: AccountSnapshot, loaded: LoadedPolicy, *,
                   snapshot, candidate_views, market_view, scenario_view,
                   universe_view: ExAnteUniverse, trade_date: str,
                   controls: StrategyConfig, strict: bool, **common) -> dict:
    ref = snapshots.builtin_ref(
        strategy_id=BS_ACCOUNT_STRATEGY_ID,
        params={"bs_policy": loaded.config.to_dict()})
    state = _seed(acct, trade_date=trade_date)
    valued = all(position.mark is not None
                 for position in acct.account.positions)
    from .facts import (
        checkpoints_from_account, market_facts, merge_recovery,
        recovery_confirmations,
    )
    facts = market_facts(
        snapshot, acct.account, trade_date=trade_date,
        calendar=tuple(universe_view.trade_dates),
        prices={candidate.code: candidate.price
                for candidate in candidate_views
                if candidate.price is not None},
        checkpoints=checkpoints_from_account(acct))
    recovery = merge_recovery(
        acct.recovery,
        recovery_confirmations(
            snapshot, market=market_view, rows={},
            market_meta=universe_view.market_meta,
            market_context=universe_view.market_context))
    runtime = AccountPolicyRuntime(loaded.config, strategy_ref=ref,
                                   controls=controls)
    try:
        decision = runtime.decide(
            state, candidates=candidate_views,
            decision_time=common["cutoff"],
            snapshot=snapshot, market=market_view, scenarios=scenario_view,
            recovery=recovery, policy_states=acct.policy_states,
            trade_date=trade_date, calendar=tuple(universe_view.trade_dates),
            current_valuation_known=valued, decision_marks=state.marks,
            trends=facts["trends"], pressures=facts["pressures"],
            themes=facts["themes"],
            held_days_by_batch=facts["held_days_by_batch"])
    finally:
        runtime.close()
    bundle = decision.bundle
    limits = dict(getattr(bundle, "action_limits", {}) or {})
    restrictions = list(common.pop("restrictions"))
    restrictions.extend(dict(item) for item in
                        (limits.get("owner_restrictions") or ()))
    if strict and not bool(universe_view.coverage.get("calendar_complete")):
        raise BsAdviceError(
            "严格研究拒绝运行：last5/T-1 交易日历不完整或被截断",
            code="insufficient_data", exit_code=1)
    if strict and (snapshot.coverage.get("truncated")
                   or int(snapshot.coverage.get("usable_facts") or 0) == 0):
        raise BsAdviceError(
            "严格研究拒绝运行：证据覆盖不足或被截断",
            code="insufficient_data", exit_code=1)
    advice = Advice(
        status=str(bundle.status), decision=bundle.to_dict(),
        intents=tuple(intent_row(item) for item in decision.intents),
        protections=_protection_rows(
            limits.get("protection_obligations") or ()),
        rejected=tuple(bundle.rejected),
        missing_capabilities=tuple(bundle.missing_capabilities),
        restrictions=tuple(restrictions),
        recovery=(acct.recovery.to_dict() if acct.recovery else {}),
        **common)
    payload = advice.to_dict()
    payload["trend_audit"] = facts["trend_audit"]
    payload["account_checkpoints"] = checkpoints_from_account(acct)
    return payload


def intent_row(intent) -> Mapping:
    return {
        "action": intent.action, "code": intent.code, "qty": intent.qty,
        "plan_id": intent.plan_id, "obligation_id": intent.obligation_id,
        "limit_price": intent.limit_price, "reason": intent.reason,
        "owner": dict(intent.strategy_ref or {}),
        "batch_ids": list(intent.batch_ids or ()),
        "sequence": int(intent.sequence or 0),
        "family_id": intent.family_id,
    }


def build_advice_from_files(*, account_file: str, policy_file: Optional[str],
                            cutoff: str, db_path=None, strict: bool = False,
                            controls: Optional[StrategyConfig] = None) -> dict:
    """CLI 显式本机路径入口：读取 → 校验 → 同一 ``build_advice`` 用例。"""
    from .policy import read_json_object

    account_payload = read_json_object(account_file, what="账户文件")
    policy_payload = (read_json_object(policy_file, what="策略配置")
                      if policy_file else None)
    return build_advice(
        account_payload=account_payload, policy_payload=policy_payload,
        cutoff=cutoff, db_path=db_path, strict=strict, controls=controls,
        policy_source=str(policy_file or "<none>"))


__all__ = ["BsAdviceError", "build_advice", "build_advice_from_files",
           "origin_batch_id"]
