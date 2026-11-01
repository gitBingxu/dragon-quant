"""``dashboard`` 命令 E2E：D1-D7（真实 CLI 子进程，离线）。

- D1 正常启动 + ``/api/meta`` 等 200 + SIGTERM 释放端口；
- D2 未装 [web] + 非 TTY：手动指令 + exit 1，绝不创建 pip 子进程；
- D3 ``--yes`` + pip 被 procguard 拒绝：exit 1 + 手动指令，无 traceback；
- D4 静态缓存头；
- D5 P0 端点冒烟（seed dragon.db / assets.db），含 trade-dates.items 全长；
- D6 入选后表现端点（fake xueqiu.get_kline，零网络）；
- D7 近几日卡片读原始扫描真龙榜（含被 5 日去重排除项），回测池不变。
"""

from __future__ import annotations

import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

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
PERF_SCENARIO = E2E_ROOT / "fixtures" / "scenario_dashboard_perf.json"
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
            for entry in dates["data"]:
                assert len(entry["items"]) == entry["count"]
                assert entry["preview"] == entry["items"][:3]
            by_date = {entry["trade_date"]: entry for entry in dates["data"]}
            # 09-11 的卡片来自原始扫描真龙榜：600002 is_true_dragon=False 不入卡。
            assert [item["code"] for item in by_date["2026-09-11"]["items"]] == [
                "600001",
            ]
            # 08-17 无扫描贡献 → 回退 dragons_v2（遗留日期兼容）。
            assert [item["code"] for item in by_date["2026-08-17"]["items"]] == [
                "600001",
            ]
            detail = server.request(
                "GET", "/api/dragons/20260911/600002").json()
            assert detail["dims_source"] == "scan_stocks"


def test_d6_performance_endpoint_uses_fake_kline_only():
    with sandbox("dashboard-d6") as box:
        seed_full(box)
        with start_web_server(
            _dashboard_command(),
            sandbox=box,
            port=0,
            ready_path="/",
            env_override={"DQ_E2E_SCENARIO": str(PERF_SCENARIO)},
        ) as server:
            response = server.request(
                "GET", "/api/dragons/20260817/600001/performance")
            assert response.status == 200
            body = response.json()

            assert body["status"] == "ok"
            assert body["as_of"] == "2026-09-11"
            assert body["buy_date"] == "2026-08-18"
            assert body["buy_price"] == 10.0
            assert body["max_return_since_entry"] == 8.0
            assert body["max_drawdown_since_entry"] == -4.0
            assert body["latest_bar_date"] == "2026-09-11"
            assert body["bars_in_window"] == 5
            assert body["bars_after_entry"] == 4
            assert set(body) == {
                "trade_date", "code", "caliber", "status", "as_of",
                "buy_date", "buy_price", "max_return_since_entry",
                "max_drawdown_since_entry", "peak_date", "latest_bar_date",
                "bars_in_window", "bars_after_entry", "error",
            }
            assert "max_return_5d" not in body
            assert "max_drawdown_5d" not in body


def _merge_scan_seeds(*seeds: dict) -> dict:
    merged: dict = {"tables": {}}
    for seed in seeds:
        for name, table in seed["tables"].items():
            if name in merged["tables"]:
                merged["tables"][name]["rows"].extend(table["rows"])
            else:
                merged["tables"][name] = {
                    "columns": table["columns"],
                    "primary_key": table["primary_key"],
                    "rows": list(table["rows"]),
                }
    return merged


