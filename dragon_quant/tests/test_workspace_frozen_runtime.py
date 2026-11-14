"""冻结运行时与 PIT：同 ID 不同版本、跨 workspace、坏快照、Context 裁剪、worker。"""

from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from dragon_quant.infra.models import KBar
from dragon_quant.review_account.market import at, build_daily_row
from dragon_quant.review_account.models import StrategyConfig
from dragon_quant.review_account.strategy.runtime import (
    FrozenReferenceError, WorkerUserStrategyRuntime, build_user_runtime,
    make_provider, make_worker_factory,
)
from dragon_quant.strategy_sdk import (
    FACTOR_STATUS_OK, FactorValue, freeze,
)
from dragon_quant.workspace import config, main
from dragon_quant.workspace import factors as factor_lib

MARKER_CODE = (
    "class S:\n"
    "    marker = {marker!r}\n"
    "    def evaluate_entry(self, ctx): return None\n"
    "    def evaluate_exit(self, ctx): return None\n"
    "def create_strategy(params): return S()\n"
)


def _workspace(tmp_path, name="ws"):
    root = tmp_path / name
    main.init_workspace(str(root))
    return root


def _set_marker(root, marker):
    (root / "strategies" / "example_momentum" / "strategy.py").write_text(
        MARKER_CODE.format(marker=marker), encoding="utf-8")


def _provider(cfg, **kwargs):
    return make_provider(cfg, factor_reader_factory=None, **kwargs)


def test_same_id_different_params_uses_frozen_snapshot(tmp_path):
    root = _workspace(tmp_path)
    _set_marker(root, "OLD")
    ref = main.freeze_strategy(main.resolve_selection(str(root), "user:example_momentum"))
    _set_marker(root, "NEW")
    # 活动选择是新版本，但旧持仓 ref 必须执行旧快照。
    provider = _provider(StrategyConfig())
    runtime = provider.build(ref)
    assert runtime.instance.marker == "OLD"
    provider.close()


def test_cross_workspace_same_slug_uses_ref_workspace(tmp_path):
    a = _workspace(tmp_path, "a")
    b = _workspace(tmp_path, "b")
    _set_marker(a, "A_VERSION")
    _set_marker(b, "B_VERSION")
    ref_a = main.freeze_strategy(main.resolve_selection(str(a), "user:example_momentum"))
    # 当前工作区是 b；ref 指向 a 的快照，必须加载 A_VERSION。
    provider = _provider(StrategyConfig())
    runtime = provider.build(ref_a)
    assert runtime.instance.marker == "A_VERSION"
    provider.close()


def test_missing_snapshot_is_explicit_failure(tmp_path):
    root = _workspace(tmp_path)
    ref = main.freeze_strategy(main.resolve_selection(str(root), "user:example_momentum"))
    ref = dict(ref, snapshot_id="missing_snapshot")
    with pytest.raises(FrozenReferenceError) as exc:
        _provider(StrategyConfig()).build(ref)
    assert "缺失" in str(exc.value) or "损坏" in str(exc.value)


def test_tampered_snapshot_is_explicit_failure(tmp_path):
    root = _workspace(tmp_path)
    ref = main.freeze_strategy(main.resolve_selection(str(root), "user:example_momentum"))
    target = root / "snapshots" / ref["snapshot_id"] / "strategies" / "example_momentum" / "strategy.py"
    target.write_text("# changed\n", encoding="utf-8")
    with pytest.raises(FrozenReferenceError) as exc:
        _provider(StrategyConfig()).build(ref)
    assert "哈希" in str(exc.value)


def test_deleted_current_strategy_file_still_exits_from_snapshot(tmp_path):
    root = _workspace(tmp_path)
    _set_marker(root, "FROZEN_SNAPSHOT")
    ref = main.freeze_strategy(main.resolve_selection(str(root), "user:example_momentum"))
    (root / "strategies" / "example_momentum" / "strategy.py").unlink()
    provider = _provider(StrategyConfig())
    try:
        runtime = provider.build(ref)
        assert runtime.instance.marker == "FROZEN_SNAPSHOT"
    finally:
        provider.close()


