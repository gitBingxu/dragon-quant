"""Task 14：``review_account.strategy`` 包化 + 纯拆分等价门禁。

两阶段：

- **14a**：整文件搬 ``strategy.py`` → ``strategy/main.py``，建包 ``__init__``
  保留公共/私有测试面，并用正式 pytest 用例记录函数级源码基线；
- **14b**：按批准映射纯拆 ``common/buy/checks/sell``，``main.py`` 只做门面
  重导出，``execution/priority/risk`` 移入包、旧路径保留迁移期 alias。

函数等价判定：对每个导出的公开/私有函数取 ``inspect.getsource`` 后做
``ast.dump(include_attributes=False)`` 规范化，再取 sha256。AST 规范化忽略
空行/缩进/注释，只比对语法树；基线由 ``DQ_STRATEGY_BASELINE_WRITE=1`` 的
pytest 用例在 14a 生成，14b 只允许比对，不允许重写。
"""

from __future__ import annotations

import ast
import hashlib
import importlib
import inspect
import json
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
REVIEW_ACCOUNT = ROOT / "dragon_quant" / "review_account"
STRATEGY_DIR = REVIEW_ACCOUNT / "strategy"
BASELINE = ROOT / "dragon_quant" / "tests" / "fixtures" / "strategy_functions_baseline.json"

# SPEC §4 的 strategy 行（通配展开）。公共面无遗漏。
PUBLIC_NAMES = (
    "evaluate_buy",
    "evaluate_divergence_buy",
    "evaluate_sell",
    "entry_veto",
    "explain_buy_candidate",
    "explain_buy_checks",
    "evaluate_buy_checks",
    "buy_check_placeholders",
    "RuleResult",
    "BuyRejected",
    "BuyCheck",
    "BuyCheckContext",
    "SellContext",
    "SellRule",
    "BUY_CHECK_PASSED",
    "BUY_CHECK_FAILED",
    "BUY_CHECK_PENDING",
    "BUY_CHECK_WINDOW_CLOSED",
    "BUY_CHECK_UNAVAILABLE",
    "BUY_CHECK_SKIPPED",
    "BUY_CHECK_STATUSES",
    "BUY_CHECK_LABELS",
    "BUY_CHECK_STATUS_LABELS",
    "BUY_CHECK_DISPLAY_ORDER",
    "BUY_CHECK_SELECTION_ORDER",
    "BUY_SIGNAL_MA5_PULLBACK",
    "BUY_SIGNAL_OPEN_TURN_STRONG",
    "BUY_SIGNAL_DIVERGENCE_BREAK",
    "BUY_SIGNAL_DRAGON_COMEBACK",
    "_SELL_RULES",
    "_DEFAULT_OFF_RULES",
    "_tiered_drawdown",
)

# strategy.py 的全部顶层函数（46 个），拆分前后逐一比对语法树。
FUNCTION_NAMES = (
    "_apply_entry_priority_gate",
    "_buy",
    "_compute_buy_signal",
    "_count_shrinking_one_word_boards",
    "_ctx",
    "_detail_frame",
    "_divergence_check",
    "_divergence_preconditions",
    "_divergence_window",
    "_dragon_check",
    "_enrich_detail",
    "_entry_quality_gate",
    "_find_wave_peak",
    "_make_check",
    "_no_pattern_reason",
    "_open_common_gate",
    "_payload",
    "_rule_breakeven",
    "_rule_divergence",
    "_rule_dragon_comeback",
    "_rule_first_day_stop",
    "_rule_half",
    "_rule_hard_stop",
    "_rule_hard_time_stop",
    "_rule_high_open_clear",
    "_rule_ma5_pullback",
    "_rule_soft_time_stop",
    "_rule_trailing",
    "_rule_turn_strong",
    "_rule_volume_spike",
    "_rule_weak_close",
    "_safe_rule",
    "_sell",
    "_tiered_drawdown",
    "_turn_strong_check",
    "_turn_strong_preconditions",
    "_weak_close_break",
    "_window_label",
    "buy_check_placeholders",
    "entry_veto",
    "evaluate_buy",
    "evaluate_buy_checks",
    "evaluate_divergence_buy",
    "evaluate_sell",
    "explain_buy_candidate",
    "explain_buy_checks",
)

