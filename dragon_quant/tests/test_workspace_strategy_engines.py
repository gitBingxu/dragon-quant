"""统一运行时接入 TradingEngine：自定义策略改变入场/退出，系统约束不可绕过。"""

from __future__ import annotations

import pytest

from dragon_quant.review_account.engine import TradingEngine
from dragon_quant.review_account.market import at
from dragon_quant.review_account.models import AccountState, MarketEvent, StrategyConfig
from dragon_quant.review_account.strategy.runtime import (
    StrategyRuntimeError, build_builtin_runtime, build_runtime_for_selection,
)
from dragon_quant.workspace import main as workspace_main

DAY = "2026-11-11"


def _row(phase, time, **overrides):
    stamp = at(DAY, time)
    row = {
        "date": DAY, "phase": phase, "observed_at": stamp, "bar_timestamp": stamp,
        "open": 11.0, "close": 11.0, "bar_close": 11.0, "bar_high": 11.0,
        "bar_low": 11.0, "ma5": 10.5, "prev_close": 10.8, "open_gap_pct": 1.9,
        "limit_up": 11.88, "limit_down": 9.72, "data_quality": "strict_5min",
        "execution_price": 11.0, "prev_row": {}, "hist_rows": [], "intraday_bars": [],
        "volume": 1_000_000, "prev_volume": 900_000,
    }
    row.update(overrides)
    return row


def _candidate():
    return {"code": "600001", "name": "样本", "rank": 1, "composite_score": 80}


def _engine(tmp_path, strategy_id="user:example_momentum"):
    root = tmp_path / "ws"
    workspace_main.init_workspace(str(root))
    selection = workspace_main.resolve_selection(str(root), strategy_id)
    cfg = StrategyConfig()
    runtime = build_runtime_for_selection(selection, cfg)
    state = AccountState(cfg.initial_cash)
    return TradingEngine(cfg, state, runtime=runtime), state, cfg


def test_user_strategy_changes_entry_decision(tmp_path):
    stamp = at(DAY, "09:30")
    event = MarketEvent(timestamp=stamp, phase="open",
                        rows={"600001": _row("open", "09:30")},
                        candidates=[_candidate()])
    # 内建：缺少上日基准 → 不产生信号
    builtin_state = AccountState(StrategyConfig().initial_cash)
    builtin = TradingEngine(StrategyConfig(), builtin_state)
    assert builtin.step(MarketEvent(timestamp=stamp, phase="open",
                                    rows={"600001": _row("open", "09:30")},
                                    candidates=[_candidate()]))["pending"] == []
    # 用户策略：高开 1.9% 触发
    engine, _state, _cfg = _engine(tmp_path)
    result = engine.step(event)
    assert [o["side"] for o in result["pending"]] == ["BUY"]
    assert result["pending"][0]["code"] == "momentum_breakout"
    assert result["pending"][0]["position_pct"] == 50.0


def test_user_strategy_entry_fills_on_next_event_with_position_pct(tmp_path):
    engine, state, cfg = _engine(tmp_path)
    t0, t1 = at(DAY, "09:30"), at(DAY, "09:35")
    engine.step(MarketEvent(timestamp=t0, phase="open",
                            rows={"600001": _row("open", "09:30")},
                            candidates=[_candidate()]))
    result = engine.step(MarketEvent(timestamp=t1, phase="bar",
                                     rows={"600001": _row("bar", "09:35")},
                                     candidates=[]))
    assert len(result["trades"]) == 1
    trade = result["trades"][0]
    assert trade.side == "BUY"
    # notional 满仓约 9000 股，position_pct=50 → 4500 股；仍受现金/手数约束。
    assert trade.qty == 4500
    assert state.positions[0].strategy_ref["strategy_id"] == "user:example_momentum"


def test_t_plus_one_prevents_same_day_exit(tmp_path):
    root = tmp_path / "ws"
    workspace_main.init_workspace(str(root))
    selection = workspace_main.resolve_selection(str(root), "user:example_momentum")
    cfg = StrategyConfig(stop_loss_pct=-1.0)
    runtime = build_runtime_for_selection(selection, cfg)
    state = AccountState(cfg.initial_cash)
    engine = TradingEngine(cfg, state, runtime=runtime)
    t0, t1, t2 = at(DAY, "09:30"), at(DAY, "09:35"), at(DAY, "10:00")
    engine.step(MarketEvent(timestamp=t0, phase="open",
                            rows={"600001": _row("open", "09:30")},
                            candidates=[_candidate()]))
    engine.step(MarketEvent(timestamp=t1, phase="bar",
                            rows={"600001": _row("bar", "09:35")}, candidates=[]))
    assert len(state.positions) == 1
    # 同一天即使策略给卖出信号，T+1 约束也不能成交。
    low_row = _row("bar", "10:00", bar_low=9.0, bar_close=9.0, execution_price=9.0)
    result = engine.step(MarketEvent(timestamp=t2, phase="bar",
                                     rows={"600001": low_row}, candidates=[]))
    assert result["trades"] == []
    assert len(state.positions) == 1


