"""prep 单日效果投影 ``day_effect``（方案 §7.2）。

覆盖板块 code/name 匹配、legacy 主线回退、严格逐股分组与 pending/cannot 区分。
"""

from __future__ import annotations

import unittest

from web_ui.backend.presenters.day_effect import build_day_effect

T = "2026-09-11"
NEXT = "2026-09-14"
CALENDAR = ["2026-09-10", T, NEXT, "2026-09-15", "2026-09-16", "2026-09-17"]


def _bar(day, *, open_px=10.0, high=10.5, low=9.5, close=10.2,
         volume=1000.0):
    return {"date": day, "open": open_px, "high": high, "low": low,
            "close": close, "volume": volume}


def _snapshot(*, position_pct=0.5, candidates=None, mainlines=None,
              main_boards=None) -> dict:
    payload = {
        "trade_date": T, "version": "0.8",
        "sentiment": {"position_pct": position_pct},
        "review_candidates": candidates if candidates is not None else [
            {"code": "600001", "name": "龙头A", "role": "leader",
             "review_score": 88.0, "eligibility": "eligible",
             "position_pct": 20},
            {"code": "600002", "name": "跟风B", "eligibility": "not_qualified",
             "position_pct": 0},
            {"code": "600003", "name": "跟风C", "eligibility": "eligible",
             "position_pct": 0},
            {"code": "600004", "name": "冲突D", "eligibility": "unknown",
             "position_pct": 20},
            {"code": "600005", "name": "缺仓E", "eligibility": "eligible",
             "position_pct": None},
        ],
    }
    if mainlines is not None:
        payload["review"] = {"mainlines": mainlines}
    if main_boards is not None:
        payload["main_boards"] = main_boards
    return payload


def _bars() -> dict:
    return {
        "600001": [
            _bar("2026-09-14", open_px=10.0, high=10.8, low=9.8, close=10.5),
            _bar("2026-09-15", open_px=10.5, high=11.2, low=9.9, close=11.0),
            _bar("2026-09-16", open_px=11.0, high=12.0, low=10.5, close=11.8),
            _bar("2026-09-17", open_px=11.8, high=11.9, low=10.0, close=10.4),
        ],
        "600003": [_bar("2026-09-14", open_px=None)],
    }


BLOCKS = {"rows": [
    {"name": "机器人", "code": "885517", "change": 2.31},
    {"name": "AI算力", "code": "885100", "change": 1.0},
]}
NEXT_BLOCKS = {"rows": [
    {"name": "机器人", "code": "885517", "change": 3.14},
    {"name": "AI算力", "code": "885100", "change": 1.5},
]}


def _build(**overrides):
    kwargs = dict(
        trade_date=T, snapshot=_snapshot(mainlines=[
            {"rank": 1, "board_name": "机器人", "category": "概念",
             "is_mainline": True},
            {"rank": 2, "board_name": "AI算力", "category": "概念",
             "is_mainline": False},
        ]),
        calendar=CALENDAR, blocks=BLOCKS, next_blocks=NEXT_BLOCKS,
        bars=_bars())
    kwargs.update(overrides)
    return build_day_effect(**kwargs)


class TestBoards(unittest.TestCase):
    def test_only_true_mainline_and_code_match(self):
        payload = _build()
        boards = payload["boards"]
        self.assertTrue(boards["available"])
        self.assertEqual(boards["source"], "review_mainlines")
        self.assertEqual(len(boards["rows"]), 1)
        row = boards["rows"][0]
        self.assertEqual(row["board_name"], "机器人")
        self.assertEqual(row["board_code"], "885517")
        self.assertEqual(row["match"], "code")
        self.assertEqual(row["t_change_pct"], 2.31)
        self.assertEqual(row["next_change_pct"], 3.14)
        self.assertEqual(boards["summary"], {
            "evaluated": 1, "missing": 0, "mean_change_pct": 3.14})

    def test_legacy_main_boards_are_full_fallback(self):
        payload = _build(snapshot=_snapshot(main_boards=[
            {"board_name": "机器人"}, {"board_name": "AI算力"}]))
        boards = payload["boards"]
        self.assertEqual(boards["source"], "main_boards")
        self.assertEqual([row["board_name"] for row in boards["rows"]],
                         ["机器人", "AI算力"])
        self.assertTrue(all(row["is_mainline"] for row in boards["rows"]))
        self.assertIn("legacy_main_boards", payload["warnings"])

    def test_unique_name_fallback_and_ambiguous(self):
        blocks = {"rows": [{"name": "机器人", "code": "885517", "change": 1.0}]}
        renamed = {"rows": [{"name": "机器人", "change": 2.0}]}
        row = _build(blocks=blocks, next_blocks=renamed)["boards"]["rows"][0]
        self.assertEqual(row["match"], "name")
        self.assertEqual(row["next_change_pct"], 2.0)
        self.assertIn("board_name_fallback", row["warnings"])

        ambiguous = {"rows": [{"name": "机器人", "change": 2.0},
                              {"name": "机器人", "change": 3.0}]}
        row = _build(blocks=blocks, next_blocks=ambiguous)["boards"]["rows"][0]
        self.assertIsNone(row["next_change_pct"])
        self.assertEqual(row["exclusion_reason"], "ambiguous_board_name")

    def test_missing_assets_and_changes(self):
        no_next = _build(blocks={"rows": [
            {"name": "机器人", "code": "885517", "change": 2.0}]},
            next_blocks=None)["boards"]
        self.assertTrue(no_next["available"])
        self.assertEqual(no_next["rows"][0]["exclusion_reason"],
                         "next_board_asset_missing")

        no_t = _build(blocks=None)["boards"]
        self.assertFalse(no_t["available"])
        self.assertEqual(no_t["reason"], "board_asset_missing")

        missing_change = _build(blocks={"rows": [
            {"name": "机器人", "code": "885517"}]})["boards"]
        self.assertEqual(missing_change["rows"][0]["exclusion_reason"],
                         "change_missing")

        missing_next = _build(next_blocks={"rows": [
            {"name": "银行", "code": "885999", "change": 1.0}]})["boards"]
        self.assertEqual(missing_next["rows"][0]["exclusion_reason"],
                         "next_board_missing")

    def test_board_code_missing(self):
        payload = _build(blocks={"rows": [{"name": "机器人", "change": 2.0}]},
                         next_blocks={"rows": [
                             {"name": "机器人", "change": 3.0}]})
        row = payload["boards"]["rows"][0]
        self.assertIsNone(row["board_code"])
        self.assertEqual(row["exclusion_reason"], "board_code_missing")
        # 仍可按唯一名称回退取得次日涨跌（但明确标注 code 缺失）。
        self.assertEqual(row["next_change_pct"], 3.0)


