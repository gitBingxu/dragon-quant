"""C2：各冻结归属按自身规则保护，活动 BS 不接管别的策略批次（R6/L4）。

覆盖验收矩阵：

1. 同 code 不同 owner 用各自成本/失效线，不先加权平均；只失效的 owner 退出，
   另一 owner 的批次、订单归属与下一真实事件成交证据独立。
2. 冻结身份（workspace/strategy/snapshot/params_hash/factor_versions）不同即不同
   归属；伪造 params、快照缺失/被篡改/版本不兼容显式受限，不回退当前源码。
3. 同 owner 不同 plan/失效价只卖命中 scope；部分成交残余与 T+1 批次不被误耗；
   同事件多个 owner 的保护都保留。
4. 活动 BS 切换时旧 owner 仍按冻结规则；旧内建/合法 user worker 各有一个可执行
   保护；坏 owner 不取消好 owner 保护，新增风险按未知占用受限。
5. 纯核不触达存储/运行时；两个适配器（场景/账本）对同一条批次 scope 的成交
   序列（量/价/费/现金/预留/批次）一致。
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest

from dragon_quant.review_account.execution_core.models import Batch, SELLABLE_ON
from dragon_quant.review_account.execution_core.runtime_facts import (
    TEACHING_QUOTE_MAX_AGE_MS,
)
from dragon_quant.review_account.execution_core.risk import RiskBook, RiskCaps
from dragon_quant.review_account.ledger import AccountLedger
from dragon_quant.review_account.ledger.matching import (
    ExecutionCostModel, owner_identity,
)
from dragon_quant.review_account.strategy import bs_policy as bs
from dragon_quant.review_account.strategy.account_engine import AccountEngine
from dragon_quant.review_account.strategy.account_runtime import (
    AccountPolicyRuntime,
)
from dragon_quant.review_account.strategy.bs_policy.plans import (
    ProtectionObligation,
)
from dragon_quant.review_account.strategy.bs_policy.scenario_execution import (
    ScenarioExecution,
)
from dragon_quant.review_account.strategy.owner_protection import (
    OwnerProtectionResolver, policy_config_from_ref, verify_builtin_ref,
)
from dragon_quant.review_account.strategy.runtime import (
    FrozenReferenceError, make_provider,
)
from dragon_quant.tests._bs_policy_support import _account, _position
from dragon_quant.tests.support import patch_storage_dbs
from dragon_quant.workspace import main as workspace_main
from dragon_quant.workspace import snapshots

T0 = 1_700_000_000_000
DAY = "2026-11-18"
NEXT = "2026-11-19"
CALENDAR = ("2026-11-16", "2026-11-17", "2026-11-18", "2026-11-19")


@contextmanager
def _env(tmp_path):
    dragon = str(tmp_path / "dragon.db")
    with patch_storage_dbs(dragon, str(tmp_path / "assets.db")), \
            patch("dragon_quant.infra.storage.paths.DB_PATH", Path(dragon)):
        yield


def _bs_ref(config, strategy_id: str = bs.BS_ACCOUNT_STRATEGY_ID) -> dict:
    return snapshots.builtin_ref(
        strategy_id=strategy_id,
        params={"bs_policy": config.to_dict()})


def _config(stop_pct: float) -> bs.BSPolicyConfig:
    base = bs.teaching_fixture()
    disaster = max(-99.0, stop_pct - 10.0)
    return bs.teaching_fixture(invalidation={
        **base.invalidation.to_dict(), "price_stop_pct": stop_pct,
        "disaster_stop_pct": disaster, "confirm_deadline_days": 2})


def _batch(batch_id, code, *, cost, owner, plan_id="p1", remaining=1000,
           entry="2026-11-16", sellable_mode=SELLABLE_ON,
           sellable_date="2026-11-17") -> Batch:
    return Batch(batch_id=batch_id, code=code, qty=remaining,
                 remaining=remaining, entry_date=entry, cost_price=cost,
                 strategy_ref=owner, plan_id=plan_id,
                 sellable_mode=sellable_mode, sellable_date=sellable_date)


def _seed(ledger, batches):
    state = ledger.load_state()
    state.batches = list(batches)
    state.marks = {}
    ledger.commit_event(expected_revision=state.revision, event_id="seed",
                        source_hash="seed", content_hash="seed", state=state)


class _Event:
    def __init__(self, ts, day, rows):
        self.timestamp = ts
        self.trade_date = day
        self.rows = rows
        self.unavailable = {}
        self.last_closes = {}
        self.candidates = []
        self.phase = "bar"
        self.allow_buy = True
        self.evidence_available = ()


def _row(ts, price, *, bar_low=None, bar_close=None, capacity=10 ** 9,
         tradable=True):
    row = {"execution_price": price, "execution_timestamp": ts,
           "available_at": ts, "available_qty": capacity, "tradable": tradable,
           "phase": "bar"}
    if bar_low is not None:
        row["bar_low"] = bar_low
    if bar_close is not None:
        row["bar_close"] = bar_close
    return row


def _engine(ledger, runtime, calendar=CALENDAR):
    return AccountEngine(ledger, runtime, ExecutionCostModel(),
                         calendar=list(calendar), order_ttl_ms=None,
                         risk_caps=RiskCaps.teaching_fixture(),
                         max_quote_age_ms=TEACHING_QUOTE_MAX_AGE_MS)


# ─── 1. 同 code 不同 owner 不平均成本 ───────────────────────────────────────


def test_same_code_two_owners_only_invalidated_owner_exits(tmp_path):
    cfg_a, cfg_b = _config(-20.0), _config(-40.0)   # A stop 8；B stop 12
    ref_a, ref_b = _bs_ref(cfg_a), _bs_ref(cfg_b)
    with _env(tmp_path):
        ledger = AccountLedger("owners")
        ledger.create(initial_cash=100_000.0, strategy_ref=ref_a)
        _seed(ledger, [
            _batch("a1", "600001", cost=10.0, owner=ref_a, plan_id="pA"),
            _batch("b1", "600001", cost=20.0, owner=ref_b, plan_id="pB")])
        runtime = AccountPolicyRuntime(cfg_a, strategy_ref=ref_a)
        try:
            engine = _engine(ledger, runtime)
            first = engine.process_event(
                _Event(T0, DAY, {"600001": _row(T0, 11.0)}), decide=True,
                decision_kwargs={"pressures": {"600001": 0.1}})
            intents = list(first["documented_intents"])
            assert [(i.action, i.qty) for i in intents] == [("sell", 1000)]
            assert owner_identity(intents[0].strategy_ref) == owner_identity(ref_b)
            assert intents[0].batch_ids == ("b1",)
            # 决策事件不成交；下一真实事件按 11 成交且只消耗 B。
            second = engine.process_event(
                _Event(T0 + 3_600_000, DAY, {"600001": _row(T0 + 3_600_000, 11.0)}))
            assert len(second["fills"]) == 1
            state = engine.state()
            remaining = {batch.batch_id: batch.remaining for batch in state.batches}
            assert remaining == {"a1": 1000, "b1": 0}
            owners = {order.strategy_ref.get("snapshot_id") for order in state.orders}
            assert ref_a["snapshot_id"] not in owners       # A 从未下单
            assert ref_b["snapshot_id"] in owners
            assert state.orders[0].batch_ids == ("b1",)
        finally:
            runtime.close()


# ─── 2. 冻结身份：缓存/伪造/快照 ─────────────────────────────────────────────


def test_frozen_identity_distinguishes_snapshot_and_factor_versions(tmp_path):
    root = tmp_path / "ws"
    workspace_main.init_workspace(str(root))
    factor = root / "factors" / "custom.py"

    def write_factor(label):
        factor.write_text(
            "SPECS=[FactorSpec(name='x', label=%r, frequency='1d',"
            " source='computed', compute=None, impl_key='x')]\n" % label,
            encoding="utf-8")

    write_factor("first")
    cfg = __import__("dragon_quant.workspace.config", fromlist=["config"])
    ws = cfg.load_workspace(root)
    ws.factors = ["factors/custom.py"]
    cfg.write_workspace(ws)
    descriptor = root / "strategies" / "example_momentum" / "strategy.json"
    meta = json.loads(descriptor.read_text(encoding="utf-8"))
    meta["required_factors"] = ["x"]
    descriptor.write_text(json.dumps(meta), encoding="utf-8")

    first = workspace_main.freeze_strategy(
        workspace_main.resolve_selection(str(root), "user:example_momentum"))
    write_factor("second")
    second = workspace_main.freeze_strategy(
        workspace_main.resolve_selection(str(root), "user:example_momentum"))
    # 同 strategy_id 同 params，但 snapshot/factor_versions 不同 → 不同归属。
    assert first["strategy_id"] == second["strategy_id"]
    assert first["params"] == second["params"]
    assert owner_identity(first) != owner_identity(second)
    provider = make_provider_strategy()
    try:
        runtime_one = provider.build(first)
        runtime_two = provider.build(second)
        assert runtime_one is not runtime_two
        assert provider.build(first) is runtime_one      # 同身份才命中缓存
        forged = dict(first, params={**first["params"], "min_open_gap_pct": 99})
        with pytest.raises(FrozenReferenceError):
            provider.build(forged)
    finally:
        provider.close()


def make_provider_strategy():
    from dragon_quant.review_account.models import StrategyConfig
    return make_provider(StrategyConfig())


def test_builtin_bs_ref_rejects_forged_params_and_snapshot():
    ref = _bs_ref(_config(-20.0))
    verify_builtin_ref(ref)
    forged_params = dict(ref, params={
        "bs_policy": {**ref["params"]["bs_policy"],
                      "invalidation": {**_config(-20.0).invalidation.to_dict(),
                                       "price_stop_pct": -99.0}}})
    with pytest.raises(FrozenReferenceError):
        verify_builtin_ref(forged_params)
    forged_snapshot = dict(ref, snapshot_id="builtin:forged")
    with pytest.raises(FrozenReferenceError):
        verify_builtin_ref(forged_snapshot)
    # 合法冻结 BS 参数可解析；缺参数/非法配置显式失败，不套当前配置。
    assert policy_config_from_ref(ref).invalidation.price_stop_pct == -20.0
    with pytest.raises(FrozenReferenceError):
        policy_config_from_ref({"strategy_id": bs.BS_ACCOUNT_STRATEGY_ID,
                                "params": {}})


def test_new_bs_ref_requires_complete_proof(tmp_path):
    """O3：新 BS 稳定 ID 无裸 ref 兼容；缺任一完整性字段即拒绝。"""
    from dragon_quant.review_account.models import StrategyConfig

    config = _config(-20.0)
    full = _bs_ref(config)
    for field in ("params_hash", "snapshot_id", "toolkit_version"):
        broken = dict(full)
        broken.pop(field)
        with pytest.raises(FrozenReferenceError):
            verify_builtin_ref(broken)
    bare = {"strategy_id": bs.BS_ACCOUNT_STRATEGY_ID,
            "params": {"bs_policy": config.to_dict()}}
    with pytest.raises(FrozenReferenceError):
        verify_builtin_ref(bare)
    with pytest.raises(FrozenReferenceError):
        policy_config_from_ref(bare)
    # 正式入口不接受裸 ref（活动运行时构造即失败）。
    with pytest.raises(FrozenReferenceError):
        OwnerProtectionResolver(active_ref=bare, active_config=config,
                                controls=StrategyConfig())
    # 缓存命中前仍校验：伪造 params 即使同 ID 也被拒绝。
    provider = make_provider(StrategyConfig())
    try:
        # W4a：旧 SDK entry/exit 入口显式拒绝；新账户协议入口可用并校验完整性。
        with pytest.raises(FrozenReferenceError):
            provider.build(full)
        account_runtime = provider.build_account(full)
        assert account_runtime.config.content_hash == config.content_hash
        account_runtime.close()
        forged = dict(full, params={
            "bs_policy": {**full["params"]["bs_policy"],
                          "invalidation": {
                              **config.invalidation.to_dict(),
                              "price_stop_pct": -21.0}}})
        with pytest.raises(FrozenReferenceError):
            verify_builtin_ref(forged)
        with pytest.raises(FrozenReferenceError):
            provider.build(forged)
        with pytest.raises(FrozenReferenceError):
            provider.build_account(forged)
    finally:
        provider.close()


def test_missing_proof_owner_is_restricted_but_good_owner_kept():
    """O3：坏 BS owner 缺字段 → restricted + 新增风险受限；好 owner 保护保留。"""
    from dragon_quant.review_account.execution_core.models import LedgerState
    from dragon_quant.review_account.models import StrategyConfig

    config = _config(-20.0)
    ref = _bs_ref(config)
    bare = {"strategy_id": bs.BS_ACCOUNT_STRATEGY_ID,
            "params": {"bs_policy": config.to_dict()}}
    state = LedgerState(cash=100_000.0, trade_date=DAY, marks={
        "600001": 11.0, "600004": 8.0}, batches=[
        _batch("a1", "600001", cost=20.0, owner=ref),
        _batch("x1", "600004", cost=10.0, owner=bare)])
    resolver = OwnerProtectionResolver(
        active_ref=ref, active_config=config, controls=StrategyConfig())
    try:
        obligations, restrictions = resolver.resolve(
            state, trade_date=DAY, marks=state.marks,
            rows={"600004": _row(T0, 8.0, bar_close=8.0)},
            calendar=CALENDAR)
        assert any(item.code == "600001" and item.executable
                   for item in obligations)
        assert any(item.code == "600004" and not item.executable
                   and item.kind == "restricted" for item in obligations)
        assert any(item["reason"] == "owner_policy_unavailable"
                   for item in restrictions)
    finally:
        resolver.close()


# ─── 3. 同 owner 不同 plan/scope、部分成交残余、T+1 ────────────────────────


def test_same_owner_scope_partial_residual_and_restart(tmp_path):
    config = _config(-20.0)
    ref = _bs_ref(config)
    with _env(tmp_path):
        ledger = AccountLedger("scope")
        ledger.create(initial_cash=100_000.0, strategy_ref=ref)
        _seed(ledger, [
            # p1 cost20 → stop16，mark 11 命中；p2 cost10 → stop8 不命中。
            _batch("p1", "600001", cost=20.0, owner=ref, plan_id="plan-1"),
            _batch("p2", "600001", cost=10.0, owner=ref, plan_id="plan-2")])
        runtime = AccountPolicyRuntime(config, strategy_ref=ref)
        try:
            engine = _engine(ledger, runtime)
            first = engine.process_event(
                _Event(T0, DAY, {"600001": _row(T0, 11.0)}), decide=True,
                decision_kwargs={"pressures": {"600001": 0.1}})
            intents = list(first["documented_intents"])
            assert len(intents) == 1
            assert intents[0].batch_ids == ("p1",)     # 只卖命中计划批次
            # 部分成交（容量 600）：残余保留，不消耗 p2。
            second = engine.process_event(
                _Event(T0 + 3_600_000, DAY,
                       {"600001": _row(T0 + 3_600_000, 11.0, capacity=600)}))
            assert [fill["qty"] for fill in second["fill_records"]] == [600]
            state = engine.state()
            remaining = {batch.batch_id: batch.remaining for batch in state.batches}
            assert remaining == {"p1": 400, "p2": 1000}
            order = next(o for o in state.orders if o.side == "sell")
            assert order.batch_ids == ("p1",)
            assert order.plan_id == "plan-1"      # 批次计划范围保留到订单
            # scope 落库：重启后继续只卖 p1 残余。
            rows = ledger.query_orders()
            assert json.loads(rows[0]["batch_ids_json"]) == ["p1"]
            engine2 = _engine(ledger, AccountPolicyRuntime(config, strategy_ref=ref))
            third = engine2.process_event(
                _Event(T0 + 7_200_000, DAY,
                       {"600001": _row(T0 + 7_200_000, 11.0)}))
            assert [fill["qty"] for fill in third["fill_records"]] == [400]
            remaining = {batch.batch_id: batch.remaining for batch in
                         engine2.state().batches}
            assert remaining == {"p1": 0, "p2": 1000}
        finally:
            runtime.close()


def test_t1_locked_batch_keeps_protection_pending_with_scope(tmp_path):
    config = _config(-20.0)
    ref = _bs_ref(config)
    with _env(tmp_path):
        ledger = AccountLedger("t1")
        ledger.create(initial_cash=100_000.0, strategy_ref=ref)
        _seed(ledger, [
            _batch("p1", "600001", cost=20.0, owner=ref, plan_id="plan-1"),
            # 未知可卖日的批次：保护义务保留原因，但不得当日卖出。
            _batch("p2", "600001", cost=20.0, owner=ref, plan_id="plan-2",
                   sellable_mode="unknown", sellable_date=None)])
        runtime = AccountPolicyRuntime(config, strategy_ref=ref)
        try:
            engine = _engine(ledger, runtime)
            first = engine.process_event(
                _Event(T0, DAY, {"600001": _row(T0, 11.0)}), decide=True,
                decision_kwargs={"pressures": {"600001": 0.1}})
            intents = list(first["documented_intents"])
            assert [i.batch_ids for i in intents] == [("p1",)]
            state = engine.state()
            pending = {batch.batch_id: batch.remaining for batch in state.batches}
            assert pending == {"p1": 1000, "p2": 1000}
            sells = [o for o in state.orders if o.side == "sell"]
            assert len(sells) == 1 and sells[0].batch_ids == ("p1",)
        finally:
            runtime.close()


def test_tampered_snapshot_is_restricted_without_cancelling_active_protection(
        tmp_path):
    from dragon_quant.review_account.execution_core.models import LedgerState
    from dragon_quant.review_account.models import StrategyConfig

    cfg_a = _config(-20.0)
    ref_a = _bs_ref(cfg_a)
    ref_user = _user_ref(tmp_path)
    tampered = (tmp_path / "userws" / "snapshots" / ref_user["snapshot_id"]
                / "strategies" / "example_momentum" / "strategy.py")
    tampered.write_text("# tampered\n", encoding="utf-8")
    provider = make_provider(StrategyConfig())
    state = LedgerState(cash=100_000.0, trade_date=DAY, marks={
        "600001": 11.0, "600003": 8.0}, batches=[
        _batch("a1", "600001", cost=20.0, owner=ref_a),
        _batch("u1", "600003", cost=10.0, owner=ref_user)])
    resolver = OwnerProtectionResolver(
        active_ref=ref_a, active_config=cfg_a,
        controls=StrategyConfig(), provider=provider)
    try:
        obligations, restrictions = resolver.resolve(
            state, trade_date=DAY, marks=state.marks,
            rows={"600003": _row(T0, 8.0, bar_close=8.0)},
            calendar=CALENDAR)
        # 好 owner（活动 BS）的保护仍可执行；被篡改快照的 owner 显式受限。
        assert any(item.kind in ("price_invalidation", "disaster")
                   and item.code == "600001" and item.executable
                   for item in obligations)
        assert any(item.kind == "restricted" and item.code == "600003"
                   and not item.executable for item in obligations)
        assert any(item["reason"] == "owner_frozen_runtime_unavailable"
                   for item in restrictions)
    finally:
        resolver.close()


def test_observation_bundle_keeps_owner_protection_without_config():
    ref = _bs_ref(_config(-20.0))
    obligation = ProtectionObligation(
        obligation_id="price_invalidation:600001:a1", code="600001",
        kind="price_invalidation", action="exit", qty=1000, executable=True,
        owner=ref, batch_ids=("a1",))
    context = bs.PolicyContext(
        decision_time="2026-11-18T15:00:00+08:00", config=None,
        account=_account(cash=100_000.0),
        owner_protections=(obligation,), active_owner=ref)
    bundle = bs.evaluate_account_policy(context)
    assert bundle.status == "observe"
    sells = [action for action in bundle.selected_actions
             if action.action == "exit"]
    assert sells and sells[0].batch_ids == ("a1",)
    assert bundle.action_limits["protection_obligations"]


def test_time_invalidation_does_not_add_duplicate_giveback_sell(tmp_path):
    """已有逐批失效义务的持仓不再叠加回吐/收缩（避免同持仓双份卖单）。"""
    config = _config(-20.0)                     # time_limit_days=5
    ref = _bs_ref(config)
    with _env(tmp_path):
        ledger = AccountLedger("dedupe")
        ledger.create(initial_cash=100_000.0, strategy_ref=ref)
        _seed(ledger, [_batch("t1", "600001", cost=10.0, owner=ref,
                              entry="2026-11-10", sellable_date="2026-11-11")])
        runtime = AccountPolicyRuntime(config, strategy_ref=ref)
        try:
            engine = _engine(ledger, runtime, calendar=(
                "2026-11-10", "2026-11-11", "2026-11-12", "2026-11-13",
                "2026-11-16", "2026-11-17", "2026-11-18", "2026-11-19"))
            first = engine.process_event(
                _Event(T0, DAY, {"600001": _row(T0, 11.0, capacity=0)}),
                decide=True)
            intents = list(first["documented_intents"])
            assert len(intents) == 1
            assert intents[0].action == "sell" and intents[0].qty == 1000
            obligations = first["bundle"].action_limits[
                "protection_obligations"]
            kinds = [item["kind"] for item in obligations]
            assert "time_invalidation" in kinds
            assert "giveback" not in kinds
        finally:
            runtime.close()


def _recovery(config):
    return bs.RecoveryState(
        active=True, stage=1, days_since_trigger=2,
        conditions_met=tuple(config.recovery.conditions),
        trigger_event=T0, trigger_trade_date=DAY,
        last_advanced_trade_date=DAY)


def _ledger_state(batches, *, marks=None):
    from dragon_quant.review_account.execution_core.models import LedgerState

    return LedgerState(cash=100_000.0, trade_date=DAY,
                       marks=marks or {}, batches=list(batches))


def test_contraction_offsets_only_covered_batches_not_whole_code(tmp_path):
    """O1：已知强退出只抵扣其覆盖数量；同 code 其余批次仍按剩余目标收缩。"""
    config = _config(-20.0)                     # time_limit_days=5
    ref = _bs_ref(config)
    state = _ledger_state([
        _batch("old", "600001", cost=10.0, owner=ref, plan_id="p-old",
               remaining=100, entry="2026-11-10", sellable_date="2026-11-11"),
        _batch("young", "600001", cost=10.0, owner=ref, plan_id="p-young",
               remaining=1900, entry="2026-11-17",
               sellable_date="2026-11-18")],
        marks={"600001": 10.0})
    runtime = AccountPolicyRuntime(config, strategy_ref=ref)
    try:
        decision = runtime.decide(
            state, candidates=(), decision_time=f"{DAY}T15:00:00+08:00",
            trade_date=DAY, recovery=_recovery(config),
            calendar=("2026-11-10", "2026-11-11", "2026-11-12", "2026-11-13",
                      "2026-11-16", "2026-11-17", "2026-11-18", "2026-11-19"))
        obligations = decision.bundle.action_limits["protection_obligations"]
        by_kind = {item["kind"]: item for item in obligations}
        assert by_kind["time_invalidation"]["batch_ids"] == ("old",)
        assert by_kind["time_invalidation"]["qty"] == 100
        # 总 2000 的原收缩目标 1000，扣已明确退出 100 → 剩余 900（不是整项豁免）。
        assert by_kind["contraction"]["batch_ids"] == ("young",)
        assert by_kind["contraction"]["qty"] == 900
        intents = {(i.code, i.action, i.qty, i.batch_ids)
                   for i in decision.intents}
        assert intents == {
            ("600001", "sell", 100, ("old",)),
            ("600001", "sell", 900, ("young",))}
    finally:
        runtime.close()


def test_contraction_keeps_remaining_target_after_partial_and_restart(tmp_path):
    """O1：部分成交后按剩余目标继续，不用新 qty 重新减半，也不重复下单。"""
    config = _config(-20.0)
    ref = _bs_ref(config)
    with _env(tmp_path):
        ledger = AccountLedger("remain")
        ledger.create(initial_cash=100_000.0, strategy_ref=ref)
        _seed(ledger, [
            _batch("young", "600001", cost=10.0, owner=ref,
                   remaining=2000, entry="2026-11-17",
                   sellable_date="2026-11-18")])
        calendar = ("2026-11-10", "2026-11-11", "2026-11-12", "2026-11-13",
                    "2026-11-16", "2026-11-17", "2026-11-18", "2026-11-19")
        runtime = AccountPolicyRuntime(config, strategy_ref=ref)
        try:
            engine = _engine(ledger, runtime, calendar=calendar)
            first = engine.process_event(
                _Event(T0, DAY, {"600001": _row(T0, 10.0)}), decide=True,
                decision_kwargs={"recovery": _recovery(config)})
            assert [(i.action, i.qty) for i in first["documented_intents"]] == [
                ("sell", 1000)]                 # 原始收缩目标：半仓 1000
            # 下一事件只成交 400 → 剩余目标 600，不用 (2000-400)/2=800 重算。
            engine.process_event(_Event(
                T0 + 3_600_000, DAY,
                {"600001": _row(T0 + 3_600_000, 10.0, capacity=400)}))
            state = engine.state()
            assert state.batches[0].remaining == 1600
            second = engine.process_event(
                _Event(T0 + 7_200_000, DAY,
                       {"600001": _row(T0 + 7_200_000, 10.0, capacity=0)}),
                decide=True,
                decision_kwargs={"recovery": _recovery(config)})
            assert [(i.action, i.qty) for i in second["documented_intents"]] == [
                ("sell", 600)]
            # 同一保护义务+scope 不因重启/重新决策重复下单。
            sells = [o for o in engine.state().orders
                     if o.side == "sell" and not o.is_terminal]
            assert len(sells) == 1
            assert sells[0].quantity_remaining == 600
            engine2 = _engine(ledger, AccountPolicyRuntime(
                config, strategy_ref=ref), calendar=calendar)
            engine2.process_event(
                _Event(T0 + 10_800_000, DAY,
                       {"600001": _row(T0 + 10_800_000, 10.0, capacity=0)}),
                decide=True, decision_kwargs={"recovery": _recovery(config)})
            sells = [o for o in engine2.state().orders
                     if o.side == "sell" and not o.is_terminal]
            assert len(sells) == 1 and sells[0].quantity_remaining == 600
        finally:
            runtime.close()


def test_contraction_scope_retained_when_uncovered_batch_t1_locked(tmp_path):
    """O1：未覆盖批次 T+1 不可卖时义务保留 scope/原因，不因已覆盖批次整项消失。"""
    config = _config(-20.0)
    ref = _bs_ref(config)
    state = _ledger_state([
        _batch("old", "600001", cost=10.0, owner=ref, remaining=100,
               entry="2026-11-10", sellable_date="2026-11-11"),
        _batch("young", "600001", cost=10.0, owner=ref, remaining=1900,
               entry="2026-11-17", sellable_mode="unknown",
               sellable_date=None)], marks={"600001": 10.0})
    runtime = AccountPolicyRuntime(config, strategy_ref=ref)
    try:
        decision = runtime.decide(
            state, candidates=(), decision_time=f"{DAY}T15:00:00+08:00",
            trade_date=DAY, recovery=_recovery(config),
            calendar=("2026-11-10", "2026-11-11", "2026-11-12", "2026-11-13",
                      "2026-11-16", "2026-11-17", "2026-11-18", "2026-11-19"))
        obligations = {item["kind"]: item for item in
                       decision.bundle.action_limits["protection_obligations"]}
        contraction = obligations["contraction"]
        assert contraction["batch_ids"] == ("young",)
        assert contraction["executable"] is False
        assert "no_sellable_quantity" in contraction["reasons"]
        # 仅有已覆盖批次的强退出意图；不可卖的收缩不伪造卖量。
        assert {i.batch_ids for i in decision.intents} == {("old",)}
    finally:
        runtime.close()


# ─── 4. 活动 BS 切换：旧 owner 冻结规则/worker/坏 owner ────────────────────

USER_STRATEGY = '''
class S:
    def evaluate_entry(self, ctx):
        return None
    def evaluate_exit(self, ctx):
        from dragon_quant.strategy_sdk import StrategyIntent
        row = ctx.row or {}
        if row.get("bar_close", 99) < 9:
            return StrategyIntent(action="sell", reason_code="user_exit",
                                  exit_fraction=1.0)
        return None
def create_strategy(params):
    return S()
'''


def _user_ref(tmp_path):
    root = tmp_path / "userws"
    workspace_main.init_workspace(str(root))
    (root / "strategies" / "example_momentum" / "strategy.py").write_text(
        USER_STRATEGY, encoding="utf-8")
    return workspace_main.freeze_strategy(
        workspace_main.resolve_selection(str(root), "user:example_momentum"))


def test_active_switch_keeps_legacy_and_worker_owner_protection(tmp_path):
    from dragon_quant.review_account.models import StrategyConfig
    from dragon_quant.review_account.strategy.runtime import make_worker_factory
    from dragon_quant.workspace.config import BUILTIN_STRATEGY_ID

    cfg_a = _config(-20.0)                      # 活动 A：mark 11 > stop 8，不退出
    ref_a = _bs_ref(cfg_a)
    ref_builtin = snapshots.builtin_ref(strategy_id=BUILTIN_STRATEGY_ID,
                                        params={})
    ref_user = _user_ref(tmp_path)
    ref_bad = {"strategy_id": "user:ghost", "workspace": str(tmp_path / "nope"),
               "snapshot_id": "missing", "params": {},
               "params_hash": snapshots.params_hash({})}
    provider = make_provider(StrategyConfig(),
                             worker_factory=make_worker_factory(timeout=15))
    with _env(tmp_path):
        ledger = AccountLedger("switch")
        ledger.create(initial_cash=100_000.0, strategy_ref=ref_a)
        _seed(ledger, [
            _batch("a1", "600001", cost=10.0, owner=ref_a, plan_id="pA"),
            _batch("l1", "600002", cost=10.0, owner=ref_builtin, plan_id="pL"),
            _batch("u1", "600003", cost=10.0, owner=ref_user, plan_id="pU"),
            _batch("x1", "600004", cost=10.0, owner=ref_bad, plan_id="pX")])
        runtime = AccountPolicyRuntime(cfg_a, strategy_ref=ref_a,
                                       provider=provider)
        try:
            engine = _engine(ledger, runtime)
            rows = {
                "600001": _row(T0, 11.0),
                "600002": _row(T0, 8.0, bar_low=8.0, bar_close=8.0),
                "600003": _row(T0, 8.0, bar_low=8.0, bar_close=8.0),
                "600004": _row(T0, 8.0, bar_low=8.0, bar_close=8.0),
            }
            first = engine.process_event(_Event(T0, DAY, rows), decide=True,
                                         decision_kwargs={
                                             "pressures": {"600001": 0.1}})
            intents = list(first["documented_intents"])
            sells = {(i.strategy_ref.get("strategy_id"), i.code)
                     for i in intents if i.action == "sell"}
            assert ("builtin:dragon_pullback_daily", "600002") in sells
            assert ("user:example_momentum", "600003") in sells
            assert all(code != "600001" for _, code in sells)
            assert all(code != "600004" for _, code in sells)
            limits = dict(first["bundle"].action_limits)
            restrictions = [dict(item) for item in
                            limits["owner_restrictions"]]
            assert any(item["reason"] == "owner_frozen_runtime_unavailable"
                       for item in restrictions)
            restricted = [item for item in limits["protection_obligations"]
                          if item["code"] == "600004"]
            assert restricted and restricted[0]["executable"] is False
            # 坏 owner 使新增风险受限（未知占用），但不取消其它 owner 的保护。
            assert limits["new_risk_restricted"] is True
            assert "owner_protection_unknown" in limits["restriction_reasons"]
            # 下一事件三个好 owner 的卖单都能成交，坏 owner 的批次保留。
            filled = engine.process_event(_Event(
                T0 + 3_600_000, DAY,
                {code: _row(T0 + 3_600_000, price, bar_low=price,
                            bar_close=price)
                 for code, price in (("600001", 11.0), ("600002", 8.0),
                                     ("600003", 8.0), ("600004", 8.0))}))
            assert len(filled["fills"]) == 2
            remaining = {batch.batch_id: batch.remaining
                         for batch in engine.state().batches}
            assert remaining == {"a1": 1000, "l1": 0, "u1": 0, "x1": 1000}
            assert RiskBook.from_state(engine.state()).has_unknown_holding
        finally:
            runtime.close()


# ─── 5. 纯核边界 + 两适配器批次 scope 成交一致 ──────────────────────────────


def test_owner_protection_adapter_matches_scenario_execution(tmp_path):
    """两适配器对**同一条批次 scope 保护义务**产生一致的量/价/费/现金/批次。"""
    from datetime import datetime, timedelta, timezone

    from dragon_quant.strategy_sdk.types import AccountOrderIntent

    cst = timezone(timedelta(hours=8))
    dates = ("2026-11-19", "2026-11-20", "2026-11-23", "2026-11-24",
             "2026-11-25")
    prices = (11.0, 11.0, 11.0, 11.0, 11.0)
    stamps = tuple(int(datetime.fromisoformat(f"{day}T15:00:00").replace(
        tzinfo=cst).timestamp() * 1000) for day in dates)
    config = bs.teaching_fixture()
    ref = _bs_ref(config)
    # 两适配器各自从批次视图派生内部 batch_id；这里比较同一 owner/义务的成交
    # 序列（量/价/费/现金/批次），显式 scope 语义由账本侧测试单独覆盖。
    obligation = ProtectionObligation(
        obligation_id="price_invalidation:600001", code="600001",
        kind="price_invalidation", action="exit", qty=1000, executable=True,
        owner=ref, plan_id="plan-1")

    # 账本适配器：同一 owner/obligation/batch scope 的卖单。
    from dragon_quant.review_account.strategy.account_runtime import AccountDecision

    class _Runtime:
        strategy_ref = ref

        def __init__(self, intents):
            self.intents = intents

        def decide(self, state, *, candidates, decision_time, **kwargs):
            return AccountDecision(bundle=None, intents=tuple(self.intents))

    with _env(tmp_path):
        ledger = AccountLedger("parity")
        ledger.create(initial_cash=100_000.0, strategy_ref=ref)
        _seed(ledger, [_batch("p1", "600001", cost=20.0, owner=ref)])
        intent = AccountOrderIntent(
            action="sell", code="600001", qty=1000, plan_id="plan-1",
            obligation_id=obligation.obligation_id, strategy_ref=ref)
        engine = _engine(ledger, _Runtime([intent]), calendar=dates)
        engine.process_event(
            _Event(stamps[0], dates[0], {"600001": _row(stamps[0], prices[0])}),
            decide=True)
        second = engine.process_event(
            _Event(stamps[1], dates[1], {"600001": _row(stamps[1], prices[1])}))
        engine_state = engine.state()
        engine_fills = [(fill["side"], fill["code"], fill["qty"],
                         fill["price"], fill["fee"])
                        for fill in second["fill_records"]]

    plan = bs.PolicyPlan(plan_id="hold", plan_kind=bs.PLAN_HOLD, code="600001",
                         executable=True, protection=(obligation,))
    steps = [{"at": f"{day}T15:00:00+08:00",
              "available_at": f"{day}T15:00:00+08:00", "trade_date": day,
              "prices": {"600001": prices[index]},
              "tradable": {"600001": True},
              "capacity": {"600001": 10 ** 9}}
             for index, day in enumerate(dates)]
    path = bs.scenario_set_from_records(
        [{"scenario_id": "parity", "kind": "teaching_fixture", "fixture": True,
          "calendar": list(dates), "steps": steps}],
        spec=config.scenario).paths[0]
    account = _account(
        cash=100_000.0,
        positions=(_position(
            code="600001", qty=1000, sellable_qty=1000, cost_price=20.0,
            entry_date="2026-11-16", mark=11.0, strategy_ref=ref,
            batches=(bs.BatchView(
                batch_id="p1", qty=1000, sellable_qty=1000,
                entry_date="2026-11-16", cost_price=20.0,
                sellable_date="2026-11-17", strategy_ref=ref),),),))
    outcome = bs.simulate_plan(
        plan, path, account=account, spec=config.scenario, owner=ref,
        execution=ScenarioExecution(risk_caps=RiskCaps.teaching_fixture()))
    assert outcome.unfilled == ()
    scenario_fills = [(fill["side"], fill["code"], fill["qty"],
                       fill["price"], fill["fee"])
                      for fill in outcome.timeline[1]["fills"]]
    # 决策事件不成交；下一事件按同一核心撮合：量/价/费/现金/批次一致。
    assert len(engine_fills) == len(scenario_fills) == 1
    assert engine_fills[0] == pytest.approx(scenario_fills[0])
    assert engine_state.batches[0].remaining == 0
    assert outcome.timeline[1]["positions"] == {}
    assert engine_state.cash == pytest.approx(outcome.timeline[1]["cash"])