def test_context_masks_future_daily_ohlcv_and_slices_intraday(tmp_path):
    root = _workspace(tmp_path)
    selection = main.resolve_selection(str(root), "user:example_momentum")
    runtime = build_user_runtime(selection, StrategyConfig())
    history = []
    for offset in range(25):
        day = (date(2026, 10, 17) + timedelta(days=offset)).isoformat()
        history.append(KBar(at(day, "15:00"), 1000, 10, 11, 9, 10, 0, 0, 1, 10000))
    history.append(KBar(at("2026-11-11", "15:00"), 99999, 10, 15, 5, 14, 4, 40, 10, 999999))
    row = build_daily_row(history, "2026-11-11", "open", at("2026-11-11", "09:30"))
    ctx = runtime._context(candidate={}, row=row, position=None, account={},
                           trade_date="2026-11-11",
                           timestamp=at("2026-11-11", "09:30"), phase="open", factors={})
    for field in ("close", "high", "low", "volume"):
        assert field not in ctx.row, field
    assert ctx.row["bar_close"] == row["bar_close"]


def test_kbar_objects_are_frozen_not_shared(tmp_path):
    bar = KBar(at("2026-11-11", "09:35"), 1, 10, 11, 9, 10, 0, 0, 1, 100)
    frozen = freeze({"bars": [bar], "nested": {"row": {"intraday_bars": [bar]}}})
    with pytest.raises(TypeError):
        frozen["bars"][0]["close"] = 99
    assert bar.close == 10.0
    assert isinstance(frozen["nested"]["row"]["intraday_bars"], tuple)
    assert frozen["nested"]["row"]["intraday_bars"][0]["close"] == 10


def test_factor_lookup_uses_candidate_code_and_trade_date(tmp_path):
    root = _workspace(tmp_path)
    (root / "factors" / "custom.py").write_text(
        "from dragon_quant.infra.factor.types import FactorFrame, FactorSpec\n"
        "def compute(ctx): return FactorFrame()\n"
        "SPECS=[FactorSpec(name='test_factor', label='t', frequency='1d',"
        " source='computed', compute=compute, impl_key='t')]\n", encoding="utf-8")
    cfg = config.load_workspace(root)
    cfg.factors = ["factors/custom.py"]
    config.write_workspace(cfg)
    cfg = config.load_workspace(root)
    descriptor = root / "strategies" / "example_momentum" / "strategy.json"
    meta = json.loads(descriptor.read_text(encoding="utf-8"))
    meta["required_factors"] = ["test_factor"]
    descriptor.write_text(json.dumps(meta), encoding="utf-8")

    selection = main.resolve_selection(str(root), "user:example_momentum")
    specs = factor_lib.combined_specs(cfg)
    mapping = factor_lib.resolve_required_factors(cfg, ["test_factor"], specs=specs)
    mapped = list(mapping.values())[0]
    seen = {}

    class Reader:
        def values_for(self, **kwargs):
            seen.update(kwargs)
            return {mapped: FactorValue(name=mapped, value=1.0, status=FACTOR_STATUS_OK)}

    runtime = build_user_runtime(selection, StrategyConfig(), factor_reader=Reader(),
                                 required_factors=[mapped],
                                 factor_versions={mapped: "vX"})
    runtime.evaluate_entry(
        candidate={"code": "600001", "trade_date": "2026-11-10"},
        row={"date": "2026-11-11", "phase": "open"}, cfg=StrategyConfig(),
        prev_row={}, hist_rows=[], intraday_bars=[], sentiment_pct=None,
        trade_date="2026-11-11", timestamp=1794351000000, phase="open", account={})
    assert seen["code"] == "600001"
    assert seen["trade_date"] == "2026-11-10"
    assert seen["versions"] == {mapped: "vX"}


def test_worker_executes_user_strategy_out_of_process(tmp_path):
    root = _workspace(tmp_path)
    ref = main.freeze_strategy(main.resolve_selection(str(root), "user:example_momentum"))
    provider = _provider(StrategyConfig(), worker_factory=make_worker_factory(timeout=15))
    try:
        runtime = provider.build(ref)
        assert isinstance(runtime, WorkerUserStrategyRuntime)
        stamp = at("2026-11-11", "09:30")
        row = {"date": "2026-11-11", "phase": "open", "observed_at": stamp,
               "bar_timestamp": stamp, "open": 11.0, "bar_close": 11.0,
               "bar_high": 11.0, "bar_low": 11.0, "ma5": 10.5, "prev_close": 10.8,
               "open_gap_pct": 1.9, "limit_up": 11.88, "limit_down": 9.72,
               "execution_price": 11.0, "prev_row": {}, "hist_rows": [],
               "intraday_bars": []}
        signal = runtime.evaluate_entry(
            candidate={"code": "600001", "trade_date": "2026-11-10"}, row=row,
            cfg=StrategyConfig(), prev_row={}, hist_rows=[], intraday_bars=[],
            sentiment_pct=None, trade_date="2026-11-11", timestamp=stamp,
            phase="open", account={})
        assert signal["code"] == "momentum_breakout"
    finally:
        provider.close()


