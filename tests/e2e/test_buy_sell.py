"""M1E2 live-trade behavior baseline: BU1–BU11 and SE1–SE6.

Every case runs the real ``python -m dragon_quant buy|sell`` subprocess in the
M0 sandbox.  Provider data is the reviewed ``scenario_account.json`` fixture;
the account is a temporary ``dragon.db``.  ``created_at`` / ``updated_at`` are
wall-clock columns and are deliberately not pinned.

Two contract notes (basis facts, not silent gaps):

* BU2 "未落库" is not reachable through the CLI: when the existing signal's
  ``fill_timestamp`` is already before ``--at``, the shadow state marks the
  name as held, so the second run is idempotent without re-inserting.  The test
  pins that real behaviour (one row, ``already_holding``).
* BU10 sentiment ban is a candidate-level rejection: ``details[].reason_code
  == "sentiment_ban"`` with four ``skipped`` checks.  ``buy_eval_skipped`` is
  reserved for event-level gates (risk / sold-today), so it stays empty.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path

from tests.e2e.fixtures import seed_builders as sb
from tests.e2e.harness.assertlib import assert_json_keys_in_order, sqlite_rows
from tests.e2e.harness.canonical import floats_close
from tests.e2e.harness.sandbox import EnvSandbox
from tests.e2e.harness.seed import seed_db
from tests.e2e.test_review_account import (  # reviewed shared helpers
    account_dragon,
    drop_bars,
    prep_snapshot_table,
    seed_account,
    write_variant,
)

E2E_ROOT = Path(__file__).resolve().parent
SCENARIO = E2E_ROOT / "fixtures" / "scenario_account.json"
GOLDEN = E2E_ROOT / "golden" / "live_trade"
NOW = "2026-09-11T15:30:00+08:00"
MIDDAY = "2026-09-11T12:00:00+08:00"
CLOSED = "2026-09-11T16:00:00+08:00"
GRACE = "2026-09-11T15:05:00+08:00"
PRE_OPEN = "2026-09-11T09:15:00+08:00"
MAIN_ARGS = ["--date", "20260907", "--at", "10:00"]

BUY_TOP_KEYS = [
    "ok", "mode", "action", "buys", "details", "sentiment_pct",
    "data_quality", "degraded", "suspended_codes", "unavailable_codes",
    "fired_gate", "buy_eval_skipped", "ranking_mode", "pool_source",
    "fallback_reason", "pool_empty", "prep_snapshot_date", "auction_rows",
    "priority_degraded", "reason_text", "forced",
]
CHECK_KEYS = ["rule", "label", "window", "status", "selected", "reason"]
CHECK_STATUSES = {"passed", "failed", "pending", "window_closed",
                  "unavailable", "skipped"}

SIGNAL_COLUMNS = {
    "id": "INTEGER",
    "code": "TEXT NOT NULL", "name": "TEXT",
    "source": "TEXT", "entry_date": "TEXT NOT NULL",
    "entry_price": "REAL", "entry_reason_code": "TEXT",
    "entry_reason_text": "TEXT", "entry_signal_json": "TEXT",
    "highest_return": "REAL", "highest_price": "REAL",
    "took_profit_half": "INTEGER",
    "status": "TEXT", "exit_date": "TEXT",
    "exit_price": "REAL", "exit_reason_code": "TEXT",
    "exit_reason_text": "TEXT", "exit_signal_json": "TEXT",
    "hold_days": "INTEGER", "created_at": "TEXT", "updated_at": "TEXT",
    "qty": "INTEGER", "half_exit_date": "TEXT", "half_exit_price": "REAL",
    "half_exit_qty": "INTEGER", "half_exit_reason_code": "TEXT",
    "half_exit_reason_text": "TEXT", "half_exit_signal_json": "TEXT",
}


def runner():
    from tests.e2e.harness import runner as module

    return module


@contextmanager
def sandbox(case_id: str, *, now: str = NOW):
    box = EnvSandbox.for_case(case_id, now=now)
    try:
        yield box
    finally:
        box.cleanup()


def run_cli(box: EnvSandbox, args: list[str], *,
            scenario: Path = SCENARIO, timeout: int = 120,
            clock_mode: str = "runtime"):
    overrides = {"DQ_E2E_SCENARIO": str(scenario),
                 "DQ_E2E_CLOCK_MODE": clock_mode}
    return runner().run_cli(
        args, sandbox=box, timeout=timeout, env_override=overrides)


def seed_sentiment(box: EnvSandbox, position_pct: float) -> None:
    seed_account(box, snapshot=prep_snapshot_table(
        "2026-09-04", position_pct, []))


def signals(box: EnvSandbox) -> list[dict]:
    return sqlite_rows(box.data_dir / "dragon.db", "buy_sell_signals",
                       order_by="id")


def golden(name: str):
    return json.loads((GOLDEN / name).read_text(encoding="utf-8"))


def signal_seed(signal: dict | None = None) -> dict:
    row = signal or {
        "id": 1, "code": "600001", "name": "样本", "source": "v2",
        "entry_date": "2026-09-07", "entry_price": 10.02,
        "entry_reason_code": "buy_open_ma5_pullback",
        "entry_reason_text": "开盘贴近上日MA5，回踩承接",
        "entry_signal_json": json.dumps({
            "date": "2026-09-07", "phase": "open",
            "fill_timestamp": 1788744600001, "execution_price": 10.0},
            ensure_ascii=False),
        "highest_return": 0.29940119760480943, "highest_price": 10.05,
        "took_profit_half": 0, "status": "open",
    }
    return {"tables": {"buy_sell_signals": {
        "columns": SIGNAL_COLUMNS, "primary_key": ["id"], "rows": [row],
    }}}


def write_config(box: EnvSandbox, name: str, payload: dict) -> str:
    path = box.root / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


# ─── BU1 / BU2 ─────────────────────────────────────────────────────────────


def test_bu1_buy_success_sentiment_checks_and_signal_row():
    with sandbox("m1e2-bu1") as box:
        seed_sentiment(box, 0.6)
        result = run_cli(box, ["buy", *MAIN_ARGS])
        assert result.returncode == 0, result.stderr
        lines = result.stdout.splitlines()
        assert lines[0] == "【买入建议 · 2026-09-07】"
        assert "  建议仓位 60%（盘后复盘情绪温度，仅参考不代下单）" in lines
        assert "🟢 买入 样本（600001）@10.02" in result.stdout
        for text in ("① 开盘回踩MA5", "② 弱转强", "③ 分歧买龙", "④ 龙回头"):
            assert text in result.stdout

        rows = signals(box)
        assert len(rows) == 1
        row = rows[0]
        want = golden("signal_open.json")["signal"]
        for key in ("code", "name", "source", "entry_date", "status",
                    "entry_reason_code", "qty", "took_profit_half"):
            assert row[key] == want[key], (key, row[key], want[key])
        for key in ("entry_price", "highest_return", "highest_price"):
            assert floats_close(row[key], want[key], rel=0, abs_tol=1e-9)
        entry = json.loads(row["entry_signal_json"])
        assert entry["fill_timestamp"] == 1788744600001
        assert entry["execution_price"] == 10.0


def test_bu2_second_buy_run_is_idempotent():
    with sandbox("m1e2-bu2") as box:
        seed_account(box)
        assert run_cli(box, ["buy", *MAIN_ARGS]).returncode == 0
        assert len(signals(box)) == 1
        second = run_cli(box, ["buy", *MAIN_ARGS])
        assert second.returncode == 0, second.stderr
        assert "其他候选（1 只）：" in second.stdout
        assert "已买入" in second.stdout
        assert len(signals(box)) == 1


# ─── BU3–BU8 time gates and empty pool ─────────────────────────────────────


def test_bu3_no_candidate_exit_0():
    with sandbox("m1e2-bu3") as box:
        result = run_cli(box, ["buy", *MAIN_ARGS, "--json"])
        assert result.returncode == 0, result.stderr
        payload = json.loads(result.stdout)
        assert payload["action"] == "idle"
        assert payload["buys"] == []
        assert payload["pool_empty"] is True
        assert payload["reason_text"] == "候选池暂无触发买点的标的"
        assert not (box.data_dir / "dragon.db").exists() or signals(box) == []


def test_bu4_historical_without_at_exit_1():
    with sandbox("m1e2-bu4") as box:
        result = run_cli(box, ["buy", "--date", "20260907"])
        assert result.returncode == 1
        assert "⏸ 暂不执行买入：历史日期必须提供 --at HH:MM" in result.stderr


def test_bu5_before_0930_rejected_even_with_force():
    for force in ([], ["--force"]):
        with sandbox(f"m1e2-bu5-{bool(force)}", now=PRE_OPEN) as box:
            result = run_cli(box, ["buy", *force])
            assert result.returncode == 1
            assert "当前尚未开盘（09:30 开始）" in result.stderr


def test_bu6_midday_force_replays_and_marks_signal():
    with sandbox("m1e2-bu6-mid", now=MIDDAY) as box:
        seed_account(box, dragons=[account_dragon("2026-09-10")])
        result = run_cli(box, ["buy", "--force"])
        assert result.returncode == 0, result.stderr
        assert "⚠️ 强制模式：当前非交易时段，已回放至最后一个完整 5 分钟 K" in result.stdout
        assert "🟢 买入 样本（600001）@10.02" in result.stdout
        rows = signals(box)
        assert len(rows) == 1
        entry = json.loads(rows[0]["entry_signal_json"])
        assert entry["forced"] is True


def test_bu6_closed_force_json_reports_forced():
    with sandbox("m1e2-bu6-close", now=CLOSED) as box:
        result = run_cli(box, ["buy", "--force", "--json"])
        assert result.returncode == 0, result.stderr
        payload = json.loads(result.stdout)
        assert payload["forced"] is True
        assert payload["action"] == "idle"


def test_bu7_1500_1506_grace_needs_no_force():
    with sandbox("m1e2-bu7", now=GRACE) as box:
        result = run_cli(box, ["buy"])
        assert result.returncode == 0, result.stderr
        assert "强制模式" not in result.stdout
        assert "【买入建议 · 2026-09-11】" in result.stdout


def test_bu8_at_wins_over_force():
    with sandbox("m1e2-bu8", now=MIDDAY) as box:
        seed_account(box)
        result = run_cli(box, ["buy", *MAIN_ARGS, "--force", "--json"])
        assert result.returncode == 0, result.stderr
        payload = json.loads(result.stdout)
        assert payload["forced"] is False
        assert [buy["code"] for buy in payload["buys"]] == ["600001"]
        entry = json.loads(signals(box)[0]["entry_signal_json"])
        assert "forced" not in entry


# ─── BU9 JSON checks ───────────────────────────────────────────────────────


def test_bu9_json_keys_and_four_checks():
    with sandbox("m1e2-bu9") as box:
        seed_account(box)
        result = run_cli(box, ["buy", *MAIN_ARGS, "--json"])
        assert result.returncode == 0, result.stderr
        payload = json.loads(result.stdout)
        assert_json_keys_in_order(payload, BUY_TOP_KEYS)
        assert set(payload) == set(BUY_TOP_KEYS)

        open_detail = next(
            detail for detail in payload["details"]
            if detail["code"] == "600001"
            and detail["reason_code"] == "buy_open_ma5_pullback"
            and detail["phase"] == "open")
        checks = open_detail["checks"]
        assert len(checks) == 4
        for check in checks:
            assert_json_keys_in_order(check, CHECK_KEYS)
            assert check["status"] in CHECK_STATUSES
        assert [check["status"] for check in checks] == [
            "passed", "pending", "pending", "failed"]
        assert checks[0]["selected"] is True
        assert [check["rule"] for check in checks] == [
            "buy_open_ma5_pullback", "buy_open_turn_strong",
            "buy_divergence_first_break", "buy_dragon_comeback"]
        assert checks == golden("buy_checks.json")["checks"]
        assert [buy["code"] for buy in payload["buys"]] == ["600001"]


# ─── BU10 / BU11 and data quality ──────────────────────────────────────────


def test_bu10_sentiment_ban_rejects_before_pattern_rules():
    with sandbox("m1e2-bu10") as box:
        seed_sentiment(box, 0.0)
        config = write_config(box, "ban.json", {"sentiment_ban_enabled": True})
        result = run_cli(box, ["buy", *MAIN_ARGS, "--json", "--config", config])
        assert result.returncode == 0, result.stderr
        payload = json.loads(result.stdout)
        assert payload["sentiment_pct"] == 0.0
        assert payload["buys"] == []
        assert payload["buy_eval_skipped"] == []
        ban = next(detail for detail in payload["details"]
                   if detail["reason_code"] == "sentiment_ban")
        assert ban["reason_text"] == "情绪空仓，禁买"
        assert [check["status"] for check in ban["checks"]] == ["skipped"] * 4
        assert all("情绪空仓，禁买" in check["reason"]
                   for check in ban["checks"])
        assert not (box.data_dir / "dragon.db").exists() or signals(box) == []


def test_bu11_suspended_candidate_is_reported():
    def suspend(payload):
        drop_bars(payload, daily={"2026-09-07"}, five_min={"2026-09-07"})

    with sandbox("m1e2-bu11") as box:
        seed_account(box)
        scenario = write_variant(box, "suspended.json", suspend)
        result = run_cli(box, ["buy", *MAIN_ARGS, "--json"], scenario=scenario)
        assert result.returncode == 0, result.stderr
        payload = json.loads(result.stdout)
        assert payload["suspended_codes"] == ["600001"]
        assert payload["unavailable_codes"] == {}
        assert payload["data_quality"] == "strict_5min"
        assert payload["degraded"] == [
            "停牌跳过 1 只：600001（当日不评估，持仓冻结）"]
        assert payload["buys"] == []
        assert not (box.data_dir / "dragon.db").exists() or signals(box) == []


def test_bu_data_quality_daily_fallback_is_observable():
    def fallback(payload):
        drop_bars(payload, five_min={"2026-09-07"})

    with sandbox("m1e2-bu-fallback") as box:
        seed_account(box)
        scenario = write_variant(box, "fallback.json", fallback)
        result = run_cli(box, ["buy", *MAIN_ARGS, "--json"], scenario=scenario)
        assert result.returncode == 0, result.stderr
        payload = json.loads(result.stdout)
        assert payload["data_quality"] == "daily_fallback"
        assert [buy["code"] for buy in payload["buys"]] == ["600001"]


# ─── SE1–SE6 ───────────────────────────────────────────────────────────────


def test_se1_sell_closes_signal_with_reason_and_hold_days():
    with sandbox("m1e2-se1") as box:
        seed_account(box)
        assert run_cli(box, ["buy", *MAIN_ARGS]).returncode == 0
        result = run_cli(box, ["sell", "--date", "20260908", "--at", "10:00"])
        assert result.returncode == 0, result.stderr
        assert "🔴 卖出 样本（600001）@9.98，持有 1 天" in result.stdout
        want = golden("signal_closed.json")["signal"]
        row = signals(box)[0]
        for key in ("status", "exit_date", "exit_reason_code", "hold_days"):
            assert row[key] == want[key], (key, row[key], want[key])
        assert floats_close(row["exit_price"], want["exit_price"], rel=0,
                            abs_tol=1e-9)


def test_se2_half_then_full_close():
    with sandbox("m1e2-se2") as box:
        seed_account(box)
        assert run_cli(box, ["buy", *MAIN_ARGS]).returncode == 0
        config = write_config(box, "first_day.json",
                              {"sell_rule_toggles": {"first_day_stop": True}})

        first = run_cli(box, ["sell", "--date", "20260908", "--at", "10:00",
                              "--config", config])
        assert first.returncode == 0, first.stderr
        assert "🔴 减半仓 样本（600001）@9.98，持有 1 天" in first.stdout
        assert "🟢 继续持有 样本（600001）（已减半仓）" in first.stdout
        half = signals(box)[0]
        want = golden("half_open.json")["signal"]
        for key in ("status", "took_profit_half", "half_exit_date",
                    "half_exit_qty", "half_exit_reason_code"):
            assert half[key] == want[key], (key, half[key], want[key])
        assert floats_close(half["half_exit_price"], want["half_exit_price"],
                            rel=0, abs_tol=1e-9)

        second = run_cli(box, ["sell", "--date", "20260909", "--at", "10:00",
                               "--config", config])
        assert second.returncode == 0, second.stderr
        assert "🔴 卖出 样本（600001）@9.98，持有 2 天" in second.stdout
        closed = signals(box)[0]
        want = golden("half_closed.json")["signal"]
        for key in ("status", "took_profit_half", "exit_date",
                    "exit_reason_code", "hold_days", "half_exit_qty"):
            assert closed[key] == want[key], (key, closed[key], want[key])


def test_se3_no_position_exit_0():
    with sandbox("m1e2-se3") as box:
        result = run_cli(box, ["sell", "--date", "20260907", "--at", "10:00"])
        assert result.returncode == 0, result.stderr
        assert "持仓暂无卖出信号" in result.stdout


def test_se4_time_gates_match_buy():
    with sandbox("m1e2-se4-reject", now=MIDDAY) as box:
        rejected = run_cli(box, ["sell"])
        assert rejected.returncode == 1
        assert "当前午间休市（11:30–13:00）" in rejected.stderr
    with sandbox("m1e2-se4-force", now=MIDDAY) as box:
        forced = run_cli(box, ["sell", "--force"])
        assert forced.returncode == 0, forced.stderr
        assert "⚠️ 强制模式：当前非交易时段，已回放至最后一个完整 5 分钟 K" in forced.stdout
    with sandbox("m1e2-se4-early", now=PRE_OPEN) as box:
        early = run_cli(box, ["sell", "--force"])
        assert early.returncode == 1
        assert "当前尚未开盘（09:30 开始）" in early.stderr


def test_se5_suspended_position_is_frozen():
    def suspend(payload):
        drop_bars(payload, daily={"2026-09-08"}, five_min={"2026-09-08"})

    with sandbox("m1e2-se5") as box:
        seed_db(box, signal_seed())
        seed_db(box, sb.mirror_db_seed([account_dragon()]),
                db_name="assets.db")
        scenario = write_variant(box, "suspended.json", suspend)
        result = run_cli(box, ["sell", "--date", "20260908", "--at", "10:00"],
                         scenario=scenario)
        assert result.returncode == 0, result.stderr
        assert "停牌跳过 1 只：600001（当日不评估，持仓冻结）" in result.stdout
        row = signals(box)[0]
        assert row["status"] == "open"
        assert row["exit_date"] is None


def test_se6_sell_has_no_json_option():
    with sandbox("m1e2-se6") as box:
        result = run_cli(box, ["sell", "--json"])
        assert result.returncode == 2
        assert "unrecognized arguments: --json" in result.stderr


# ─── BU12 / SE7: dual-mode clock (M9B1) ────────────────────────────────────


def test_bu12_runtime_clock_mode_midday_gate_uses_the_runtime_clock():
    """Runtime-clock mode: ``buy`` must gate on the frozen 12:00, not real now."""
    with sandbox("m1e2-bu12", now=MIDDAY) as box:
        result = run_cli(box, ["buy", "--date", "20260911"],
                         clock_mode="runtime")
        assert result.returncode == 1
        assert "当前午间休市（11:30–13:00）" in result.stderr


def test_se7_runtime_clock_mode_midday_gate_matches_buy():
    with sandbox("m1e2-se7", now=MIDDAY) as box:
        result = run_cli(box, ["sell", "--date", "20260911"],
                         clock_mode="runtime")
        assert result.returncode == 1
        assert "当前午间休市（11:30–13:00）" in result.stderr
