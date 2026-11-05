"""dashboard presenter 纯投影单测（方案 §5 / §9.1，TDD 先 RED）。

覆盖：
- 资产日历三态与 ``expected_ok/expected_total``（含 main 必须修改 1 的回归）；
- 合法缺席（same_day_only_history / first_day_no_plan / no_prev_plan /
  review_assets_disabled / not_attempted / legal_empty）；
- 完全缺失日返回全部 expected 缺失（不 404 的数据契约）；
- 主线跨日 delta、补名优先级、成效分组；
- 竞价采集状态 vs 决策质量；
- ``high_num`` derived 解码与「需重算」。
"""

from __future__ import annotations

import pytest

from web_ui.backend.presenters import auction_view, prep_view

TODAY = "2026-09-11"


def _calendar_day(**kwargs) -> dict:
    base = {
        "asset_types": [],
        "report": None,
        "report_error": None,
        "has_snapshot": False,
    }
    base.update(kwargs)
    return base


CORE = ["dragons", "ths_limit_up_pool", "ths_block_top", "macro_global"]
SAME_DAY = ["market_breadth", "fg_boards", "fund_flow"]
AUX = ["holdings", "cninfo_announcements", "prev_plan",
       "auction_feedback", "t1_intraday"]
ALL_ASSETS = CORE + SAME_DAY + ["stock_daily"] + AUX


def _full_assets(report_status: dict[str, str] | None = None) -> dict:
    """一日内全部 expected 资产都存在（历史日，无 prev_plan 依赖）。"""
    assets = {asset: {"rows": []} for asset in CORE + SAME_DAY + ["stock_daily"]}
    assets.update({asset: {"items": []} for asset in AUX})
    assets["collection_report"] = {
        asset: {"status": (report_status or {}).get(asset, "ok")}
        for asset in ALL_ASSETS
    }
    return assets


# ─── 日历三态 ──────────────────────────────────────────────────────────────


def test_complete_day_requires_all_expected_assets_and_snapshot():
    rows = {"2026-09-09": _calendar_day(
        asset_types=list(_full_assets()) + ["collection_report"],
        report=_full_assets()["collection_report"],
        has_snapshot=True,
    )}
    calendar = prep_view.build_calendar(
        rows=rows, start_date="2026-09-01", end_date=TODAY, today=TODAY,
        review_assets_enabled=True, first_snapshot_date="2026-09-01",
    )
    day = calendar["days"][0]
    assert day["status"] == "complete"
    assert day["expected_ok"] == day["expected_total"]
    assert set(day["expected_missing"]) == set()
    assert set(day["expected_degraded"]) == set()


def test_missing_any_expected_asset_makes_day_incomplete():
    assets = _full_assets()
    del assets["dragons"]
    report = assets.pop("collection_report")
    rows = {"2026-09-09": _calendar_day(
        asset_types=list(assets), report=report, has_snapshot=True)}
    day = prep_view.build_calendar(
        rows=rows, start_date="2026-09-01", end_date=TODAY, today=TODAY,
        review_assets_enabled=True, first_snapshot_date="2026-09-01",
    )["days"][0]
    assert day["status"] == "incomplete"
    assert "dragons" in day["expected_missing"]
    assert day["expected_ok"] == day["expected_total"] - 1


def test_current_day_missing_market_breadth_is_incomplete():
    """main 必须修改 1 回归：当日 same_day_only 缺失同样使当天不完备。"""
    assets = _full_assets()
    del assets["market_breadth"]
    report = assets.pop("collection_report")
    rows = {"2026-09-11": _calendar_day(
        asset_types=list(assets), report=report, has_snapshot=True)}
    day = prep_view.build_calendar(
        rows=rows, start_date="2026-09-01", end_date=TODAY, today=TODAY,
        review_assets_enabled=True, first_snapshot_date="2026-09-01",
    )["days"][0]
    assert day["status"] == "incomplete"
    assert "market_breadth" in day["expected_missing"]