def test_experiments_variant_runner_injects_runtime(monkeypatch):
    from dragon_quant.review_account import experiments as exp

    captured = {}

    def boom(cfg, **kwargs):
        captured.update(kwargs)
        raise ValueError("stop")

    monkeypatch.setattr(exp, "AccountSimulator", boom)
    result, error = exp._run_variant(
        StrategyConfig(), object(), "2026-01-01", "2026-01-02", {}, None,
        runtime="RT", runtime_resolver="RS")
    assert result is None
    assert error and "invalid_variant" in error
    assert captured["runtime"] == "RT"
    assert captured["runtime_resolver"] == "RS"


def test_factor_versions_come_from_frozen_snapshot(tmp_path):
    """D4：因子版本必须取自已冻结源码，当前因子文件改动不得影响冻结引用。"""
    root = _workspace(tmp_path)
    factor_rel = "factors/custom.py"

    def write_factor(label):
        (root / factor_rel).write_text(
            "SPECS=[FactorSpec(name='x', label=%r, frequency='1d', "
            "source='computed', compute=None, impl_key='x')]\n" % label,
            encoding="utf-8")

    write_factor("first")
    cfg = config.load_workspace(root)
    cfg.factors = [factor_rel]
    config.write_workspace(cfg)
    descriptor = root / "strategies" / "example_momentum" / "strategy.json"
    meta = json.loads(descriptor.read_text(encoding="utf-8"))
    meta["required_factors"] = ["x"]
    descriptor.write_text(json.dumps(meta), encoding="utf-8")
    before = {entry["name"]: entry["version"]
              for entry in factor_lib.metadata_only_specs(config.load_workspace(root))}

    ref = main.freeze_strategy(main.resolve_selection(
        str(root), "user:example_momentum"))
    assert ref["factor_versions"] == {
        name: before[name] for name in ref["required_factors"]}

    # 冻结后修改当前因子源码：冻结版本保持不变，重新冻结才得到新版本。
    write_factor("second")
    after = {entry["name"]: entry["version"]
             for entry in factor_lib.metadata_only_specs(config.load_workspace(root))}
    assert after != before
    assert ref["factor_versions"] == {
        name: before[name] for name in ref["required_factors"]}
    reselected = main.resolve_selection(str(root), "user:example_momentum")
    refrozen = main.freeze_strategy(reselected)
    assert refrozen["snapshot_id"] != ref["snapshot_id"]
    assert refrozen["factor_versions"] == {
        name: after[name] for name in refrozen["required_factors"]}


def _weekdays(start: date, count: int) -> list[str]:
    days, day = [], start
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day.isoformat())
        day += timedelta(days=1)
    return days


THRESHOLD_STRATEGY = '''
from dragon_quant.strategy_sdk import StrategyIntent


class Threshold:
    def __init__(self, params):
        params = dict(params or {})
        self.threshold = float(params.get("min_open_gap_pct", 1.0))

    def evaluate_entry(self, ctx):
        row = ctx.row
        if not row or ctx.phase != "open":
            return None
        gap = row.get("open_gap_pct")
        if gap is None or gap < self.threshold:
            return None
        return StrategyIntent(action="buy", reason_code="threshold_buy",
                              priority=100, position_pct=50.0,
                              evidence={"open_gap_pct": gap})

    def evaluate_exit(self, ctx):
        if ctx.phase != "late" or not ctx.position:
            return None
        return StrategyIntent(action="sell", reason_code="threshold_exit",
                              priority=100, exit_fraction=1.0)


def create_strategy(params):
    return Threshold(params)
'''


