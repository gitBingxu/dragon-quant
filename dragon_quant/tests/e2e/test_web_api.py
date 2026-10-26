"""M1F1 Web/HTTP behaviour baseline: R9-R11, RA7, W1-W8.

Every case launches a *real* ``python -m dragon_quant`` server subprocess in the
M0 sandbox.  The child gets ``DQ_E2E_ALLOW_LOCALHOST=1`` (localhost only, the
preflight still proves external DNS/sockets stay blocked), ``--no-browser`` and
``--port 0``; the real port is parsed from the child's startup URL, so no
preselected port is ever raced.  Teardown uses the process group and asserts
the discovered port is released.  The parent process talks to the child over
``127.0.0.1`` only.

R11 (single-row review write-back failure) is a plain CLI case: a temporary
SQLite trigger makes one ``UPDATE`` abort, and the run must warn on stderr,
keep going, and exit 0.
"""

from __future__ import annotations

import socket
import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

from dragon_quant.tests.e2e.fixtures import seed_builders as sb
from dragon_quant.tests.e2e.harness.assertlib import assert_exit, sqlite_count, sqlite_rows
from dragon_quant.tests.e2e.harness.canonical import floats_close
from dragon_quant.tests.e2e.harness.sandbox import EnvSandbox
from dragon_quant.tests.e2e.harness.seed import seed_db
from dragon_quant.tests.e2e.harness.web import (
    WebServerError,
    free_port,
    port_is_free,
    start_web_server,
)

E2E_ROOT = Path(__file__).resolve().parent
REPO_ROOT = E2E_ROOT.parents[2]
DIST_INDEX = REPO_ROOT / "web_ui" / "dist" / "index.html"
REVIEW_SCENARIO = E2E_ROOT / "fixtures" / "scenario_review.json"
ACCOUNT_SCENARIO = E2E_ROOT / "fixtures" / "scenario_account.json"
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


# ─── seeds ──────────────────────────────────────────────────────────────────


def web_dragon_rows() -> list[dict]:
    """Three dragons: one completed (summary winner), two pending."""

    rows = [
        sb.dragon_row("2026-08-17", "600001", "龙头A", 1, 88.0, ["AI算力"]),
        sb.dragon_row("2026-08-21", "600002", "跟风B", 2, 72.0, ["机器人"]),
        sb.dragon_row("2026-08-28", "600003", "补涨C", 1, 64.0, ["AI算力"]),
    ]
    rows[0].update(
        review_status="completed",
        buy_date="2026-08-18",
        buy_price=10.0,
        max_return_5d=8.0,
        max_drawdown_5d=-4.0,
        max_return_hold_days=3,
    )
    return rows


def review_seed_rows() -> list[dict]:
    """Three *pending* dragons matching ``scenario_review`` (R1 fixture)."""

    return [
        sb.dragon_row("2026-08-17", "600001", "龙头A", 1, 88.0, ["AI算力"]),
        sb.dragon_row("2026-08-21", "600002", "跟风B", 2, 72.0, ["机器人"]),
        sb.dragon_row("2026-08-28", "600003", "补涨C", 1, 64.0, ["AI算力"]),
    ]


def account_row() -> dict:
    return sb.dragon_row("2026-09-04", "600001", "样本", 1, 80.0, ["AI算力"])


def seed_dragons(box: EnvSandbox, rows: list[dict]) -> None:
    seed_db(box, sb.dragon_db_seed(rows))
    seed_db(box, sb.mirror_db_seed(rows), db_name="assets.db")


def seed_account(box: EnvSandbox) -> None:
    seed_db(box, sb.mirror_db_seed([account_row()]), db_name="assets.db")


# ─── server helpers ─────────────────────────────────────────────────────────

REVIEW_UI = ("dragon_quant", "review", "--ui-only", "--no-browser")
REVIEW_ACCOUNT_UI = ("dragon_quant", "review-account", "--ui-only", "--no-browser")


def server_command(module_args: tuple[str, ...], port: int, *extra: str) -> list[str]:
    return [sys.executable, "-m", *module_args, *extra, "--port", str(port)]


def account_ui_command(port: int, *extra: str) -> list[str]:
    return [
        sys.executable, "-m", "dragon_quant", "review-account",
        "--from", "20260904", "--to", "20260908",
        "--ui", "--no-browser", *extra, "--port", str(port),
    ]


