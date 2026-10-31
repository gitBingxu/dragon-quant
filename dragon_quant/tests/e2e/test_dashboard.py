"""``dashboard`` 命令 E2E：D1-D5（真实 CLI 子进程，离线）。

- D1 正常启动 + ``/api/meta`` 等 200 + SIGTERM 释放端口；
- D2 未装 [web] + 非 TTY：手动指令 + exit 1，绝不创建 pip 子进程；
- D3 ``--yes`` + pip 被 procguard 拒绝：exit 1 + 手动指令，无 traceback；
- D4 静态缓存头；
- D5 P0 端点冒烟（seed dragon.db / assets.db）。
"""

from __future__ import annotations

import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path

from dragon_quant.tests.e2e.fixtures import seed_builders as sb
from dragon_quant.tests.e2e.harness.seed import seed_db
from dragon_quant.tests.e2e.harness.sandbox import EnvSandbox
from dragon_quant.tests.e2e.harness.web import (
    port_is_free,
    start_web_server,
)

E2E_ROOT = Path(__file__).resolve().parent
REPO_ROOT = E2E_ROOT.parents[2]
DIST_ASSETS = REPO_ROOT / "web_ui" / "dist" / "assets"
NOW = "2026-09-11T15:30:00+08:00"


def runner():
    from dragon_quant.tests.e2e.harness import runner as module

    return module


@contextmanager
def sandbox(case_id: str):
    box = EnvSandbox.for_case(case_id, now=NOW)
    try:
        yield box
    finally:
        box.cleanup()


def _dashboard_command(*extra: str) -> list[str]:
    return [sys.executable, "-m", "dragon_quant", "dashboard",
            "--no-browser", *extra]


def seed_full(box: EnvSandbox) -> None:
    rows = [
        sb.dragon_row("2026-08-17", "600001", "龙头A", 1, 88.0, ["AI算力"]),
        sb.dragon_row("2026-09-11", "600002", "跟风B", 2, 70.0, ["机器人"]),
    ]
    dragon_spec = sb.dragon_db_seed(rows)
    dragon_spec["tables"].update(sb.scan_db_seed()["tables"])
    seed_db(box, dragon_spec)

    assets_spec = sb.mirror_db_seed(rows)
    for spec in (
        sb.factor_db_seed(),
        sb.prep_assets_tables({
            "dragons": {"rows": [{"code": "600001"}], "as_of": "2026-09-11"},
            "collection_report": {"dragons": {"status": "ok"}},
        }),
        sb.prep_snapshot_seed(),
        sb.auction_snapshots_seed([{
            "trade_date": "2026-09-11", "code": "600001", "source": "v2",
            "analysis_json": json.dumps({
                "code": "600001", "is_weak": False, "strength": 80.0,
                "level": "强", "direction": "bull", "rank": 1,
                "validation_status": "ok", "data_quality": {"status": "ok"},
                "risk_events": [], "config_version": "auction_v2",
            }, ensure_ascii=False),
            "created_at": "2026-09-11 09:26:00",
        }]),
        sb.auction_assets_seed([{
            "trade_date": "2026-09-11", "code": "600001", "name": "龙头A",
            "items_json": "[]", "quote_json": None,
            "created_at": "2026-09-11 09:26:00",
        }]),
    ):
        assets_spec["tables"].update(spec["tables"])
    seed_db(box, assets_spec, db_name="assets.db")


def start_dashboard(box: EnvSandbox, *extra: str, ready_path: str = "/"):
    command = _dashboard_command(*extra)
    return start_web_server(command, sandbox=box, port=0, ready_path=ready_path)


def test_d1_dashboard_starts_and_serves_meta():
    with sandbox("dashboard-d1") as box:
        seed_full(box)
        with start_dashboard(box) as server:
            assert server.port != 0
            assert "Review Web UI 已启动" in server.stdout
            assert f"http://localhost:{server.port}/" in server.stdout
            assert "http://localhost:0" not in server.stdout
            meta = server.request("GET", "/api/meta")
            assert meta.status == 200
            assert meta.json()["version"]
            assert server.request("GET", "/api/scans").status == 200
            assert server.request("GET", "/api/prep/dates").status == 200
        assert server.returncode is not None
        assert port_is_free(server.port)