def _experiment_fixture():
    """合成冻结上游：12 个交易日、单一候选、每日高开 5% 的日K。"""
    all_days = _weekdays(date(2026, 9, 1), 45)
    window = all_days[-12:]
    rows, price = [], 10.0
    for day in all_days:
        open_px = round(price * 1.05, 2)
        close = round(open_px * 1.01, 2)
        rows.append({"date": day, "open": open_px,
                     "high": round(max(open_px, close) * 1.01, 2),
                     "low": round(min(open_px, close) * 0.99, 2),
                     "close": close, "volume": 1_000_000,
                     "amount": 10_000_000, "pct": 5.0, "turnover": 1.0})
        price = close
    candidate = {"code": "600001", "name": "样本", "rank": 1,
                 "composite_score": 80, "is_true_dragon": True,
                 "trade_date": window[0]}
    frozen = {
        "calendar": window,
        "codes": ["600001"],
        "bars": {"600001": rows},
        "dragons": {day: [dict(candidate)] for day in window},
        "auctions": {}, "snapshots": {}, "snapshot_meta": {}, "sentiments": {},
    }
    return frozen, window


def _run_threshold_experiment(ref, provider, *, variants):
    from dragon_quant.review_account import experiments as exp
    from dragon_quant.research.local_account_market import LocalAccountMarket
    from dragon_quant.review_account.strategy.runtime import (
        make_frozen_runtime_factories,
    )

    frozen, window = _experiment_fixture()
    baseline_factory, variant_factory = make_frozen_runtime_factories(provider, ref)
    report = exp.run_experiments(
        run={"id": 1, "date_from": window[0], "date_to": window[-1]},
        cfg=StrategyConfig(), data=LocalAccountMarket(frozen["bars"], window),
        frozen=frozen, runtime=provider.build(ref), runtime_resolver=None,
        strategy_ref=ref, parameter_variants=variants,
        runtime_factory=baseline_factory,
        variant_runtime_factory=variant_factory)
    return report, frozen, window


def test_real_parameter_variant_changes_trades_and_identity(tmp_path):
    """D2：真实 provider + 真实 runtime_factory + 真实撮合，阈值变体必须改变成交。"""
    from dragon_quant.review_account import experiments as exp
    from dragon_quant.research.local_account_market import LocalAccountMarket
    from dragon_quant.review_account.simulator import AccountSimulator

    root = _workspace(tmp_path)
    strategy_py = root / "strategies" / "example_momentum" / "strategy.py"
    strategy_py.write_text(THRESHOLD_STRATEGY, encoding="utf-8")
    descriptor = root / "strategies" / "example_momentum" / "strategy.json"
    meta = json.loads(descriptor.read_text(encoding="utf-8"))
    meta["parameters"]["min_open_gap_pct"] = {
        "type": "number", "default": 1.0, "min": 0.0, "max": 100.0}
    descriptor.write_text(json.dumps(meta), encoding="utf-8")

    ref = main.freeze_strategy(main.resolve_selection(
        str(root), "user:example_momentum"))
    # 冻结后改写当前源码：变体/基线都必须仍从快照执行，不受当前源码影响。
    strategy_py.write_text("raise RuntimeError('current source must not run')\n",
                           encoding="utf-8")
    provider = _provider(StrategyConfig())
    try:
        report, frozen, window = _run_threshold_experiment(
            ref, provider, variants=[{"min_open_gap_pct": 99.0}])
        baseline = report["variants"]["baseline"]["validation"]
        variant = report["variants"]["param_variant_0"]["validation"]
        assert baseline["trade_count"] > 0
        assert baseline["closed_count"] > 0
        assert variant["trade_count"] == 0
        assert variant["closed_count"] == 0
        assert report["variants"]["param_variant_0"]["parameters"] == {
            "min_open_gap_pct": 99.0}
        assert report["frozen"]["strategy_ref"]["params"]["min_open_gap_pct"] == 1.0
        assert report["frozen"]["strategy_ref"]["snapshot_id"]

        # 真实撮合的成交原因：基线用 threshold_buy 成交并当日退出。
        splits = report["splits"]["validation"]
        direct_runtime = provider.build(ref, fresh=True)
        try:
            simulator = AccountSimulator(
                StrategyConfig(), data=LocalAccountMarket(frozen["bars"], window),
                candidate_source=exp._frozen_dragon_loader(frozen),
                auction_source=exp._frozen_auction_loader(frozen),
                snapshot_loader=exp._frozen_snapshot_loader(frozen),
                sentiment_loader=exp._frozen_sentiment_loader(frozen),
                factor_loader=exp._frozen_factor_loader(frozen),
                runtime=direct_runtime)
            result = simulator.run(*splits)
        finally:
            closer = getattr(direct_runtime, "close", None)
            if callable(closer):
                closer()
        assert {trade.reason_code for trade in result["trades"]} == {
            "threshold_buy", "threshold_exit"}

        # 变体真的走了新快照身份：snapshot_id / params_hash 均不同。
        from dragon_quant.workspace import snapshots

        variant_ref = snapshots.publish_parameter_variant(
            ref, {**ref["params"], "min_open_gap_pct": 99.0})
        assert variant_ref["snapshot_id"] != ref["snapshot_id"]
        assert variant_ref["params_hash"] != ref["params_hash"]
        assert provider.build(ref).parameters["min_open_gap_pct"] == 1.0
    finally:
        provider.close()


