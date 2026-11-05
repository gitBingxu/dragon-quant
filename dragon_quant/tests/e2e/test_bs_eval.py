"""BS 回放 E2E（BS1–BS6）：真实 CLI 子进程 + fake provider + 临时库。

``scenario_bs_eval`` 是确定性买卖触发序列：双口径 / 半仓腿 / partial /
日K兜底 / pending / no_signal / cannot_evaluate。BS6 复用同一个沙箱先跑
``bs-eval``，再启动真实 Web 子进程读取回放结果。
"""

from __future__ import annotations

import json
import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path

from dragon_quant.tests.e2e.fixtures import seed_builders as sb
from dragon_quant.tests.e2e.harness.assertlib import sqlite_rows
from dragon_quant.tests.e2e.harness.sandbox import EnvSandbox
from dragon_quant.tests.e2e.harness.seed import seed_db

E2E_ROOT = Path(__file__).resolve().parent
REPO_ROOT = E2E_ROOT.parents[2]
SCENARIO = E2E_ROOT / "fixtures" / "scenario_bs_eval.json"
DIST_INDEX = REPO_ROOT / "web_ui" / "dist" / "index.html"
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


def seed_dragons(box: EnvSandbox) -> None:
    rows = [
        sb.dragon_row("2026-09-02", "600001", "样本A", 1, 80.0, ["AI算力"]),
        sb.dragon_row("2026-09-02", "600002", "样本B", 2, 70.0, ["机器人"]),
        sb.dragon_row("2026-09-02", "600003", "样本C", 3, 60.0, ["AI算力"]),
        sb.dragon_row("2026-09-02", "600004", "样本D", 4, 55.0, ["机器人"]),
        sb.dragon_row("2026-09-02", "600006", "样本F", 5, 52.0, ["AI算力"]),
        sb.dragon_row("2026-09-09", "600005", "样本E", 1, 85.0, ["机器人"]),
    ]
    seed_db(box, sb.dragon_db_seed(rows))


def run_bs(box: EnvSandbox, args: list[str], *, timeout: int = 120):
    return runner().run_cli(
        ["bs-eval", *args], sandbox=box, timeout=timeout,
        env_override={"DQ_E2E_SCENARIO": str(SCENARIO)},
    )


def signals(box: EnvSandbox) -> dict[str, dict]:
    rows = sqlite_rows(box.data_dir / "dragon.db", "bs_signals",
                       order_by="trade_date, code")
    return {row["code"]: row for row in rows}


def runs(box: EnvSandbox) -> list[dict]:
    return sqlite_rows(box.data_dir / "dragon.db", "bs_runs", order_by="id")


def default_run(box: EnvSandbox, extra: list[str] | None = None):
    return run_bs(box, ["--from", "20260901", "--to", "20260930",
                        *(extra or [])])


# ─── BS1 ───────────────────────────────────────────────────────────────────


def test_bs1_buy_signal_dual_prices_and_metrics():
    with sandbox("bs1") as box:
        seed_dragons(box)
        result = default_run(box)
        assert result.returncode == 0, result.stderr
        assert "BS 回放完成" in result.stdout
        # main_acceptance：CLI 冷启动提示必须出现。
        assert "首次使用建议先 `--limit 5` 试跑" in result.stdout

        row = signals(box)["600001"]
        assert row["eval_status"] == "closed"
        assert row["buy_date"] == "2026-09-03"
        assert row["buy_rule_code"] == "buy_open_ma5_pullback"
        assert row["buy_loose_price"] == 9.8
        assert row["buy_strict_price"] == 10.02
        assert row["buy_loose_max_return_5d"] is not None
        assert row["buy_strict_max_return_5d"] is not None
        assert row["buy_window_partial"] == 0
        assert row["buy_data_quality"] == "strict_5min"
        assert row["data_quality"] == "strict_5min"


# ─── BS2 ───────────────────────────────────────────────────────────────────


