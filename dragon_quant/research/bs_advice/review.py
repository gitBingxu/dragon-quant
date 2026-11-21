"""review-account BS 分支：严格 PIT 盘中事件 + 真实账户政策执行（A1/A2/A4）。

- 事前母集来自本地 dragons/prep/holdings/tracked（``universe``）；
- 执行只用已校准、会话内可得的 1min 观测（``events``）；日 K 不冒充成交；
- 账户检查点（恢复/政策状态/未成订单/预留/标记/资金归属）经共享适配进入
  ``AccountEngine``/``replay``，不另造账本；
- 显式 review 运行默认持久化到 ``AccountLedger``，可用 ``research bs-ledger``
  有界读回；``persist=False`` 仅作明确的研究预览。
"""

from __future__ import annotations

from typing import Callable, Mapping, Optional

from dragon_quant.review_account.execution_core.matching import ExecutionCostModel
from dragon_quant.review_account.strategy.bs_policy import BS_ACCOUNT_STRATEGY_ID
from dragon_quant.review_account.strategy.policy_replay import (
    ReplayInput, replay_account_policy,
)
from dragon_quant.workspace import snapshots

from .account_file import AccountSnapshot
from .assembly import assemble_snapshot
from .events import (
    DEFAULT_MAX_LAG_MS, DEFAULT_PARTICIPATION, build_intraday_events,
)
from .policy import LoadedPolicy
from .store import persist_run
from .universe import _default_dragons, assemble_universe

DEFAULT_MAX_QUOTE_AGE_MS = 120_000


class BsReviewError(ValueError):
    def __init__(self, message: str, *, code: str = "bs_review_error",
                 exit_code: int = 1) -> None:
        super().__init__(message)
        self.code = code
        self.exit_code = int(exit_code)


def _known_trading_days(db_path=None) -> list[str]:
    """本地已存日 K 的交易日集合（只作 T+1 可卖日历，不取价格）。"""
    from dragon_quant.infra.assets import observations

    try:
        groups, _total = observations.bar_groups(
            None, None, periods=["daily"], limit=20000, db_path=db_path)
    except observations.ObservationStoreError:
        return []
    return sorted({str(row["trade_date"]) for row in groups
                   if row.get("trade_date")})


def _daily_codes(date_from: str, date_to: str, db_path=None) -> tuple[str, ...]:
    """区间内本地日 K 出现过的代码（滚动 cohort 的候选池上界，不取价格）。"""
    from dragon_quant.infra.assets import observations

    try:
        rows = observations.query_bars(date_from, date_to, periods=["daily"],
                                       db_path=db_path, strict=False)
    except observations.ObservationStoreError:
        return ()
    return tuple(sorted({str(row["code"]) for row in rows if row.get("code")}))


def _cost_model(config) -> ExecutionCostModel:
    spec = config.scenario
    return ExecutionCostModel(
        commission_rate=spec.commission_rate,
        min_commission=spec.min_commission,
        stamp_tax_rate=spec.stamp_tax_rate,
        buy_slippage=spec.buy_slippage,
        sell_slippage=spec.sell_slippage, lot_size=spec.lot_size)


def _dragons_codes(date_from: str, date_to: str, reader) -> tuple[str, ...]:
    if reader is None:
        return ()
    try:
        rows = reader(date_from, date_to) or []
    except Exception:  # noqa: BLE001 - 源不可用只影响候选池上界
        return ()
    return tuple(sorted({str(row.get("code")) for row in rows
                         if isinstance(row, Mapping) and row.get("code")}))


def _execution_support_facts(scenario) -> dict:
    import math

    values = (scenario.commission_rate, scenario.min_commission,
              scenario.stamp_tax_rate, scenario.buy_slippage,
              scenario.sell_slippage, scenario.lot_size)
    valid = all(isinstance(value, (int, float))
                and not isinstance(value, bool) and math.isfinite(value)
                and value >= 0 for value in values) and scenario.lot_size > 0
    return {"cost_model_present": bool(valid),
            "fee_fields_valid": bool(valid)}