def test_historical_same_day_only_without_report_is_exempt():
    """历史 same_day_only 不可补采：豁免且不误报。"""
    assets = {asset: {"rows": []} for asset in CORE}
    assets["collection_report"] = {"dragons": {"status": "ok"}}
    rows = {"2026-09-09": _calendar_day(
        asset_types=list(assets), report=assets["collection_report"],
        has_snapshot=True)}
    day = prep_view.build_calendar(
        rows=rows, start_date="2026-09-01", end_date=TODAY, today=TODAY,
        review_assets_enabled=True, first_snapshot_date="2026-09-01",
    )["days"][0]
    exempt = {item["asset_type"]: item["reason"] for item in day["exempt"]}
    assert exempt["market_breadth"] == "same_day_only_history"
    assert "market_breadth" not in day["expected_missing"]


def test_review_enabled_missing_stock_daily_is_incomplete():
    """main 必须修改 1 回归：review 启用且当日应有 stock_daily 时缺失 → 不完备。"""
    assets = {asset: {"rows": []} for asset in CORE + SAME_DAY}
    assets["collection_report"] = {
        asset: {"status": "ok"} for asset in CORE + SAME_DAY}
    rows = {"2026-09-11": _calendar_day(
        asset_types=list(assets), report=assets["collection_report"],
        has_snapshot=True)}
    enabled = prep_view.build_calendar(
        rows=rows, start_date="2026-09-01", end_date=TODAY, today=TODAY,
        review_assets_enabled=True, first_snapshot_date="2026-09-01",
    )["days"][0]
    assert enabled["status"] == "incomplete"
    assert "stock_daily" in enabled["expected_missing"]

    disabled = prep_view.build_calendar(
        rows=rows, start_date="2026-09-01", end_date=TODAY, today=TODAY,
        review_assets_enabled=False, first_snapshot_date="2026-09-01",
    )["days"][0]
    assert "stock_daily" not in disabled["expected_missing"]


def test_cninfo_error_makes_day_incomplete():
    """main 必须修改 1 回归：cninfo 有 report 且 error → 当天 incomplete。"""
    assets = _full_assets(report_status={"cninfo_announcements": "error"})
    report = assets.pop("collection_report")
    rows = {"2026-09-09": _calendar_day(
        asset_types=list(assets), report=report, has_snapshot=True)}
    day = prep_view.build_calendar(
        rows=rows, start_date="2026-09-01", end_date=TODAY, today=TODAY,
        review_assets_enabled=True, first_snapshot_date="2026-09-01",
    )["days"][0]
    assert day["status"] == "incomplete"
    assert "cninfo_announcements" in day["expected_degraded"]


def test_legal_empty_holdings_and_cninfo_count_as_ok():
    assets = _full_assets(report_status={
        "holdings": "empty", "cninfo_announcements": "empty"})
    report = assets.pop("collection_report")
    rows = {"2026-09-09": _calendar_day(
        asset_types=list(assets), report=report, has_snapshot=True)}
    day = prep_view.build_calendar(
        rows=rows, start_date="2026-09-01", end_date=TODAY, today=TODAY,
        review_assets_enabled=True, first_snapshot_date="2026-09-01",
    )["days"][0]
    assert day["status"] == "complete"
    exempt = {item["asset_type"]: item["reason"] for item in day["exempt"]}
    assert exempt["holdings"] == "legal_empty"
    assert exempt["cninfo_announcements"] == "legal_empty"


def test_core_empty_is_not_legal_empty():
    assets = _full_assets(report_status={"dragons": "empty"})
    report = assets.pop("collection_report")
    rows = {"2026-09-09": _calendar_day(
        asset_types=list(assets), report=report, has_snapshot=True)}
    day = prep_view.build_calendar(
        rows=rows, start_date="2026-09-01", end_date=TODAY, today=TODAY,
        review_assets_enabled=True, first_snapshot_date="2026-09-01",
    )["days"][0]
    assert day["status"] == "incomplete"
    assert "dragons" in day["expected_degraded"]