def test_d1_dashboard_sigterm_releases_port():
    with sandbox("dashboard-d1-sigterm") as box:
        server = start_dashboard(box)
        port = server.port
        assert port != 0 and not port_is_free(port)
        server.stop()
        assert server.returncode is not None
        assert port_is_free(port)


def _write_shim(root: Path) -> Path:
    shim = root / "shim"
    shim.mkdir(parents=True, exist_ok=True)
    (shim / "fastapi.py").write_text(
        "raise ImportError('E2E shim: fastapi unavailable')\n", encoding="utf-8")
    (shim / "uvicorn.py").write_text(
        "raise ImportError('E2E shim: uvicorn unavailable')\n", encoding="utf-8")
    return shim


def _shim_env(box: EnvSandbox) -> dict[str, str]:
    shim = _write_shim(box.root)
    base = box.env()["PYTHONPATH"]
    return {"PYTHONPATH": os.pathsep.join((str(shim), base))}


def test_d2_missing_web_extra_non_tty_gives_manual_hint():
    with sandbox("dashboard-d2") as box:
        result = runner().run_cli(
            ["dashboard", "--no-browser", "--port", "0"],
            sandbox=box, timeout=60, env_override=_shim_env(box))
        assert result.returncode == 1
        assert 'python -m pip install "dragon-quant[web]"' in result.stderr
        assert "pipx inject dragon-quant" in result.stderr
        assert "Traceback" not in result.stderr
        assert "--break-system-packages" not in result.stderr
        assert "已启动" not in result.stdout


def test_d3_yes_pip_failure_exits_with_manual_hint():
    with sandbox("dashboard-d3") as box:
        result = runner().run_cli(
            ["dashboard", "--yes", "--no-browser", "--port", "0"],
            sandbox=box, timeout=60, env_override=_shim_env(box))
        assert result.returncode == 1
        assert "Traceback" not in result.stderr
        assert "--break-system-packages" not in result.stderr
        assert 'python -m pip install "dragon-quant[web]"' in result.stderr


def test_d4_static_cache_headers():
    with sandbox("dashboard-d4") as box:
        seed_full(box)
        with start_dashboard(box) as server:
            root = server.request("GET", "/")
            assert root.status == 200
            assert root.headers["cache-control"] == (
                "no-cache, no-store, must-revalidate"
            )
            asset = next(iter(sorted(DIST_ASSETS.glob("*.js"))), None)
            assert asset is not None
            response = server.request("GET", f"/assets/{asset.name}")
            assert response.status == 200
            assert response.headers["cache-control"] == (
                "public, max-age=31536000, immutable"
            )


def test_d5_p0_endpoints_smoke():
    with sandbox("dashboard-d5") as box:
        seed_full(box)
        with start_dashboard(box) as server:
            meta = server.request("GET", "/api/meta").json()
            assert meta["dragon_db"] is True and meta["assets_db"] is True

            scans = server.request("GET", "/api/scans").json()
            assert scans["count"] == 1
            scan_id = scans["data"][0]["id"]
            stocks = server.request(
                "GET", f"/api/scans/{scan_id}/stocks").json()
            assert {row["code"] for row in stocks["data"]} == {"600001", "600002"}

            factors = server.request("GET", "/api/factors").json()
            assert factors["count"] > 0
            values = server.request(
                "GET", "/api/factors/values?trade_date=20260911").json()
            assert values["rows"]
            assert values["coverage_source"] == "build_run"
            runs = server.request(
                "GET", "/api/factors/runs?trade_date=20260911").json()
            assert runs["count"] == 1 and runs["data"][0]["ok"] is True

            prep_dates = server.request("GET", "/api/prep/dates").json()
            assert prep_dates["count"] >= 1
            prep = server.request("GET", "/api/prep/20260911").json()
            assert len(prep["assets"]) == 13

            auction = server.request("GET", "/api/auction/20260911").json()
            assert auction["count"] == 1
            assert auction["summary"]["strong"] == 1

            dates = server.request("GET", "/api/dragons/trade-dates").json()
            assert dates["count"] >= 1
            detail = server.request(
                "GET", "/api/dragons/20260911/600002").json()
            assert detail["dims_source"] == "scan_stocks"