@contextmanager
def web_server(box: EnvSandbox, command: list[str], *, ready_path: str = "/",
               timeout: float = 90.0, scenario: Path | None = None):
    """Start with ``--port 0`` and connect to the OS-assigned port."""

    command = list(command)
    assert command[-2] == "--port", command
    command[-1] = "0"
    env_override = {"DQ_E2E_SCENARIO": str(scenario)} if scenario else None
    server = start_web_server(
        command, sandbox=box, port=0, ready_path=ready_path,
        timeout=timeout, env_override=env_override)
    try:
        yield server
    finally:
        server.stop()


# ─── W1 / W3 / W4 / W7: review UI routes ────────────────────────────────────


def test_w1_w3_w4_w7_review_ui_routes_and_404():
    index_html = DIST_INDEX.read_bytes()
    with sandbox("m1f1-routes") as box:
        seed_dragons(box, web_dragon_rows())
        with web_server(box, server_command(REVIEW_UI, 0)) as server:
            # B2: ``--port 0`` is replaced by a real OS-assigned port.
            assert server.port != 0
            assert port_is_free(server.port) is False

            root = server.request("GET", "/")
            assert root.status == 200
            assert root.body == index_html
            assert root.headers["content-type"].startswith("text/html")

            for path in ("/account", "/some/spa/route"):
                page = server.request("GET", path)
                assert page.status == 200, path
                assert page.body == index_html, path

            dragons = server.request("GET", "/api/dragons")
            assert dragons.status == 200
            payload = dragons.json()
            assert payload["count"] == 3
            assert [row["code"] for row in payload["data"]] == [
                "600001", "600002", "600003"]
            assert [row["review_status"] for row in payload["data"]] == [
                "completed", "pending", "pending"]

            filtered = server.request(
                "GET", "/api/dragons?status=completed&code=6000")
            assert filtered.json()["count"] == 1
            assert filtered.json()["data"][0]["code"] == "600001"

            summary = server.request("GET", "/api/summary").json()
            assert summary == {
                "total": 3,
                "completed": 1,
                "pending": 2,
                "avg_return": 8.0,
                "win_rate": 100.0,
                "best_stock_code": "600001",
                "best_stock_name": "龙头A",
                "best_return": 8.0,
                "source": "v2",
            }

            missing = server.request("GET", "/api/does-not-exist")
            assert missing.status == 404
            assert missing.json() == {"error": "not found"}

            # Static traversal must never escape ``web_ui/dist``: the SPA
            # fallback may answer 200, but the body is the index, never a
            # repository/OS file.
            for path in ("/../README.md", "/%2e%2e/%2e%2e/etc/passwd",
                         "/assets/../../README.md"):
                traversal = server.request("GET", path)
                assert traversal.status in (200, 404), path
                if traversal.status == 200:
                    assert traversal.body == index_html, path
                assert b"root:" not in traversal.body
                assert b"# dragon-quant" not in traversal.body

        assert "Review Web UI 已启动" in server.stdout
        assert f"http://localhost:{server.port}/" in server.stdout
        assert "http://localhost:0" not in server.stdout
        assert server.returncode is not None
        assert port_is_free(server.port)


# ─── W8: SIGTERM exits and releases the port ────────────────────────────────


def test_w8_sigterm_exits_and_releases_port():
    with sandbox("m1f1-w8") as box:
        seed_dragons(box, web_dragon_rows())
        # B2: the first server deliberately uses ``--port 0`` so the test also
        # proves the discovered URL is real (never 0) and that teardown frees
        # exactly that port.
        server = start_web_server(
            server_command(REVIEW_UI, 0), sandbox=box, port=0)
        port = server.port
        assert port != 0
        assert port_is_free(port) is False
        server.stop()
        assert server.returncode is not None
        assert server.returncode != 0  # terminated by the harness signal
        assert port_is_free(port)

        # A second server can immediately reuse the same (explicit) port.
        again = start_web_server(
            server_command(REVIEW_UI, port), sandbox=box, port=port)
        try:
            assert again.request("GET", "/api/summary").status == 200
        finally:
            again.stop()
        assert port_is_free(port)


# ─── startup failure must still clean up ────────────────────────────────────