def build_review_pack(*, account: AccountSnapshot, policy: LoadedPolicy,
                      date_from: str, date_to: str,
                      cutoff: Optional[str] = None, db_path=None,
                      participation: float = DEFAULT_PARTICIPATION,
                      max_lag_ms: int = DEFAULT_MAX_LAG_MS,
                      calendar=None, cohort_mode: str = "rolling",
                      dragons_reader: Optional[Callable] = None,
                      prep_reader: Optional[Callable] = None,
                      calendar_reader: Optional[Callable] = None,
                      plan_reader: Optional[Callable] = None) -> dict:
    """一次冻结实验输入：母集/证据/事件/日历/执行配置（两个 variant 共用）。"""
    if account.account.cash is None:
        raise BsReviewError("账户现金未知，无法回放执行（不伪造余额）",
                            code="account_cash_unknown", exit_code=2)
    if cohort_mode not in ("rolling", "fixed"):
        raise BsReviewError("cohort_mode 必须是 rolling/fixed",
                            code="invalid_request", exit_code=2)
    dragons_reader = dragons_reader or _default_dragons

    def _universe_for(day: str):
        return assemble_universe(
            as_of=day, db_path=db_path, dragons_reader=dragons_reader,
            prep_reader=prep_reader, calendar_reader=calendar_reader,
            plan_reader=plan_reader, holdings=account.codes,
            watch_codes=account.watch_codes)

    universe = _universe_for(date_from)
    decision_days = [day for day in _known_trading_days(db_path)
                     if date_from <= day <= date_to]
    if not decision_days:
        decision_days = [date_from]
    if cohort_mode == "rolling":
        daily_universes = [(_universe_for(day), day) for day in decision_days]
    else:
        daily_universes = [(universe, date_from)]
    pool_codes = set(account.codes)
    for item, _day in daily_universes:
        pool_codes |= set(item.codes)
        pool_codes |= set(_dragons_codes(date_from, date_to, dragons_reader))
    pool_codes |= set(_daily_codes(date_from, date_to, db_path=db_path))
    pool_codes = tuple(sorted(pool_codes))
    if not pool_codes:
        raise BsReviewError("事前母集与持仓均为空，无可评估标的",
                            code="no_universe")
    universe_factory = _universe_for if cohort_mode == "rolling" else None
    cutoff = cutoff or f"{date_to}T15:30:00+08:00"
    # 事实/持有期判定使用**调用方交易日历 ∪ 本地日K交易日**：日K缺一根不能
    # 缩小日历把断档误当连续（趋势规则要求 6 个连续完整交易日）。
    event_calendar = sorted({str(day) for day in (calendar or ())}
                            | set(_known_trading_days(db_path)))
    try:
        base = assemble_snapshot(cutoff=cutoff, codes=pool_codes,
                                 db_path=db_path)
        known_days = _known_trading_days(db_path)
        intraday = build_intraday_events(
            codes=pool_codes, universe=universe, base_snapshot=base.snapshot,
            date_from=date_from, date_to=date_to, cutoff=cutoff,
            db_path=db_path, participation=participation,
            max_lag_ms=max_lag_ms, scenario_spec=policy.config.scenario,
            universe_factory=universe_factory, account=account,
            calendar=event_calendar, entry_version=policy.content_hash,
            execution_facts=_execution_support_facts(policy.config.scenario))
    except Exception as exc:  # noqa: BLE001 - 本地资产不可用显式失败
        from dragon_quant.infra.assets import observations
        from dragon_quant.review_account.evidence import EvidenceError

        if isinstance(exc, (observations.ObservationStoreError, EvidenceError)):
            raise BsReviewError(
                f"本地 assets.db 不可用（不隐式回退雪球/日K捷径）: {exc}",
                code="local_assets_missing") from exc
        raise
    if not intraday.events:
        raise BsReviewError(
            "本地无严格可执行的盘中分钟事件（不触网补数、不用日K收盘冒充成交）",
            code="local_intraday_events_missing")
    calendar = sorted(set(calendar or ()) |
                      {event.trade_date for event in intraday.events} |
                      set(known_days))
    ref = snapshots.builtin_ref(
        strategy_id=BS_ACCOUNT_STRATEGY_ID,
        params={"bs_policy": policy.config.to_dict()})
    return {
        "universe": universe, "pool_codes": pool_codes,
        "base": base, "intraday": intraday, "calendar": calendar,
        "cutoff": cutoff, "ref": ref,
        "cost_model": _cost_model(policy.config),
        "participation": participation, "max_lag_ms": max_lag_ms,
        "cohort_mode": cohort_mode,
        "decision_universes": [{
            "trade_date": day, "coverage": dict(item.coverage),
            "gaps": [dict(gap) for gap in item.gaps],
            "items": [entry.to_dict() for entry in item.items],
            "market_context": dict(item.market_context),
            "market_meta": dict(item.market_meta),
        } for item, day in daily_universes],
    }


