"""M1D1 prep behavior baseline: P1–P14 + C18.

Every case runs the real ``python -m dragon_quant prep`` subprocess through the
M0 sandbox (fake providers, frozen clock, temporary data root).  Rows are
seeded only into ``<sandbox>/data``; the real data directory is watched by the
session sentinel.  Goldens under ``dragon_quant/tests/e2e/golden/prep`` are reviewed
constants: tests compare against them and never regenerate them.

Deviation discipline: the assertions below pin *actual* production behavior.
Where the approved spec describes a branch that cannot be reached through the
CLI (see P12 / P14 notes), the test documents the reachable behavior instead
of weakening the assertion.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from dragon_quant.tests.e2e.fixtures import seed_builders as sb
from dragon_quant.tests.e2e.harness.assertlib import (
    assert_exit,
    sqlite_count,
    sqlite_rows,
)
from dragon_quant.tests.e2e.harness.canonical import assert_json_equal
from dragon_quant.tests.e2e.harness.sandbox import EnvSandbox
from dragon_quant.tests.e2e.harness.seed import seed_db

E2E_ROOT = Path(__file__).resolve().parent
SCENARIO = E2E_ROOT / "fixtures" / "scenario_prep.json"
GOLDEN = E2E_ROOT / "golden" / "prep"
NOW = sb.PREP_NOW
TRADE_DATE = sb.TRADE_DATE
DATE_ARG = "20260911"
HISTORICAL_NOW = "2026-09-16T16:45:00+08:00"

# 旧 4 行摘要 + 逐只清单的批准前缀（M4 只在其后追加「复盘摘要」，逐字不变）。
LEGACY_STDOUT_PREFIX = """是否建议买入：是
建议仓位：50%
建议等级：一般
买入理由：复盘日 2026-09-11；外围：日经225期指 +0.61%、富时A50期指 -0.87%（同日收盘）；美债10Y 5.15%（较前值 +6bp，近似）；COMEX金 -0.63%；涨停 62 家；跌停 3 家；炸板率 1.6%；昨日涨停溢价 +5.67%；晋级率 66.7%；最高 4 板；主线：机器人、AI算力、银行科技。涨跌停家数处中性区间，建议仓位 50%。

✅ 龙头E-轻仓博弈
- 复盘分 70.3 / B
- 龙头 / 仓位 4.0%
- 主线 机器人 第1

