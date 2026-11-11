"""持仓归属与退出路由：冻结版本、半仓比例、不重复开仓。"""

from __future__ import annotations

from dragon_quant.review_account.engine import TradingEngine
from dragon_quant.review_account.market import at
from dragon_quant.review_account.models import AccountState, Position, StrategyConfig
from dragon_quant.review_account.strategy.execution import apply_sell_fill
from dragon_quant.review_account.strategy.runtime import (
    build_builtin_runtime, build_runtime_for_selection,
)
from dragon_quant.workspace import main as workspace_main

DAY = "2026-11-11"


def _selection(tmp_path, strategy_id):
    root = tmp_path / f"ws-{strategy_id.split(':')[1]}"
    workspace_main.init_workspace(str(root))
    return workspace_main.resolve_selection(str(root), strategy_id)


def test_runtime_routing_by_frozen_ownership(tmp_path):
    selection = _selection(tmp_path, "user:example_momentum")
    cfg = StrategyConfig()
    active = build_runtime_for_selection(selection, cfg)
    other = build_builtin_runtime(cfg)
    calls = []

    def resolver(strategy_id, ref):
        calls.append(strategy_id)
        return other

    engine = TradingEngine(cfg, AccountState(cfg.initial_cash), runtime=active,
                           runtime_resolver=resolver)
    own = Position("600001", "样本", 100, DAY, 10.0, 1000.0, "x", "x",
                   strategy_ref={"strategy_id": "user:example_momentum"})
    foreign = Position("600002", "样本2", 100, DAY, 10.0, 1000.0, "x", "x",
                       strategy_ref={"strategy_id": "user:example_meanrev"})
    legacy = Position("600003", "样本3", 100, DAY, 10.0, 1000.0, "x", "x")
    assert engine._runtime_for(own) is active
    assert engine._runtime_for(foreign) is other
    assert calls == ["user:example_meanrev"]
    # 旧持仓无 strategy_ref → legacy 内建，不冒用活动策略。
    assert engine._runtime_for(legacy).source == "builtin"


def test_user_exit_fraction_half_sells_partial(tmp_path):
    root = tmp_path / "ws-half"
    workspace_main.init_workspace(str(root))
    strategy = root / "strategies" / "example_momentum" / "strategy.py"
    strategy.write_text(
        "from dragon_quant.strategy_sdk import StrategyIntent\n"
        "class S:\n"
        "    descriptor = {}\n"
        "    def evaluate_entry(self, ctx):\n"
        "        return None\n"
        "    def evaluate_exit(self, ctx):\n"
        "        return StrategyIntent(action='sell', reason_code='half_out',\n"
        "                              exit_fraction=0.5)\n"
        "def create_strategy(params):\n"
        "    return S()\n", encoding="utf-8")
    selection = workspace_main.resolve_selection(str(root), "user:example_momentum")
    cfg = StrategyConfig()
    runtime = build_runtime_for_selection(selection, cfg)
    stamp = at(DAY, "10:00")
    position = Position("600001", "样本", 1000, "2026-11-10", 10.0, 10_000.0, "x", "x",
                        strategy_ref={"strategy_id": "user:example_momentum"})
    signal = runtime.evaluate_exit(
        position=position, row={"date": DAY, "phase": "bar", "observed_at": stamp,
                                "bar_close": 11.0, "bar_low": 11.0},
        hold_days=1, cfg=cfg, intraday_bars=[], trade_date=DAY,
        timestamp=stamp, phase="bar", account={"cash": 0})
    assert signal["signal"]["exit_fraction"] == 0.5
    fill = apply_sell_fill(position, 11.0, signal["code"], cfg,
                           signal["signal"]["exit_fraction"])
    assert fill["partial"] is True
    assert fill["qty"] == 500
    assert position.took_profit_half is True


def test_exit_routing_uses_frozen_snapshot_not_current_source(tmp_path):
    """旧仓退出必须执行开仓时冻结的源码，而不是后来修改的当前源码。"""
    from dragon_quant.review_account.strategy.runtime import make_runtime_resolver

    root = tmp_path / "ws-snapshot"
    workspace_main.init_workspace(str(root))
    momentum = workspace_main.resolve_selection(str(root), "user:example_momentum")
    ref = workspace_main.freeze_strategy(momentum)
    # 修改当前源码为「总是卖出」：冻结快照仍应是原动量逻辑（此处不触发退出）。
    strategy = root / "strategies" / "example_momentum" / "strategy.py"
    strategy.write_text(
        "from dragon_quant.strategy_sdk import StrategyIntent\n"
        "class S:\n"
        "    descriptor = {}\n"
        "    def evaluate_entry(self, ctx):\n"
        "        return None\n"
        "    def evaluate_exit(self, ctx):\n"
        "        return StrategyIntent(action='sell', reason_code='always', exit_fraction=1.0)\n"
        "def create_strategy(params):\n"
        "    return S()\n", encoding="utf-8")

    base = workspace_main.resolve_selection(str(root), "user:example_meanrev")
    resolver = make_runtime_resolver(base, StrategyConfig())
    runtime = resolver("user:example_momentum", ref)
    assert runtime is not None
    stamp = at(DAY, "10:00")
    position = Position("600001", "样本", 1000, "2026-11-10", 10.0, 10_000.0, "x", "x",
                        strategy_ref=ref)
    row = {"date": DAY, "phase": "bar", "observed_at": stamp,
           "bar_close": 10.5, "bar_low": 10.1}
    assert runtime.evaluate_exit(position=position, row=row, hold_days=1,
                                 cfg=StrategyConfig(), intraday_bars=[], trade_date=DAY,
                                 timestamp=stamp, phase="bar", account={"cash": 0}) is None


def test_same_code_is_not_opened_twice(tmp_path):
    from dragon_quant.review_account.models import MarketEvent

    selection = _selection(tmp_path, "user:example_momentum")
    cfg = StrategyConfig()
    state = AccountState(cfg.initial_cash)
    state.positions.append(Position("600001", "样本", 100, "2026-11-10", 10.0,
                                    1000.0, "x", "x"))
    engine = TradingEngine(cfg, state,
                           runtime=build_runtime_for_selection(selection, cfg))
    stamp = at(DAY, "09:30")
    row = {"date": DAY, "phase": "open", "observed_at": stamp, "bar_timestamp": stamp,
           "open": 11.0, "close": 11.0, "bar_close": 11.0, "bar_high": 11.0,
           "bar_low": 11.0, "ma5": 10.5, "prev_close": 10.8, "open_gap_pct": 1.9,
           "limit_up": 11.88, "limit_down": 9.72, "data_quality": "strict_5min",
           "execution_price": 11.0, "prev_row": {}, "hist_rows": [], "intraday_bars": []}
    engine.step(MarketEvent(timestamp=stamp, phase="open", rows={"600001": row},
                            candidates=[{"code": "600001", "name": "样本", "rank": 1}]))
    assert len(state.positions) == 1  # 已持仓 → 不开第二份