def test_parse_strategy_params_cli_values():
    from dragon_quant.commands.research import _parse_strategy_params

    assert _parse_strategy_params(None) is None
    parsed = _parse_strategy_params(["a=1", "b=text", "c=true"])
    assert parsed == [{"a": 1, "b": "text", "c": True}]


def _open_rows(final_close, high, volume):
    history = [KBar(at((date(2026, 10, 17) + timedelta(days=i)).isoformat(), "15:00"),
                    1000, 10, 11, 9, 10, 0, 0, 1, 10000) for i in range(25)]
    today = KBar(at("2026-11-11", "15:00"), volume, 10, high, 5, final_close,
                 final_close - 10, (final_close / 10 - 1) * 100, 10, volume * 10)
    return build_daily_row(history + [today], "2026-11-11", "open",
                           at("2026-11-11", "09:30"))


def test_open_context_and_meanrev_decision_ignore_future_ohlcv(tmp_path):
    """D1：仅改当日未来 close/high/low/volume，完整公开 Context 与决策必须不变。"""
    root = _workspace(tmp_path)
    selection = main.resolve_selection(str(root), "user:example_meanrev")
    runtime = build_user_runtime(selection, StrategyConfig())
    def context(row):
        return runtime._context(
            candidate={}, row=row, position=None, account={},
            trade_date="2026-11-11", timestamp=at("2026-11-11", "09:30"),
            phase="open", factors={})

    one, two = context(_open_rows(14, 15, 99999)), context(_open_rows(8, 11, 11111))
    # 完整公开 Context（含 prev_row / hist_rows / intraday_bars）必须逐字段相等：
    # 仅当日未来 close/high/low/volume 不同，不得有任何字段随之变化。
    assert dict(one.row) == dict(two.row)
    assert one.parameters == two.parameters
    assert one.candidate == two.candidate
    # 派生指标不得泄漏（Open 白名单只保留可知数据）
    for leaked in ("ma5", "ma10", "ma20", "volume_ma5", "amount_ma5",
                   "close_to_ma5_pct", "return_3d", "return_5d",
                   "max_return", "max_drawdown_5d", "avg_amplitude_5", "atr",
                   "close", "high", "low", "pct", "amount", "turnover", "volume"):
        assert leaked not in one.row, leaked
    assert one.row["open"] == 10 and one.row["bar_close"] == 10
    assert one.row["prev_row"]["ma5"] is not None  # 上一日已完成均线仍可用于决策
    signal_one = runtime.evaluate_entry(
        candidate={"code": "600001", "trade_date": "2026-11-10"},
        row=_open_rows(14, 15, 99999), cfg=StrategyConfig(), prev_row={},
        hist_rows=[], intraday_bars=[], sentiment_pct=None,
        trade_date="2026-11-11", timestamp=at("2026-11-11", "09:30"),
        phase="open", account={})
    signal_two = runtime.evaluate_entry(
        candidate={"code": "600001", "trade_date": "2026-11-10"},
        row=_open_rows(8, 11, 11111), cfg=StrategyConfig(), prev_row={},
        hist_rows=[], intraday_bars=[], sentiment_pct=None,
        trade_date="2026-11-11", timestamp=at("2026-11-11", "09:30"),
        phase="open", account={})
    assert signal_one == signal_two