class TestStocks(unittest.TestCase):
    def test_groups_and_strict_results(self):
        payload = _build()
        stocks = payload["stocks"]
        self.assertTrue(stocks["available"])
        rows = {row["code"]: row for row in stocks["rows"]}
        self.assertEqual(rows["600001"]["group"], "recommended")
        self.assertEqual(rows["600002"]["group"], "not_recommended")
        self.assertEqual(rows["600003"]["group"], "not_recommended")
        self.assertEqual(rows["600004"]["group"], "data_insufficient")
        self.assertEqual(rows["600005"]["group"], "data_insufficient")
        self.assertIn("inconsistent_group", payload["warnings"][0])

        leader = rows["600001"]
        self.assertEqual(leader["status"], "completed")
        self.assertEqual(leader["buy_date"], NEXT)
        self.assertEqual(leader["buy_price"], 10.0)
        self.assertEqual(leader["max_return_5d"], 20.0)
        self.assertEqual(leader["max_drawdown_5d"], -2.0)
        self.assertTrue(leader["partial_window"])

        self.assertEqual(rows["600002"]["status"], "cannot_evaluate")
        self.assertEqual(rows["600002"]["reason"], "missing_bar")
        self.assertEqual(rows["600003"]["status"], "cannot_evaluate")
        self.assertEqual(rows["600003"]["reason"], "invalid_bar")

        summary = {group["group"]: group for group in stocks["summary"]}
        self.assertEqual(summary["recommended"]["valid"], 1)
        self.assertEqual(summary["recommended"]["mean_max_return_5d"], 20.0)
        self.assertEqual(summary["recommended"]["median_max_return_5d"], 20.0)
        self.assertEqual(summary["not_recommended"]["valid"], 0)
        self.assertEqual(summary["not_recommended"]["cannot_evaluate"], 2)

    def test_no_next_trading_day_marks_pending(self):
        payload = _build(calendar=["2026-09-10", T])
        self.assertIsNone(payload["next_trade_date"])
        self.assertTrue(all(row["status"] == "pending"
                            for row in payload["stocks"]["rows"]))
        self.assertEqual(payload["stocks"]["rows"][0]["reason"],
                         "next_trading_day_not_available")
        self.assertEqual(payload["status"], "insufficient_data")
        self.assertEqual(payload["reason"], "next_trading_day_unavailable")

    def test_window_not_started_maps_to_pending(self):
        # 买入 bar 有效但未来日历只有买入日 → 共享核窗口为空 → pending。
        payload = _build(calendar=["2026-09-10", T, NEXT])
        leader = next(row for row in payload["stocks"]["rows"]
                      if row["code"] == "600001")
        self.assertEqual(leader["status"], "pending")
        self.assertEqual(leader["reason"], "window_not_started")

    def test_snapshot_missing_status(self):
        payload = _build(snapshot=None)
        self.assertEqual(payload["status"], "insufficient_data")
        self.assertEqual(payload["reason"], "snapshot_missing")
        self.assertFalse(payload["boards"]["available"])
        self.assertFalse(payload["stocks"]["available"])

    def test_signal_missing_marks_all_candidates_data_insufficient(self):
        snapshot = _snapshot()
        snapshot["sentiment"] = {}
        payload = _build(snapshot=snapshot)
        rows = payload["stocks"]["rows"]
        self.assertTrue(all(row["group"] == "data_insufficient" for row in rows))
        self.assertTrue(all(row["eligibility"] is not None for row in rows))


if __name__ == "__main__":
    unittest.main()
