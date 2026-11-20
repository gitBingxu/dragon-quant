"""BS 政策测试共享构造器（不是测试用例）。"""

from __future__ import annotations

from dragon_quant.review_account.evidence import CapabilityCheck
from dragon_quant.review_account.strategy import bs_policy as bs

# ─── 构造辅助 ────────────────────────────────────────────────────────────────


def _checks(*rule_ids: str, capability: str = "intraday_bars",
            status: str = "usable") -> tuple[CapabilityCheck, ...]:
    return tuple(CapabilityCheck(rule_id=rule_id, capability=capability,
                                 status=status, protects="new_risk")
                 for rule_id in rule_ids)


ENTRY_CHECKS = ("entry:breakout:600001", "entry:pullback:600001",
                "entry:confirm:600001", "entry:probe:600001",
                "confirm:600001")


def _candidate(**overrides) -> bs.CandidateView:
    values = dict(
        code="600001", name="候选一", eligible=True, tradable=True,
        price=10.0, pressure_pct=0.10, rank=1, theme_ids=("T1",),
        structure={"breakout_level": 10.2, "pullback_level": 9.8,
                   "confirm_level": 10.5, "probe_level": 10.0},
        evidence_checks=_checks(*ENTRY_CHECKS),
    )
    values.update(overrides)
    return bs.CandidateView(**values)


def _account(**overrides) -> bs.AccountView:
    values = dict(cash=100_000.0, positions=())
    values.update(overrides)
    return bs.AccountView(**values)


def _position(**overrides) -> bs.PositionView:
    values = dict(code="600001", name="持仓", qty=1000, sellable_qty=1000,
                  cost_price=10.0, entry_date="2026-11-17", mark=10.0,
                  theme_ids=("T1",), pressure_pct=0.10, trend="up", held_days=1)
    values.update(overrides)
    return bs.PositionView(**values)


#: 教学 fixture 显式交易日历（真实工作日；跨周末用于 T+1 校验）。
TEACHING_DATES = ("2026-11-19", "2026-11-20", "2026-11-23", "2026-11-24",
                  "2026-11-25")
#: 教学 fixture 显式事件容量（股/事件；有限假设，不是“无限”）。
TEACHING_CAPACITY = 10_000_000


def _steps(prices_by_scenario: dict[str, list[float]], *,
           tradable: dict[str, bool] | None = None,
           tradable_by_step: dict[int, dict[str, bool]] | None = None,
           trade_dates: list[str] | None = None,
           evidence_by_step: dict[int, tuple[str, ...]] | None = None,
           capacity: dict[str, int] | None = None,
           capacity_by_step: dict[int, dict[str, int]] | None = None,
           at_by_step: dict[int, str] | None = None,
           kind: str = "teaching_fixture", fixture: bool = True):
    codes = {code for series in prices_by_scenario.values()
             for code in series}
    dates = list(trade_dates) if trade_dates else list(TEACHING_DATES)
    paths = []
    for scenario_id, series_by_code in prices_by_scenario.items():
        steps = []
        for index in range(1, 6):
            prices = {code: float(series[index - 1])
                      for code, series in series_by_code.items()}
            flags = {}
            caps = {}
            for code in codes:
                flag = True
                if tradable is not None:
                    flag = tradable.get(code, True)
                if tradable_by_step and index in tradable_by_step:
                    flag = tradable_by_step[index].get(code, flag)
                flags[code] = flag
                caps[code] = (capacity or {}).get(
                    code, TEACHING_CAPACITY)
                if capacity_by_step and index in capacity_by_step:
                    caps[code] = capacity_by_step[index].get(code, caps[code])
            at = ((at_by_step or {}).get(index)
                  or f"{dates[index - 1]}T15:00:00+08:00")
            steps.append({"at": at,
                          "prices": prices, "tradable": flags,
                          "trade_date": (trade_dates[index - 1]
                                         if trade_dates else dates[index - 1]),
                          "capacity": caps,
                          "evidence_available": (
                              evidence_by_step or {}).get(index, ())})
        paths.append({"scenario_id": scenario_id, "kind": kind,
                      "steps": steps, "mature": True, "fixture": fixture,
                      "calendar": list(dates),
                      "basis": "pytest relative fixture path",
                      "provenance": {"capacity_assumption":
                                     "explicit_teaching_fixture"}})
    return paths