def test_d7_recent_cards_use_raw_scan_true_dragons():
    """卡片读原始扫描真龙（含被 5 日去重排除的 rank1），回测池仍去重。"""
    with sandbox("dashboard-d7") as box:
        dragons = [
            sb.dragon_row("2026-08-17", "600003", "龙头C", 2, 80.0, ["AI算力"]),
            sb.dragon_row("2026-08-17", "600004", "龙头D", 3, 75.0, ["机器人"]),
        ]
        dragon_spec = sb.dragon_db_seed(dragons)
        scan_spec = _merge_scan_seeds(
            sb.scan_db_seed("v2_20260817_5", "2026-08-17", stocks=[
                sb.scan_stock_row("v2_20260817_5", "600001", "青岛食品", 1, 88.0),
                sb.scan_stock_row("v2_20260817_5", "600003", "龙头C", 2, 80.0),
                sb.scan_stock_row("v2_20260817_5", "600004", "龙头D", 3, 75.0),
            ]),
            # 当日全部真龙被去重：dragons_v2 无该日行。
            sb.scan_db_seed("v2_20260818_5", "2026-08-18", stocks=[
                sb.scan_stock_row("v2_20260818_5", "600005", "龙头E", 1, 82.0),
            ]),
        )
        dragon_spec["tables"].update(scan_spec["tables"])
        seed_db(box, dragon_spec)
        seed_db(box, sb.mirror_db_seed([]), db_name="assets.db")

        with start_web_server(
            _dashboard_command(),
            sandbox=box,
            port=0,
            ready_path="/",
            env_override={"DQ_E2E_SCENARIO": str(PERF_SCENARIO)},
        ) as server:
            dates = server.request("GET", "/api/dragons/trade-dates").json()
            by_date = {entry["trade_date"]: entry for entry in dates["data"]}

            cards = by_date["2026-08-17"]
            assert cards["count"] == 3
            assert [item["code"] for item in cards["items"]] == [
                "600001", "600003", "600004",
            ]
            assert cards["items"][0]["rank"] == 1
            assert cards["preview"] == cards["items"][:3]

            deduped_day = by_date["2026-08-18"]
            assert deduped_day["count"] == 1
            assert [item["code"] for item in deduped_day["items"]] == ["600005"]

            # 回测池 / 总览仍只读 dragons_v2（5 日去重不变）。
            pool = server.request(
                "GET",
                "/api/dragons?date_from=2026-08-17&date_to=2026-08-17").json()
            assert pool["count"] == 2
            assert {row["code"] for row in pool["data"]} == {"600003", "600004"}
            assert server.request("GET", "/api/summary").json()["total"] == 2

            # 明细回退：dragons_v2 无行，取原始扫描贡献与五维。
            detail = server.request(
                "GET", "/api/dragons/20260817/600001").json()
            assert detail["scan_id"] == "v2_20260817_5"
            assert detail["rank"] == 1
            assert detail["dims_source"] == "scan_stocks"
            assert detail["dims"]["drive"] == 90.0
            assert detail["review_loose"]["buy_date"] is None
            assert detail["review_strict"] is None

            # 绩效：记录存在性来自扫描贡献，日 K 走 fake provider。
            performance = server.request(
                "GET", "/api/dragons/20260817/600001/performance")
            assert performance.status == 200
            assert performance.json()["status"] == "ok"
            assert performance.json()["buy_date"] == "2026-08-18"

            # 两处都不存在才 404。
            missing = server.request("GET", "/api/dragons/20260817/600999")
            assert missing.status == 404
            missing_perf = server.request(
                "GET", "/api/dragons/20260817/600999/performance")
            assert missing_perf.status == 404


# ─── D8-D10：dashboard 运维与复盘成效（方案 §9.5） ─────────────────────────

PREP_CORE = ["dragons", "ths_limit_up_pool", "ths_block_top", "macro_global"]
PREP_SAME_DAY = ["market_breadth", "fg_boards", "fund_flow"]
PREP_AUX = ["holdings", "cninfo_announcements", "prev_plan",
            "auction_feedback", "t1_intraday"]
PREP_ALL = PREP_CORE + PREP_SAME_DAY + ["stock_daily"] + PREP_AUX


def _stock_daily_payload(day: str, code: str = "600001") -> dict:
    bar_days = ["2026-09-10", "2026-09-11", "2026-09-14", "2026-09-15",
                "2026-09-16", "2026-09-17", "2026-09-18"]
    bars = []
    price = 10.0
    for offset, bar_day in enumerate(bar_days):
        bars.append({
            "date": bar_day, "open": price + offset * 0.1,
            "high": price + offset * 0.1 + 0.2,
            "low": price + offset * 0.1 - 0.1,
            "close": price + offset * 0.1 + 0.05,
            "volume": 1000.0, "amount": 500_000_000.0,
        })
    flat = [
        {"date": bar_day, "open": 10.0, "high": 10.0, "low": 10.0,
         "close": 10.0, "volume": 1000.0, "amount": 500_000_000.0}
        for bar_day in bar_days
    ]
    return {"as_of": day, "trade_date": day, "rows": {code: bars, "600002": flat}}