def test_invalid_user_output_aborts_without_trade(tmp_path):
    root = tmp_path / "ws"
    workspace_main.init_workspace(str(root))
    strategy = root / "strategies" / "example_momentum" / "strategy.py"
    strategy.write_text(
        "from dragon_quant.strategy_sdk import StrategyIntent\n"
        "class S:\n"
        "    descriptor = {}\n"
        "    def evaluate_entry(self, ctx):\n"
        "        return StrategyIntent(action='buy', reason_code='x', priority=float('nan'))\n"
        "    def evaluate_exit(self, ctx):\n"
        "        return None\n"
        "def create_strategy(params):\n"
        "    return S()\n", encoding="utf-8")
    selection = workspace_main.resolve_selection(str(root), "user:example_momentum")
    cfg = StrategyConfig()
    state = AccountState(cfg.initial_cash)
    engine = TradingEngine(cfg, state,
                           runtime=build_runtime_for_selection(selection, cfg))
    stamp = at(DAY, "09:30")
    with pytest.raises(StrategyRuntimeError) as exc:
        engine.step(MarketEvent(timestamp=stamp, phase="open",
                                rows={"600001": _row("open", "09:30")},
                                candidates=[_candidate()]))
    assert "user:example_momentum" in str(exc.value)
    assert state.pending == []  # 异常前没有落处于 pending 的订单


def test_edited_strategy_source_is_reloaded_not_cached(tmp_path):
    """同一进程内改策略源码后必须执行新代码（冻结哈希与执行版本一致）。"""
    root = tmp_path / "ws"
    workspace_main.init_workspace(str(root))
    strategy = root / "strategies" / "example_momentum" / "strategy.py"

    def write(returns_signal: bool) -> None:
        body = (
            "from dragon_quant.strategy_sdk import StrategyIntent\n"
            "class S:\n"
            "    descriptor = {}\n"
            "    def evaluate_entry(self, ctx):\n"
            + ("        return StrategyIntent(action='buy', reason_code='edited')\n"
               if returns_signal else "        return None\n")
            + "    def evaluate_exit(self, ctx):\n"
            "        return None\n"
            "def create_strategy(params):\n"
            "    return S()\n"
        )
        strategy.write_text(body, encoding="utf-8")

    write(False)
    engine, _state, _cfg = _engine(tmp_path, "user:example_momentum")
    stamp = at(DAY, "09:30")
    assert engine.step(MarketEvent(timestamp=stamp, phase="open",
                                   rows={"600001": _row("open", "09:30")},
                                   candidates=[_candidate()]))["pending"] == []

    write(True)
    engine2, _state2, _cfg2 = _engine(tmp_path, "user:example_momentum")
    assert [o["code"] for o in engine2.step(
        MarketEvent(timestamp=stamp, phase="open",
                    rows={"600001": _row("open", "09:30")},
                    candidates=[_candidate()]))["pending"]] == ["edited"]


def test_builtin_runtime_is_equivalent_to_default_engine():
    stamp = at(DAY, "09:30")

    def event():
        return MarketEvent(timestamp=stamp, phase="open",
                           rows={"600001": _row("open", "09:30")},
                           candidates=[_candidate()])

    cfg = StrategyConfig()
    default = TradingEngine(cfg, AccountState(cfg.initial_cash))
    with_runtime = TradingEngine(cfg, AccountState(cfg.initial_cash),
                                 runtime=build_builtin_runtime(cfg))
    a = default.step(event())
    b = with_runtime.step(event())
    assert a["pending"] == b["pending"]
    assert a["reason_code"] == b["reason_code"]


def test_builtin_entry_signal_carries_frozen_strategy_ref(tmp_path):
    """M5：内建开仓也必须把冻结身份+有效参数写入 entry_signal。"""
    from dragon_quant.review_account.service import _build_strategy_runtime

    cfg = StrategyConfig()
    runtime, resolver, ref = _build_strategy_runtime(None, cfg)
    state = AccountState(cfg.initial_cash)
    engine = TradingEngine(cfg, state, runtime=runtime, runtime_resolver=resolver)
    stamp = at(DAY, "09:35")
    row = _row("bar", "09:35")
    order = TradingEngine._order("600001", "样本", {
        "action": "BUY", "code": "buy_open_ma5_pullback", "reason_text": "x",
        "signal": {"date": DAY}}, stamp)
    trade, _closed = engine._fill(order, row, 10.0, DAY)
    assert trade is not None
    ref_payload = trade.signal["strategy_ref"]
    assert ref_payload["strategy_id"].startswith("builtin:")
    assert ref_payload.get("params")
    assert state.positions[0].strategy_ref == ref_payload
    closer = getattr(resolver, "close", None)
    if callable(closer):
        closer()
