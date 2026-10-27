"""M1E2 review-account behavior baseline: RA1–RA8 (RA7 is M1F Web).

Real ``python -m dragon_quant review-account`` subprocesses against the M0
sandbox.  The synthetic provider payload is equivalent to the unit-test
``dragon_quant.tests.test_review_account.FakeData`` (weekday calendar, flat 10.00 daily
bars, 48 five-minute bars per day, 9.50 dip on 2026-09-08) so the reviewed
golden ``final_equity == 99495.199`` is reproduced through the real CLI,
``MarketData`` and a temporary SQLite database.

Coverage notes (not silently skipped):

* RA6 ``data_quality`` is **not exposed** by the ``review-account`` CLI print
  path (only ``buy``/``sell`` print it); the observable parts covered here are
  the suspended/degraded notes plus the unchanged golden through a daily
  fallback day.  ``data_quality`` itself is asserted in ``test_buy_sell.py``.
* RA7 ``--ui`` / ``--ui-only`` starts a real HTTP server and is covered by the
  M1F Web cases in ``test_web_api.py`` (``test_ra7_*``); this module keeps no
  placeholder skip.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path

import pytest

from dragon_quant.tests.e2e.fixtures import seed_builders as sb
from dragon_quant.tests.e2e.harness.assertlib import sqlite_rows
from dragon_quant.tests.e2e.harness.canonical import floats_close
from dragon_quant.tests.e2e.harness.sandbox import EnvSandbox
from dragon_quant.tests.e2e.harness.seed import seed_db

E2E_ROOT = Path(__file__).resolve().parent
SCENARIO = E2E_ROOT / "fixtures" / "scenario_account.json"
GOLDEN = E2E_ROOT / "golden" / "review_account"
NOW = "2026-09-11T15:30:00+08:00"
ACCOUNT_TABLES = (
    "review_account_runs",
    "review_account_snapshots",
    "review_account_trades",
    "review_account_positions",
    "review_account_events",
)


def runner():
    from dragon_quant.tests.e2e.harness import runner as module

    return module


@contextmanager
def sandbox(case_id: str, *, now: str = NOW):
    box = EnvSandbox.for_case(case_id, now=now)
    try:
        yield box
    finally:
        box.cleanup()


def run_account(box: EnvSandbox, args: list[str], *,
                scenario: Path = SCENARIO, timeout: int = 120,
                clock_mode: str = "runtime"):
    overrides = {"DQ_E2E_SCENARIO": str(scenario),
                 "DQ_E2E_CLOCK_MODE": clock_mode}
    return runner().run_cli(
        args, sandbox=box, timeout=timeout, env_override=overrides)


def golden_json(name: str):
    return json.loads((GOLDEN / name).read_text(encoding="utf-8"))


def golden_text(name: str) -> str:
    return (GOLDEN / name).read_text(encoding="utf-8")


# ─── seeds ──────────────────────────────────────────────────────────────────


def prep_snapshot_table(trade_date: str, position_pct: float,
                        candidates: list[dict]) -> dict:
    payload = {
        "trade_date": trade_date,
        "version": "0.6",
        "sentiment": {"position_pct": position_pct,
                      "position_advice": "正常", "degraded": []},
        "main_boards": [],
        "macro": {"available": False},
        "candidates": list(candidates),
        "dragon_source": {"window_days": 5, "dates": []},
    }
    return {"tables": {"prep_snapshots": {
        "columns": {"trade_date": "TEXT NOT NULL", "source": "TEXT NOT NULL",
                    "result_json": "TEXT NOT NULL", "created_at": "TEXT"},
        "primary_key": ["trade_date", "source"],
        "rows": [{"trade_date": trade_date, "source": "v2",
                  "result_json": json.dumps(payload, ensure_ascii=False,
                                            sort_keys=True),
                  "created_at": f"{trade_date} 16:45:00"}],
    }}}


def seed_account(box: EnvSandbox, *, dragons: list[dict] | None = None,
                 snapshot: dict | None = None) -> None:
    rows = dragons if dragons is not None else [account_dragon()]
    spec = {"tables": dict(sb.mirror_db_seed(rows)["tables"])}
    if snapshot is not None:
        spec["tables"].update(snapshot["tables"])
    seed_db(box, spec, db_name="assets.db")


def account_dragon(trade_date: str = "2026-09-04") -> dict:
    return sb.dragon_row(trade_date, "600001", "样本", 1, 80.0, ["AI算力"])


def write_variant(box: EnvSandbox, name: str, mutate) -> Path:
    payload = json.loads(SCENARIO.read_text(encoding="utf-8"))
    mutate(payload)
    path = box.root / name
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _day_of(timestamp: int) -> str:
    import datetime

    return datetime.datetime.fromtimestamp(
        timestamp / 1000,
        datetime.timezone(datetime.timedelta(hours=8))).date().isoformat()


def drop_bars(payload: dict, *, daily: set[str] = frozenset(),
              five_min: set[str] = frozenset()) -> None:
    xueqiu = payload["providers"]["xueqiu"]
    for code, bars in xueqiu["get_kline"]["by_code"].items():
        if code == "SH000001":
            continue  # the index calendar must stay complete
        xueqiu["get_kline"]["by_code"][code] = [
            bar for bar in bars if _day_of(bar["timestamp"]) not in daily]
    for code, bars in xueqiu["get_5min_kline_for"]["by_code"].items():
        xueqiu["get_5min_kline_for"]["by_code"][code] = [
            bar for bar in bars if _day_of(bar["timestamp"]) not in five_min]


# ─── read helpers ──────────────────────────────────────────────────────────


def table_counts(box: EnvSandbox) -> dict[str, int]:
    import sqlite3

    db = box.data_dir / "dragon.db"
    counts = {}
    for table in ACCOUNT_TABLES:
        try:
            counts[table] = len(sqlite_rows(db, table))
        except sqlite3.Error:
            # RA3/RA4 legitimately stop before the schema/table exists.
            counts[table] = 0
    return counts


def review_fields(box: EnvSandbox) -> dict:
    db = box.data_dir / "dragon.db"
    runs = sqlite_rows(db, "review_account_runs")
    run = runs[0]
    projections = {
        "run": {
            "source": run["source"],
            "strategy_name": run["strategy_name"],
            "date_from": run["date_from"],
            "date_to": run["date_to"],
            "initial_cash": run["initial_cash"],
            "final_equity": run["final_equity"],
            "total_return": run["total_return"],
            "max_drawdown": run["max_drawdown"],
            "trade_count": run["trade_count"],
            "win_rate": run["win_rate"],
        },
        "snapshots": [],
        "trades": [],
        "positions": [],
        "events": [],
    }
    for row in sqlite_rows(db, "review_account_snapshots",
                           order_by="trade_date"):
        position = json.loads(row["positions_json"] or "[]")
        projections["snapshots"].append({
            "trade_date": row["trade_date"], "cash": row["cash"],
            "market_value": row["market_value"],
            "total_equity": row["total_equity"],
            "daily_return": row["daily_return"],
            "cumulative_return": row["cumulative_return"],
            "drawdown": row["drawdown"],
            "position_code": row["position_code"],
            "position_name": row["position_name"],
            "position_qty": row["position_qty"],
            "position_cost": row["position_cost"],
            "position_market_price": row["position_market_price"],
            "position_unrealized_return": row["position_unrealized_return"],
            "position": [{key: item.get(key) for key in
                          ("code", "qty", "entry_date", "entry_price",
                           "took_profit_half")} for item in position],
        })
    for row in sqlite_rows(db, "review_account_trades", order_by="id"):
        projections["trades"].append({
            key: row[key] for key in
            ("trade_date", "code", "name", "side", "price", "qty", "amount",
             "fee", "realized_pnl", "cash_after", "position_after",
             "reason_code", "reason_text")})
    for row in sqlite_rows(db, "review_account_positions", order_by="id"):
        projections["positions"].append({
            key: row[key] for key in
            ("code", "name", "entry_date", "entry_price", "qty",
             "entry_reason_code", "exit_date", "exit_price",
             "exit_reason_code", "hold_days", "status", "realized_return",
             "post_exit_max_return", "post_exit_max_drawdown",
             "post_exit_window_days")})
    for row in sqlite_rows(db, "review_account_events", order_by="id"):
        signal = json.loads(row["signal_json"] or "{}")
        projections["events"].append({
            "event_date": row["event_date"], "event_type": row["event_type"],
            "code": row["code"], "title": row["title"],
            "reason_code": row["reason_code"], "cash": row["cash"],
            "total_equity": row["total_equity"],
            "ranking_mode": signal.get("ranking_mode"),
            "pool_source": signal.get("pool_source"),
            "fallback_reason": signal.get("fallback_reason"),
        })
    return projections


def assert_numeric_mapping(actual: dict, expected: dict, *,
                           abs_tol: float = 1e-9) -> None:
    for key, want in expected.items():
        got = actual[key]
        if isinstance(want, bool) or isinstance(want, str) or want is None:
            assert got == want, (key, got, want)
        else:
            assert floats_close(float(got), float(want), rel=0,
                                abs_tol=abs_tol), (key, got, want)


# ─── RA1 golden ────────────────────────────────────────────────────────────


def test_ra1_golden_equity_tables_and_post_exit():
    golden = golden_json("golden.json")
    with sandbox("m1e2-ra1") as box:
        seed_account(box)
        result = run_account(
            box, ["review-account", "--from", "20260904", "--to", "20260908"])
        assert result.returncode == 0, result.stderr
        assert result.stderr == ""
        assert result.stdout == golden_text("stdout.txt")

        assert table_counts(box) == {
            "review_account_runs": 1,
            "review_account_snapshots": 3,
            "review_account_trades": 2,
            "review_account_positions": 1,
            "review_account_events": 3,
        }
        fields = review_fields(box)
        assert_numeric_mapping(fields["run"], golden["run"], abs_tol=1e-6)
        assert floats_close(fields["run"]["final_equity"], 99495.199,
                            rel=0, abs_tol=1e-6)
        assert fields["run"]["trade_count"] == 2

        assert len(fields["snapshots"]) == 3
        for got, want in zip(fields["snapshots"], golden["snapshots"]):
            assert got["trade_date"] == want["trade_date"]
            for key in ("cash", "market_value", "total_equity",
                        "daily_return", "cumulative_return", "drawdown",
                        "position_unrealized_return"):
                if want[key] is None:
                    assert got[key] is None
                else:
                    assert floats_close(got[key], want[key], rel=0,
                                        abs_tol=1e-6), (key, got, want)
            for key in ("position_code", "position_name", "position_qty",
                        "position_cost", "position_market_price"):
                assert got[key] == want[key], (key, got, want)
            assert got["position"] == want["position"]

        assert len(fields["trades"]) == 2
        for got, want in zip(fields["trades"], golden["trades"]):
            for key in ("trade_date", "code", "name", "side", "qty",
                        "position_after", "reason_code", "reason_text"):
                assert got[key] == want[key], (key, got, want)
            for key in ("price", "amount", "fee", "realized_pnl", "cash_after"):
                assert floats_close(got[key], want[key], rel=0,
                                    abs_tol=1e-9), (key, got, want)

        assert len(fields["positions"]) == 1
        position = fields["positions"][0]
        assert_numeric_mapping(position, golden["positions"][0], abs_tol=1e-6)
        assert position["post_exit_window_days"] == 3
        assert floats_close(position["post_exit_max_return"],
                            (10.2 - 9.98) / 9.98 * 100, rel=0, abs_tol=1e-9)
        assert floats_close(position["post_exit_max_drawdown"],
                            (9.8 - 9.98) / 9.98 * 100, rel=0, abs_tol=1e-9)

        assert [event["event_type"] for event in fields["events"]] == [
            "IDLE", "BUY", "SELL"]
        for got, want in zip(fields["events"], golden["events"]):
            assert_numeric_mapping(got, want, abs_tol=1e-6)


def test_ra2_config_legal_changes_position_sizing():
    golden = golden_json("config_legal.json")
    with sandbox("m1e2-ra2") as box:
        seed_account(box)
        config = box.root / "strategy.json"
        config.write_text(json.dumps({"max_position_pct": 50}),
                          encoding="utf-8")
        result = run_account(
            box, ["review-account", "--from", "20260904", "--to", "20260908",
                  "--config", str(config)])
        assert result.returncode == 0, result.stderr
        run = review_fields(box)["run"]
        assert_numeric_mapping(run, golden["run"], abs_tol=1e-6)
        assert floats_close(run["final_equity"], 99750.149, rel=0,
                            abs_tol=1e-6)
        assert run["final_equity"] != pytest.approx(99495.199, abs=1e-6)
        trades = sqlite_rows(box.data_dir / "dragon.db",
                             "review_account_trades", order_by="id")
        assert [(t["side"], t["qty"]) for t in trades] == [
            ("BUY", 4900), ("SELL", 4900)]
        params = json.loads(
            sqlite_rows(box.data_dir / "dragon.db",
                        "review_account_runs")[0]["strategy_params_json"])
        assert params["max_position_pct"] == 50


def test_ra3_config_invalid_exit_1_without_persistence():
    with sandbox("m1e2-ra3") as box:
        seed_account(box)
        config = box.root / "bad.json"
        config.write_text(json.dumps({"bogus_parameter": 1}),
                          encoding="utf-8")
        result = run_account(
            box, ["review-account", "--from", "20260904", "--to", "20260908",
                  "--config", str(config)])
        assert result.returncode == 1
        assert "ValueError: 未知策略参数: bogus_parameter" in result.stderr
        assert table_counts(box)["review_account_runs"] == 0


def test_ra4_missing_dates_exit_0_no_write():
    with sandbox("m1e2-ra4") as box:
        result = run_account(box, ["review-account"])
        assert result.returncode == 0
        assert result.stdout == ""
        assert ("错误: review-account 需要 --from 和 --to，或使用 --ui-only"
                in result.stderr)
        assert table_counts(box) == {table: 0 for table in ACCOUNT_TABLES}


def test_ra5_missing_daily_exit_1_without_partial_rows():
    def remove_daily(payload):
        payload["providers"]["xueqiu"]["get_kline"]["by_code"]["600001"] = []

    with sandbox("m1e2-ra5") as box:
        seed_account(box)
        scenario = write_variant(box, "missing_daily.json", remove_daily)
        result = run_account(
            box, ["review-account", "--from", "20260904", "--to", "20260908"],
            scenario=scenario)
        assert result.returncode == 1
        assert "DataCoverageError: 600001 缺少日K" in result.stderr
        assert table_counts(box) == {table: 0 for table in ACCOUNT_TABLES}


def test_ra6_suspended_day_is_reported_not_silent():
    def suspend(payload):
        drop_bars(payload, daily={"2026-09-07"}, five_min={"2026-09-07"})

    with sandbox("m1e2-ra6-suspended") as box:
        seed_account(box)
        scenario = write_variant(box, "suspended.json", suspend)
        result = run_account(
            box, ["review-account", "--from", "20260907", "--to", "20260908"],
            scenario=scenario)
        assert result.returncode == 0, result.stderr
        assert "停牌跳过: 1 只 / 1 天（600001）" in result.stdout
        assert "⚠️ 2026-09-07 停牌跳过 1 只：600001" in result.stdout
        fields = review_fields(box)
        assert_numeric_mapping(fields["run"], golden_json("suspended.json")["run"],
                               abs_tol=1e-6)


def test_ra6_daily_fallback_day_keeps_golden_equity():
    def fallback(payload):
        drop_bars(payload, five_min={"2026-09-07"})

    with sandbox("m1e2-ra6-fallback") as box:
        seed_account(box)
        scenario = write_variant(box, "fallback.json", fallback)
        result = run_account(
            box, ["review-account", "--from", "20260904", "--to", "20260908"],
            scenario=scenario)
        assert result.returncode == 0, result.stderr
        run = review_fields(box)["run"]
        assert_numeric_mapping(run, golden_json("golden.json")["run"],
                               abs_tol=1e-6)


# ─── RA8 ranking mode / fallback ───────────────────────────────────────────


def test_ra8_legacy_ranking_and_fallback_are_visible():
    with sandbox("m1e2-ra8-legacy") as box:
        seed_account(box)
        result = run_account(
            box, ["review-account", "--from", "20260904", "--to", "20260908"])
        assert result.returncode == 0, result.stderr
        assert ("排序模式: legacy（无 T-1 prep 快照 → 3 日真龙兜底）"
                in result.stdout)
        assert "候选池回退: 3 天（no_snapshot）" in result.stdout
        events = review_fields(box)["events"]
        for event in events:
            assert event["ranking_mode"] == "legacy"
            assert event["pool_source"] == "legacy"
            assert event["fallback_reason"] == "no_snapshot"


def test_ra8_prep_snapshot_pool_uses_prep_only_then_mixed():
    candidate = {**account_dragon(), "verdict": "strong",
                 "verdict_label": "强烈建议", "ignore_reason": None}
    with sandbox("m1e2-ra8-prep") as box:
        seed_account(box, dragons=[], snapshot=prep_snapshot_table(
            "2026-09-04", 0.5, [candidate]))
        result = run_account(
            box, ["review-account", "--from", "20260907", "--to", "20260908"])
        assert result.returncode == 0, result.stderr
        assert "排序模式: mixed" in result.stdout
        assert "候选池回退: 1 天（no_snapshot）" in result.stdout
        events = {event["event_date"]: event
                  for event in review_fields(box)["events"]}
        assert events["2026-09-07"]["ranking_mode"] == "prep_only"
        assert events["2026-09-07"]["pool_source"] == "prep"
        assert events["2026-09-08"]["ranking_mode"] == "legacy"
        assert events["2026-09-08"]["fallback_reason"] == "no_snapshot"


# ─── RA9: dual-mode clock (M9B1) ───────────────────────────────────────────


def test_ra9_runtime_clock_mode_excludes_intraday_today():
    """Runtime mode at 10:00: today's bar is not closed → 09-11 excluded."""
    with sandbox("ra9", now="2026-09-11T10:00:00+08:00") as box:
        seed_account(box)
        result = run_account(
            box, ["review-account", "--from", "20260907", "--to", "20260911"],
            clock_mode="runtime")
        assert result.returncode == 0, result.stderr
        dates = [row["trade_date"] for row in sqlite_rows(
            box.data_dir / "dragon.db", "review_account_snapshots",
            order_by="trade_date")]
        assert dates == ["2026-09-07", "2026-09-08", "2026-09-09",
                         "2026-09-10"]