def _prep_assets_for(day: str, *, omit: tuple[str, ...] = (),
                     report_overrides: dict[str, str] | None = None) -> dict:
    assets: dict[str, dict] = {}
    for asset_type in PREP_ALL:
        if asset_type in omit:
            continue
        if asset_type in ("holdings",):
            assets[asset_type] = {"as_of": day, "codes": []}
        elif asset_type == "cninfo_announcements":
            assets[asset_type] = {"as_of": day, "items": {}}
        elif asset_type == "prev_plan":
            assets[asset_type] = {
                "positions": [{"code": "600001", "name": "龙头A(计划)"}]}
        elif asset_type == "stock_daily":
            assets[asset_type] = _stock_daily_payload(day)
        else:
            assets[asset_type] = {"as_of": day, "rows": [{"code": "600001"}]}
    overrides = dict(report_overrides or {})
    assets["collection_report"] = {
        asset_type: {"status": overrides.get(asset_type, "ok")}
        for asset_type in PREP_ALL
        if asset_type not in omit or asset_type in overrides
    }
    return assets


def _prev_snapshot_result() -> dict:
    return {
        "trade_date": "2026-09-10",
        "version": "0.8",
        "review": {
            "mainlines": [
                {"rank": 1, "board_name": "机器人", "mainline_score": 82.0},
                {"rank": 2, "board_name": "AI算力", "mainline_score": 80.0},
            ],
            "market": {"state": "neutral", "score": 70.0, "coeff": 1.0,
                       "position_advice": "谨慎"},
            "degraded": [],
        },
        "sentiment": {"position_pct": 0.5, "position_advice": "谨慎"},
        "review_candidates": [
            {"code": "600001", "name": "龙头A", "review_score": 88.0,
             "review_grade": "强", "position_pct": 20,
             "eligibility": "eligible"},
            {"code": "600002", "name": "跟风B", "review_score": 62.5,
             "review_grade": "一般", "position_pct": 0},
        ],
        "strategy_context": {
            "positions": [{"code": "600002", "name": "跟风B"}],
        },
    }


def _current_snapshot_result() -> dict:
    return {
        "trade_date": "2026-09-11",
        "version": "0.8",
        "review": {
            "mainlines": [{
                "rank": 1, "board_name": "AI算力", "mainline_score": 88.0,
                "state": "strengthening", "high_num": 327685,
                "evidence": ["最高 327685 板"],
            }],
            "market": {
                "state": "strong", "score": 80.0, "coeff": 1.2,
                "position_advice": "正常", "score_change": 5.0,
                "inputs": {"max_board_height": 327685},
                "evidence": ["最高 327685 板"],
            },
            "degraded": [],
        },
        "review_candidates": [{"code": "600003", "name": "补涨C"}],
        "candidates": [{"code": "600004", "name": "银行D"}],
        "plan_review": {
            "plan_date": "2026-09-10", "eval_date": "2026-09-11",
            "items": [
                {"code": "600001", "planned_action": "execute_plan",
                 "planned_position": 20.0, "feedback_state": "confirmed",
                 "actual_position": 20.0, "execution_known": True,
                 "outcome_pct": 3.0},
                {"code": "600002", "planned_action": "cancel_entry",
                 "planned_position": 0.0, "feedback_state": "invalidated",
                 "actual_position": None, "execution_known": False,
                 "outcome_pct": -2.0},
            ],
            "no_trade": [{"code": "600002", "judgement": "correct_avoidance"}],
            "degraded": [],
        },
        "feedback": {"checkpoint": "T1_open", "items": [], "degraded": []},
    }


