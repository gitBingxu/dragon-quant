"""W2 配置契约：显式风险参数、缺失观察与严格模式。"""

from __future__ import annotations


import pytest

from dragon_quant.review_account.strategy import bs_policy as bs
from dragon_quant.tests._bs_policy_support import (
    _context,
    _plan,
)

# ─── 配置契约 ────────────────────────────────────────────────────────────────


def test_config_requires_explicit_risk_preference_and_budgets():
    payload = bs.teaching_fixture().to_dict()
    payload.pop("preference")
    with pytest.raises(bs.PolicyConfigError):
        bs.BSPolicyConfig.from_dict(payload)

    payload = bs.teaching_fixture().to_dict()
    payload["budgets"].pop("account")
    with pytest.raises(bs.PolicyConfigError):
        bs.BSPolicyConfig.from_dict(payload)


@pytest.mark.parametrize("mutate", [
    lambda payload: payload["preference"].update({"rho": float("nan")}),
    lambda payload: payload["budgets"].update({"per_trade": 10_000.0}),
    lambda payload: payload["sizing"].update({"probe_risk": 5_000.0}),
    lambda payload: payload["invalidation"].update({"disaster_stop_pct": -2.0}),
    lambda payload: payload["recovery"].update({"stages": [0.5, 0.5]}),
    lambda payload: payload["scenario"].update({"horizon_days": 0}),
    lambda payload: payload["scenario"].update({"commission_rate": 1.5}),
])
def test_config_rejects_non_finite_and_cross_field_conflicts(mutate):
    payload = bs.teaching_fixture().to_dict()
    mutate(payload)
    with pytest.raises(bs.PolicyConfigError):
        bs.BSPolicyConfig.from_dict(payload)


def test_config_rejects_unknown_fields_and_unsupported_schema():
    payload = bs.teaching_fixture().to_dict()
    payload["mystery"] = 1
    with pytest.raises(bs.PolicyConfigError):
        bs.BSPolicyConfig.from_dict(payload)

    payload = bs.teaching_fixture().to_dict()
    payload["budgets"]["mystery"] = 1
    with pytest.raises(bs.PolicyConfigError):
        bs.BSPolicyConfig.from_dict(payload)

    payload = bs.teaching_fixture().to_dict()
    payload["schema_version"] = 99
    with pytest.raises(bs.PolicyConfigError):
        bs.BSPolicyConfig.from_dict(payload)


def test_config_hash_is_content_addressed_and_fixture_is_labeled():
    first = bs.teaching_fixture()
    second = bs.teaching_fixture()
    third = bs.teaching_fixture(preference={"rho": 0.7})
    assert first.content_hash == second.content_hash
    assert first.content_hash != third.content_hash
    assert first.fixture is True
    assert "教学" in first.label


def test_missing_config_observes_without_inventing_defaults():
    context = _context(config=None)
    context = bs.PolicyContext(**{**context.__dict__, "config": None})
    bundle = bs.evaluate_account_policy(context)
    assert bundle.status == "observe"
    assert bundle.selected_plan_id
    assert {reason["reason"] for reason in bundle.reasons} >= {
        "preference_missing"}
    assert bundle.action_limits["max_new_risk"] == 0.0

    strict = bs.PolicyContext(**{**context.__dict__, "config": None,
                                 "strict": True})
    with pytest.raises(bs.PolicyConfigError):
        bs.evaluate_account_policy(strict)


def test_account_missing_returns_observation_without_fake_cash():
    bundle = bs.evaluate_account_policy(
        _context(account=bs.AccountView(cash=None)))
    assert bundle.status == "insufficient_data"
    assert _plan(bundle, bs.PLAN_WAIT).executable
    assert all(plan.plan_kind == bs.PLAN_WAIT for plan in bundle.plans)
