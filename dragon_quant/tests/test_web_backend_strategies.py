"""Web 策略端点：列表/当前/持久切换，冲突 409、非法 400、跨站拒绝。"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from dragon_quant.workspace import config
from dragon_quant.workspace import main as workspace_main
from web_ui.backend.app import create_app
from web_ui.backend.settings import DashboardSettings


def _client(tmp_path, **settings_kwargs):
    root = tmp_path / "ws"
    workspace_main.init_workspace(str(root))
    settings_kwargs.setdefault("workspace_writes_enabled", True)
    settings = DashboardSettings(workspace_path=str(root), **settings_kwargs)
    return TestClient(create_app(settings), base_url="http://localhost"), root


def test_list_and_current(tmp_path):
    client, root = _client(tmp_path)
    listing = client.get("/api/strategies")
    assert listing.status_code == 200
    data = listing.json()
    ids = [item["id"] for item in data["strategies"]]
    assert ids == ["builtin:dragon_pullback_daily",
                   "user:example_momentum", "user:example_meanrev"]
    assert data["current"] == "builtin:dragon_pullback_daily"
    # 页面展示的是有效参数值（默认值），不是 schema 本身。
    momentum = next(item for item in data["strategies"]
                    if item["id"] == "user:example_momentum")
    assert momentum["effective_parameters"]["min_open_gap_pct"] == 1.0
    # 参数 schema 原样返回给前端，携带可选的展示元数据。
    spec = momentum["parameters"]["min_open_gap_pct"]
    assert spec["label"] == "最小开盘涨幅"
    assert spec["unit"] == "%"
    assert isinstance(spec["help"], str) and spec["help"]
    builtin = next(item for item in data["strategies"]
                   if item["id"] == "builtin:dragon_pullback_daily")
    assert builtin["parameters"]["max_hold_days"]["label"] == "最大持有天数"
    assert builtin["parameters"]["take_profit_pct"]["help"]

    current = client.get("/api/strategies/current")
    assert current.json()["strategy_id"] == "builtin:dragon_pullback_daily"


def test_list_uninitialized_workspace_falls_back_to_builtin_read_only(tmp_path):
    root = tmp_path / "not-initialized"
    client = TestClient(create_app(DashboardSettings(
        workspace_path=str(root), workspace_writes_enabled=False)),
        base_url="http://localhost")

    response = client.get("/api/strategies")

    assert response.status_code == 200
    data = response.json()
    assert data["workspace"] is None
    assert data["workspace_id"] is None
    assert data["revision"] is None
    assert data["current"] == "builtin:dragon_pullback_daily"
    assert [item["id"] for item in data["strategies"]] == [
        "builtin:dragon_pullback_daily"]
    builtin = data["strategies"][0]
    assert builtin["id"] == "builtin:dragon_pullback_daily"
    assert builtin["current"] is True

    current = client.get("/api/strategies/current")
    assert current.status_code == 200
    assert current.json() == {
        "workspace": None,
        "workspace_id": None,
        "revision": None,
        "strategy_id": "builtin:dragon_pullback_daily",
        "parameters": {},
    }


def test_list_corrupt_workspace_still_reports_error(tmp_path):
    root = tmp_path / "corrupt"
    root.mkdir()
    (root / "workspace.json").write_text("{broken", encoding="utf-8")
    client = TestClient(create_app(DashboardSettings(workspace_path=str(root))),
                        base_url="http://localhost")

    response = client.get("/api/strategies")

    assert response.status_code == 500
    assert "workspace.json 无法解析" in response.json()["error"]


def test_list_never_imports_user_code(tmp_path):
    client, root = _client(tmp_path)
    (root / "strategies" / "example_momentum" / "strategy.py").write_text(
        "raise RuntimeError('boom')\n", encoding="utf-8")
    assert client.get("/api/strategies").status_code == 200


def test_select_persists_and_cli_reads_same_state(tmp_path):
    client, root = _client(tmp_path)
    revision = client.get("/api/strategies").json()["revision"]
    response = client.post("/api/strategies/selection",
                           json={"strategy_id": "user:example_meanrev",
                                 "expected_revision": revision})
    assert response.status_code == 200, response.text
    assert response.json()["current"] == "user:example_meanrev"
    # CLI 侧读同一保存状态
    assert workspace_main.current_selection(str(root))["strategy_id"] == "user:example_meanrev"


def test_selection_revision_conflict_is_409(tmp_path):
    client, root = _client(tmp_path)
    revision = client.get("/api/strategies").json()["revision"]
    response = client.post("/api/strategies/selection",
                           json={"strategy_id": "user:example_meanrev",
                                 "expected_revision": revision + 9})
    assert response.status_code == 409
    assert "revision" in response.json()["error"]


def test_unknown_strategy_is_400(tmp_path):
    client, root = _client(tmp_path)
    response = client.post("/api/strategies/selection",
                           json={"strategy_id": "user:nope"})
    assert response.status_code == 400
    assert "未知策略" in response.json()["error"]


def test_cross_site_origin_rejected(tmp_path):
    client, root = _client(tmp_path)
    response = client.post("/api/strategies/selection",
                           json={"strategy_id": "user:example_meanrev"},
                           headers={"Origin": "http://evil.example"})
    assert response.status_code == 400


def test_writes_disabled_returns_403(tmp_path):
    client, root = _client(tmp_path, workspace_writes_enabled=False)
    response = client.post("/api/strategies/selection",
                           json={"strategy_id": "user:example_meanrev"})
    assert response.status_code == 403


def test_account_run_gate_blocks_user_strategy_when_disabled_or_cross_site(tmp_path):
    from web_ui.backend.deps import run_account_backtest

    root = tmp_path / "ws"
    workspace_main.init_workspace(str(root))
    calls: list[str] = []

    def sentinel(**kwargs):
        calls.append("entered")
        raise ValueError("sentinel")

    app = create_app(DashboardSettings(workspace_path=str(root),
                                       workspace_writes_enabled=False))
    app.dependency_overrides[run_account_backtest] = lambda: sentinel
    client = TestClient(app, base_url="http://localhost")
    body = {"strategy_id": "user:example_momentum", "date_from": "2026-11-01",
            "date_to": "2026-11-10"}
    evil = client.post("/api/account/runs", json=body,
                       headers={"Origin": "http://evil.example"})
    assert evil.status_code == 400
    assert calls == []
    disabled = client.post("/api/account/runs", json=body)
    assert disabled.status_code == 400
    assert "未启用" in disabled.json()["error"]
    assert calls == []


HTTP_STRATEGY = '''
import os
from pathlib import Path

Path({marker!r}).write_text(str(os.getpid()))
from dragon_quant.strategy_sdk import StrategyIntent


class Threshold:
    def __init__(self, params):
        params = dict(params or {{}})
        self.threshold = float(params.get("min_open_gap_pct", 1.0))

    def evaluate_entry(self, ctx):
        row = ctx.row
        if not row or ctx.phase != "open":
            return None
        gap = row.get("open_gap_pct")
        if gap is None or gap < self.threshold:
            return None
        return StrategyIntent(action="buy", reason_code="http_threshold_buy",
                              priority=100, position_pct=50.0,
                              evidence={{"open_gap_pct": gap, "pid": os.getpid(),
                                        "factors": sorted(ctx.factors)}})

    def evaluate_exit(self, ctx):
        if ctx.phase != "late" or not ctx.position:
            return None
        return StrategyIntent(action="sell", reason_code="http_same_day_exit",
                              priority=100, exit_fraction=1.0)


def create_strategy(params):
    return Threshold(params)
'''


def _account_fixture(tmp_path, monkeypatch):
    """真实 HTTP 账户路径的离线数据层：合成日K + 候选池 + 因子值（不 mock 撮合/运行时）。"""
    from datetime import date, timedelta

    from dragon_quant.infra.models import KBar
    from dragon_quant.review_account import service as service_mod
    from dragon_quant.review_account import simulator as simulator_mod
    from dragon_quant.review_account.market import at
    from dragon_quant.strategy_sdk import FACTOR_STATUS_OK, FactorValue

    root = tmp_path / "ws"
    workspace_main.init_workspace(str(root))
    strategy_pid = tmp_path / "strategy-pid"
    factor_pid = tmp_path / "factor-pid"
    (root / "strategies" / "example_momentum" / "strategy.py").write_text(
        HTTP_STRATEGY.format(marker=str(strategy_pid)), encoding="utf-8")
    descriptor = root / "strategies" / "example_momentum" / "strategy.json"
    meta = json.loads(descriptor.read_text(encoding="utf-8"))
    meta["parameters"]["min_open_gap_pct"] = {
        "type": "number", "default": 1.0, "min": 0.0, "max": 100.0}
    meta["required_factors"] = ["x"]
    descriptor.write_text(json.dumps(meta), encoding="utf-8")
    (root / "factors" / "custom.py").write_text(
        "import os\nfrom pathlib import Path\n"
        f"Path({str(factor_pid)!r}).write_text(str(os.getpid()))\n"
        "SPECS=[FactorSpec(name='x', label='x', frequency='1d', "
        "source='computed', compute=None, impl_key='x')]\n", encoding="utf-8")
    cfg = config.load_workspace(root)
    cfg.factors = ["factors/custom.py"]
    config.write_workspace(cfg)

    days, day = [], date(2026, 9, 1)
    while len(days) < 45:
        if day.weekday() < 5:
            days.append(day.isoformat())
        day += timedelta(days=1)
    window = [d for d in days if "2026-11-02" <= d <= "2026-11-06"]
    series, price = {}, 10.0
    for day_text in days:
        open_px = round(price * 1.05, 2)
        close = round(open_px * 1.01, 2)
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
                 "trade_date": window[0]}

    class _Reader:
        def __init__(self, names):
            self.names = list(names)

        def values_for(self, **kwargs):
            return {name: FactorValue(name=name, value=1.0,
                                      status=FACTOR_STATUS_OK)
                    for name in self.names}

    monkeypatch.setattr(simulator_mod, "MarketData", OfflineMarket)
    monkeypatch.setattr(simulator_mod, "candidate_loader",
                        lambda source: (lambda day, **kw: [dict(candidate)]))
    monkeypatch.setattr(simulator_mod, "load_market_sentiment",
                        lambda *args, **kwargs: None)
    monkeypatch.setattr(service_mod, "_factor_reader_factory",
                        lambda names, versions: _Reader(names))
    return root, window, strategy_pid, factor_pid


def test_http_account_run_executes_user_strategy_and_factor_off_parent(tmp_path,
                                                                     monkeypatch):
    """D4：真实 HTTP 账户入口 + 离线数据层，用户策略在 worker、因子父进程不导入。"""
    import os

    root, window, strategy_pid, factor_pid = _account_fixture(tmp_path, monkeypatch)
    body = {"strategy_id": "user:example_momentum",
            "date_from": window[0], "date_to": window[-1]}

    # 1) 门禁：未启用本地写 → 完全不执行用户代码。
    disabled = TestClient(create_app(DashboardSettings(
        workspace_path=str(root), workspace_writes_enabled=False)),
        base_url="http://localhost")
    blocked = disabled.post("/api/account/runs", json=body)
    assert blocked.status_code == 400
    assert "未启用" in blocked.json()["error"]
    assert not strategy_pid.exists() and not factor_pid.exists()

    # 2) 跨站 Origin 同样在用户代码之前拒绝。
    client = TestClient(create_app(DashboardSettings(
        workspace_path=str(root), workspace_writes_enabled=True)),
        base_url="http://localhost")
    evil = client.post("/api/account/runs", json=body,
                       headers={"Origin": "http://evil.example"})
    assert evil.status_code == 400
    assert not strategy_pid.exists() and not factor_pid.exists()

    # 3) 真实账户创建路径：不 override run_account_backtest，真实撮合与运行时。
    response = client.post("/api/account/runs", json=body)
    assert response.status_code == 201, response.text
    run = response.json()["data"]
    assert run["trade_count"] > 0
    ref = run["run_meta"]["strategy_ref"]
    assert ref["strategy_id"] == "user:example_momentum"
    assert ref["snapshot_id"] and ref["params_hash"]
    # 策略模块/工厂/eval 都在 worker 子进程（PID 与 HTTP 父进程不同）。
    assert strategy_pid.exists()
    strategy_process = int(strategy_pid.read_text())
    assert strategy_process != os.getpid()
    # 因子模块的顶层代码从未执行（父进程与 worker 都不 import 用户因子）。
    assert not factor_pid.exists()