def run_bs_review(*, account: AccountSnapshot, policy: LoadedPolicy,
                  date_from: str, date_to: str, cutoff: Optional[str] = None,
                  db_path=None, persist: bool = True,
                  participation: float = DEFAULT_PARTICIPATION,
                  max_lag_ms: int = DEFAULT_MAX_LAG_MS,
                  max_quote_age_ms: Optional[int] = DEFAULT_MAX_QUOTE_AGE_MS,
                  calendar=None,
                  cohort_mode: str = "rolling",
                  market_override=None,
                  dragons_reader: Optional[Callable] = None,
                  prep_reader: Optional[Callable] = None,
                  calendar_reader: Optional[Callable] = None,
                  plan_reader: Optional[Callable] = None) -> dict:
    pack = build_review_pack(
        account=account, policy=policy, date_from=date_from, date_to=date_to,
        cutoff=cutoff, db_path=db_path, participation=participation,
        max_lag_ms=max_lag_ms, calendar=calendar, cohort_mode=cohort_mode,
        dragons_reader=dragons_reader, prep_reader=prep_reader,
        calendar_reader=calendar_reader, plan_reader=plan_reader)
    intraday = pack["intraday"]
    events = intraday.events
    if market_override is not None:
        from dataclasses import replace

        events = tuple(
            replace(event, decision_kwargs={
                **(event.decision_kwargs or {}), "market": market_override})
            for event in events)
    result = _execute(
        account, policy, ref=pack["ref"], events=events,
        calendar=pack["calendar"], date_from=date_from, date_to=date_to,
        snapshot_id=pack["base"].snapshot.snapshot_id,
        cost_model=pack["cost_model"], max_quote_age_ms=max_quote_age_ms,
        persist=persist)
    result["universe"] = pack["universe"].to_dict()
    result["execution_coverage"] = dict(intraday.coverage)
    result["execution_gaps"] = [dict(item) for item in intraday.gaps]
    result["universe_snapshots"] = [dict(item)
                                    for item in intraday.universe_snapshots]
    result["calibration"] = pack["base"].calibration
    result.setdefault("notes", []).append(
        "严格 PIT：1min 会话内可得观测；日K只作估值/结构；容量按参与率打折")
    if not persist:
        result.setdefault("notes", []).append("研究预览：未持久化（persist=False）")
    return result


def _execute(account, policy, *, ref, events, calendar, date_from, date_to,
             snapshot_id, cost_model, max_quote_age_ms, persist) -> dict:
    if persist:
        result = persist_run(
            account=account, policy=policy, ref=ref,
            snapshot_id=snapshot_id, events=events,
            date_from=date_from, date_to=date_to, cost_model=cost_model,
            calendar=calendar, max_quote_age_ms=max_quote_age_ms)
        result["notes"] = []
        return result
    replay = replay_account_policy(ReplayInput(
        ledger_id=f"bs-preview:{date_from}:{date_to}:{policy.content_hash[:12]}",
        account=account.account, config=policy.config, strategy_ref=ref,
        events=tuple(events), calendar=tuple(calendar),
        max_quote_age_ms=max_quote_age_ms, cost_model=cost_model,
        recovery=account.recovery, policy_states=account.policy_states,
        pending_orders=account.pending_orders,
        reservations=account.core_reservations,
        mark_facts=account.mark_facts, batch_facts=account.batch_facts,
        fund_credits=account.fund_credits))
    fills = [dict(fill) for record in replay.events
             for fill in (record.get("fills") or ())]
    from .store import _jsonable

    return {
        "schema_version": "bs_run/1", "mode": "preview", "persisted": False,
        "orders_placed": False, "ledger_id": None, "revision": None,
        "date_from": date_from, "date_to": date_to,
        "policy": policy.to_dict(),
        "events": [_jsonable(item) for item in replay.events],
        "final_state": replay.final_state, "fills": fills,
        "orders": [], "batches": [], "notes": [],
    }


__all__ = ["BsReviewError", "DEFAULT_MAX_QUOTE_AGE_MS", "run_bs_review"]
