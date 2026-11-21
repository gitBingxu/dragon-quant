"""账户政策运行时（W3）：账本投影 → W2 纯核 → 订单意图窄适配。

职责边界：本层只做 **Adapter + 编排**——把批次账本投影成 W2 的只读
``AccountView``，调用公共 ``evaluate_account_policy``，再把 ``DecisionBundle`` 的
选中动作窄适配成订单意图。它不重复实现 G1–G8，也不直接撮合/记账（那是
:mod:`account_engine` + :mod:`ledger`）。

R6：买入批次与本运行时冻结归属绑定；卖出只统计**同一冻结归属**的批次，新 BS
不接管旧仓或其它策略的持仓（旧仓退出由原 ref 的运行时负责）。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Iterable, Mapping, Optional, Sequence

from dragon_quant.review_account.models import StrategyConfig
from dragon_quant.review_account.strategy import bs_policy as bs
from dragon_quant.strategy_sdk.types import (
    ACCOUNT_PROTOCOL_VERSION, AccountOrderIntent,
)

from dragon_quant.review_account.ledger.matching import _owner_key
from dragon_quant.review_account.ledger.models import LedgerState
from dragon_quant.review_account.ledger.projection import project_account_view
from dragon_quant.review_account.strategy.owner_protection import (
    OwnerProtectionResolver,
)

ACCOUNT_RUNTIME_VERSION = "account_runtime/2"


def build_account_runtime(ref: Mapping, *, controls: Optional[StrategyConfig] = None,
                          provider=None) -> "AccountPolicyRuntime":
    """按**完整冻结 ref** 构造新账户政策 runtime（A4 协议入口，不读当前源码）。

    复用 ``verify_builtin_ref`` + ``policy_config_from_ref`` 的完整性/内容哈希
    校验；缺失字段、伪造 params 或与当前工具指纹不一致显式拒绝。
    """
    from dragon_quant.review_account.evidence import thaw
    from dragon_quant.review_account.strategy.owner_protection import (
        policy_config_from_ref, verify_builtin_ref,
    )

    frozen = dict(thaw(dict(ref or {})))
    verify_builtin_ref(frozen)
    config = policy_config_from_ref(frozen)
    return AccountPolicyRuntime(config, strategy_ref=frozen,
                                controls=controls or StrategyConfig(),
                                provider=provider)


@dataclass(frozen=True)
class AccountDecision:
    """一次账户政策决策：完整 bundle + 供引擎撮合的订单意图。"""

    bundle: bs.DecisionBundle
    intents: tuple[AccountOrderIntent, ...]

    def to_dict(self) -> dict:
        return {
            "bundle": self.bundle.to_dict() if self.bundle is not None else None,
            "intents": [
                {"action": intent.action, "code": intent.code,
                 "qty": intent.qty, "plan_id": intent.plan_id,
                 "obligation_id": intent.obligation_id,
                 "limit_price": intent.limit_price,
                 "risk_amount": intent.risk_amount, "reason": intent.reason,
                 "owner": dict(intent.strategy_ref or {}),
                 "batch_ids": list(intent.batch_ids or ())}
                for intent in self.intents],
        }


class AccountPolicyRuntime:
    """内聚的 BS 账户政策运行时（W4 再接入用户入口/Web）。

    ``resolver`` 是外层 owner 保护 Adapter：按每个批次的完整冻结身份解析各自的
    保护义务；纯核只接收不可变义务值，不接触 DB/worker/回调。``resolver`` 关闭
    由本运行时 ``close()`` 统一负责。
    """

    protocol_version = ACCOUNT_PROTOCOL_VERSION

    def __init__(self, config: bs.BSPolicyConfig, *, strategy_ref: Mapping,
                 controls: Optional[StrategyConfig] = None,
                 provider=None, resolver: Optional[OwnerProtectionResolver] = None,
                 owns_resolver: bool = True):
        if not isinstance(config, bs.BSPolicyConfig):
            raise bs.PolicyConfigError("config 必须是 BSPolicyConfig")
        config.validate()
        self.config = config
        self.strategy_ref = dict(strategy_ref or {})
        self.controls = controls or StrategyConfig()
        self.resolver = resolver or OwnerProtectionResolver(
            active_ref=self.strategy_ref, active_config=config,
            controls=self.controls, provider=provider)
        self.owns_resolver = owns_resolver

    def close(self) -> None:
        if self.owns_resolver and self.resolver is not None:
            self.resolver.close()

    # ── 投影 ────────────────────────────────────────────────────────────────
    def project(self, state: LedgerState, **kwargs) -> bs.AccountView:
        return project_account_view(state, **kwargs)

    def owned_qty(self, state: LedgerState, code: str,
                  owner: Optional[Mapping] = None,
                  batch_ids: Sequence[str] = ()) -> int:
        key = _owner_key(owner if owner is not None else self.strategy_ref)
        scope = frozenset(batch_ids or ())
        return sum(batch.remaining for batch in state.batches
                   if batch.code == code
                   and _owner_key(batch.strategy_ref) == key
                   and (not scope or batch.batch_id in scope))

    # ── 决策 ────────────────────────────────────────────────────────────────
    def decide(self, state: LedgerState, *, candidates: Iterable,
               decision_time: str, snapshot=None, market=None, envelope=None,
               recovery=None, policy_states: Iterable = (), scenarios=None,
               probability=None, strict: bool = False,
               themes=None, pressures=None, trends=None, held_days=None,
               held_days_by_batch=None,
               rows=None, trade_date: str = "", calendar: Sequence[str] = (),
               event_time: Optional[int] = None, phase: str = "bar",
               decision_marks=None, current_valuation_known: Optional[bool] = None,
               ) -> AccountDecision:
        account = self.project(state, themes=themes, pressures=pressures,
                               trends=trends, held_days=held_days,
                               held_days_by_batch=held_days_by_batch)
        obligations, restrictions = self.resolver.resolve(
            state, trade_date=trade_date or state.trade_date,
            marks=decision_marks if decision_marks is not None else state.marks,
            rows=rows, calendar=calendar, held_days=held_days,
            held_days_by_batch=held_days_by_batch,
            event_time=event_time, phase=phase)
        context = bs.PolicyContext(
            decision_time=decision_time, config=self.config, snapshot=snapshot,
            account=account, candidates=tuple(candidates),
            market=market or bs.MarketView(),
            risk_envelope=envelope, scenarios=scenarios,
            probability=probability, recovery_state=recovery,
            policy_states=tuple(policy_states), strict=strict,
            owner_protections=tuple(obligations),
            owner_restrictions=tuple(restrictions),
            active_owner=self.strategy_ref,
            require_fill_proof=True,
            current_valuation_known=current_valuation_known)
        bundle = bs.evaluate_account_policy(context)
        plan_id = bundle.selected_plan_id or ""
        return AccountDecision(
            bundle=bundle,
            intents=tuple(self._intents(bundle, state, plan_id,
                                        event_time=event_time)))

    def _intents(self, bundle: bs.DecisionBundle, state: LedgerState,
                 plan_id: str, *, event_time: Optional[int] = None
                 ) -> list[AccountOrderIntent]:
        # 完整业务身份归并：同 code/方向的**不同归属/计划/批次 scope**是不同
        # 订单，不能被 (code, side) 吞掉（活动 BS 不接管别的策略批次）。
        chosen: dict[tuple, AccountOrderIntent] = {}
        switch_sells = [
            action for action in bundle.selected_actions
            if getattr(action, "action", "") == bs.ACTION_SWITCH
            and (getattr(action, "delta_qty", None) or 0) < 0]
        for action in bundle.selected_actions:
            if not getattr(action, "executable", True):
                continue
            code = action.code
            if not code:
                continue
            owner = dict(getattr(action, "owner", {}) or {}) or self.strategy_ref
            batch_ids = tuple(getattr(action, "batch_ids", ()) or ())
            # 保护动作保留其批次/计划范围；普通计划动作沿用选中计划。
            intent_plan = str(getattr(action, "plan_id", "") or plan_id)
            owned = self.owned_qty(state, code, owner, batch_ids)
            obligation = getattr(action, "obligation_id", "") or ""
            intent = None
            if action.action in (bs.ACTION_ENTER, bs.ACTION_PROBE, bs.ACTION_ADD):
                qty = self._buy_qty(action, owned)
                if qty > 0:
                    intent = self._intent("buy", action, code, qty, intent_plan,
                                          owner=owner, batch_ids=())
            elif action.action == bs.ACTION_SWITCH:
                # G7：父卖腿 + 子买腿；子买依赖**本次事件分配**的父卖单稳定 ID。
                if (getattr(action, "delta_qty", None) or 0) < 0:
                    qty = min(owned, abs(int(action.delta_qty)))
                    if qty > 0:
                        intent = self._intent(
                            "sell", action, code, qty, intent_plan,
                            owner=owner, batch_ids=batch_ids)
                else:
                    qty = self._buy_qty(action, owned)
                    if qty > 0:
                        intent = self._intent(
                            "buy", action, code, qty, intent_plan,
                            owner=owner, batch_ids=())
                    parent = self._switch_parent(switch_sells, action)
                    if intent is not None and parent is not None \
                            and event_time is not None:
                        intent = replace(intent, depends_on=(
                            self._leg_order_id(parent, intent_plan, owner,
                                               event_time),))
            elif obligation:
                # 保护义务：数量是该 scope 的剩余目标，不再按整 owner 放大。
                qty = self._obligation_qty(action, owned)
                if qty > 0:
                    side = "sell"
                    intent = self._intent(side, action, code, qty, intent_plan,
                                          owner=owner, batch_ids=batch_ids)
            elif action.action == bs.ACTION_EXIT:
                if owned > 0:
                    intent = self._intent("sell", action, code, owned,
                                          intent_plan, owner=owner,
                                          batch_ids=batch_ids)
            elif action.action == bs.ACTION_REDUCE:
                qty = self._reduce_qty(action, owned)
                if qty > 0:
                    intent = self._intent("sell", action, code, qty, intent_plan,
                                          owner=owner, batch_ids=batch_ids)
            if intent is None:
                continue
            key = (_owner_key(intent.strategy_ref), intent.code, intent.action,
                   intent.plan_id, intent.obligation_id, intent.batch_ids)
            current = chosen.get(key)
            if current is None or (not current.obligation_id
                                   and intent.obligation_id):
                chosen[key] = intent
        # 稳定顺序：父卖腿（sequence 0）先于子买腿（sequence 1），保护义务最低。
        return sorted(chosen.values(), key=lambda item: int(item.sequence or 0))

    @staticmethod
    def _switch_parent(switch_sells, action):
        for candidate in switch_sells:
            if getattr(candidate, "family_id", "") and \
                    candidate.family_id == getattr(action, "family_id", ""):
                return candidate
        return switch_sells[0] if switch_sells else None

    @staticmethod
    def _leg_order_id(action, plan_id: str, owner: Mapping,
                      event_time: int) -> str:
        from dragon_quant.review_account.execution_core.identity import (
            stable_order_id,
        )

        return stable_order_id(
            event_time=int(event_time), code=action.code, action="sell",
            plan_id=plan_id, obligation_id="",
            sequence=int(getattr(action, "sequence", 0) or 0),
            batch_ids=tuple(getattr(action, "batch_ids", ()) or ()),
            owner=owner)

    @staticmethod
    def _obligation_qty(action, owned: int) -> int:
        delta = getattr(action, "delta_qty", None)
        if delta:
            return min(owned, abs(int(delta)))
        target = getattr(action, "target_qty", None)
        if target is not None and action.action == bs.ACTION_EXIT:
            return owned
        return 0

    @staticmethod
    def _buy_qty(action, owned: int) -> int:
        delta = getattr(action, "delta_qty", None)
        if delta:
            return int(delta)
        target = getattr(action, "target_qty", None)
        if target:
            return max(0, int(target) - owned)
        return 0

    @staticmethod
    def _reduce_qty(action, owned: int) -> int:
        delta = getattr(action, "delta_qty", None)
        if delta:
            return min(owned, abs(int(delta)))
        return max(0, owned // 2)

    def _intent(self, side: str, action, code: str, qty: int,
                plan_id: str, *, owner: Mapping,
                batch_ids: Sequence[str] = ()) -> AccountOrderIntent:
        from dragon_quant.review_account.evidence import thaw

        condition = getattr(action, "condition_ref", None)
        trigger_kind = ""
        trigger_price = None
        if condition is not None and getattr(condition, "kind", "") in (
                "price_above", "price_below"):
            trigger_kind = condition.kind
            trigger_price = condition.value
        return AccountOrderIntent(
            action=side, code=code, qty=qty, plan_id=plan_id,
            obligation_id=getattr(action, "obligation_id", ""),
            limit_price=None,
            risk_amount=float(getattr(action, "risk_amount", 0.0) or 0.0),
            reason=getattr(action, "condition", "") or "",
            strategy_ref=dict(thaw(dict(owner or self.strategy_ref))),
            trigger_kind=trigger_kind, trigger_price=trigger_price,
            evidence_rule=str(getattr(action, "evidence_rule", "") or ""),
            evidence_ids=tuple(getattr(action, "evidence_ids", ()) or ()),
            depends_on=tuple(getattr(action, "depends_on", ()) or ()),
            valid_until_event=getattr(action, "valid_until_event", None),
            sequence=int(getattr(action, "sequence", 0) or 0),
            family_id=str(getattr(action, "family_id", "") or ""),
            pressure_pct=getattr(action, "pressure_pct", None),
            invalidate_below=getattr(action, "invalidate_below", None),
            batch_ids=tuple(batch_ids or ()))