def test_bs2_idempotent_terminal_and_force():
    with sandbox("bs2") as box:
        seed_dragons(box)
        first = default_run(box)
        assert first.returncode == 0, first.stderr
        before = signals(box)
        total = len(before)
        assert total == 6

        second = default_run(box, ["--json"])
        assert second.returncode == 0, second.stderr
        payload = json.loads(second.stdout)
        assert payload["counters"]["rows_scanned"] == 6
        assert payload["counters"]["rows_skipped_terminal"] == 3
        assert payload["counters"]["rows_evaluated"] == 3
        after = signals(box)
        assert len(after) == total
        assert after["600001"]["updated_at"] == before["600001"]["updated_at"]
        assert after["600001"]["content_hash"] == before["600001"]["content_hash"]

        forced = default_run(box, ["--force"])
        assert forced.returncode == 0, forced.stderr
        run_rows = runs(box)
        assert len(run_rows) == 3
        assert run_rows[-1]["id"] > run_rows[-2]["id"]
        assert run_rows[-1]["force"] == 1
        final = signals(box)
        assert len(final) == total
        assert final["600001"]["run_id"] == run_rows[-1]["id"]


# ─── BS3 ───────────────────────────────────────────────────────────────────


def test_bs3_pending_no_signal_and_cannot_evaluate():
    with sandbox("bs3") as box:
        seed_dragons(box)
        assert default_run(box).returncode == 0
        rows = signals(box)

        assert rows["600005"]["eval_status"] == "pending"
        assert rows["600005"]["eval_note"] == "buy_window_not_ready"
        assert rows["600005"]["is_terminal"] == 0

        assert rows["600002"]["eval_status"] == "no_signal"
        assert rows["600002"]["is_terminal"] == 1
        assert rows["600002"]["buy_date"] is None

        assert rows["600003"]["eval_status"] == "cannot_evaluate"
        assert rows["600003"]["eval_note"] == "daily_missing"
        assert rows["600003"]["is_terminal"] == 0


# ─── BS4 ───────────────────────────────────────────────────────────────────


def test_bs4_sell_closed_half_leg_and_partial_window():
    with sandbox("bs4") as box:
        seed_dragons(box)
        assert default_run(box).returncode == 0
        rows = signals(box)

        full = rows["600001"]
        assert full["sell_date"] == "2026-09-04"
        assert full["sell_rule_code"] == "first_day_stop_loss"
        assert full["sell_price"] is not None
        assert full["sell_window_partial"] == 0
        assert full["is_terminal"] == 1

        half = rows["600006"]
        assert half["sell_half_date"] == "2026-09-04"
        assert half["sell_half_rule_code"] == "next_day_limit_up_half"
        assert half["sell_half_qty"] is not None
        assert half["sell_date"] == "2026-09-07"
        assert half["sell_window_partial"] == 1
        assert half["is_terminal"] == 0


# ─── BS5 ───────────────────────────────────────────────────────────────────


def test_bs5_daily_fallback_quality_is_observable():
    with sandbox("bs5") as box:
        seed_dragons(box)
        assert default_run(box).returncode == 0
        row = signals(box)["600004"]
        assert row["buy_date"] == "2026-09-03"
        assert row["buy_data_quality"] == "daily_fallback"
        assert row["sell_data_quality"] == "daily_fallback"
        assert row["data_quality"] == "daily_fallback"
        assert row["fallback_days"] > 0
        assert row["replay_days"] == row["fallback_days"] + row["suspended_days"]


# ─── BS6 ───────────────────────────────────────────────────────────────────


def test_bs6_dashboard_serves_bs_api_and_spa():
    from dragon_quant.tests.e2e.harness.web import start_web_server

    index_html = DIST_INDEX.read_bytes()
    with sandbox("bs6") as box:
        seed_dragons(box)
        assert default_run(box).returncode == 0
        command = [sys.executable, "-m", "dragon_quant", "review",
                   "--ui-only", "--no-browser", "--port", "0"]
        server = start_web_server(
            command, sandbox=box, port=0, ready_path="/bs",
            timeout=120.0, env_override={"DQ_E2E_SCENARIO": str(SCENARIO)},
        )
        try:
            overview = server.request("GET", "/api/bs/overview")
            assert overview.status == 200
            payload = overview.json()
            assert payload["run"] is not None
            assert payload["summary"]["rows_total"] == 6
            assert len(payload["strategy"]["buy_signals"]) == 4
            assert len(payload["strategy"]["sell_rules"]) == 10

            buys = server.request("GET", "/api/bs/buys").json()
            assert buys["count"] == 3
            assert {item["code"] for item in buys["data"]} == {
                "600001", "600004", "600006"}

            sells = server.request("GET", "/api/bs/sells").json()
            assert sells["count"] == 3
            assert all("verdict" in item for item in sells["data"])

            page = server.request("GET", "/bs")
            assert page.status == 200
            assert page.body == index_html
            assert page.headers["content-type"].startswith("text/html")
        finally:
            server.stop()
