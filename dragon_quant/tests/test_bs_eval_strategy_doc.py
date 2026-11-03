"""策略说明文案与 models/sell.py 的单一真值保持一致。"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from dragon_quant.bs_eval import constants, strategy_doc
from dragon_quant.review_account.models import (
    BUY_SIGNAL_CODES,
    DEFAULT_OFF_SELL_RULES,
    SELL_RULE_NAMES,
)

SELL_PY = (Path(__file__).resolve().parents[1]
           / "review_account" / "strategy" / "sell.py")


def _sell_reason_codes() -> set[str]:
    """从 ``_sell(...)`` 字面量 + 赋给 ``code`` 变量的三元字面量提取原因码。"""

    tree = ast.parse(SELL_PY.read_text(encoding="utf-8"), filename=str(SELL_PY))
    pattern = re.compile(r"^[a-z][a-z0-9_]+$")
    codes: set[str] = set()

    def collect(node: ast.AST) -> None:
        for child in ast.walk(node):
            if isinstance(child, ast.Constant) and isinstance(child.value, str) \
                    and pattern.match(child.value):
                codes.add(child.value)

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == "_sell" and node.args:
                collect(node.args[0])
        if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == "code"
                for target in node.targets):
            collect(node.value)
    return codes


def test_buy_signals_cover_all_four_in_order():
    assert [item["code"] for item in strategy_doc.BUY_SIGNALS] == list(BUY_SIGNAL_CODES)
    for item in strategy_doc.BUY_SIGNALS:
        assert item["label"] and item["window"] and item["summary"]


def test_sell_rules_cover_all_ten_in_order_and_defaults():
    assert [item["name"] for item in strategy_doc.SELL_RULES] == list(SELL_RULE_NAMES)
    for item in strategy_doc.SELL_RULES:
        assert item["default_enabled"] is (
            item["name"] not in DEFAULT_OFF_SELL_RULES)


def test_reason_mapping_matches_every_sell_rule_reason_code():
    assert set(strategy_doc.SELL_REASON_TO_RULE) == _sell_reason_codes()
    assert set(strategy_doc.SELL_REASON_TO_RULE.values()) == set(SELL_RULE_NAMES)
    assert set(strategy_doc.SELL_REASON_LABELS) == set(strategy_doc.SELL_REASON_TO_RULE)


def test_thresholds_derive_from_constants_single_source():
    assert strategy_doc.THRESHOLDS == {
        "buy_window_days": constants.BUY_WINDOW_DAYS,
        "window_skip": constants.WINDOW_SKIP_DAYS,
        "window_size": constants.WINDOW_SIZE_DAYS,
        "buy_win_return_gt": constants.BUY_WIN_RETURN_GT,
        "buy_win_drawdown_gt": constants.BUY_WIN_DRAWDOWN_GT,
        "sell_good_drawdown_le": constants.SELL_GOOD_DRAWDOWN_LE,
        "sell_fly_return_ge": constants.SELL_FLY_RETURN_GE,
        "max_replay_trading_days": constants.MAX_REPLAY_TRADING_DAYS,
    }
    assert strategy_doc.EVALUATOR_VERSION == constants.EVALUATOR_VERSION


def test_label_maps_are_projected_from_the_same_data():
    assert strategy_doc.BUY_SIGNAL_LABELS == {
        item["code"]: item["label"] for item in strategy_doc.BUY_SIGNALS}
    assert strategy_doc.SELL_RULE_LABELS == {
        item["name"]: item["label"] for item in strategy_doc.SELL_RULES}