银行D-忽略：公告「立案调查公告」：硬风险：hard_risk_announcement
龙头A-忽略：流动性不足：amount=100000000
补涨C-忽略：流动性不足：amount=50000000
跟风B-忽略：流动性不足：amount=80000000"""

TOP_KEYS = ["trade_date", "version", "sentiment", "main_boards", "macro",
            "candidates", "dragon_source", "review", "review_candidates",
            "strategy_context"]
SENTIMENT_KEYS = [
    "up_cnt", "down_cnt", "limit_up_cnt", "limit_down_cnt", "total_amount",
    "temperature_score", "position_advice", "position_pct", "limit_up_cnt_ths",
    "limit_down_cnt_ths", "limit_up_cnt_t1_fg", "limit_down_cnt_t1_fg",
    "position_input", "broken_cnt", "broken_rate", "yesterday_premium_pct",
    "promotion_rate", "max_board_height", "lhb_cnt", "factor_sources",
    "degraded",
]
MACRO_KEYS_ORDER = ["available", "as_of", "session", "instruments",
                    "degraded", "sources"]
MACRO_RECORD_KEYS = [
    "key", "label", "kind", "price_unit", "chg_unit", "caliber", "price",
    "prev_close", "chg", "pct", "chg_bp", "chg_bp_approx", "precision_note",
    "source", "code", "market", "session_date", "session_kind", "raw_date",
    "raw_time", "stale",
]
FACTS_KEYS = [
    "pool_available", "in_pool_today", "t_pct", "t_turnover", "t_amount",
    "open_num", "high_days", "reason_type_today", "limit_up_type",
    "first_limit_up_time", "limit_up_suc_rate", "main_net_amount",
    "flow_available", "announcements_available", "announcement_count",
    "risk_announcement",
]
BOARD_KEYS = ["board_name", "limit_up_num", "high", "dragon_count"]
DRAGON_SOURCE_KEYS = ["trade_date", "scan_id", "version", "count",
                      "window_days", "dates", "version_mixed"]


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


def run_prep(box: EnvSandbox, args: list[str], *, scenario: Path = SCENARIO,
             timeout: int = 180, clock_mode: str = "runtime"):
    overrides = {"DQ_E2E_SCENARIO": str(scenario),
                 "DQ_E2E_CLOCK_MODE": clock_mode}
    return runner().run_cli(
        args, sandbox=box, timeout=timeout, env_override=overrides)


def run_python(box: EnvSandbox, code: str):
    return runner().run_python(code, sandbox=box, timeout=60)


def golden_json(name: str):
    return json.loads((GOLDEN / name).read_text(encoding="utf-8"))


def golden_text(name: str) -> str:
    return (GOLDEN / name).read_text(encoding="utf-8")


def seed_full(box: EnvSandbox, *, source_rows=None, mirror_rows=None) -> None:
    rows = sb.flatten_window_rows()
    seed_db(box, sb.dragon_db_seed(rows if source_rows is None else source_rows),
            db_name="dragon.db")
    seed_db(box, sb.mirror_db_seed(rows if mirror_rows is None else mirror_rows),
            db_name="assets.db")


def seed_historical_assets(box: EnvSandbox) -> dict[str, dict]:
    """Healthy non-``same_day_only`` assets for a historical-day run (P10)."""

    codes = [row["code"] for row in sb.flatten_window_rows()]
    assets = {
        "dragons": sb.dragon_payload(TRADE_DATE, sb.WINDOW_DATES),
        "ths_limit_up_pool": sb.pool_payload(),
        "ths_block_top": sb.block_payload(),
        "cninfo_announcements": sb.cninfo_payload(HISTORICAL_NOW[:10], codes),
        "macro_global": sb.macro_payload(),
        "collection_report": sb.report_ok([
            "dragons", "ths_limit_up_pool", "ths_block_top",
            "cninfo_announcements", "macro_global"]),
    }
    seed_db(box, sb.prep_assets_tables(assets), db_name="assets.db")
    return assets


def write_variant(box: EnvSandbox, name: str, mutate) -> Path:
    payload = json.loads(SCENARIO.read_text(encoding="utf-8"))
    mutate(payload)
    path = box.root / name
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def prep_assets(box: EnvSandbox) -> dict[str, dict]:
    db = box.data_dir / "assets.db"
    return {
        row["asset_type"]: json.loads(row["payload_json"])
        for row in sqlite_rows(db, "prep_assets")
    }


def report_of(assets: dict[str, dict]) -> dict:
    return assets["collection_report"]


def snapshot_result(box: EnvSandbox) -> dict:
    rows = sqlite_rows(box.data_dir / "assets.db", "prep_snapshots")
    assert len(rows) == 1, rows
    return json.loads(rows[0]["result_json"])


def exec_sql(box: EnvSandbox, db_name: str, sql: str,
             params: tuple = ()) -> None:
    """Mutate a *temporary* sandbox database (test setup only)."""

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


def candidates_by_code(snapshot: dict) -> dict[str, dict]:
    return {candidate["code"]: candidate for candidate in snapshot["candidates"]}


# ─── P1 / P2: successful prep ───────────────────────────────────────────────


def test_p1_success_human_output_and_persisted_rows_match_reviewed_golden():
    with sandbox("p1") as box:
        seed_full(box)
        result = run_prep(box, ["prep", "--date", DATE_ARG])
        assert_exit(result, 0)
        assert result.stdout == golden_text("stdout.txt")
        legacy_prefix, separator, review_summary = \
            result.stdout.partition("\n\n复盘摘要")
        assert separator, "默认 CLI 必须在旧前缀后追加复盘摘要段"
        assert legacy_prefix == LEGACY_STDOUT_PREFIX, "旧 4 行摘要与逐只清单必须逐字不变"
        assert "review_score" in review_summary
        assert "eligibility=" in review_summary
        assert "单票建议仓位" in review_summary
        assert result.stderr == (
            "复盘日期 2026-09-11：增量补采 13 项资产：dragons, "
            "ths_limit_up_pool, ths_block_top, market_breadth, fg_boards, "
            "holdings, fund_flow, cninfo_announcements, macro_global, "
            "stock_daily, prev_plan, auction_feedback, t1_intraday\n"
        )

        assets = prep_assets(box)
        assert_json_equal(assets, golden_json("assets.json"))
        assert set(assets) == {
            "dragons", "ths_limit_up_pool", "ths_block_top", "market_breadth",
            "fg_boards", "holdings", "fund_flow", "cninfo_announcements",
            "macro_global", "stock_daily", "prev_plan", "auction_feedback",
            "t1_intraday", "collection_report",
        }
        report = report_of(assets)
        assert {name: entry["status"] for name, entry in report.items()} == {
            "dragons": "ok", "ths_limit_up_pool": "ok", "ths_block_top": "ok",
            "market_breadth": "ok", "fg_boards": "ok", "holdings": "empty",
            "fund_flow": "ok", "cninfo_announcements": "ok",
            "macro_global": "ok", "stock_daily": "ok",
            "prev_plan": "empty", "auction_feedback": "empty",
            "t1_intraday": "empty",
        }

        snapshot = snapshot_result(box)
        assert snapshot["version"] == "0.8"
        assert snapshot["trade_date"] == TRADE_DATE
        assert snapshot["dragon_source"]["window_days"] == 5
        assert [day["status"] for day in snapshot["dragon_source"]["dates"]] == \
            ["ok"] * 5
        assert snapshot["sentiment"]["position_pct"] == 0.5
        assert snapshot["sentiment"]["degraded"] == []
        assert snapshot["macro"]["degraded"] == []
        assert snapshot["macro"]["session"] == "same_day"
        assert set(snapshot["macro"]["instruments"]) == set(sb.MACRO_KEYS)
        assert snapshot["macro"]["sources"] == {key: "tdx"
                                               for key in sb.MACRO_KEYS}

        candidates = candidates_by_code(snapshot)
        assert list(candidates) == ["600005", "600004", "600001", "600003",
                                    "600002"]
        # 0.8：verdict 由 review 档位与仓位确定性映射（S/A 且仓位>0 才是 strong）
        assert candidates["600005"]["verdict"] == "light"
        assert {code: candidates[code]["verdict"]
                for code in ("600001", "600002", "600003", "600004")} == {
            "600001": "ignore", "600002": "ignore",
            "600003": "ignore", "600004": "ignore"}
        assert "立案调查公告" in candidates["600004"]["ignore_reason"]
        assert "review_pool" in candidates["600005"]
        assert candidates["600005"]["facts"]["in_pool_today"] is True
        assert candidates["600005"]["facts"]["flow_available"] is True

        assert sqlite_count(box.data_dir / "assets.db", "prep_snapshots") == 1
        assert sqlite_count(box.data_dir / "assets.db", "prep_assets") == 14

        # C18: the macro call requested exactly the four MACRO_SPECS keys and
        # never fell through to Yahoo/urllib (source_errors stays empty).
        macro_calls = [record for record in call_records(box)
                       if record["method"] == "get_macro_quotes"]
        assert len(macro_calls) == 1
        assert macro_calls[0]["provider"] == "tdx"
        assert macro_calls[0]["args"]["specs"]["keys"] == list(sb.MACRO_KEYS)
        assert assets["macro_global"]["source_errors"] == {}
        assert assets["macro_global"]["degraded"] == []

        assert transport(call_records(box)) == [
            ("tdx", "get_kline"),
            ("ths", "get_limit_up_pool"),
            ("ths", "get_block_top"),
            ("tdx", "get_market_breadth_rows"),
            ("tdx", "get_fg_boards"),
            ("tdx", "batch_get_quotes"),
            ("tdx", "get_announcements"),
            ("tdx", "get_announcements"),
            ("tdx", "get_announcements"),
            ("tdx", "get_announcements"),
            ("tdx", "get_announcements"),
            ("tdx", "get_macro_quotes"),
        ]


def test_p2_json_key_order_and_snapshot_equality():
    with sandbox("p2") as box:
        seed_full(box)
        result = run_prep(box, ["prep", "--date", DATE_ARG, "--json"])
        assert_exit(result, 0)
        assert result.stderr.startswith("复盘日期 2026-09-11：增量补采 13 项资产")
        payload = json.loads(result.stdout)
        assert list(payload) == TOP_KEYS
        assert list(payload["sentiment"]) == SENTIMENT_KEYS
        assert list(payload["macro"]) == MACRO_KEYS_ORDER
        for key in sb.MACRO_KEYS:
            assert list(payload["macro"]["instruments"][key]) == MACRO_RECORD_KEYS
        for board in payload["main_boards"]:
            assert list(board) == BOARD_KEYS
        assert list(payload["dragon_source"]) == DRAGON_SOURCE_KEYS
        for candidate in payload["candidates"]:
            assert list(candidate["facts"]) == FACTS_KEYS
            # 0.8 投影在旧字段之后追加 review 来源标记（只增键）
            assert list(candidate)[-4:] == [
                "position_pct", "is_true_dragon", "review_pool", "review_eligible"]
        assert_json_equal(payload, golden_json("snapshot.json"))
        # The persisted snapshot is the same pure function of the assets.
        assert_json_equal(snapshot_result(box), payload)


def test_p2b_default_date_uses_latest_mirror_day_and_prints_hint():
    with sandbox("p2b") as box:
        seed_full(box)
        result = run_prep(box, ["prep", "--json"])
        assert_exit(result, 0)
        payload = json.loads(result.stdout)
        assert payload["trade_date"] == TRADE_DATE
        assert result.stderr.startswith("复盘日期 2026-09-11：增量补采 13 项资产")


# ─── P3: incremental zero-collection ───────────────────────────────────────


def test_p3_second_run_collects_nothing_and_reuses_assets():
    with sandbox("p3") as box:
        seed_full(box)
        assert_exit(run_prep(box, ["prep", "--date", DATE_ARG]), 0)
        assets_before = prep_assets(box)
        snapshot_before = snapshot_result(box)
        created_before = {row["asset_type"]: row["created_at"]
                          for row in sqlite_rows(box.data_dir / "assets.db",
                                                 "prep_assets")}
        offset = len(call_records(box))

        second = run_prep(box, ["prep", "--date", DATE_ARG])
        assert_exit(second, 0)
        assert second.stderr == (
            "复盘日期 2026-09-11：资产已就绪（无需采集），直接重算快照\n")
        assert second.stdout == golden_text("stdout.txt")
        # Zero *collection*: the only transport call is the mandatory calendar
        # check that gates the trade date (it is not a collector).
        assert transport_since(box, offset) == [("tdx", "get_kline")]
        delta_methods = [record["method"] for record in call_records(box)[offset:]]
        assert "get_kline" in delta_methods
        assert not [name for name in delta_methods if name in {
            "get_limit_up_pool", "get_block_top", "get_market_breadth_rows",
            "get_fg_boards", "batch_get_quotes", "get_announcements",
            "get_macro_quotes"}]

        assert_json_equal(prep_assets(box), assets_before)
        assert_json_equal(snapshot_result(box), snapshot_before)
        created_after = {row["asset_type"]: row["created_at"]
                         for row in sqlite_rows(box.data_dir / "assets.db",
                                                "prep_assets")}
        assert created_after == created_before


# ─── P4: partial re-collection ──────────────────────────────────────────────


def test_p4_missing_asset_is_the_only_one_recollected():
    with sandbox("p4") as box:
        seed_full(box)
        assert_exit(run_prep(box, ["prep", "--date", DATE_ARG]), 0)
        assets_before = prep_assets(box)
        offset = len(call_records(box))
        exec_sql(box, "assets.db",
                 "DELETE FROM prep_assets WHERE trade_date = ? AND asset_type = ?",
                 (TRADE_DATE, "fg_boards"))

        second = run_prep(box, ["prep", "--date", DATE_ARG])
        assert_exit(second, 0)
        assert second.stderr == "复盘日期 2026-09-11：增量补采 1 项资产：fg_boards\n"
        assert transport_since(box, offset) == [
            ("tdx", "get_kline"),
            ("tdx", "get_fg_boards"),
        ]
        assets_after = prep_assets(box)
        assert_json_equal(assets_after["fg_boards"], assets_before["fg_boards"])
        assert report_of(assets_after)["fg_boards"]["status"] == "ok"
        for asset_type in assets_before:
            if asset_type == "fg_boards":
                continue
            assert_json_equal(assets_after[asset_type], assets_before[asset_type])


def test_p4b_failed_recollection_does_not_touch_other_assets():
    with sandbox("p4b") as box:
        seed_full(box)
        assert_exit(run_prep(box, ["prep", "--date", DATE_ARG]), 0)
        assets_before = prep_assets(box)
        exec_sql(box, "assets.db",
                 "DELETE FROM prep_assets WHERE trade_date = ? AND asset_type = ?",
                 (TRADE_DATE, "fg_boards"))
        scenario = write_variant(
            box, "scenario_fg_error.json",
            lambda payload: payload["providers"]["tdx"].__setitem__(
                "get_fg_boards", {"error": "RuntimeError: 模拟 FG 故障"}))

        second = run_prep(box, ["prep", "--date", DATE_ARG], scenario=scenario)
        assert_exit(second, 0)
        assets_after = prep_assets(box)
        report = report_of(assets_after)
        assert report["fg_boards"]["status"] == "error"
        assert "模拟 FG 故障" in report["fg_boards"]["error"]
        assert "fg_boards" in snapshot_result(box)["sentiment"]["degraded"]
        for asset_type in assets_before:
            if asset_type in ("fg_boards", "collection_report"):
                continue
            assert_json_equal(assets_after[asset_type], assets_before[asset_type])
        # Healthy report entries for the untouched assets are preserved.
        for asset_type in ("dragons", "ths_limit_up_pool", "fund_flow",
                           "cninfo_announcements", "macro_global"):
            assert report[asset_type]["status"] == "ok"


# ─── P5 / P6: recompute and force ──────────────────────────────────────────


def test_p5_recompute_is_offline_and_rebuilds_only_the_dragons_asset():
    with sandbox("p5") as box:
        seed_full(box)
        assert_exit(run_prep(box, ["prep", "--date", DATE_ARG]), 0)
        assets_before = prep_assets(box)
        snapshot_before = snapshot_result(box)
        offset = len(call_records(box))

        result = run_prep(box, ["prep", "--date", DATE_ARG, "--recompute"])
        assert_exit(result, 0)
        assert result.stderr == ""
        assert transport_since(box, offset) == []

        dragons = prep_assets(box)["dragons"]
        assert dragons["window_source"] == "local"
        assert dragons["rebuilt_from_window_days"] == 5
        assert isinstance(dragons["rebuilt_at"], str) and dragons["rebuilt_at"]
        assert json.loads(json.dumps(dragons))["rows"] == \
            assets_before["dragons"]["rows"]
        rebuilt_audit = ("rebuilt_at", "window_source",
                         "rebuilt_from_window_days")
        assert_json_equal(
            {key: value for key, value in dragons.items()
             if key not in rebuilt_audit},
            {key: value for key, value in assets_before["dragons"].items()
             if key not in rebuilt_audit})
        assert_json_equal(report_of(prep_assets(box)),
                          report_of(assets_before))
        assert_json_equal(snapshot_result(box), snapshot_before)


def test_p6_force_recollects_every_collectable_asset():
    with sandbox("p6") as box:
        seed_full(box)
        assert_exit(run_prep(box, ["prep", "--date", DATE_ARG]), 0)
        snapshot_before = snapshot_result(box)
        offset = len(call_records(box))

        result = run_prep(box, ["prep", "--date", DATE_ARG, "--force"])
        assert_exit(result, 0)
        assert result.stderr == (
            "复盘日期 2026-09-11：增量补采 13 项资产：dragons, "
            "ths_limit_up_pool, ths_block_top, market_breadth, fg_boards, "
            "holdings, fund_flow, cninfo_announcements, macro_global, "
            "stock_daily, prev_plan, auction_feedback, t1_intraday\n")
        methods = [method for _, method in transport_since(box, offset)]
        assert methods[:1] == ["get_kline"]
        assert methods.count("get_announcements") == 5
        assert set(methods) == {
            "get_kline", "get_limit_up_pool", "get_block_top",
            "get_market_breadth_rows", "get_fg_boards", "batch_get_quotes",
            "get_announcements", "get_macro_quotes",
        }
        assert_json_equal(snapshot_result(box), snapshot_before)


def test_p6b_force_on_a_historical_day_still_skips_same_day_only_assets():
    with sandbox("p6b", now=HISTORICAL_NOW) as box:
        seed_full(box)
        offset = len(call_records(box))
        result = run_prep(box, ["prep", "--date", DATE_ARG, "--force"])
        assert_exit(result, 0)
        methods = {method for _, method in transport_since(box, offset)}
        assert "get_limit_up_pool" in methods
        assert "get_block_top" in methods
        assert "get_announcements" in methods
        assert "get_macro_quotes" in methods
        assert not methods & {"get_market_breadth_rows", "get_fg_boards",
                              "batch_get_quotes"}
        report = report_of(prep_assets(box))
        for asset_type in ("market_breadth", "fg_boards", "fund_flow"):
            assert asset_type not in report


# ─── P7–P9: time gate and date validation ──────────────────────────────────


def test_p7_daily_gate_rejects_intraday_run():
    with sandbox("p7", now="2026-09-11T15:30:00+08:00") as box:
        result = run_prep(box, ["prep", "--date", DATE_ARG])
        assert_exit(result, 1)
        assert result.stdout == ""
        assert result.stderr == (
            "❌ 复盘失败：2026-09-11 盘后数据尚未就绪，请 16:40 后运行 prep\n")
        assert transport(call_records(box)) == []
        assert not (box.data_dir / "assets.db").exists()
        # ``--force`` never bypasses the 16:40 gate (unlike ``scan --force``).
        forced = run_prep(box, ["prep", "--date", DATE_ARG, "--force"])
        assert_exit(forced, 1)
        assert forced.stderr == result.stderr
        assert transport(call_records(box)) == []


def test_p8_non_trading_day_is_distinct_from_calendar_failure():
    with sandbox("p8", now=HISTORICAL_NOW) as box:
        result = run_prep(box, ["prep", "--date", "20260912"])
        assert_exit(result, 1)
        assert result.stdout == ""
        assert result.stderr == "❌ 复盘失败：2026-09-12 非交易日\n"

    with sandbox("p8b", now=HISTORICAL_NOW) as box:
        scenario = write_variant(
            box, "scenario_no_calendar.json",
            lambda payload: payload["providers"]["tdx"]["get_kline"].__setitem__(
                "by_code", {"SH000001": []}))
        result = run_prep(box, ["prep", "--date", DATE_ARG], scenario=scenario)
        assert_exit(result, 1)
        assert result.stdout == ""
        assert result.stderr == (
            "❌ 复盘失败：交易日历数据源失败（TDX 指数日K为空），请稍后重试\n")
        assert result.stderr.find("非交易日") == -1


def test_p9_future_date_is_rejected_in_both_output_modes():
    with sandbox("p9") as box:
        result = run_prep(box, ["prep", "--date", "20260912"])
        assert_exit(result, 1)
        assert result.stdout == ""
        assert result.stderr == "❌ 复盘失败：复盘日 2026-09-12 是未来日期\n"

    with sandbox("p9b") as box:
        result = run_prep(box, ["prep", "--date", "20260912", "--json"])
        assert_exit(result, 1)
        assert result.stderr == ""
        assert json.loads(result.stdout) == {"error": "复盘日 2026-09-12 是未来日期"}


# ─── P10: historical same_day_only degradation ─────────────────────────────


def test_p10_historical_day_never_collects_same_day_only_assets():
    with sandbox("p10", now=HISTORICAL_NOW) as box:
        seed_historical_assets(box)
        offset = len(call_records(box))
        result = run_prep(box, ["prep", "--date", DATE_ARG])
        assert_exit(result, 0)
        assert transport_since(box, offset) == [("tdx", "get_kline")]
        report = report_of(prep_assets(box))
        for asset_type in ("market_breadth", "fg_boards", "fund_flow"):
            assert asset_type not in report
        snapshot = snapshot_result(box)
        assert {"market_breadth", "fg_boards", "fund_flow"} <= \
            set(snapshot["sentiment"]["degraded"])
        assert snapshot["sentiment"]["position_pct"] is None
        assert snapshot["sentiment"]["limit_up_cnt"] == 62
        assert snapshot["sentiment"]["broken_cnt"] == 1
        assert result.stdout.splitlines()[:3] == [
            "是否建议买入：无法判断（数据不足）",
            "建议仓位：—",
            "建议等级：不建议买入（数据不足）",
        ]
        assert "数据降级：窗口加宽后资金/公告覆盖不足、FG 板块数据缺失、" \
               "主力净额缺失、市场宽度缺失" in result.stdout
        assert "情绪数据不足，无法给出买入建议" in result.stdout
        assert "无参与复盘标的" not in result.stdout


# ─── P11: mirror reconciliation ────────────────────────────────────────────


def test_p11a_default_date_with_empty_mirror_refuses_to_guess():
    with sandbox("p11a") as box:
        rows = sb.flatten_window_rows()
        seed_db(box, sb.dragon_db_seed(rows), db_name="dragon.db")
        result = run_prep(box, ["prep"])
        assert_exit(result, 1)
        assert result.stdout == ""
        assert "assets.db.dragons 为空而源库有 7 行" in result.stderr
        assert "assets backfill-dragons" in result.stderr
        assert not (box.data_dir / "assets.db").exists()


def test_p11b_explicit_date_with_missing_target_day_fails_the_collector():
    with sandbox("p11b") as box:
        rows = sb.flatten_window_rows()
        mirror = [row for row in rows if row["trade_date"] != TRADE_DATE]
        seed_full(box, mirror_rows=mirror)
        result = run_prep(box, ["prep", "--date", DATE_ARG])
        assert_exit(result, 0)
        report = report_of(prep_assets(box))
        assert report["dragons"]["status"] == "error"
        assert "源库有龙头记录而镜像缺行" in report["dragons"]["error"]
        assert "backfill-dragons" in report["dragons"]["error"]
        snapshot = snapshot_result(box)
        assert snapshot["trade_date"] == TRADE_DATE
        # 0.8：候选主集合来自 review 详细池；dragons 缺失不阻断池内其它资产
        assert ({item["code"] for item in snapshot["candidates"]}
                == {item["code"] for item in snapshot["review_candidates"]})
        assert all(item["review_pool"] for item in snapshot["candidates"])
        assert "dragons" in snapshot["sentiment"]["degraded"]


def test_p11c_historical_mirror_gap_degrades_but_keeps_other_days():
    with sandbox("p11c") as box:
        rows = sb.flatten_window_rows()
        mirror = [row for row in rows if row["trade_date"] != "2026-09-07"]
        seed_full(box, mirror_rows=mirror)
        result = run_prep(box, ["prep", "--date", DATE_ARG])
        assert_exit(result, 0)
        dragons = prep_assets(box)["dragons"]
        first_day = dragons["days"][0]
        assert first_day["trade_date"] == "2026-09-07"
        assert first_day["status"] == "error"
        assert first_day["error"].startswith("mirror_stale_day")
        assert dragons["count"] == 6
        snapshot = snapshot_result(box)
        assert "dragons_mirror_missing_day" in snapshot["sentiment"]["degraded"]
        assert snapshot["dragon_source"]["dates"][0]["status"] == "error"
        assert set(candidates_by_code(snapshot)) == {
            "600001", "600002", "600003", "600005", "600004"}
        assert "数据降级：龙头镜像缺日" in result.stdout


# ─── P12: window degradations ──────────────────────────────────────────────


def test_p12a_short_calendar_marks_window_short():
    with sandbox("p12a") as box:
        seed_full(box)

        def shorten(payload: dict) -> None:
            bars = payload["providers"]["tdx"]["get_kline"]["by_code"]["SH000001"]
            payload["providers"]["tdx"]["get_kline"]["by_code"]["SH000001"] = bars[-2:]

        scenario = write_variant(box, "scenario_short_calendar.json", shorten)
        result = run_prep(box, ["prep", "--date", DATE_ARG], scenario=scenario)
        assert_exit(result, 0)
        dragons = prep_assets(box)["dragons"]
        assert dragons["window_trade_dates"] == ["2026-09-10", "2026-09-11"]
        snapshot = snapshot_result(box)
        assert snapshot["dragon_source"]["window_days"] == 5
        assert len(snapshot["dragon_source"]["dates"]) == 2
        assert "dragons_window_short" in snapshot["sentiment"]["degraded"]
        assert set(candidates_by_code(snapshot)) == {
            "600001", "600002", "600003", "600005", "600004"}


def test_p12b_empty_window_is_empty_not_an_error():
    with sandbox("p12b") as box:
        seed_db(box, sb.dragon_db_seed([]), db_name="dragon.db")
        seed_db(box, sb.mirror_db_seed([]), db_name="assets.db")
        result = run_prep(box, ["prep", "--date", DATE_ARG])
        assert_exit(result, 0)
        report = report_of(prep_assets(box))
        assert report["dragons"]["status"] == "empty"
        snapshot = snapshot_result(box)
        # 0.8：candidates 与 review_candidates 是同一主集合（dragons 空不阻断池内其它资产）
        assert ({item["code"] for item in snapshot["candidates"]}
                == {item["code"] for item in snapshot["review_candidates"]})
        assert snapshot["dragon_source"]["count"] == 0
        assert "dragons_partial" in snapshot["sentiment"]["degraded"]


def test_p12c_unreadable_mirror_raises_inside_the_collector():
    with sandbox("p12c") as box:
        seed_db(box, sb.dragon_db_seed([]), db_name="dragon.db")
        # Required probe columns only -> schema shape passes, row reads fail.
        seed_db(box, {"tables": {"dragons": {
            "columns": {"trade_date": "TEXT NOT NULL", "code": "TEXT NOT NULL",
                        "name": "TEXT", "composite_score": "REAL",
                        "review_status": "TEXT"},
            "primary_key": ["trade_date", "code"],
            "rows": [],
        }}}, db_name="assets.db")
        result = run_prep(box, ["prep", "--date", DATE_ARG])
        assert_exit(result, 0)
        report = report_of(prep_assets(box))
        assert report["dragons"]["status"] == "error"
        assert "5日龙头读取失败" in report["dragons"]["error"]
        snapshot = snapshot_result(box)
        assert ({item["code"] for item in snapshot["candidates"]}
                == {item["code"] for item in snapshot["review_candidates"]})
        assert "dragons" in snapshot["sentiment"]["degraded"]


# ─── P13: macro degradation (C18 negative direction) ───────────────────────


def test_p13a_missing_instrument_degrades_without_a_yahoo_attempt():
    with sandbox("p13a") as box:
        seed_full(box)

        def drop_a50(payload: dict) -> None:
            payload["providers"]["tdx"]["get_macro_quotes"].pop("cn_a50")

        scenario = write_variant(box, "scenario_macro_missing.json", drop_a50)
        result = run_prep(box, ["prep", "--date", DATE_ARG], scenario=scenario)
        assert_exit(result, 0)
        macro_asset = prep_assets(box)["macro_global"]
        assert macro_asset["degraded"] == ["cn_a50"]
        # No Yahoo/urllib attempt: a fallback failure would be recorded here.
        assert macro_asset["source_errors"] == {}
        macro = snapshot_result(box)["macro"]
        assert macro["available"] is True
        assert macro["degraded"] == ["cn_a50"]
        assert macro["session"] == "same_day"
        degraded = snapshot_result(box)["sentiment"]["degraded"]
        assert "macro_partial" in degraded
        assert "macro_same_day_missing" in degraded
        assert "宏观部分品种缺失" in result.stdout
        assert "外围期指同日数据缺失" in result.stdout


def test_p13b_previous_session_index_marks_mixed_session():
    with sandbox("p13b") as box:
        seed_full(box)

        def shift_a50(payload: dict) -> None:
            record = payload["providers"]["tdx"]["get_macro_quotes"]["cn_a50"]
            record["session_date"] = "2026-09-10"
            record["raw_date"] = "20260910"

        scenario = write_variant(box, "scenario_macro_previous.json", shift_a50)
        result = run_prep(box, ["prep", "--date", DATE_ARG], scenario=scenario)
        assert_exit(result, 0)
        macro_asset = prep_assets(box)["macro_global"]
        assert macro_asset["degraded"] == []
        assert macro_asset["source_errors"] == {}
        snapshot = snapshot_result(box)
        assert snapshot["macro"]["session"] == "mixed"
        assert snapshot["macro"]["instruments"]["cn_a50"]["session_kind"] == \
            "previous_session"
        assert snapshot["macro"]["instruments"]["jp_nikkei"]["session_kind"] == \
            "same_day"
        assert "macro_same_day_missing" in snapshot["sentiment"]["degraded"]
        assert "macro_partial" not in snapshot["sentiment"]["degraded"]


# ─── P14: compute-stage isolation ──────────────────────────────────────────


def test_p14_single_factor_failure_is_isolated_and_snapshot_still_written():
    with sandbox("p14") as box:
        seed_full(box)
        assert_exit(run_prep(box, ["prep", "--date", DATE_ARG]), 0)
        # Unhashable stock code inside block_top raises inside
        # compute_main_boards only, which the compute-stage guard isolates.
        broken = sb.block_payload()
        broken["rows"][0]["stock_list"] = [{"code": ["not", "hashable"]}]
        exec_sql(box, "assets.db",
                 "UPDATE prep_assets SET payload_json = ? "
                 "WHERE trade_date = ? AND asset_type = 'ths_block_top'",
                 (json.dumps(broken, ensure_ascii=False), TRADE_DATE))
        offset = len(call_records(box))

        result = run_prep(box, ["prep", "--date", DATE_ARG, "--recompute",
                                "--json"])
        assert_exit(result, 0)
        assert transport_since(box, offset) == []
        payload = json.loads(result.stdout)
        assert payload["version"] == "0.8"
        assert payload["main_boards"] == []
        assert "main_boards" in payload["sentiment"]["factor_errors"]
        assert "TypeError" in payload["sentiment"]["factor_errors"]["main_boards"]
        # The other factors still computed from the untouched assets.
        assert payload["sentiment"]["limit_up_cnt"] == 62
        assert len(payload["candidates"]) == 5
        assert_json_equal(snapshot_result(box), payload)


# ─── C18 static coverage ───────────────────────────────────────────────────


# ─── P15 / P16: dual-mode clock (M9B1) ─────────────────────────────────────


def test_p15_runtime_clock_mode_drives_the_1640_gate():
    """Runtime-clock mode: the 16:40 gate must read the frozen clock, not now."""
    with sandbox("p15", now="2026-09-11T15:30:00+08:00") as box:
        result = run_prep(box, ["prep", "--date", DATE_ARG],
                          clock_mode="runtime")
        assert_exit(result, 1)
        assert result.stdout == ""
        assert result.stderr == (
            "❌ 复盘失败：2026-09-11 盘后数据尚未就绪，请 16:40 后运行 prep\n")
        assert not (box.data_dir / "assets.db").exists()


def test_p16_runtime_clock_mode_full_prep_matches_reviewed_golden():
    with sandbox("p16") as box:
        seed_full(box)
        result = run_prep(box, ["prep", "--date", DATE_ARG],
                          clock_mode="runtime")
        assert_exit(result, 0)
        assert result.stdout == golden_text("stdout.txt")
        assert_json_equal(prep_assets(box), golden_json("assets.json"))
        snapshot = snapshot_result(box)
        assert snapshot["version"] == "0.8"
        assert snapshot["trade_date"] == TRADE_DATE
        assert snapshot["sentiment"]["position_pct"] == 0.5


def test_c18_fake_macro_response_covers_every_macro_spec():
    scenario = json.loads(SCENARIO.read_text(encoding="utf-8"))
    assert set(scenario["providers"]["tdx"]["get_macro_quotes"]) == \
        set(sb.MACRO_KEYS)
    with sandbox("c18") as box:
        result = run_python(
            box,
            "from dragon_quant.infra.provider.macro import MACRO_KEYS\n"
            "print(','.join(MACRO_KEYS))\n")
        assert_exit(result, 0)
        # Cross-check the reviewed fixture against the production spec source.
        assert result.stdout.strip() == ",".join(sb.MACRO_KEYS)