def test_web_runner_cleans_up_when_server_fails_to_start():
    with sandbox("m1f1-startup-failure") as box:
        seed_dragons(box, web_dragon_rows())
        port = free_port()
        blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        blocker.bind(("127.0.0.1", port))
        blocker.listen(1)
        try:
            with pytest.raises(WebServerError) as excinfo:
                start_web_server(
                    server_command(REVIEW_UI, port), sandbox=box, port=port,
                    timeout=30.0)
            assert str(port) in str(excinfo.value)
        finally:
            blocker.close()
        assert port_is_free(port)


# ─── W5 / W6: account run lifecycle and series endpoints ────────────────────


def test_w5_w6_account_run_lifecycle_and_series():
    with sandbox("m1f1-account-api") as box:
        seed_account(box)
        with web_server(box, server_command(REVIEW_UI, 0),
                        scenario=ACCOUNT_SCENARIO) as server:
            created = server.request("POST", "/api/account/runs", payload={
                "date_from": "2026-09-04",
                "date_to": "2026-09-08",
                "initial_cash": 100000,
                "name": "E2E 账户",
            }, timeout=60.0)
            assert created.status == 201, created.text
            body = created.json()
            run_id = body["run_id"]
            assert body["data"]["display_name"] == "E2E 账户"
            assert floats_close(body["data"]["final_equity"], 99495.199,
                                rel=0, abs_tol=1e-6)

            runs = server.request("GET", "/api/account/runs").json()
            assert runs["count"] == 1
            assert runs["data"][0]["id"] == run_id

            snapshots = server.request(
                "GET", f"/api/account/snapshots?run_id={run_id}").json()
            assert len(snapshots["data"]) == 3
            assert [row["trade_date"] for row in snapshots["data"]] == [
                "2026-09-04", "2026-09-07", "2026-09-08"]

            trades = server.request(
                "GET", f"/api/account/trades?run_id={run_id}").json()
            assert len(trades["data"]) == 2

            positions = server.request(
                "GET", f"/api/account/positions?run_id={run_id}").json()
            assert len(positions["data"]) == 1
            assert positions["stats"]["total_trades"] == 1
            assert positions["stats"]["rows"][-1]["reason_code"] == "__total__"

            events = server.request(
                "GET", f"/api/account/events?run_id={run_id}").json()
            assert events["data"]

            benchmark = server.request(
                "GET", f"/api/account/benchmark?run_id={run_id}").json()
            assert benchmark["code"] == "SH000001"
            assert benchmark["name"] == "上证指数"
            assert len(benchmark["data"]) == 3
            assert set(benchmark["data"][0]) == {
                "trade_date", "close", "total_equity", "cumulative_return"}

            bad = server.request("GET", "/api/account/trades?run_id=999999")
            assert bad.status == 200 and bad.json()["data"] == []
            # Baseline fact: GET route errors (ValueError) are reported as 500
            # by the current server (only POST/DELETE map ValueError to 400).
            no_run_id = server.request("GET", "/api/account/trades")
            assert no_run_id.status == 500
            assert no_run_id.json() == {"error": "missing run_id"}

            deleted = server.request(
                "DELETE", f"/api/account/runs?run_id={run_id}")
            assert deleted.status == 200
            assert deleted.json() == {"deleted": True, "run_id": run_id}
            again_delete = server.request(
                "DELETE", f"/api/account/runs?run_id={run_id}")
            assert again_delete.status == 404
            missing_id = server.request("DELETE", "/api/account/runs")
            assert missing_id.status == 400
            assert missing_id.json() == {"error": "missing run_id"}
            assert server.request("GET", "/api/account/runs").json()["count"] == 0

            assert server.request("POST", "/api/account/runs",
                                  payload={"date_from": "2026-09-04"}).status == 400


# ─── R9: --ui-only starts the review server and stops cleanly ───────────────


def test_r9_review_ui_only_serves_root():
    with sandbox("m1f1-r9") as box:
        seed_dragons(box, web_dragon_rows())
        with web_server(box, server_command(REVIEW_UI, 0)) as server:
            assert server.request("GET", "/").status == 200
        assert server.returncode is not None
        assert port_is_free(server.port)


# ─── R10: --ui runs the review backtest before serving ──────────────────────