def test_completely_missing_day_status_and_latest_record_date():
    rows = {
        "2026-09-01": _calendar_day(asset_types=["collection_report"],
                                   report={}),
        "2026-09-10": _calendar_day(asset_types=list(_full_assets()),
                                    report=_full_assets()["collection_report"],
                                    has_snapshot=True),
    }
    calendar = prep_view.build_calendar(
        rows=rows, start_date="2026-09-01", end_date=TODAY, today=TODAY,
        review_assets_enabled=True, first_snapshot_date="2026-09-01",
    )
    # 只有 collection_report 的日期不算「有资产」，也不触发 latest。
    only_report = next(day for day in calendar["days"]
                       if day["trade_date"] == "2026-09-01")
    assert only_report["status"] == "missing"
    assert only_report["has_assets"] is False
    assert only_report["asset_count"] == 0
    # 完全缺失日仍给出全部 expected 缺失（前端点击用）。
    assert set(only_report["expected_missing"]) >= set(CORE)
    assert calendar["latest_record_date"] == "2026-09-10"


def test_first_day_prev_plan_is_exempt():
    assets = _full_assets()
    del assets["prev_plan"]
    report = assets.pop("collection_report")
    report.pop("prev_plan", None)
    rows = {"2026-09-01": _calendar_day(
        asset_types=list(assets), report=report, has_snapshot=True)}
    day = prep_view.build_calendar(
        rows=rows, start_date="2026-09-01", end_date=TODAY, today=TODAY,
        review_assets_enabled=True, first_snapshot_date="2026-09-01",
    )["days"][0]
    exempt = {item["asset_type"]: item["reason"] for item in day["exempt"]}
    assert exempt["prev_plan"] == "first_day_no_plan"
    assert "prev_plan" not in day["expected_missing"]


def test_no_prev_plan_exempts_feedback_assets():
    assets = _full_assets()
    del assets["prev_plan"]
    del assets["auction_feedback"]
    del assets["t1_intraday"]
    report = assets.pop("collection_report")
    for key in ("prev_plan", "auction_feedback", "t1_intraday"):
        report.pop(key, None)
    rows = {"2026-09-05": _calendar_day(
        asset_types=list(assets), report=report, has_snapshot=True)}
    day = prep_view.build_calendar(
        rows=rows, start_date="2026-09-01", end_date=TODAY, today=TODAY,
        review_assets_enabled=True, first_snapshot_date="2026-09-01",
    )["days"][0]
    exempt = {item["asset_type"]: item["reason"] for item in day["exempt"]}
    assert exempt["auction_feedback"] == "no_prev_plan"
    assert exempt["t1_intraday"] == "no_prev_plan"


# ─── 单日资产表 ────────────────────────────────────────────────────────────


def test_asset_rows_on_missing_day_return_all_expected_missing():
    rows = prep_view.build_asset_rows(
        payloads={}, trade_date="2026-09-01", today=TODAY,
        review_assets_enabled=True, has_prior_snapshot=False,
    )
    summary = prep_view.summarize_expected(rows)
    assert summary["expected_total"] == len(CORE)
    assert summary["expected_ok"] == 0
    assert all(not row["present"] for row in rows)
    for asset in CORE:
        assert next(row for row in rows if row["asset_type"] == asset)["expected"]


def test_asset_rows_categories_and_expected_flags():
    payloads = _full_assets()
    payloads.pop("collection_report")
    rows = prep_view.build_asset_rows(
        payloads=payloads, trade_date="2026-09-09", today=TODAY,
        review_assets_enabled=True, has_prior_snapshot=True,
    )
    by_type = {row["asset_type"]: row for row in rows}
    assert by_type["dragons"]["category"] == "core"
    assert by_type["market_breadth"]["category"] == "same_day_only"
    assert by_type["stock_daily"]["category"] == "review_conditional"
    assert by_type["prev_plan"]["category"] == "auxiliary"
    assert len(rows) == 13
    assert "collection_report" not in by_type
    # 历史 same_day_only 无 report 但有 payload → 仍 expected（payload 存在）。
    assert by_type["market_breadth"]["expected"] is True
    assert by_type["dragons"]["asset_complete"] is True


def test_asset_rows_as_of_mismatch_blocks_complete():
    payloads = _full_assets()
    payloads.pop("collection_report")
    payloads["dragons"]["as_of"] = "2026-09-01"
    rows = prep_view.build_asset_rows(
        payloads=payloads, trade_date="2026-09-09", today=TODAY,
        review_assets_enabled=True, has_prior_snapshot=True,
    )
    dragons = next(row for row in rows if row["asset_type"] == "dragons")
    assert dragons["asset_complete"] is False