def _seed_prep_ops(box: EnvSandbox) -> None:
    dragon_spec = sb.dragon_db_seed([])
    seed_db(box, dragon_spec)
    assets_spec = sb.mirror_db_seed([])
    merged = _merge_scan_seeds(
        sb.prep_assets_tables(_prep_assets_for(
            "2026-09-08", omit=("stock_daily",)), trade_date="2026-09-08"),
        sb.prep_assets_tables(_prep_assets_for(
            "2026-09-09", report_overrides={"cninfo_announcements": "error"}),
            trade_date="2026-09-09"),
        sb.prep_assets_tables(_prep_assets_for("2026-09-10"),
                              trade_date="2026-09-10"),
        sb.prep_assets_tables(_prep_assets_for(
            "2026-09-11", omit=("market_breadth", "stock_daily")),
            trade_date="2026-09-11"),
        sb.prep_snapshot_rows([
            {"trade_date": "2026-09-10", "result": _prev_snapshot_result()},
            {"trade_date": "2026-09-11", "result": _current_snapshot_result()},
        ]),
    )
    assets_spec["tables"].update(merged["tables"])
    seed_db(box, assets_spec, db_name="assets.db")


def test_d8_prep_calendar_review_context_and_missing_day():
    with sandbox("dashboard-d8") as box:
        _seed_prep_ops(box)
        with start_dashboard(box) as server:
            calendar = server.request(
                "GET", "/api/prep/calendar?days=30&end=2026-09-11").json()
            assert calendar["available"] is True
            assert calendar["latest_record_date"] == "2026-09-11"
            by_date = {day["trade_date"]: day for day in calendar["days"]}
            assert by_date["2026-09-10"]["status"] == "complete"
            assert by_date["2026-09-11"]["status"] == "incomplete"
            assert "market_breadth" in by_date["2026-09-11"]["expected_missing"]
            assert by_date["2026-09-09"]["status"] == "incomplete"
            assert "cninfo_announcements" in by_date["2026-09-09"]["expected_degraded"]
            assert "stock_daily" in by_date["2026-09-11"]["expected_missing"]
            # 历史日 stock_daily 无 report/payload → not_attempted（不是当日应有）。
            exempt = {item["asset_type"]: item["reason"]
                      for item in by_date["2026-09-08"]["exempt"]}
            assert exempt["stock_daily"] in ("not_attempted",
                                             "review_assets_disabled")

            watermark = server.request("GET", "/api/prep/20260911").json()
            assert watermark["has_prior_snapshot"] is True
            assert watermark["first_record_day"] is False
            assert watermark["expected_ok"] == watermark["expected_total"] - 2
            by_type = {row["asset_type"]: row for row in watermark["assets"]}
            assert by_type["dragons"]["category"] == "core"
            assert by_type["dragons"]["asset_complete"] is True
            assert by_type["market_breadth"]["expected"] is True
            assert by_type["market_breadth"]["asset_complete"] is False

            missing_day = server.request("GET", "/api/prep/20200101")
            assert missing_day.status == 200
            body = missing_day.json()
            assert body["has_snapshot"] is False
            assert body["expected_ok"] == 0
            assert body["expected_total"] >= 4
            assert all(not row["present"] for row in body["assets"])

            snapshot = server.request(
                "GET", "/api/prep/20260911/snapshot").json()
            derived = snapshot["derived"]
            assert derived["needs_recompute"] is True
            assert derived["mainlines"][0]["board_height"] == 5
            assert derived["mainlines"][0]["evidence"][0] == "最高 5 板"
            assert derived["market"]["max_board_height"] == 5

            context = server.request(
                "GET", "/api/prep/20260911/review-context").json()
            assert context["available"] is True
            assert context["prev_trade_date"] == "2026-09-10"
            mainline = context["mainlines"][0]
            assert mainline["rank_delta"] == 1
            assert mainline["score_delta"] == 8.0
            assert mainline["state"] == "strengthening"
            names = {item["code"]: item for item in context["plan_names"]}
            assert names["600001"]["name"] == "龙头A(计划)"
            assert names["600001"]["source"] == "prev_plan_asset"
            effect = context["plan_effect"]
            assert effect["available"] is True
            groups = {group["key"]: group for group in effect["groups"]}
            assert groups["recommended"]["mean_outcome_pct"] == 3.0
            assert groups["not_recommended"]["mean_outcome_pct"] == -2.0