def test_parameter_variant_gets_new_snapshot_and_distinct_instance(tmp_path):
    """D2：参数变体必须新建引用/快照并按新 params_hash 构造独立实例。"""
    from dragon_quant.workspace import snapshots

    root = _workspace(tmp_path)
    ref = main.freeze_strategy(main.resolve_selection(str(root), "user:example_momentum"))
    merged = {**ref["params"], "min_open_gap_pct": 99}
    variant = snapshots.publish_parameter_variant(ref, merged)
    assert variant["snapshot_id"] != ref["snapshot_id"]
    assert variant["params_hash"] != ref["params_hash"]
    provider = _provider(StrategyConfig())
    try:
        base_runtime = provider.build(ref)
        variant_runtime = provider.build(variant)
        assert base_runtime is not variant_runtime
        assert base_runtime.parameters["min_open_gap_pct"] == 1.0
        assert variant_runtime.parameters["min_open_gap_pct"] == 99
        # 伪造 ref（改 params 不更新 hash）必须拒绝
        forged = dict(ref, params={**ref["params"], "min_open_gap_pct": 99})
        with pytest.raises(FrozenReferenceError):
            provider.build(forged)
    finally:
        provider.close()


def test_worker_lazy_helper_print_and_pid_isolation(tmp_path):
    root = _workspace(tmp_path)
    (root / "helpers" / "late.py").write_text("VALUE = 42\n", encoding="utf-8")
    pid_file = tmp_path / "strategy-pid"
    (root / "strategies" / "example_momentum" / "strategy.py").write_text(
        "import os\n"
        "from pathlib import Path\n"
        f"Path({str(pid_file)!r}).write_text(str(os.getpid()))\n"
        "class S:\n"
        "    def evaluate_entry(self, ctx):\n"
        "        print('noise from strategy stdout')\n"
        "        from helpers.late import VALUE\n"
        "        from dragon_quant.strategy_sdk import StrategyIntent\n"
        "        return StrategyIntent(action='buy', reason_code='lazy',\n"
        "                              evidence={'value': VALUE, 'pid': os.getpid()})\n"
        "    def evaluate_exit(self, ctx): return None\n"
        "def create_strategy(params): return S()\n", encoding="utf-8")
    ref = main.freeze_strategy(main.resolve_selection(str(root), "user:example_momentum"))
    provider = _provider(StrategyConfig(), worker_factory=make_worker_factory(timeout=10))
    try:
        runtime = provider.build(ref)
        stamp = at("2026-11-11", "09:30")
        signal = runtime.evaluate_entry(
            candidate={"code": "600001", "trade_date": "2026-11-10"},
            row={"date": "2026-11-11", "phase": "open"}, cfg=StrategyConfig(),
            prev_row={}, hist_rows=[], intraday_bars=[], sentiment_pct=None,
            trade_date="2026-11-11", timestamp=stamp, phase="open", account={})
        assert signal["code"] == "lazy"
        assert signal["signal"]["evidence"]["value"] == 42
        import os
        assert int(pid_file.read_text()) != os.getpid()
        assert signal["signal"]["evidence"]["pid"] != os.getpid()
    finally:
        provider.close()


def test_worker_helper_isolation_across_workspaces(tmp_path):
    outputs = []
    for name, value in (("a", 1), ("b", 2)):
        root = _workspace(tmp_path, name)
        (root / "helpers" / "value.py").write_text(f"VALUE = {value}\n", encoding="utf-8")
        (root / "strategies" / "example_momentum" / "strategy.py").write_text(
            "class S:\n"
            "    def evaluate_entry(self, ctx):\n"
            "        from helpers.value import VALUE\n"
            "        from dragon_quant.strategy_sdk import StrategyIntent\n"
            "        return StrategyIntent(action='buy', reason_code='lazy',\n"
            "                              evidence={'value': VALUE})\n"
            "    def evaluate_exit(self, ctx): return None\n"
            "def create_strategy(params): return S()\n", encoding="utf-8")
        ref = main.freeze_strategy(main.resolve_selection(str(root), "user:example_momentum"))
        provider = _provider(StrategyConfig(), worker_factory=make_worker_factory(timeout=10))
        try:
            runtime = provider.build(ref)
            signal = runtime.evaluate_entry(
                candidate={"code": "600001"}, row={"date": "2026-11-11", "phase": "open"},
                cfg=StrategyConfig(), prev_row={}, hist_rows=[], intraday_bars=[],
                sentiment_pct=None, trade_date="2026-11-11",
                timestamp=at("2026-11-11", "09:30"), phase="open", account={})
            outputs.append(signal["signal"]["evidence"]["value"])
        finally:
            provider.close()
    assert outputs == [1, 2]