def _scenarios(config: bs.BSPolicyConfig, prices=None, **kwargs) -> bs.ScenarioSet:
    if prices is None:
        prices = {
            "up": {"600001": [10.0, 10.2, 10.4, 10.6, 10.8]},
            "down": {"600001": [10.0, 9.8, 9.6, 9.4, 9.2]},
            "flat": {"600001": [10.0, 10.0, 10.0, 10.0, 10.0]},
        }
    return bs.scenario_set_from_records(_steps(prices, **kwargs), spec=config.scenario)


def _context(*, config=None, account=None, candidates=None, scenarios=None,
             market=None, envelope=None, probability=None, recovery=None,
             policy_states=(), strict=False, snapshot=None,
             decision_time="2026-11-18T16:00:00+08:00"):
    config = config or bs.teaching_fixture()
    if scenarios is None:
        scenarios = _scenarios(config)
    return bs.PolicyContext(
        decision_time=decision_time,
        config=config,
        account=account if account is not None else _account(),
        candidates=tuple(candidates or ()),
        market=market or bs.MarketView(regime="trend_up",
                                       new_risk_allowed=True),
        risk_envelope=envelope,
        scenarios=scenarios,
        probability=probability,
        recovery_state=recovery,
        policy_states=tuple(policy_states),
        snapshot=snapshot,
        strict=strict,
    )


def _plan(bundle, kind: str, code: str = ""):
    return next((plan for plan in bundle.plans
                 if plan.plan_kind == kind and (not code or plan.code == code)),
                None)


def _comparison(bundle, plan_id: str):
    return next((item for item in bundle.comparisons
                 if item.plan_id == plan_id), None)



def _probe_state(**overrides) -> bs.PositionPolicyState:
    values = dict(code="600001", probe_plan_id="probe:600001:s1:t10.0000",
                  probe_filled_at="2026-11-18T10:00:00+08:00",
                  probe_evidence_ids=("obs-probe",), adds_done=0,
                  version="v1")
    values.update(overrides)
    return bs.PositionPolicyState(**values)


def _confirmation(**overrides) -> bs.EvidenceConfirmation:
    values = dict(rule_id="confirm:600001", evidence_ids=("obs-confirm",),
                  available_at="2026-11-18T15:00:00+08:00", version="v2",
                  snapshot_id="")
    values.update(overrides)
    return bs.EvidenceConfirmation(**values)

def _long_hold_config():
    base = bs.teaching_fixture()
    return bs.teaching_fixture(invalidation={
        **base.invalidation.to_dict(), "time_limit_days": 10,
        "confirm_deadline_days": 2})

def _fake_snapshot(cutoff="2026-11-18T09:00:00+08:00", snapshot_id="snap-1"):
    from dragon_quant.review_account.evidence import EvidenceRequest, EvidenceSnapshot
    return EvidenceSnapshot(
        snapshot_id=snapshot_id, cutoff=cutoff, facts=(),
        request=EvidenceRequest(cutoff=cutoff),
        source_manifest={"store_state": "ready", "sources": {},
                         "calibration_version": "1", "calibrations": {}},
        coverage={"facts": 0, "usable_facts": 0, "truncated": False,
                  "unbounded_scope": False, "limits": {"facts": 10, "bars": 10},
                  "by_capability": {}},
        content_hash="test")


def _state_context(*, confirmation=None, state=None, snapshot=None):
    candidate = _candidate(confirmations=(confirmation,)) if confirmation \
        else _candidate()
    return _context(
        account=_account(cash=100_000.0, positions=(_position(),)),
        candidates=(candidate,), policy_states=(state or _probe_state(),),
        snapshot=snapshot)