# 14b 批准映射：函数/类/常量的唯一归属模块。
# 唯一结构偏离（已登记 deviations）：``BuyCheckContext`` 定义在 ``common``，
# ``checks`` 仍重导出——这样 ``buy ↔ checks`` 的相互依赖保持无环；类本身无
# 行为，函数等价性仍由源码哈希保证。
SPLIT_FUNCTIONS = {
    "common": {
        "_safe_rule", "_payload", "_buy", "_sell", "_ctx",
    },
    "buy": {
        "entry_veto", "_entry_quality_gate", "evaluate_buy", "_compute_buy_signal",
        "_apply_entry_priority_gate", "_open_common_gate", "_rule_ma5_pullback",
        "_turn_strong_preconditions", "_rule_turn_strong",
        "_count_shrinking_one_word_boards", "_divergence_preconditions",
        "_rule_divergence", "evaluate_divergence_buy", "_find_wave_peak",
        "_rule_dragon_comeback",
    },
    "checks": {
        "_divergence_window", "_window_label", "_make_check", "buy_check_placeholders",
        "_turn_strong_check", "_divergence_check", "_dragon_check",
        "evaluate_buy_checks", "explain_buy_checks", "_no_pattern_reason",
        "_enrich_detail", "_detail_frame", "explain_buy_candidate",
    },
    "sell": {
        "_rule_first_day_stop", "_rule_hard_stop", "_tiered_drawdown",
        "_rule_trailing", "_rule_breakeven", "_rule_high_open_clear",
        "_rule_soft_time_stop", "_rule_weak_close", "_rule_hard_time_stop",
        "_rule_half", "_rule_volume_spike", "evaluate_sell", "_weak_close_break",
    },
}

SPLIT_CLASSES = {
    "common": {"RuleResult", "BuyCheckContext", "BuyRejected"},
    "checks": {"BuyCheck"},
    "sell": {"SellContext", "SellRule"},
}

SPLIT_CONSTANTS = {
    "checks": {
        "BUY_CHECK_PASSED", "BUY_CHECK_FAILED", "BUY_CHECK_PENDING",
        "BUY_CHECK_WINDOW_CLOSED", "BUY_CHECK_UNAVAILABLE", "BUY_CHECK_SKIPPED",
        "BUY_CHECK_STATUSES", "BUY_CHECK_LABELS", "BUY_CHECK_STATUS_LABELS",
        "BUY_CHECK_DISPLAY_ORDER", "BUY_CHECK_SELECTION_ORDER",
    },
    "sell": {"_SELL_RULES", "_DEFAULT_OFF_RULES"},
}


def _split_applied() -> bool:
    """14b 拆分是否已落地；``DQ_STRATEGY_SPLIT_EXPECT=1`` 用于拆分前记录 RED。

    正常门禁只看文件是否出现；opt-in 开关让 14b 的失败用例在拆分前也能真正
    执行（而不是 skip），从而留下 RED→GREEN 两段证据。
    """
    if os.environ.get("DQ_STRATEGY_SPLIT_EXPECT") == "1":
        return True
    return (STRATEGY_DIR / "buy.py").exists()


def _normalized_source(obj) -> str:
    source = inspect.getsource(obj)
    tree = ast.parse(source)
    return ast.dump(tree, annotate_fields=True, include_attributes=False)


def strategy_function_hashes() -> dict[str, str]:
    """对 ``strategy.main`` 导出的每个顶层函数取规范化源码 sha256。"""
    main = importlib.import_module("dragon_quant.review_account.strategy.main")
    hashes: dict[str, str] = {}
    for name in FUNCTION_NAMES:
        fn = getattr(main, name)
        assert inspect.isfunction(fn), f"{name} 不是函数"
        digest = hashlib.sha256(_normalized_source(fn).encode("utf-8")).hexdigest()
        hashes[name] = digest
    return dict(sorted(hashes.items()))


# ─── 14a：包化 + 基线 ─────────────────────────────────────────────────────