def _seed_scan_ops(box: EnvSandbox) -> None:
    dragon_spec = sb.dragon_db_seed([])
    merged = _merge_scan_seeds(
        sb.scan_db_seed("v2_20260911_5", "2026-09-11"),
        sb.scan_db_seed("v2_20260910_5", "2026-09-10"),
        sb.scan_log_seed("v2_20260911_5", [
            {"ts": 3.0, "category": "phase:A", "level": "info", "message": "A 完成"},
            {"ts": 2.0, "category": "api:tdx", "level": "error",
             "message": "timeout", "data": {"ok": False}},
            {"ts": 1.0, "category": "api:tdx", "level": "warn", "message": "retry",
             "data": {"ok": True}},
        ]),
    )
    dragon_spec["tables"].update(merged["tables"])
    seed_db(box, dragon_spec)
    seed_db(box, sb.mirror_db_seed([]), db_name="assets.db")


def test_d9_scan_runtime_states_and_logs():
    with sandbox("dashboard-d9") as box:
        _seed_scan_ops(box)
        with start_dashboard(box) as server:
            listing = server.request("GET", "/api/scans").json()
            by_id = {row["id"]: row for row in listing["data"]}
            assert by_id["v2_20260911_5"]["status"] == "completed_with_errors"
            assert by_id["v2_20260911_5"]["error_n"] == 1
            assert by_id["v2_20260911_5"]["log_entries"] == 3
            assert by_id["v2_20260911_5"]["true_dragon_n"] == 1
            assert by_id["v2_20260910_5"]["status"] == "logs_missing"
            assert by_id["v2_20260910_5"]["log_entries"] == 0

            page = server.request(
                "GET", "/api/scans/v2_20260911_5/logs?limit=2").json()
            assert page["available"] is True
            assert page["total"] == 3
            assert len(page["entries"]) == 2
            assert page["entries"][0]["message"] == "A 完成"
            assert "data" not in page["entries"][0]
            assert page["summary"]["api_error"] == 1
            assert page["summary"]["phases"][0]["key"] == "A"

            errors = server.request(
                "GET", "/api/scans/v2_20260911_5/logs?level=error").json()
            assert errors["total"] == 1

            missing = server.request(
                "GET", "/api/scans/v2_20260910_5/logs").json()
            assert missing["available"] is False
            assert missing["entries"] == []


def _auction_row(day: str, code: str, *, rank: int = 1, weak: bool = False) -> dict:
    return {
        "trade_date": day, "code": code, "source": "v2",
        "analysis_json": json.dumps({
            "version": "2", "code": code, "is_weak": weak,
            "strength": 80.0 if not weak else 10.0, "level": "弱" if weak else "强",
            "direction": "bear" if weak else "bull", "rank": rank,
            "validation_status": "ok", "data_quality": {"status": "ok"},
            "risk_events": [], "config_version": "auction_v2",
            "unverified": False,
            "evidence": {"last_time": "09:25:00", "official_open_px": 10.5,
                         "last_indicative_price": 10.6,
                         "last_matched_lots": 50000,
                         "final_unmatched_lots_signed": 150},
        }, ensure_ascii=False),
        "created_at": f"{day} 09:26:00",
    }


def _seed_auction_ops(box: EnvSandbox) -> None:
    seed_db(box, sb.dragon_db_seed([]))
    assets_spec = sb.mirror_db_seed([])
    merged = _merge_scan_seeds(
        sb.auction_assets_seed([
            {"trade_date": "2026-09-09", "code": "600001", "name": "龙头A",
             "items_json": "[]", "quote_json": None,
             "created_at": "2026-09-09 09:26:00"},
            {"trade_date": "2026-09-10", "code": "600001", "name": "龙头A",
             "items_json": "[]", "quote_json": None,
             "created_at": "2026-09-10 09:26:00"},
            {"trade_date": "2026-09-11", "code": "600001", "name": "龙头A",
             "items_json": "[]", "quote_json": None,
             "created_at": "2026-09-11 09:26:00"},
        ]),
        sb.auction_snapshots_seed([
            _auction_row("2026-09-10", "600001"),
            _auction_row("2026-09-11", "600001"),
        ]),
        sb.prep_assets_tables(
            {"stock_daily": _stock_daily_payload("2026-09-11")},
            trade_date="2026-09-11"),
    )
    assets_spec["tables"].update(merged["tables"])
    seed_db(box, assets_spec, db_name="assets.db")