def test_asset_rows_review_disabled_marks_stock_daily_exempt():
    payloads = {asset: {"rows": []} for asset in CORE}
    rows = prep_view.build_asset_rows(
        payloads=payloads, trade_date="2026-09-11", today=TODAY,
        review_assets_enabled=False, has_prior_snapshot=True,
    )
    stock_daily = next(row for row in rows if row["asset_type"] == "stock_daily")
    assert stock_daily["expected"] is False
    assert stock_daily["legal_absence"] == "review_assets_disabled"


# ─── derived（high_num） ───────────────────────────────────────────────────


def test_derived_decodes_packed_high_num_and_flags_recompute():
    snapshot = {
        "review": {
            "mainlines": [{
                "rank": 1, "board_name": "机器人", "mainline_score": 80.0,
                "high_num": 327685, "state": "stable",
                "evidence": ["最高 327685 板", "涨停 20 家"],
            }],
            "market": {
                "state": "strong", "score": 70.0,
                "inputs": {"max_board_height": 327685},
                "evidence": ["最高 327685 板"],
            },
        },
    }
    derived = prep_view.build_snapshot_derived(snapshot)
    assert derived["needs_recompute"] is True
    mainline = derived["mainlines"][0]
    assert mainline["stored_high_num"] == 327685
    assert mainline["board_height"] == 5
    assert mainline["needs_recompute"] is True
    assert mainline["evidence"] == ["最高 5 板", "涨停 20 家"]
    assert derived["market"]["source"] == "review"
    assert derived["market"]["max_board_height"] == 5
    assert derived["market"]["needs_recompute"] is True
    assert derived["market"]["evidence"] == ["最高 5 板"]


def test_derived_unpacked_values_are_untouched():
    snapshot = {
        "main_boards": [{"board_name": "AI算力", "high_num": 4,
                         "evidence": ["最高 4 板"]}],
        "sentiment": {"max_board_height": 4, "limit_up_cnt": 30,
                      "limit_down_cnt": 2},
    }
    derived = prep_view.build_snapshot_derived(snapshot)
    assert derived["needs_recompute"] is False
    assert derived["mainlines"][0]["source"] == "legacy"
    assert derived["mainlines"][0]["board_height"] == 4
    assert derived["market"]["source"] == "sentiment"
    assert derived["market"]["max_board_height"] == 4
    assert derived["market"]["evidence"][0] == "涨停 30 家 / 跌停 2 家"


def test_derived_invalid_packed_value_without_text_is_none():
    snapshot = {
        "review": {
            "mainlines": [{"board_name": "X", "high_num": 131075}],
            "market": {"inputs": {"max_board_height": 131075}},
        },
    }
    derived = prep_view.build_snapshot_derived(snapshot)
    assert derived["mainlines"][0]["board_height"] is None
    assert derived["market"]["max_board_height"] is None


# ─── review-context（跨日 / 补名 / 成效） ─────────────────────────────────