def test_strategy_is_a_package():
    module = importlib.import_module("dragon_quant.review_account.strategy")
    assert Path(module.__file__).name == "__init__.py"
    assert not (REVIEW_ACCOUNT / "strategy.py").exists()
    assert (STRATEGY_DIR / "main.py").exists()


def test_public_surface_is_complete():
    import dragon_quant.review_account.strategy as strategy

    for name in PUBLIC_NAMES:
        assert hasattr(strategy, name), name


def test_baseline_write_is_opt_in_and_records_every_function():
    if os.environ.get("DQ_STRATEGY_BASELINE_WRITE") != "1":
        pytest.skip("strategy baseline write is opt-in")
    BASELINE.parent.mkdir(parents=True, exist_ok=True)
    BASELINE.write_text(
        json.dumps(strategy_function_hashes(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def test_split_functions_match_pre_split_source():
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    assert set(baseline) == set(FUNCTION_NAMES)
    assert strategy_function_hashes() == baseline


# ─── 14b：纯拆分映射 ─────────────────────────────────────────────────────


@pytest.mark.skipif(not _split_applied(), reason="14b 纯拆分尚未应用")
def test_functions_live_in_the_approved_split_modules():
    for module_name, names in SPLIT_FUNCTIONS.items():
        module = importlib.import_module(
            f"dragon_quant.review_account.strategy.{module_name}")
        for name in sorted(names):
            obj = getattr(module, name)
            assert obj.__module__ == module.__name__, (
                f"{name} 应在 {module.__name__}，实际 {obj.__module__}")


@pytest.mark.skipif(not _split_applied(), reason="14b 纯拆分尚未应用")
def test_classes_and_constants_live_in_the_approved_split_modules():
    for module_name, names in SPLIT_CLASSES.items():
        module = importlib.import_module(
            f"dragon_quant.review_account.strategy.{module_name}")
        for name in sorted(names):
            assert getattr(module, name).__module__ == module.__name__, name
    for module_name, names in SPLIT_CONSTANTS.items():
        module = importlib.import_module(
            f"dragon_quant.review_account.strategy.{module_name}")
        for name in sorted(names):
            assert name in vars(module), f"{name} 应在 {module.__name__}"


@pytest.mark.skipif(not _split_applied(), reason="14b 纯拆分尚未应用")
def test_buy_check_context_is_reexported_from_checks():
    """结构偏离守卫：类定义在 common，但 ``strategy.checks`` 仍暴露同一对象。"""
    from dragon_quant.review_account.strategy import checks, common

    assert checks.BuyCheckContext is common.BuyCheckContext


@pytest.mark.skipif(not _split_applied(), reason="14b 纯拆分尚未应用")
def test_main_has_no_rule_implementations():
    source = (STRATEGY_DIR / "main.py").read_text(encoding="utf-8")
    assert "def evaluate_sell(" not in source
    assert "def evaluate_buy(" not in source
    tree = ast.parse(source)
    definitions = [
        node.name for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    ]
    assert definitions == [], definitions


@pytest.mark.skipif(not _split_applied(), reason="14b 纯拆分尚未应用")
def test_execution_priority_risk_live_in_the_strategy_package():
    import dragon_quant.review_account.strategy.execution as execution
    import dragon_quant.review_account.strategy.priority as priority
    import dragon_quant.review_account.strategy.risk as risk

    assert Path(execution.__file__).parent.name == "strategy"
    assert Path(priority.__file__).parent.name == "strategy"
    assert Path(risk.__file__).parent.name == "strategy"
    # Task 16: 旧的顶层 review_account.{execution,priority,risk} 已删除。
    for stale in ("execution", "priority", "risk"):
        assert not (STRATEGY_DIR.parent / f"{stale}.py").exists(), stale


@pytest.mark.skipif(not _split_applied(), reason="14b 纯拆分尚未应用")
def test_entry_veto_patch_target_is_canonical_buy_module():
    """拆分后唯一需要迁移的 patch 点：``strategy.buy.entry_veto``。"""
    import dragon_quant.review_account.strategy.buy as buy

    assert callable(buy.entry_veto)
    assert buy.entry_veto.__module__ == buy.__name__