def test_worker_timeout_raises_and_cleans(tmp_path):
    root = _workspace(tmp_path)
    (root / "strategies" / "example_momentum" / "strategy.py").write_text(
        "import time\n"
        "class S:\n"
        "    def evaluate_entry(self, ctx):\n"
        "        time.sleep(5)\n"
        "        return None\n"
        "    def evaluate_exit(self, ctx): return None\n"
        "def create_strategy(params): return S()\n", encoding="utf-8")
    ref = main.freeze_strategy(main.resolve_selection(str(root), "user:example_momentum"))
    provider = _provider(StrategyConfig(), worker_factory=make_worker_factory(timeout=0.5))
    try:
        runtime = provider.build(ref)
        with pytest.raises(Exception) as exc:
            runtime.evaluate_entry(
                candidate={"code": "600001"}, row={"date": "2026-11-11", "phase": "open"},
                cfg=StrategyConfig(), prev_row={}, hist_rows=[], intraday_bars=[],
                sentiment_pct=None, trade_date="2026-11-11",
                timestamp=at("2026-11-11", "09:30"), phase="open", account={})
        assert "超时" in str(exc.value) or "无响应" in str(exc.value)
    finally:
        provider.close()


def test_worker_helper_change_after_freeze_uses_frozen_value(tmp_path):
    root = _workspace(tmp_path)
    (root / "helpers" / "value.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "strategies" / "example_momentum" / "strategy.py").write_text(
        "class S:\n"
        "    def evaluate_entry(self, ctx):\n"
        "        from helpers.value import VALUE\n"
        "        from dragon_quant.strategy_sdk import StrategyIntent\n"
        "        return StrategyIntent(action='buy', reason_code='v',\n"
        "                              evidence={'value': VALUE})\n"
        "    def evaluate_exit(self, ctx): return None\n"
        "def create_strategy(params): return S()\n", encoding="utf-8")
    ref = main.freeze_strategy(main.resolve_selection(str(root), "user:example_momentum"))
    (root / "helpers" / "value.py").write_text("VALUE = 2\n", encoding="utf-8")
    provider = _provider(StrategyConfig(), worker_factory=make_worker_factory(timeout=10))
    try:
        runtime = provider.build(ref)
        signal = runtime.evaluate_entry(
            candidate={"code": "600001"}, row={"date": "2026-11-11", "phase": "open"},
            cfg=StrategyConfig(), prev_row={}, hist_rows=[], intraday_bars=[],
            sentiment_pct=None, trade_date="2026-11-11",
            timestamp=at("2026-11-11", "09:30"), phase="open", account={})
        assert signal["signal"]["evidence"]["value"] == 1
    finally:
        provider.close()


def test_cli_service_user_strategy_runs_in_worker(tmp_path):
    """D3：CLI 服务层用户策略默认也走独立 worker（父 PID 不同）。"""
    import os

    from dragon_quant.review_account.service import _build_strategy_runtime

    root = _workspace(tmp_path)
    (root / "strategies" / "example_momentum" / "strategy.py").write_text(
        "import os\n"
        "class S:\n"
        "    def evaluate_entry(self, ctx):\n"
        "        from dragon_quant.strategy_sdk import StrategyIntent\n"
        "        return StrategyIntent(action='buy', reason_code='cli',\n"
        "                              evidence={'pid': os.getpid()})\n"
        "    def evaluate_exit(self, ctx): return None\n"
        "def create_strategy(params): return S()\n", encoding="utf-8")
    selection = main.resolve_selection(str(root), "user:example_momentum")
    runtime, resolver, _ref = _build_strategy_runtime(selection, StrategyConfig())
    try:
        assert isinstance(runtime, WorkerUserStrategyRuntime)
        signal = runtime.evaluate_entry(
            candidate={"code": "600001"}, row={"date": "2026-11-11", "phase": "open"},
            cfg=StrategyConfig(), prev_row={}, hist_rows=[], intraday_bars=[],
            sentiment_pct=None, trade_date="2026-11-11",
            timestamp=at("2026-11-11", "09:30"), phase="open", account={})
        assert signal["signal"]["evidence"]["pid"] != os.getpid()
    finally:
        resolver.close()
