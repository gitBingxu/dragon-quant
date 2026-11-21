"""2026-11-21 三处 dashboard 回归：策略选择解析 / 重新回测 / 增量身份。

覆盖 KR2（内置策略在「无 workspace」与「失效本机登记」下可正常新建，显式/环境
路径与坏配置仍显式失败）与 KR3 的服务端一半（旧记录不改、新全量记录冻结
``strategy_ref``、随后按冻结身份增量续算）。

数据层全部离线：合成日 K + 候选池 + 空 prep/竞价仓储；不触网、不写真实库，
workspace 初始化一律 ``register=False``（测试不登记临时路径）。
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from dragon_quant.review_account import service as service_mod
from dragon_quant.review_account import simulator as simulator_mod
from dragon_quant.review_account import storage as account_store
from dragon_quant.review_account.models import StrategyConfig
from dragon_quant.workspace import config as ws_config
from dragon_quant.workspace import main as workspace_main
from dragon_quant.workspace import snapshots as ws_snapshots
from web_ui.backend.app import create_app
from web_ui.backend.settings import DashboardSettings

# ── 离线市场夹具 ────────────────────────────────────────────────────────────


def _market_days() -> list[str]:
    today = date.today()
    days: list[str] = []
    day = today - timedelta(days=70)
    while day <= today:
        if day.weekday() < 5:
            days.append(day.isoformat())
        day += timedelta(days=1)
    return days


def _offline_market(monkeypatch) -> list[str]:
    """把 MarketData/候选池/情绪全部换成本地合成数据。"""
    from dragon_quant.infra.models import KBar
    from dragon_quant.review_account.market import at

    days = _market_days()
    series: dict[str, KBar] = {}
    price = 10.0
    for day_text in days:
        open_px = round(price * 1.01, 2)
        close = round(open_px * 1.002, 2)
        series[day_text] = KBar(
            at(day_text, "15:00"), 1_000_000, open_px,
            round(max(open_px, close) * 1.01, 2),
            round(min(open_px, close) * 0.99, 2), close, 0, 5.0, 1.0,
            10_000_000)
        price = close

    class OfflineMarket:
        def __init__(self, provider=None, cache=None, offline=False):
            self._series = series

        def calendar(self, start, end, include_today=False):
            return [d for d in days if start <= d <= end]

        def daily(self, code, start, refresh=False):
            return [self._series[d] for d in days]

        def try_intraday(self, code, day, until=None):
            return None

        def intraday(self, code, day, until=None):
            from dragon_quant.review_account.market import DataCoverageError
            raise DataCoverageError("offline fixture has no intraday")

    candidate = {"code": "600001", "name": "样本", "rank": 1,
                 "composite_score": 80, "is_true_dragon": True,
                 "trade_date": days[0]}

    monkeypatch.setattr(simulator_mod, "MarketData", OfflineMarket)
    # 增量路径在 service 层直接构造 MarketData（不经 simulator 命名空间）。
    monkeypatch.setattr(service_mod, "MarketData", OfflineMarket)
    monkeypatch.setattr(simulator_mod, "candidate_loader",
                        lambda source: (lambda day, **kw: [dict(candidate)]))
    monkeypatch.setattr(simulator_mod, "load_market_sentiment",
                        lambda *args, **kwargs: None)
    return days


def _settings(**kwargs) -> DashboardSettings:
    kwargs.setdefault("workspace_path", None)
    kwargs.setdefault("workspace_writes_enabled", False)
    return DashboardSettings(**kwargs)


def _client(settings) -> TestClient:
    return TestClient(create_app(settings), base_url="http://localhost")


def _client_with_sentinel(settings) -> tuple[TestClient, list[str]]:
    """把 runner 换成哨兵：用于证明错误在进入回测前就被拒绝。"""
    from web_ui.backend.deps import account_repository, run_account_backtest

    calls: list[str] = []

    def sentinel(**kwargs):
        calls.append("entered")
        raise ValueError("RUN_ACCOUNT_WAS_CALLED")

    app = create_app(settings)
    app.dependency_overrides[run_account_backtest] = lambda: sentinel
    app.dependency_overrides[account_repository] = lambda: _StubAccount()
    return TestClient(app, base_url="http://localhost"), calls


class _StubAccount:
    def get_review_account_run(self, run_id: int) -> dict:  # pragma: no cover - 未到达
        return {"id": run_id}


def _full_body(days: list[str], *, strategy_id: str | None = None) -> dict:
    body = {"date_from": days[-12], "date_to": days[-7], "initial_cash": 100000}
    if strategy_id is not None:
        body["strategy_id"] = strategy_id
    return body


def _deleted_registration(tmp_path) -> tuple[Path, str]:
    """模拟历史脚本：登记一个随后被删除的临时 workspace。"""
    stale = tmp_path / "dq-ui-check" / "ws"
    stale.mkdir(parents=True)
    (stale / "workspace.json").write_text("{}", encoding="utf-8")
    path = str(stale)
    for child in stale.iterdir():
        child.unlink()
    stale.rmdir()
    stale.parent.rmdir()
    assert not Path(path).exists()
    return Path(path), path


def _legacy_snapshot(trade_date: str) -> dict:
    return {
        "trade_date": trade_date, "cash": 100000.0, "market_value": 0.0,
        "total_equity": 100000.0, "daily_return": 0.0,
        "cumulative_return": 0.0, "drawdown": 0.0, "position_code": "",
        "position_name": "", "position_qty": 0, "position_cost": None,
        "position_market_price": None, "position_unrealized_return": None,
        "positions_json": "[]",
    }


def _legacy_run(days: list[str], *, params: dict | None = None) -> int:
    """旧记录：有策略配置与快照，但 run_meta 没有 strategy_ref。"""
    cfg = StrategyConfig().to_json_dict()
    cfg.update(params or {})
    return account_store.create_review_account_run(
        source="v2",
        strategy_name="dragon_pullback_daily",
        strategy_params_json=json.dumps(cfg, ensure_ascii=False),
        date_from=days[-12],
        date_to=days[-7],
        initial_cash=100000.0,
        final_equity=100000.0,
        total_return=0.0,
        max_drawdown=0.0,
        trade_count=0,
        win_rate=None,
        display_name=f"{days[-12]} ~ {days[-7]}",
        start_equity=100000.0,
        run_meta_json=json.dumps({"data_quality": "strict_5min"}, ensure_ascii=False),
        children={
            "snapshots": [_legacy_snapshot(days[-7])],
            "trades": [],
            "positions": [],
            "events": [],
        },
    )


# ── KR2：内置默认 / 显式内置 ID 在无 workspace 与失效登记下可新建 ───────────


def test_builtin_creation_without_workspace_persists_frozen_ref(tmp_path, monkeypatch):
    days = _offline_market(monkeypatch)
    monkeypatch.setattr(ws_config, "read_active_workspace", lambda: None)
    monkeypatch.delenv("DQ_WORKSPACE", raising=False)
    client = _client(_settings())

    resp = client.post("/api/account/runs",
                       json=_full_body(days, strategy_id="builtin:dragon_pullback_daily"))

    assert resp.status_code == 201, resp.text
    run = resp.json()["data"]
    ref = run["run_meta"]["strategy_ref"]
    assert ref["strategy_id"] == "builtin:dragon_pullback_daily"
    # 冻结的是**完整有效**参数（与落库 strategy_params 同一份），不是空字典。
    assert ref["params"] == run["strategy_params"]
    assert ref["params"]["stop_loss_pct"] == StrategyConfig().stop_loss_pct
    assert ref["params"]["max_hold_days"] == StrategyConfig().max_hold_days
    assert ref["params"]["initial_cash"] == run["initial_cash"]
    assert ref["params_hash"] == ws_snapshots.params_hash(ref["params"])
    # 参数落库在同一份 strategy_params 里（增量从此重建 cfg）。
    assert run["strategy_params"]["source"] == "v2"
    assert run["base_run_id"] is None


def test_builtin_default_creation_without_strategy_id(tmp_path, monkeypatch):
    days = _offline_market(monkeypatch)
    monkeypatch.setattr(ws_config, "read_active_workspace", lambda: None)
    monkeypatch.delenv("DQ_WORKSPACE", raising=False)
    client = _client(_settings())

    resp = client.post("/api/account/runs", json=_full_body(days))

    assert resp.status_code == 201, resp.text
    run = resp.json()["data"]
    assert run["run_meta"]["strategy_ref"]["strategy_id"] == "builtin:dragon_pullback_daily"


def test_builtin_creation_ignores_deleted_implicit_registration(tmp_path, monkeypatch):
    """报告中的故障：本机登记指向已删除的 /tmp workspace，内建新建必须仍可用。"""
    days = _offline_market(monkeypatch)
    stale, stale_path = _deleted_registration(tmp_path)
    monkeypatch.setattr(ws_config, "read_active_workspace", lambda: stale)
    monkeypatch.delenv("DQ_WORKSPACE", raising=False)
    client = _client(_settings())

    resp = client.post("/api/account/runs",
                       json=_full_body(days, strategy_id="builtin:dragon_pullback_daily"))

    assert resp.status_code == 201, resp.text
    run = resp.json()["data"]
    assert run["run_meta"]["strategy_ref"]["strategy_id"] == "builtin:dragon_pullback_daily"
    # 不因回退而隐式 init / 改写登记：临时路径依旧不存在。
    assert not Path(stale_path).exists()
    assert not (stale / "workspace.json").exists()


def test_deleted_registration_does_not_silently_swap_user_strategy(tmp_path, monkeypatch):
    """只对内置/默认回退：显式用户策略在失效登记下仍显式失败，不静默改跑内置。"""
    days = _offline_market(monkeypatch)
    stale, _ = _deleted_registration(tmp_path)
    monkeypatch.setattr(ws_config, "read_active_workspace", lambda: stale)
    monkeypatch.delenv("DQ_WORKSPACE", raising=False)
    client = _client(_settings())

    resp = client.post("/api/account/runs",
                       json=_full_body(days, strategy_id="user:example_momentum"))

    assert resp.status_code == 400
    assert "未知策略" in resp.json()["error"]


def test_explicit_missing_workspace_still_errors(tmp_path, monkeypatch):
    days = _offline_market(monkeypatch)
    missing = tmp_path / "missing-ws"
    client = _client(_settings(workspace_path=str(missing)))

    resp = client.post("/api/account/runs",
                       json=_full_body(days, strategy_id="builtin:dragon_pullback_daily"))

    assert resp.status_code == 400
    assert "未初始化" in resp.json()["error"]
    assert not missing.exists()


def test_env_missing_workspace_still_errors(tmp_path, monkeypatch):
    days = _offline_market(monkeypatch)
    missing = tmp_path / "missing-env-ws"
    monkeypatch.setattr(ws_config, "read_active_workspace", lambda: None)
    monkeypatch.setenv("DQ_WORKSPACE", str(missing))
    client = _client(_settings())

    resp = client.post("/api/account/runs",
                       json=_full_body(days, strategy_id="builtin:dragon_pullback_daily"))

    assert resp.status_code == 400
    assert "未初始化" in resp.json()["error"]


def test_corrupt_workspace_is_reported_not_concealed(tmp_path, monkeypatch):
    days = _offline_market(monkeypatch)
    corrupt = tmp_path / "corrupt-ws"
    corrupt.mkdir()
    (corrupt / "workspace.json").write_text("{broken", encoding="utf-8")
    client = _client(_settings(workspace_path=str(corrupt)))

    # 无 strategy_id 时也不例外：不能吞掉坏配置后静默跑内建默认。
    resp = client.post("/api/account/runs", json=_full_body(days))

    assert resp.status_code == 400
    assert "workspace.json 无法解析" in resp.json()["error"]


def test_builtin_creation_freezes_saved_workspace_parameters(tmp_path, monkeypatch):
    days = _offline_market(monkeypatch)
    root = tmp_path / "ws"
    workspace_main.init_workspace(str(root), register=False)
    cfg = ws_config.load_workspace(root)
    cfg.selected_parameters = {"stop_loss_pct": -7.0}
    ws_config.write_workspace(cfg)
    client = _client(_settings(workspace_path=str(root)))

    resp = client.post("/api/account/runs",
                       json=_full_body(days, strategy_id="builtin:dragon_pullback_daily"))

    assert resp.status_code == 201, resp.text
    run = resp.json()["data"]
    ref = run["run_meta"]["strategy_ref"]
    assert ref["strategy_id"] == "builtin:dragon_pullback_daily"
    # 保存值进入冻结的完整有效参数（显式请求参数此时未给）。
    assert ref["params"] == run["strategy_params"]
    assert ref["params"]["stop_loss_pct"] == -7.0
    assert ref["params_hash"] == ws_snapshots.params_hash(ref["params"])
    assert run["strategy_params"]["stop_loss_pct"] == -7.0
    # 新建不隐式改写选择/登记。
    assert ws_config.load_workspace(root).selected_parameters == {"stop_loss_pct": -7.0}
    assert ws_config.resolve_workspace_path(None) != root


def test_saved_bs_selection_remains_explicit_error(tmp_path, monkeypatch):
    days = _offline_market(monkeypatch)
    root = tmp_path / "ws-bs"
    workspace_main.init_workspace(str(root), register=False)
    cfg = ws_config.load_workspace(root)
    cfg.selected_strategy_id = ws_config.BUILTIN_BS_STRATEGY_ID
    ws_config.write_workspace(cfg)
    client = _client(_settings(workspace_path=str(root)))

    resp = client.post("/api/account/runs", json=_full_body(days))

    assert resp.status_code == 400
    assert "BS" in resp.json()["error"]


# ── F1（round-1）：省略 strategy_id 不得绕开选择校验 ───────────────────────


def test_explicit_missing_workspace_rejected_before_runner_without_strategy_id(
        tmp_path, monkeypatch):
    """主验收复现：不传 strategy_id 也必须先拒绝未初始化的显式 workspace。"""
    days = _offline_market(monkeypatch)
    missing = tmp_path / "missing-ws"
    client, calls = _client_with_sentinel(_settings(workspace_path=str(missing)))

    resp = client.post("/api/account/runs", json=_full_body(days))

    assert resp.status_code == 400, resp.text
    assert "未初始化" in resp.json()["error"]
    assert "RUN_ACCOUNT_WAS_CALLED" not in resp.json()["error"]
    assert calls == []
    assert not missing.exists()


def test_env_missing_workspace_rejected_before_runner_without_strategy_id(
        tmp_path, monkeypatch):
    days = _offline_market(monkeypatch)
    missing = tmp_path / "missing-env"
    monkeypatch.setattr(ws_config, "read_active_workspace", lambda: None)
    monkeypatch.setenv("DQ_WORKSPACE", str(missing))
    client, calls = _client_with_sentinel(_settings())

    resp = client.post("/api/account/runs", json=_full_body(days))

    assert resp.status_code == 400, resp.text
    assert "未初始化" in resp.json()["error"]
    assert calls == []


def test_no_workspace_still_enters_runner_with_builtin_selection(tmp_path, monkeypatch):
    """无 workspace 且无 strategy_id：仍交由服务层内置默认（不能因新校验而拒绝）。"""
    days = _offline_market(monkeypatch)
    monkeypatch.setattr(ws_config, "read_active_workspace", lambda: None)
    monkeypatch.delenv("DQ_WORKSPACE", raising=False)
    seen: dict = {}

    from web_ui.backend.deps import account_repository, run_account_backtest

    def recorder(**kwargs):
        seen.update(kwargs)
        raise ValueError("RECORDED")

    app = create_app(_settings())
    app.dependency_overrides[run_account_backtest] = lambda: recorder
    app.dependency_overrides[account_repository] = lambda: _StubAccount()
    client = TestClient(app, base_url="http://localhost")

    resp = client.post("/api/account/runs", json=_full_body(days))

    assert resp.status_code == 400
    assert resp.json()["error"] == "RECORDED"
    selection = seen.get("selection")
    assert selection is not None
    assert selection.strategy_id == ws_config.BUILTIN_STRATEGY_ID
    assert selection.workspace is None


def test_deleted_registration_without_strategy_id_still_creates(tmp_path, monkeypatch):
    days = _offline_market(monkeypatch)
    stale, _ = _deleted_registration(tmp_path)
    monkeypatch.setattr(ws_config, "read_active_workspace", lambda: stale)
    monkeypatch.delenv("DQ_WORKSPACE", raising=False)
    client = _client(_settings())

    resp = client.post("/api/account/runs", json=_full_body(days))

    assert resp.status_code == 201, resp.text
    run = resp.json()["data"]
    assert run["run_meta"]["strategy_ref"]["strategy_id"] == ws_config.BUILTIN_STRATEGY_ID


# ── F2（round-1）：冻结完整有效参数，显式请求参数优先 ───────────────────────


def test_builtin_runtime_freezes_effective_config_not_saved_overrides():
    """主验收纯复现：ref/cfg/runtime 三处一致，且用显式参数。"""
    from dragon_quant.review_account.service import _build_strategy_runtime

    selection = workspace_main.StrategySelection(
        strategy_id=ws_config.BUILTIN_STRATEGY_ID,
        descriptor=workspace_main.find_descriptor(None, ws_config.BUILTIN_STRATEGY_ID),
        workspace=None,
        parameters={"stop_loss_pct": -7.0},
    )
    cfg = StrategyConfig.from_dict({"stop_loss_pct": -6.0})

    runtime, resolver, ref = _build_strategy_runtime(selection, cfg)
    try:
        assert cfg.stop_loss_pct == -6.0
        assert ref["params"]["stop_loss_pct"] == -6.0
        assert ref["params"] == cfg.to_json_dict()
        assert ref["params_hash"] == ws_snapshots.params_hash(ref["params"])
        assert runtime._cfg(cfg).stop_loss_pct == -6.0
    finally:
        resolver.close()
    # 不同有效参数 → 不同身份/哈希。
    other = StrategyConfig.from_dict({"stop_loss_pct": -8.0})
    runtime2, resolver2, ref2 = _build_strategy_runtime(selection, other)
    try:
        assert ref2["params_hash"] != ref["params_hash"]
        assert ref2["snapshot_id"] != ref["snapshot_id"]
    finally:
        resolver2.close()


def test_saved_workspace_params_do_not_override_explicit_request_params(
        tmp_path, monkeypatch):
    """HTTP：保存 -7 + 请求 -6 → 落库/冻结/实际运行时/增量重建均为 -6。"""
    days = _offline_market(monkeypatch)
    root = tmp_path / "ws"
    workspace_main.init_workspace(str(root), register=False)
    cfg = ws_config.load_workspace(root)
    cfg.selected_parameters = {"stop_loss_pct": -7.0}
    ws_config.write_workspace(cfg)
    client = _client(_settings(workspace_path=str(root)))

    body = _full_body(days, strategy_id="builtin:dragon_pullback_daily")
    body["strategy_params"] = {"stop_loss_pct": -6.0}
    resp = client.post("/api/account/runs", json=body)

    assert resp.status_code == 201, resp.text
    run = resp.json()["data"]
    ref = run["run_meta"]["strategy_ref"]
    assert run["strategy_params"]["stop_loss_pct"] == -6.0
    assert ref["params"] == run["strategy_params"]
    assert ref["params_hash"] == ws_snapshots.params_hash(ref["params"])

    # 之后修改保存选择：既有冻结身份与增量运行时不得被它逆转。
    cfg = ws_config.load_workspace(root)
    cfg.selected_parameters = {"stop_loss_pct": -8.0}
    ws_config.write_workspace(cfg)
    base = account_store.get_review_account_run(run["id"])
    base_cfg = StrategyConfig.from_dict({
        **base["strategy_params"], "source": "v2",
        "strategy_name": base["strategy_name"],
        "initial_cash": base["initial_cash"],
    })
    runtime, resolver, returned = service_mod._runtime_from_frozen_ref(base, base_cfg)
    try:
        assert runtime._cfg(base_cfg).stop_loss_pct == -6.0
    finally:
        resolver.close()
    assert returned["params"]["stop_loss_pct"] == -6.0

    appended = client.post("/api/account/runs/incremental", json={"run_id": run["id"]})
    assert appended.status_code == 201, appended.text
    after = account_store.get_review_account_run(run["id"])
    assert after["run_meta"]["strategy_ref"]["params"]["stop_loss_pct"] == -6.0


# ── KR3 服务端：旧记录不可增量 → 新全量冻结身份 → 按冻结身份增量 ────────────


def test_legacy_run_rejects_incremental_then_full_rerun_enables_it(tmp_path, monkeypatch):
    days = _offline_market(monkeypatch)
    monkeypatch.setattr(ws_config, "read_active_workspace", lambda: None)
    monkeypatch.delenv("DQ_WORKSPACE", raising=False)
    client = _client(_settings())

    legacy_id = _legacy_run(days, params={"stop_loss_pct": -6.0})
    before = account_store.get_review_account_run(legacy_id)

    # 1) 旧记录：缺 strategy_ref → 明确拒绝（不换用当前规则、不静默全量重跑）。
    rejected = client.post("/api/account/runs/incremental", json={"run_id": legacy_id})
    assert rejected.status_code == 400, rejected.text
    assert "strategy_ref" in rejected.json()["error"]
    assert account_store.get_review_account_run(legacy_id) == before

    # 2) 「重新回测」等价请求：旧交易参数经 strategy_params 带入新全量记录。
    body = _full_body(days, strategy_id="builtin:dragon_pullback_daily")
    body["strategy_params"] = {**before["strategy_params"], "stop_loss_pct": -6.0}
    created = client.post("/api/account/runs", json=body)
    assert created.status_code == 201, created.text
    new_run = created.json()["data"]
    assert new_run["id"] != legacy_id
    ref = new_run["run_meta"]["strategy_ref"]
    assert ref["strategy_id"] == "builtin:dragon_pullback_daily"
    assert new_run["strategy_params"]["stop_loss_pct"] == -6.0
    # 旧记录逐字段不变（不追加、不改名、不改参数）。
    assert account_store.get_review_account_run(legacy_id) == before

    # 3) 新记录已有冻结身份 → 正常增量续算原地追加。
    incremental = client.post("/api/account/runs/incremental",
                              json={"run_id": new_run["id"]})
    assert incremental.status_code == 201, incremental.text
    appended = incremental.json()["data"]
    assert appended["id"] == new_run["id"]
    assert appended["date_to"] > new_run["date_to"]
    assert account_store.get_review_account_run(legacy_id) == before


def test_incremental_still_rejects_corrupt_frozen_ref(tmp_path, monkeypatch):
    days = _offline_market(monkeypatch)
    monkeypatch.setattr(ws_config, "read_active_workspace", lambda: None)
    monkeypatch.delenv("DQ_WORKSPACE", raising=False)

    ref = ws_snapshots.builtin_ref(strategy_id=ws_config.BUILTIN_STRATEGY_ID,
                                   params={})
    ref["params"] = {"stop_loss_pct": -9.0}  # 与 params_hash 不一致：伪造/损坏
    cfg = StrategyConfig().to_json_dict()
    run_id = account_store.create_review_account_run(
        source="v2", strategy_name="dragon_pullback_daily",
        strategy_params_json=json.dumps(cfg, ensure_ascii=False),
        date_from=days[-12], date_to=days[-7], initial_cash=100000.0,
        final_equity=100000.0, total_return=0.0, max_drawdown=0.0,
        trade_count=0, win_rate=None,
        run_meta_json=json.dumps({"strategy_ref": ref}, ensure_ascii=False),
        children={"snapshots": [_legacy_snapshot(days[-7])], "trades": [],
                  "positions": [], "events": []})
    client = _client(_settings())

    resp = client.post("/api/account/runs/incremental", json={"run_id": run_id})

    assert resp.status_code == 400, resp.text
    assert "params" in resp.json()["error"]