def test_r10_review_ui_runs_backtest_then_serves():
    with sandbox("m1f1-r10") as box:
        seed_dragons(box, review_seed_rows())
        command = [
            sys.executable, "-m", "dragon_quant", "review",
            "--date", "20260817", "--ui", "--no-browser", "--port", "0",
        ]
        with web_server(box, command, scenario=REVIEW_SCENARIO) as server:
            rows = {row["code"]: row for row in sqlite_rows(
                box.data_dir / "dragon.db", "dragons_v2", order_by="code")}
            assert rows["600001"]["review_status"] == "completed"
            assert floats_close(rows["600001"]["max_return_5d"], 8.0,
                                rel=0, abs_tol=1e-9)
            served = server.request("GET", "/api/dragons").json()
            assert served["count"] == 3
            completed = [row for row in served["data"]
                         if row["review_status"] == "completed"]
            assert [row["code"] for row in completed] == ["600001"]
        # The backtest runs *before* the server starts, so its progress text is
        # only complete once the process has been torn down.
        assert "龙头回测 [v2]" in server.stdout
        assert "Review Web UI 已启动" in server.stdout


# ─── R11: a single write-back failure only warns and continues ──────────────


def test_r11_single_row_write_failure_warns_and_continues():
    with sandbox("m1f1-r11") as box:
        seed_dragons(box, review_seed_rows())
        connection = sqlite3.connect(box.data_dir / "dragon.db")
        try:
            connection.execute(
                "CREATE TRIGGER e2e_fail_600003 BEFORE UPDATE ON dragons_v2 "
                "WHEN OLD.code = '600003' "
                "BEGIN SELECT RAISE(ABORT, '模拟写回失败'); END"
            )
            connection.commit()
        finally:
            connection.close()

        result = runner().run_cli(
            ["review"], sandbox=box, timeout=120,
            env_override={"DQ_E2E_SCENARIO": str(REVIEW_SCENARIO)})
        assert_exit(result, 0)
        assert "⚠️ 写入失败" in result.stderr
        assert "模拟写回失败" in result.stderr
        assert "completed=1" in result.stdout
        assert "no_entry=1" in result.stdout
        assert "error=1" in result.stdout

        rows = {row["code"]: row for row in sqlite_rows(
            box.data_dir / "dragon.db", "dragons_v2", order_by="code")}
        # Only the failing row keeps its previous value; the other two were
        # written before the abort and the process still exits 0.
        assert rows["600001"]["review_status"] == "completed"
        assert rows["600002"]["review_status"] == "no_entry"
        assert rows["600003"]["review_status"] == "pending"
        assert sqlite_count(box.data_dir / "dragon.db", "vpa_analysis") == 2


# ─── RA7: review-account --ui / --ui-only serve the account page ────────────


def test_ra7_review_account_ui_runs_backtest_then_serves_account():
    index_html = DIST_INDEX.read_bytes()
    with sandbox("m1f1-ra7-ui") as box:
        seed_account(box)
        with web_server(box, account_ui_command(0), ready_path="/account",
                        scenario=ACCOUNT_SCENARIO) as server:
            page = server.request("GET", "/account")
            assert page.status == 200
            assert page.body == index_html

            runs = server.request("GET", "/api/account/runs?source=v2").json()
            assert runs["count"] == 1
            assert floats_close(runs["data"][0]["final_equity"], 99495.199,
                                rel=0, abs_tol=1e-6)
            summary = server.request("GET", "/api/summary").json()
            assert summary["source"] == "v2"
        assert "账户级回测 [v2]" in server.stdout
        assert "run_id: 1" in server.stdout
        assert server.returncode is not None
        assert port_is_free(server.port)


def test_ra7_review_account_ui_only_needs_no_backtest():
    index_html = DIST_INDEX.read_bytes()
    with sandbox("m1f1-ra7-ui-only") as box:
        seed_account(box)
        with web_server(box, server_command(REVIEW_ACCOUNT_UI, 0),
                        ready_path="/account") as server:
            assert server.request("GET", "/account").body == index_html
            assert server.request("GET", "/api/summary").json()["source"] == "v2"
            assert server.request("GET", "/api/account/runs").json()["count"] == 0
            # ``--ui-only`` must not create a run as a side effect.
            assert sqlite_count(box.data_dir / "dragon.db",
                                "review_account_runs") == 0
        # ``--ui-only`` must not run the backtest at all.
        assert "账户级回测" not in server.stdout
        assert server.returncode is not None
        assert port_is_free(server.port)