def _current_snapshot() -> dict:
    return {
        "version": "0.8",
        "review": {
            "mainlines": [{
                "rank": 1, "board_name": "AI算力", "mainline_score": 88.0,
                "state": "strengthening",
            }],
            "market": {"state": "strong", "score": 80.0, "coeff": 1.2,
                       "position_advice": "正常", "score_change": 5.0},
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


def _prev_snapshot() -> dict:
    return {
        "review": {
            "mainlines": [
                {"rank": 1, "board_name": "机器人", "mainline_score": 82.0},
                {"rank": 2, "board_name": "AI算力", "mainline_score": 80.0},
            ],
            "market": {"state": "neutral", "score": 70.0, "coeff": 1.0,
                       "position_advice": "谨慎"},
        },
        "sentiment": {"position_pct": 0.5, "position_advice": "谨慎"},
        "strategy_context": {
            "positions": [{"code": "600002", "name": "跟风B"}],
        },
    }


def _prev_meta() -> dict:
    return {"trade_date": "2026-09-10", "created_at": "x", "error": None}


def test_review_context_mainline_delta_and_names():
    context = prep_view.build_review_context(
        trade_date="2026-09-11", current=_current_snapshot(),
        prev=_prev_snapshot(), prev_meta=_prev_meta(),
        prev_plan={"positions": [{"code": "600001", "name": "龙头A(计划)"}]},
    )
    assert context["available"] is True
    mainline = context["mainlines"][0]
    assert mainline["rank"] == 1
    assert mainline["prev_rank"] == 2
    assert mainline["rank_delta"] == 1
    assert mainline["score_delta"] == 8.0
    assert mainline["state"] == "strengthening"

    names = {item["code"]: item for item in context["plan_names"]}
    assert names["600001"]["name"] == "龙头A(计划)"
    assert names["600001"]["source"] == "prev_plan_asset"
    assert names["600002"]["name"] == "跟风B"
    assert names["600002"]["source"] == "prev_snapshot_context"

    effect = context["plan_effect"]
    assert effect["available"] is True
    items = {item["code"]: item for item in effect["items"]}
    assert items["600001"]["name"] == "龙头A(计划)"
    assert items["600001"]["outcome_pct"] == 3.0
    assert items["600002"]["execution_known"] is False
    assert items["600002"]["actual_position"] is None
    groups = {group["key"]: group for group in effect["groups"]}
    assert groups["recommended"]["count"] == 1
    assert groups["recommended"]["mean_outcome_pct"] == 3.0
    assert groups["not_recommended"]["count"] == 1
    assert groups["not_recommended"]["mean_outcome_pct"] == -2.0
    assert groups["unknown"]["count"] == 0
    no_trade = effect["no_trade"][0]
    assert no_trade["judgement"] == "correct_avoidance"
    assert no_trade["name"] == "跟风B"
    assert no_trade["outcome_pct"] == -2.0


def test_review_context_market_side_by_side():
    context = prep_view.build_review_context(
        trade_date="2026-09-11", current=_current_snapshot(),
        prev=_prev_snapshot(), prev_meta=_prev_meta(), prev_plan=None,
    )
    market = context["plan_effect"]["market"]
    assert market["prev"]["state"] == "neutral"
    assert market["prev"]["score"] == 70.0
    assert market["prev"]["position_pct"] == 0.5
    assert market["current"]["state"] == "strong"
    assert market["score_change"] == 5.0


def test_review_context_missing_prev_snapshot_nulls_deltas():
    context = prep_view.build_review_context(
        trade_date="2026-09-11", current=_current_snapshot(),
        prev=None, prev_meta={"trade_date": None, "created_at": None,
                              "error": None},
        prev_plan=None,
    )
    mainline = context["mainlines"][0]
    assert mainline["prev_rank"] is None
    assert mainline["rank_delta"] is None
    assert mainline["score_delta"] is None
    assert mainline["unknown_reason"] == "prev_snapshot_missing"
    assert context["prev_trade_date"] is None
    assert context["plan_effect"]["market"]["prev"]["available"] is False


def test_review_context_corrupt_prev_snapshot_reason():
    context = prep_view.build_review_context(
        trade_date="2026-09-11", current=_current_snapshot(),
        prev=None, prev_meta={"trade_date": "2026-09-10", "created_at": None,
                              "error": "JSONDecodeError"},
        prev_plan=None,
    )
    assert context["prev_trade_date"] == "2026-09-10"
    assert context["prev_meta_error"] == "JSONDecodeError"
    assert context["mainlines"][0]["unknown_reason"] == "prev_snapshot_corrupt"
    assert "prev_snapshot_corrupt" in context["degraded"]


def test_review_context_without_current_snapshot():
    context = prep_view.build_review_context(
        trade_date="2026-09-11", current=None, prev=_prev_snapshot(),
        prev_meta=_prev_meta(), prev_plan=None,
    )
    assert context["available"] is False
    assert context["reason"] == "snapshot_missing"
    assert context["plan_effect"]["available"] is False
    assert context["plan_effect"]["reason"] == "plan_review_missing"


def test_plan_effect_without_feedback_still_shows_items():
    current = _current_snapshot()
    current["feedback"] = None
    context = prep_view.build_review_context(
        trade_date="2026-09-11", current=current, prev=None,
        prev_meta={"trade_date": None, "created_at": None, "error": None},
        prev_plan=None,
    )
    effect = context["plan_effect"]
    assert effect["available"] is True
    assert len(effect["items"]) == 2


# ─── auction 采集状态 vs 决策质量 ─────────────────────────────────────────


def _auction_row(code: str, **kwargs) -> dict:
    row = {
        "code": code, "version": "2", "is_weak": False, "strength": 80.0,
        "level": "强", "direction": "bull", "rank": 1,
        "validation_status": "ok", "data_quality": {"status": "ok"},
        "risk_events": [], "config_version": "auction_v2",
        "unverified": False, "created_at": "2026-09-11 09:26:00",
        "evidence": {"last_time": "09:25:00", "official_open_px": 10.5,
                     "last_indicative_price": 10.6,
                     "last_matched_lots": 50000,
                     "final_unmatched_lots_signed": 150},
    }
    row.update(kwargs)
    return row


def test_auction_day_collection_status_and_quality_counts():
    rows = [
        _auction_row("600001"),
        _auction_row("600002", is_weak=True),
        _auction_row("600003", strength=None, level=None),
        _auction_row("600004", error="analysis_json 不是对象"),
    ]
    view = auction_view.build_auction_day(
        trade_date="2026-09-11", rows=rows,
        names={"600001": "龙头A", "600002": "跟风B", "600003": "补涨C"},
        asset_codes=["600001", "600002", "600005"],
    )
    by_code = {row["code"]: row for row in view["snapshots"]}
    assert by_code["600001"]["collection_status"] == "ok"
    assert by_code["600001"]["name"] == "龙头A"
    assert by_code["600001"]["key_evidence"]["official_open_px"] == 10.5
    assert by_code["600003"]["collection_status"] == "snapshot_only"
    assert by_code["600004"]["collection_status"] == "snapshot_only"
    assert by_code["600004"]["error"] == "analysis_json 不是对象"

    run = view["run"]
    assert run["snapshot_count"] == 4
    assert run["asset_count"] == 3
    assert run["error_rows"] == 1
    assert run["snapshot_only_codes"] == ["600003", "600004"]
    assert run["asset_only_codes"] == ["600005"]
    # veto / 数据不足是决策质量，不是采集失败。
    assert run["quality_counts"]["vetoed"] == 1
    assert run["quality_counts"]["unavailable"] == 1
    assert run["quality_counts"]["ok"] == 1
    assert by_code["600002"]["collected"] is True
    assert by_code["600002"]["evaluated"] is True
    assert "600005" in view["asset_codes"]
    assert view["available"] is True


def test_auction_day_empty_date_is_200_shaped():
    view = auction_view.build_auction_day(
        trade_date="2026-09-12", rows=[], names={}, asset_codes=[])
    assert view["available"] is False
    assert view["count"] == 0
    assert view["run"]["snapshot_count"] == 0
    assert view["run"]["evaluation_available"] is False
    assert view["snapshots"] == []


def test_auction_calendar_three_states():
    rows = {
        "2026-09-09": {"asset_codes": ["600001"], "snapshot_codes": ["600001"]},
        "2026-09-10": {"asset_codes": ["600001"], "snapshot_codes": []},
        "2026-09-11": {"asset_codes": [], "snapshot_codes": ["600002"]},
    }
    calendar = auction_view.build_auction_calendar(
        rows=rows, start_date="2026-09-01", end_date="2026-09-11")
    by_date = {day["trade_date"]: day for day in calendar["days"]}
    assert by_date["2026-09-09"]["status"] == "complete"
    assert by_date["2026-09-10"]["status"] == "incomplete"
    assert by_date["2026-09-10"]["asset_only_codes"] == ["600001"]
    assert by_date["2026-09-11"]["status"] == "incomplete"
    assert by_date["2026-09-11"]["snapshot_only_codes"] == ["600002"]
    assert calendar["latest_record_date"] == "2026-09-11"
