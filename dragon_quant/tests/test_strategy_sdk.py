"""Strategy SDK：Intent 校验、深度只读 Context、打包资源。"""

from __future__ import annotations

import math
from types import MappingProxyType

import pytest

from dragon_quant.strategy_sdk import (
    FactorValue,
    StrategyContext,
    StrategyIntent,
    iter_resources,
    read_resource,
    validate_intent,
)
from dragon_quant.strategy_sdk.types import StrategyError, freeze


def test_valid_buy_intent_normalizes_priority_to_float():
    intent = validate_intent(StrategyIntent(action="buy", reason_code="r",
                                            priority=3, position_pct=50),
                             expected_action="buy")
    assert intent.priority == 3.0
    assert intent.position_pct == 50.0
    assert intent.reason_code == "r"


@pytest.mark.parametrize("intent,expected", [
    (StrategyIntent(action="sell", reason_code="r"), "buy"),  # 方向错误
    (StrategyIntent(action="hold", reason_code="r"), "buy"),  # 非法动作
    (StrategyIntent(action="buy", reason_code=""), "buy"),    # 空原因码
    (StrategyIntent(action="buy", reason_code="r", priority=float("nan")), "buy"),
    (StrategyIntent(action="buy", reason_code="r", position_pct=0), "buy"),
    (StrategyIntent(action="buy", reason_code="r", position_pct=101), "buy"),
    (StrategyIntent(action="buy", reason_code="r", exit_fraction=0.5), "buy"),
    (StrategyIntent(action="sell", reason_code="r", exit_fraction=0.3), "sell"),
    (StrategyIntent(action="sell", reason_code="r", exit_fraction=1, position_pct=10), "sell"),
])
def test_invalid_intents_fail_explicitly(intent, expected):
    with pytest.raises(StrategyError):
        validate_intent(intent, expected_action=expected)


def test_evidence_must_be_json_serializable():
    with pytest.raises(StrategyError):
        validate_intent(StrategyIntent(action="buy", reason_code="r",
                                       evidence={"bad": {1, 2}}),
                        expected_action="buy")


def test_freeze_is_deep():
    frozen = freeze({"a": [1, {"b": 2}], "c": (3,)})
    assert isinstance(frozen, MappingProxyType)
    assert isinstance(frozen["a"], tuple)
    assert isinstance(frozen["a"][1], MappingProxyType)


def test_context_is_deeply_read_only():
    ctx = StrategyContext.build(timestamp=1, trade_date="2026-11-11", phase="open",
                                candidate={"code": "600001", "nested": {"x": 1}},
                                row={"bar_close": 10.0}, position=None,
                                account={"cash": 100000}, parameters={"p": 1},
                                factors={"f": FactorValue(name="f", value=1.0)})
    assert isinstance(ctx.candidate, MappingProxyType)
    assert isinstance(ctx.candidate["nested"], MappingProxyType)
    with pytest.raises(TypeError):
        ctx.candidate["code"] = "x"  # type: ignore[index]


def test_factor_value_missing_semantics():
    assert FactorValue(name="f", value=None).missing
    assert FactorValue(name="f", value=0.0).missing is False
    assert not math.isnan(FactorValue(name="f", value=0.0).value)


def test_packaged_resources_are_readable():
    text = read_resource("README.md")
    assert "Strategy SDK" in text
    names = iter_resources("templates")
    assert "example_momentum.strategy.py" in names
    assert "example_meanrev.strategy.json" in names
    assert read_resource("templates", "example_momentum.strategy.py")
