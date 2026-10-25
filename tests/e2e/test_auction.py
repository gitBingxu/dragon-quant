"""M1D1 auction behavior baseline: A1–A12.

Real ``python -m dragon_quant auction`` subprocesses against the M0 sandbox.
The T-1 prep snapshot, the ``dragons`` mirror and every corrupted row live in
``<sandbox>/data``; the real data directory is never touched.  Goldens under
``tests/e2e/golden/auction`` are reviewed constants.

Deviation discipline: A6 in the approved spec ("scoring failed but the raw
asset was already written") is not reachable through public behavior —
``auction.strength.evaluate`` isolates per-verdict/per-dimension failures and
``verdict_to_payload`` only raises on non-finite contract numbers, which the
scorer cannot produce.  The test instead pins the reachable sibling: assets
written + snapshot write failure -> exit 0 with ``snapshot_error``.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from tests.e2e.fixtures import seed_builders as sb
from tests.e2e.harness.assertlib import assert_exit, sqlite_count, sqlite_rows
from tests.e2e.harness.canonical import assert_json_equal
from tests.e2e.harness.sandbox import EnvSandbox
from tests.e2e.harness.seed import seed_db

E2E_ROOT = Path(__file__).resolve().parent
SCENARIO = E2E_ROOT / "fixtures" / "scenario_auction.json"
GOLDEN = E2E_ROOT / "golden" / "auction"
NOW = sb.AUCTION_NOW
TRADE_DATE = sb.TRADE_DATE
DATE_ARG = "20260911"

COLLECT_KEYS = [
    "ok", "mode", "trade_date", "source", "pool_source", "fallback_reason",
    "prep_snapshot_date", "prep_unavailable_reason", "recommended_count",
    "days_ignored", "lookback_dates", "candidate_codes", "saved_count", "saved",
    "empty", "failed", "quote", "quote_missing", "asset_read_error",
    "scored_count", "snapshot_saved_count", "snapshot_ok", "snapshot_skipped",
    "snapshot_error", "snapshots",
]
EMPTY_KEYS = [
    "ok", "mode", "trade_date", "source", "pool_source", "fallback_reason",
    "prep_snapshot_date", "prep_unavailable_reason", "pool_empty",
    "candidate_codes", "recommended_count", "ignore_reason_count",
    "ignore_reason_samples", "prep_degraded", "days_ignored", "message",
]
RECOMPUTE_KEYS = [
    "ok", "mode", "trade_date", "source", "asset_count", "usable_count",
    "skipped_empty", "quote_missing", "scored_count", "snapshot_saved_count",
    "snapshot_ok", "snapshot_skipped", "snapshot_error", "failed", "snapshots",
]
QUOTE_KEYS = [
    "code", "name", "price", "prev_close", "open_px", "high", "low", "pct",
    "chg", "turnover_rate", "amplitude", "volume", "amount", "market_cap",
    "float_market_cap", "volume_ratio", "pe", "limit_up", "limit_down",
    "avg_price", "bid1_price", "bid1_volume", "ask1_volume", "timestamp",
    "main_net_amount",
]
SUMMARY_KEYS = [
    "code", "is_weak", "strength", "direction", "level", "unverified", "rank",
    "reject_reason",
]


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


def run_auction(box: EnvSandbox, args: list[str], *,
                scenario: Path = SCENARIO, timeout: int = 120,
                clock_mode: str = "runtime"):
    overrides = {"DQ_E2E_SCENARIO": str(scenario),
                 "DQ_E2E_CLOCK_MODE": clock_mode}
    return runner().run_cli(
        args, sandbox=box, timeout=timeout, env_override=overrides)


def run_python(box: EnvSandbox, code: str, *, clock_mode: str = "runtime"):
    overrides = {"DQ_E2E_SCENARIO": str(SCENARIO),
                 "DQ_E2E_CLOCK_MODE": clock_mode}
    return runner().run_python(
        code, sandbox=box, timeout=60, env_override=overrides)


def golden_json(name: str):
    return json.loads((GOLDEN / name).read_text(encoding="utf-8"))


def golden_text(name: str) -> str:
    return (GOLDEN / name).read_text(encoding="utf-8")


def write_variant(box: EnvSandbox, name: str, mutate) -> Path:
    payload = json.loads(SCENARIO.read_text(encoding="utf-8"))
    mutate(payload)
    path = box.root / name
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def exec_sql(box: EnvSandbox, db_name: str, sql: str,
             params: tuple = ()) -> None:
    path = box.data_dir / db_name
    connection = sqlite3.connect(path)
    try:
        with connection:
            connection.execute(sql, params)
    finally:
        connection.close()


def call_records(box: EnvSandbox) -> list[dict]:
    if not box.call_log.exists():
        return []
    return [json.loads(line) for line in
            box.call_log.read_text(encoding="utf-8").splitlines() if line.strip()]


def transport(records: list[dict]) -> list[tuple[str, str]]:
    return [(record["provider"], record["method"]) for record in records
            if record["method"].startswith("get_")
            or record["method"] == "batch_get_quotes"]


def transport_since(box: EnvSandbox, offset: int) -> list[tuple[str, str]]:
    return transport(call_records(box)[offset:])


def auction_assets(box: EnvSandbox) -> list[dict]:
    rows = sqlite_rows(box.data_dir / "assets.db", "auction_assets",
                       order_by="code")
    return [{"code": row["code"], "name": row["name"],
             "items": json.loads(row["items_json"]),
             "quote": json.loads(row["quote_json"])
             if row["quote_json"] is not None else None}
            for row in rows]


def auction_snapshots(box: EnvSandbox) -> list[dict]:
    rows = sqlite_rows(box.data_dir / "assets.db", "auction_snapshots",
                       order_by="code")
    return [json.loads(row["analysis_json"]) for row in rows]


def snapshot_summaries(rows: list[dict]) -> list[dict]:
    return [{key: row.get(key) for key in SUMMARY_KEYS} for row in rows]


def seed_prep_pool(box: EnvSandbox, candidates: list[dict] | None = None) -> None:
    seed_db(box, sb.prep_snapshot_seed(candidates), db_name="assets.db")


# ─── A1 / A2: collect success, human output, JSON contract ─────────────────


def test_a1_collect_success_matches_reviewed_golden():
    with sandbox("a1") as box:
        seed_prep_pool(box)
        result = run_auction(box, ["auction"])
        assert_exit(result, 0)
        assert result.stderr == ""
        assert result.stdout == golden_text("stdout_collect.txt")

        assets = auction_assets(box)
        assert [row["code"] for row in assets] == ["600001", "600002", "600003"]
        for row in assets:
            assert row["items"] == sb.AUCTION_ITEMS[row["code"]]
            assert list(row["quote"]) and set(row["quote"]) == set(QUOTE_KEYS)
        assert json.loads(json.dumps(assets)) == golden_json("assets.json")

        snapshots = auction_snapshots(box)
        assert [row["code"] for row in snapshots] == ["600001", "600002",
                                                      "600003"]
        assert_json_equal(snapshots, golden_json("snapshots.json"))
        assert sqlite_count(box.data_dir / "assets.db", "auction_assets") == 3
        assert sqlite_count(box.data_dir / "assets.db", "auction_snapshots") == 3

        assert transport(call_records(box)) == [
            ("tdx", "get_kline"),
            ("tdx", "batch_get_quotes"),
            ("tdx", "get_auction"),
            ("tdx", "get_auction"),
            ("tdx", "get_auction"),
        ]


def test_a2_json_key_order_and_payload_contract():
    with sandbox("a2") as box:
        seed_prep_pool(box)
        result = run_auction(box, ["auction", "--json"])
        assert_exit(result, 0)
        assert result.stderr == ""
        payload = json.loads(result.stdout)
        assert list(payload) == COLLECT_KEYS
        assert_json_equal(payload, golden_json("json_collect.json"))
        assert payload["pool_source"] == "prep"
        assert payload["fallback_reason"] is None
        assert payload["prep_snapshot_date"] == sb.AUCTION_PREV_DATE
        assert payload["prep_unavailable_reason"] is None
        assert payload["recommended_count"] == 3
        assert payload["candidate_codes"] == 3
        assert payload["saved_count"] == 3
        assert payload["empty"] == []
        assert payload["failed"] == []
        assert payload["quote_missing"] == []
        assert payload["asset_read_error"] is None
        assert payload["snapshot_ok"] is True
        assert payload["snapshot_skipped"] is None
        assert payload["snapshot_error"] is None
        assert payload["quote"] == {
            "source": "tdx", "fallback_used": False, "requested": 3,
            "matched": 3, "missing": [], "degraded": False, "reason": None,
            "error": None,
        }
        assert_json_equal(snapshot_summaries(auction_snapshots(box)),
                          payload["snapshots"])


# ─── A3 / A4: pool fallback and empty pool ─────────────────────────────────


def test_a3_missing_prep_snapshot_falls_back_to_legacy_mirror():
    with sandbox("a3") as box:
        legacy = [
            sb.dragon_row("2026-09-09", "600001", "龙头A", 1, 88.0, ["AI算力"]),
            sb.dragon_row("2026-09-10", "600002", "跟风B", 2, 62.0, ["机器人"]),
        ]
        seed_db(box, sb.mirror_db_seed(legacy), db_name="assets.db")
        result = run_auction(box, ["auction", "--json"])
        assert_exit(result, 0)
        payload = json.loads(result.stdout)
        assert payload["pool_source"] == "legacy"
        assert payload["fallback_reason"] == "no_snapshot"
        assert payload["prep_snapshot_date"] is None
        assert payload["prep_unavailable_reason"] == "missing"
        assert payload["recommended_count"] == 0
        assert payload["candidate_codes"] == 2
        assert payload["lookback_dates"] == [
            "2026-09-04", "2026-09-07", "2026-09-08", "2026-09-09",
            "2026-09-10"]
        assert payload["saved_count"] == 2
        assert [row["code"] for row in auction_assets(box)] == ["600001",
                                                                "600002"]


def test_a4_empty_pool_never_touches_quotes_or_auction_endpoints():
    with sandbox("a4") as box:
        result = run_auction(box, ["auction", "--json"])
        assert_exit(result, 0)
        payload = json.loads(result.stdout)
        assert list(payload) == EMPTY_KEYS
        assert payload["pool_empty"] is True
        assert payload["candidate_codes"] == 0
        assert payload["pool_source"] == "legacy"
        assert payload["fallback_reason"] == "no_snapshot"
        assert payload["message"] == "prep 与 legacy 均无可用候选，本次不采集竞价数据"
        calls = [method for _, method in transport(call_records(box))]
        assert calls == ["get_kline"]
        assert not (box.data_dir / "assets.db").exists()


# ─── A5 / A6: collection and snapshot failure isolation ────────────────────


def test_a5_total_collection_failure_exits_nonzero_and_writes_nothing():
    with sandbox("a5") as box:
        seed_prep_pool(box)

        def fail_all(payload: dict) -> None:
            for code in list(payload["providers"]["tdx"]["get_auction"]["by_code"]):
                payload["providers"]["tdx"]["get_auction"]["by_code"][code] = {
                    "error": "RuntimeError: 模拟盘口故障"}

        scenario = write_variant(box, "scenario_auction_error.json", fail_all)
        result = run_auction(box, ["auction", "--json"], scenario=scenario)
        assert_exit(result, 1)
        assert result.stderr == ""
        payload = json.loads(result.stdout)
        assert payload["ok"] is False
        assert payload["saved_count"] == 0
        assert payload["scored_count"] == 0
        assert payload["snapshot_skipped"] == "no_payloads"
        assert payload["snapshot_ok"] is False
        assert [(item["code"], item["stage"]) for item in payload["failed"]] == [
            ("600001", "auction"), ("600002", "auction"), ("600003", "auction")]
        assert all("模拟盘口故障" in item["error"] for item in payload["failed"])
        assert sqlite_count(box.data_dir / "assets.db", "auction_assets") == 0
        assert sqlite_count(box.data_dir / "assets.db", "auction_snapshots") == 0


def test_a6_assets_written_with_snapshot_write_failure_still_exits_zero():
    with sandbox("a6") as box:
        spec = sb.prep_snapshot_seed()
        spec["tables"]["auction_snapshots"] = {
            "columns": {
                "trade_date": "TEXT NOT NULL", "code": "TEXT NOT NULL",
                "source": "TEXT NOT NULL", "analysis_json": "TEXT NOT NULL",
                "created_at": "TEXT",
            },
            "primary_key": ["trade_date", "code", "source"],
            "rows": [],
        }
        seed_db(box, spec, db_name="assets.db")
        exec_sql(box, "assets.db",
                 "CREATE TRIGGER e2e_fail_snapshot BEFORE INSERT ON "
                 "auction_snapshots BEGIN SELECT RAISE(ABORT, "
                 "'e2e snapshot write blocked'); END")

        result = run_auction(box, ["auction", "--json"])
        assert_exit(result, 0)
        payload = json.loads(result.stdout)
        assert payload["ok"] is False
        assert payload["saved_count"] == 3
        assert payload["snapshot_saved_count"] == 0
        assert payload["snapshot_ok"] is False
        assert "IntegrityError" in payload["snapshot_error"]
        assert "e2e snapshot write blocked" in payload["snapshot_error"]
        assert payload["snapshot_skipped"] is None
        assert "竞价原始资产已写入，但快照未产出" in result.stderr
        assert sqlite_count(box.data_dir / "assets.db", "auction_assets") == 3
        assert sqlite_count(box.data_dir / "assets.db", "auction_snapshots") == 0


# ─── A7–A9: recompute ──────────────────────────────────────────────────────


def test_a7_recompute_is_offline_and_reproduces_the_snapshots():
    with sandbox("a7") as box:
        seed_prep_pool(box)
        assert_exit(run_auction(box, ["auction", "--json"]), 0)
        snapshots_before = auction_snapshots(box)
        offset = len(call_records(box))

        result = run_auction(box, ["auction", "--recompute", DATE_ARG, "--json"])
        assert_exit(result, 0)
        assert transport_since(box, offset) == []
        payload = json.loads(result.stdout)
        assert list(payload) == RECOMPUTE_KEYS
        assert payload["mode"] == "recompute"
        assert payload["ok"] is True
        assert payload["asset_count"] == 3
        assert payload["usable_count"] == 3
        assert payload["skipped_empty"] == []
        assert payload["quote_missing"] == []
        assert payload["scored_count"] == 3
        assert payload["snapshot_saved_count"] == 3
        assert payload["snapshot_ok"] is True
        assert payload["snapshot_skipped"] is None
        assert payload["snapshot_error"] is None
        assert payload["failed"] == []
        assert_json_equal(payload["snapshots"],
                          snapshot_summaries(snapshots_before))
        assert_json_equal(auction_snapshots(box), snapshots_before)

        human = run_auction(box, ["auction", "--recompute", DATE_ARG])
        assert_exit(human, 0)
        assert human.stdout.splitlines()[0] == (
            "📊 2026-09-11 集合竞价强度（重算｜参与 3 只）")


def test_a8_recompute_rejects_a_future_date():
    with sandbox("a8") as box:
        result = run_auction(box, ["auction", "--recompute", "20260912", "--json"])
        assert_exit(result, 1)
        payload = json.loads(result.stdout)
        assert payload["ok"] is False
        assert payload["mode"] == "recompute"
        assert payload["trade_date"] == "2026-09-12"
        assert payload["error"] == "未来日期不可重算: 2026-09-12 > 2026-09-11"
        assert transport(call_records(box)) == []


def test_a9_recompute_without_assets_fails_without_provider_calls():
    with sandbox("a9") as box:
        result = run_auction(box, ["auction", "--recompute", "20260910", "--json"])
        assert_exit(result, 1)
        payload = json.loads(result.stdout)
        assert payload["ok"] is False
        assert payload["mode"] == "recompute"
        assert payload["error"] == "2026-09-10 无竞价原始资产，无法重算"
        assert transport(call_records(box)) == []
        assert not (box.data_dir / "assets.db").exists()


# ─── A10: --days only applies to the legacy path ───────────────────────────


def test_a10_days_is_ignored_when_the_prep_pool_succeeds():
    with sandbox("a10") as box:
        seed_prep_pool(box)
        result = run_auction(box, ["auction", "--days", "3", "--json"])
        assert_exit(result, 0)
        assert result.stderr == "⚠️ --days 仅在 legacy 路径生效，本次已忽略\n"
        payload = json.loads(result.stdout)
        assert payload["days_ignored"] is True
        assert payload["candidate_codes"] == 3


# ─── A11: quote degradation ────────────────────────────────────────────────


def test_a11_tdx_quote_miss_falls_back_to_tencent_and_marks_missing():
    with sandbox("a11") as box:
        seed_prep_pool(box)
        quote = sb.QUOTE_BY_CODE["600001"]

        def degrade(payload: dict) -> None:
            payload["providers"]["tdx"]["batch_get_quotes"] = []
            payload["providers"]["tencent"]["batch_get_quotes"] = [quote]

        scenario = write_variant(box, "scenario_quote_partial.json", degrade)
        result = run_auction(box, ["auction", "--json"], scenario=scenario)
        assert_exit(result, 0)
        payload = json.loads(result.stdout)
        assert payload["quote"] == {
            "source": "tencent", "fallback_used": True, "requested": 3,
            "matched": 1, "missing": ["600002", "600003"], "degraded": True,
            "reason": "partial", "error": None,
        }
        assert payload["quote_missing"] == ["600002", "600003"]
        assert payload["saved_count"] == 3
        assert payload["scored_count"] == 3
        snapshots = {item["code"]: item for item in payload["snapshots"]}
        assert snapshots["600001"]["reject_reason"] is None
        assert snapshots["600002"]["is_weak"] is True
        assert snapshots["600002"]["reject_reason"] == "0.4 数据不足，无法清除否决"
        assert snapshots["600003"]["reject_reason"] == "0.4 数据不足，无法清除否决"


def test_a11b_total_quote_failure_still_scores_with_quote_missing():
    with sandbox("a11b") as box:
        seed_prep_pool(box)

        def no_quotes(payload: dict) -> None:
            payload["providers"]["tdx"]["batch_get_quotes"] = []
            payload["providers"]["tencent"]["batch_get_quotes"] = []

        scenario = write_variant(box, "scenario_quote_none.json", no_quotes)
        result = run_auction(box, ["auction", "--json"], scenario=scenario)
        assert_exit(result, 0)
        payload = json.loads(result.stdout)
        assert payload["quote"]["source"] == "none"
        assert payload["quote"]["matched"] == 0
        assert payload["quote"]["degraded"] is True
        assert payload["quote"]["reason"] == "empty"
        assert payload["quote_missing"] == ["600001", "600002", "600003"]
        assert all(item["is_weak"] is True for item in payload["snapshots"])
        assert payload["snapshot_saved_count"] == 3


# ─── A12: corruption isolation ─────────────────────────────────────────────


def test_a12a_corrupt_asset_row_is_isolated_during_recompute():
    with sandbox("a12a") as box:
        seed_prep_pool(box)
        assert_exit(run_auction(box, ["auction", "--json"]), 0)
        exec_sql(box, "assets.db",
                 "UPDATE auction_assets SET items_json = '{' "
                 "WHERE trade_date = ? AND code = ?", (TRADE_DATE, "600002"))
        offset = len(call_records(box))

        result = run_auction(box, ["auction", "--recompute", DATE_ARG, "--json"])
        assert_exit(result, 0)
        assert transport_since(box, offset) == []
        payload = json.loads(result.stdout)
        assert payload["ok"] is False
        assert payload["asset_count"] == 3
        assert payload["usable_count"] == 2
        assert payload["scored_count"] == 2
        assert payload["snapshot_saved_count"] == 2
        assert [(item["code"], item["stage"]) for item in payload["failed"]] == [
            ("600002", "asset")]
        assert "JSONDecodeError" in payload["failed"][0]["error"]
        assert [item["code"] for item in payload["snapshots"]] == ["600001",
                                                                   "600003"]
        # The atomic per-day replacement keeps code set == scored set.
        assert [row["code"] for row in auction_snapshots(box)] == ["600001",
                                                                   "600003"]


def test_a12b_all_corrupt_assets_fail_without_touching_snapshots():
    with sandbox("a12b") as box:
        seed_prep_pool(box)
        assert_exit(run_auction(box, ["auction", "--json"]), 0)
        exec_sql(box, "assets.db",
                 "UPDATE auction_assets SET items_json = '{' WHERE trade_date = ?",
                 (TRADE_DATE,))
        result = run_auction(box, ["auction", "--recompute", DATE_ARG, "--json"])
        assert_exit(result, 1)
        payload = json.loads(result.stdout)
        assert payload["ok"] is False
        assert payload["error"] == (
            "无可计算资产（items 全为空或行情 payload 全部损坏）")
        assert payload["asset_count"] == 3
        assert len(payload["failed"]) == 3
        assert [item["code"] for item in auction_snapshots(box)] == [
            "600001", "600002", "600003"]


def test_a12c_snapshot_reads_isolate_corrupt_rows():
    with sandbox("a12c") as box:
        seed_db(box, sb.auction_snapshots_seed([
            {"trade_date": TRADE_DATE, "code": "600001", "source": "v2",
             "analysis_json": json.dumps({"code": "600001", "strength": 67.85,
                                          "unverified": True}),
             "created_at": f"{TRADE_DATE} 09:30:00"},
            {"trade_date": TRADE_DATE, "code": "600002", "source": "v2",
             "analysis_json": "{",
             "created_at": f"{TRADE_DATE} 09:30:00"},
        ]), db_name="assets.db")
        result = runner().run_python(
            "import json\n"
            "from dragon_quant.storage import assets_db\n"
            "rows = assets_db.list_auction_snapshots('2026-09-11')\n"
            "print(json.dumps(rows, ensure_ascii=False, sort_keys=True))\n",
            sandbox=box, timeout=60)
        assert_exit(result, 0)
        rows = json.loads(result.stdout)
        assert [row["code"] for row in rows] == ["600001", "600002"]
        assert rows[0]["strength"] == 67.85
        assert "error" not in rows[0]
        assert "JSONDecodeError" in rows[1]["error"]


# ─── A13 / A14: single runtime-clock seam (M9B1 / M9C) ──────────────────────


def test_a13_runtime_clock_mode_recompute_future_gate_uses_the_runtime_clock():
    """Runtime mode: ``_today()`` reads the frozen clock for recompute gates."""
    with sandbox("a13", now="2026-09-11T09:30:00+08:00") as box:
        code = (
            "from dragon_quant.auction.recompute import run_auction_recompute\n"
            "out = run_auction_recompute('20260912')\n"
            "print(out.exit_code)\n"
            "print(out.payload.get('error', ''))\n"
        )
        result = run_python(box, code, clock_mode="runtime")
        assert result.returncode == 0, result.stderr
        assert result.stdout.splitlines() == [
            "1", "未来日期不可重算: 2026-09-12 > 2026-09-11"]


def test_a14_auction_cli_future_gate_runs_on_the_single_runtime_clock_seam():
    """CLI path (not a service fragment): ``auction --recompute`` reads the clock."""
    with sandbox("a14", now="2026-09-11T09:30:00+08:00") as box:
        result = run_auction(box, ["auction", "--recompute", "20260912", "--json"])
        assert_exit(result, 1)
        assert json.loads(result.stdout)["error"] == (
            "未来日期不可重算: 2026-09-12 > 2026-09-11")

        proof = json.loads((box.root / "preflight.json").read_text(encoding="utf-8"))
        assert proof["injection"]["clock_mode"] == "runtime"
        assert proof["injection"]["clock_impl"] == "runtime.clock"
        assert proof["datetime_is_replaced"] is False
        assert proof["time_is_replaced"] is False
        assert proof["datetime_isinstance_ok"] is True
        assert proof["runtime_clock_type"] == "FixedClock"