def test_d10_auction_calendar_day_and_local_effect():
    with sandbox("dashboard-d10") as box:
        _seed_auction_ops(box)
        with start_dashboard(box) as server:
            calendar = server.request(
                "GET", "/api/auction/calendar?days=30&end=2026-09-11").json()
            by_date = {day["trade_date"]: day for day in calendar["days"]}
            assert by_date["2026-09-10"]["status"] == "complete"
            assert by_date["2026-09-11"]["status"] == "complete"
            assert by_date["2026-09-09"]["status"] == "incomplete"
            assert by_date["2026-09-09"]["asset_only_codes"] == ["600001"]
            assert calendar["latest_record_date"] == "2026-09-11"

            day = server.request("GET", "/api/auction/20260911").json()
            assert day["names"] == {"600001": "龙头A"}
            assert day["asset_codes"] == ["600001"]
            row = day["snapshots"][0]
            assert row["collection_status"] == "ok"
            assert row["key_evidence"]["official_open_px"] == 10.5
            assert day["run"]["snapshot_count"] == 1
            assert day["run"]["quality_counts"]["ok"] == 1

            empty = server.request("GET", "/api/auction/20200101")
            assert empty.status == 200
            assert empty.json()["available"] is False

            effect = server.request(
                "GET", "/api/auction/20260911/effect?window=20").json()
            assert effect["data_source"] == "local_stock_daily"
            assert effect["calendar_basis"] == "bars_union"
            assert effect["local_coverage"]["selected_codes"] == 1
            assert effect["local_coverage"]["codes_with_bars"] == 1
            assert len(effect["horizons"]) == 3
            assert {item["horizon"] for item in effect["selected_day"]} == {1, 3, 5}
            assert any("涨停标记" in caveat for caveat in effect["caveats"])
            # 默认 20 日/30 样本门槛未达 → 待评估，不是错误。
            assert effect["status"] == "insufficient_data"
            assert effect["reason"] == "insufficient_samples"

            bad = server.request(
                "GET", "/api/auction/20260911/effect?horizons=2")
            assert bad.status == 400


# ─── D11：计划日被否 / 推荐股 T+1/3/5 净收益（方案 §9.4） ─────────────────


def test_d11_prep_plan_followup_local_returns():
    with sandbox("dashboard-d11") as box:
        _seed_prep_ops(box)
        with start_dashboard(box) as server:
            followup = server.request(
                "GET", "/api/prep/20260911/plan-followup").json()
            assert followup["status"] == "ok"
            assert followup["plan_date"] == "2026-09-10"
            assert followup["plan_date_source"] == "plan_review"
            assert followup["data_source"] == "local_stock_daily"
            assert followup["horizons"] == [1, 3, 5]

            rows = {row["code"]: row for row in followup["rows"]}
            assert rows["600001"]["group"] == "recommended"
            assert rows["600002"]["group"] == "not_recommended"
            assert rows["600001"]["horizons"]["1"]["entry_date"] == "2026-09-11"
            for horizon, expected in (("1", 0.86), ("3", 2.84), ("5", 4.82)):
                cell = rows["600001"]["horizons"][horizon]
                assert cell["net_return_pct"] == pytest.approx(
                    expected, abs=1e-3)
                assert cell["outcome"] == "up"
            for horizon in ("1", "3", "5"):
                cell = rows["600002"]["horizons"][horizon]
                assert cell["net_return_pct"] == pytest.approx(
                    -0.13, abs=1e-6)
                assert cell["outcome"] == "down"

            horizon_1 = followup["summary"][0]
            groups = {group["group"]: group for group in horizon_1["groups"]}
            assert groups["recommended"]["valid"] == 1
            assert groups["not_recommended"]["valid"] == 1
            assert horizon_1["spread"] == pytest.approx(0.99, abs=1e-3)

            assert followup["local_coverage"]["plan_codes"] == 2
            assert any("13bp" in caveat for caveat in followup["caveats"])

            bad_horizon = server.request(
                "GET", "/api/prep/20260911/plan-followup?horizons=2")
            assert bad_horizon.status == 400
            bad_date = server.request(
                "GET", "/api/prep/not-a-date/plan-followup")
            assert bad_date.status == 400

            context = server.request(
                "GET", "/api/prep/20260911/review-context").json()
            verification = context["plan_effect"]["market"]["verification"]
            assert verification["advice"] == "hold"
            assert verification["conclusion"] == "correct"
            assert verification["evidence"]["score_change"] == 10.0
