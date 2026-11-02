"""全历史逐股准确率纯核 + 分批加载（方案 §5 / §8）。

覆盖：prep / auction 信号选择、分母与方向、flat、未成熟、calendar/missing/invalid
bar 排除、逐股聚合、全历史 builder、bars_loader 递归拆分。
"""

from __future__ import annotations

import unittest

from web_ui.backend.presenters.accuracy import (
    AccuracyWindow,
    AuctionSignalStrategy,
    PrepSignalStrategy,
    SignalSample,
    build_auction_accuracy,
    build_prep_accuracy,
    evaluate_accuracy,
)
from web_ui.backend.presenters.bars_loader import (
    BarsTruncated,
    load_bars_resilient,
)

CALENDAR = ["2026-09-01", "2026-09-02", "2026-09-03",
            "2026-09-04", "2026-09-05"]
WINDOW = AccuracyWindow(entry_offset=1, exit_offset=3)


def _bar(day: str, *, open_px: float, close: float,
         volume: float = 1000.0) -> dict:
    high = max(open_px, close) + 0.1
    low = min(open_px, close) - 0.1
    return {"date": day, "open": open_px, "high": high, "low": low,
            "close": close, "volume": volume}


def _sample(code="600001", day="2026-09-01", direction="up", name="A",
            reason=None) -> SignalSample:
    return SignalSample(code=code, name=name, snapshot_date=day,
                        expected_direction=direction, group="recommended",
                        exclusion_reason=reason)


class TestEvaluateAccuracy(unittest.TestCase):
    def test_up_correct_and_wrong_and_flat(self):
        bars = {
            "600001": [_bar("2026-09-02", open_px=10.0, close=11.0),
                       _bar("2026-09-04", open_px=11.0, close=12.0)],
            "600002": [_bar("2026-09-02", open_px=10.0, close=10.5),
                       _bar("2026-09-04", open_px=10.5, close=9.0)],
            "600003": [_bar("2026-09-02", open_px=10.0, close=10.5),
                       _bar("2026-09-04", open_px=10.5, close=10.0)],
        }
        samples = [
            _sample("600001"),
            _sample("600002", name="B"),
            _sample("600003", name="C"),
        ]
        result = evaluate_accuracy(samples, bars, CALENDAR, WINDOW,
                                   cutoff="2026-09-02")
        summary = result["summary"]
        self.assertEqual(summary["correct"], 1)
        self.assertEqual(summary["valid"], 3)
        self.assertEqual(summary["excluded"], 0)
        self.assertAlmostEqual(summary["hit_rate"], 1 / 3, places=4)
        self.assertEqual(result["stocks"][0]["code"], "600001")  # 同 valid 按 code

    def test_exclusion_reasons_and_priority(self):
        bars = {
            "600001": [_bar("2026-09-02", open_px=10.0, close=11.0),
                       _bar("2026-09-04", open_px=11.0, close=12.0)],
        }
        samples = [
            _sample("600001"),                                  # correct
            _sample("600002", day="2026-09-03"),                # immature
            _sample("600003", day="2026-08-30"),                # calendar_unavailable
            _sample("600004"),                                  # missing_bar
            _sample("600005", reason="signal_missing"),         # 样本级优先
        ]
        result = evaluate_accuracy(samples, bars, CALENDAR, WINDOW,
                                   cutoff="2026-09-02")
        reasons = result["summary"]["excluded_by_reason"]
        self.assertEqual(reasons.get("immature"), 1)
        self.assertEqual(reasons.get("calendar_unavailable"), 1)
        self.assertEqual(reasons.get("missing_bar"), 1)
        self.assertEqual(reasons.get("signal_missing"), 1)
        self.assertEqual(result["summary"]["valid"], 1)

    def test_invalid_bar_is_excluded_not_counted(self):
        bars = {"600001": [
            _bar("2026-09-02", open_px=10.0, close=10.5),
            {"date": "2026-09-04", "open": 10.5, "high": 10.5,
             "low": 10.5, "close": 10.5, "volume": 0.0},
        ]}
        result = evaluate_accuracy([_sample("600001")], bars, CALENDAR,
                                   WINDOW, cutoff="2026-09-02")
        self.assertEqual(result["summary"]["valid"], 0)
        self.assertEqual(result["summary"]["excluded_by_reason"],
                         {"invalid_bar": 1})

    def test_cutoff_none_marks_all_immature(self):
        result = evaluate_accuracy([_sample()], {}, CALENDAR, WINDOW,
                                   cutoff=None)
        self.assertEqual(result["summary"]["excluded_by_reason"],
                         {"immature": 1})

    def test_stock_aggregation_across_days(self):
        bars = {
            "600001": [_bar("2026-09-02", open_px=10.0, close=11.0),
                       _bar("2026-09-04", open_px=11.0, close=12.0),
                       _bar("2026-09-03", open_px=10.0, close=10.5),
                       _bar("2026-09-05", open_px=10.5, close=9.0)],
        }
        samples = [_sample("600001"), _sample("600001", day="2026-09-02")]
        result = evaluate_accuracy(samples, bars, CALENDAR, WINDOW,
                                   cutoff="2026-09-02")
        stock = result["stocks"][0]
        self.assertEqual(stock["valid"], 2)
        self.assertEqual(stock["correct"], 1)
        self.assertEqual(stock["excluded"], 0)
        self.assertEqual(stock["hit_rate"], 0.5)


class TestPrepSignalStrategy(unittest.TestCase):
    def _payload(self, sentiment, candidates, version="0.8"):
        return {"version": version, "sentiment": sentiment,
                "review_candidates": candidates,
                "review": {"mainlines": []}}

    def test_build_day_only_eligible_with_position(self):
        selection = PrepSignalStrategy().select(self._payload(
            {"position_pct": 0.5},
            [
                {"code": "600001", "name": "A", "eligibility": "eligible",
                 "position_pct": 20},
                {"code": "600002", "name": "B", "eligibility": "eligible",
                 "position_pct": 0},
                {"code": "600003", "name": "C", "eligibility": "unknown",
                 "position_pct": 20},
                {"code": "600004", "name": "D", "eligibility": "eligible",
                 "position_pct": None},
            ]))
        self.assertEqual(selection["day_kind"], "build")
        self.assertIsNone(selection["snapshot_exclusion"])
        included = [s.code for s in selection["samples"]
                    if s.exclusion_reason is None]
        self.assertEqual(included, ["600001"])
        excluded = {s.code: s.exclusion_reason for s in selection["samples"]
                    if s.exclusion_reason}
        self.assertEqual(excluded, {"600003": "candidate_data_insufficient",
                                    "600004": "candidate_data_insufficient"})

    def test_no_build_day_uses_position_zero_without_eligibility(self):
        selection = PrepSignalStrategy().select(self._payload(
            {"position_pct": 0},
            [
                {"code": "600001", "name": "A", "eligibility": "eligible",
                 "position_pct": 0},
                {"code": "600002", "name": "B",
                 "eligibility": "not_qualified", "position_pct": 0},
                {"code": "600003", "name": "C", "eligibility": "eligible",
                 "position_pct": 20},
                {"code": "600004", "name": "D", "position_pct": None},
            ]))
        self.assertEqual(selection["day_kind"], "no_build")
        included = {s.code for s in selection["samples"]
                    if s.exclusion_reason is None}
        self.assertEqual(included, {"600001", "600002"})
        excluded = {s.code: s.exclusion_reason for s in selection["samples"]
                    if s.exclusion_reason}
        self.assertEqual(excluded, {"600004": "candidate_data_insufficient"})
        self.assertTrue(selection["warnings"])
        self.assertIn("inconsistent_group", selection["warnings"][0])

    def test_position_advice_fallback(self):
        no_build = PrepSignalStrategy().select(self._payload(
            {"position_advice": "空仓"},
            [{"code": "600001", "position_pct": 0}]))
        self.assertEqual(no_build["day_kind"], "no_build")
        build = PrepSignalStrategy().select(self._payload(
            {"position_advice": "重仓"},
            [{"code": "600001", "eligibility": "eligible",
              "position_pct": 20}]))
        self.assertEqual(build["day_kind"], "build")

    def test_signal_missing_marks_all_candidates(self):
        selection = PrepSignalStrategy().select(self._payload(
            {}, [{"code": "600001"}, {"code": "600002"}]))
        self.assertEqual(selection["day_kind"], "signal_missing")
        self.assertEqual(
            {s.exclusion_reason for s in selection["samples"]},
            {"market_signal_missing"})

    def test_unsupported_version_and_missing_candidates(self):
        unsupported = PrepSignalStrategy().select(self._payload(
            {"position_pct": 0.5}, [], version="0.6"))
        self.assertEqual(unsupported["snapshot_exclusion"],
                         "snapshot_unsupported")
        self.assertEqual(unsupported["samples"], [])

        missing = PrepSignalStrategy().select({
            "version": "0.8", "sentiment": {"position_pct": 0.5}})
        self.assertEqual(missing["snapshot_exclusion"],
                         "review_candidates_missing")

        empty = PrepSignalStrategy().select(self._payload(
            {"position_pct": 0.5}, []))
        self.assertIsNone(empty["snapshot_exclusion"])
        self.assertEqual(empty["samples"], [])


class TestAuctionSignalStrategy(unittest.TestCase):
    def _rows(self, **overrides):
        row = {"code": "600001", "version": "2", "level": "强",
               "direction": "bull", "is_weak": False,
               "data_quality": {"status": "ok"}, "unverified": False}
        row.update(overrides)
        return [row]

    def test_direction_rules(self):
        cases = [
            ({"level": "弱"}, "down"),
            ({"level": "强", "direction": "bear"}, "down"),
            ({"level": "中", "direction": "bear"}, "down"),
            ({"level": "强", "direction": "bull"}, "up"),
            ({"level": "中", "direction": "neutral"}, "up"),
        ]
        for overrides, expected in cases:
            selection = AuctionSignalStrategy().select(
                self._rows(**overrides), {"600001": "龙头A"})
            sample = selection["samples"][0]
            self.assertEqual(sample.expected_direction, expected, overrides)
            self.assertIsNone(sample.exclusion_reason)

    def test_signal_missing_and_row_exclusions(self):
        cases = [
            {"level": None},
            {"data_quality": {"status": "unavailable"}},
            {"unverified": True},
            {"level": "强", "direction": None},
        ]
        for overrides in cases:
            selection = AuctionSignalStrategy().select(
                self._rows(**overrides), {})
            self.assertEqual(selection["samples"][0].exclusion_reason,
                             "signal_missing", overrides)
        corrupt = AuctionSignalStrategy().select(
            self._rows(error="boom"), {})
        self.assertEqual(corrupt["exclusions"]["snapshot_corrupt"], 1)
        unsupported = AuctionSignalStrategy().select(
            self._rows(version="1"), {})
        self.assertEqual(unsupported["exclusions"]["snapshot_unsupported"], 1)


class TestBuilders(unittest.TestCase):
    def _snapshot(self, day: str, candidates) -> dict:
        return {"trade_date": day, "version": "0.8",
                "sentiment": {"position_pct": 0.5},
                "review_candidates": candidates,
                "review": {"mainlines": []}}

    def test_prep_builder_full_history_counts(self):
        snapshots = {
            "2026-09-01": self._snapshot("2026-09-01", [
                {"code": "600001", "name": "A", "eligibility": "eligible",
                 "position_pct": 20},
            ]),
            "2026-09-02": self._snapshot("2026-09-02", [
                {"code": "600001", "name": "A", "eligibility": "eligible",
                 "position_pct": 20},
            ]),
            "2026-09-03": self._snapshot("2026-09-03", []),
            "2026-09-04": self._snapshot("2026-09-04", [
                {"code": "600002", "eligibility": "eligible",
                 "position_pct": 20},
            ]),
        }
        bars = {
            "600001": [_bar("2026-09-02", open_px=10.0, close=11.0),
                       _bar("2026-09-04", open_px=11.0, close=12.0),
                       _bar("2026-09-03", open_px=10.0, close=10.5),
                       _bar("2026-09-05", open_px=10.5, close=9.0)],
        }
        result = build_prep_accuracy(
            snapshot_dates=sorted(snapshots),
            load_snapshot=lambda day: snapshots.get(day),
            bar_dates=CALENDAR,
            load_bars=lambda codes, date_from, date_to: {
                "rows": bars, "truncated": False,
                "bar_dates": CALENDAR, "codes": sorted(bars)},
            today="2026-09-05")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["as_of"], "2026-09-02")
        self.assertEqual(result["latest_bar_date"], "2026-09-05")
        self.assertEqual(result["snapshot_count"], 4)
        self.assertEqual(result["sample_unit"], "stock_day")
        self.assertEqual(result["day_kind_counts"],
                         {"build": 4, "no_build": 0, "signal_missing": 0})
        self.assertEqual(result["summary"]["correct"], 1)
        self.assertEqual(result["summary"]["valid"], 2)
        self.assertEqual(result["excluded_snapshot_dates"],
                         {"snapshot_unsupported": 0, "snapshot_corrupt": 0,
                          "review_candidates_missing": 0})
        self.assertEqual(result["window"]["entry_offset"], 1)
        self.assertEqual(result["window"]["exit_offset"], 3)

    def test_prep_builder_empty_db_and_truncation(self):
        no_snapshots = build_prep_accuracy(
            snapshot_dates=[], load_snapshot=lambda day: None,
            bar_dates=[], load_bars=lambda *args: {}, today="2026-09-05")
        self.assertEqual(no_snapshots["status"], "insufficient_data")
        self.assertEqual(no_snapshots["reason"], "no_snapshots")
        self.assertIsNone(no_snapshots["as_of"])

        def truncated(*args):
            return {"rows": {}, "truncated": True, "bar_dates": [], "codes": []}

        truncated_result = build_prep_accuracy(
            snapshot_dates=["2026-09-01"],
            load_snapshot=lambda day: self._snapshot(day, [
                {"code": "600001", "eligibility": "eligible",
                 "position_pct": 20}]),
            bar_dates=CALENDAR, load_bars=truncated, today="2026-09-05")
        self.assertEqual(truncated_result["status"], "insufficient_data")
        self.assertEqual(truncated_result["reason"], "local_bars_truncated")
        self.assertEqual(truncated_result["stocks"], [])

    def test_auction_builder_window_and_day_kind(self):
        rows = {
            "2026-09-01": [{"code": "600001", "version": "2", "level": "强",
                            "direction": "bear", "is_weak": False,
                            "data_quality": {"status": "ok"},
                            "unverified": False}],
        }
        bars = {"600001": [
            _bar("2026-09-01", open_px=10.0, close=10.5),
            _bar("2026-09-03", open_px=10.5, close=9.0),
        ]}
        result = build_auction_accuracy(
            snapshot_dates=["2026-09-01"],
            load_snapshot_rows=lambda day: rows[day],
            load_names=lambda day: {"600001": "龙头A"},
            bar_dates=CALENDAR,
            load_bars=lambda codes, date_from, date_to: {
                "rows": bars, "truncated": False,
                "bar_dates": CALENDAR, "codes": sorted(bars)},
            today="2026-09-05")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["as_of"], "2026-09-03")  # calendar[-3]
        self.assertEqual(result["window"]["entry_offset"], 0)
        self.assertEqual(result["window"]["exit_offset"], 2)
        self.assertEqual(result["day_kind_counts"], {})
        self.assertEqual(result["summary"]["correct"], 1)


class TestBarsLoader(unittest.TestCase):
    def test_direct_success_dedupes_and_sorts(self):
        def load(codes, date_from, date_to):
            return {"rows": {"600001": [
                {"date": "2026-09-03", "close": 3},
                {"date": "2026-09-02", "close": 2},
            ]}, "truncated": False, "bar_dates": [], "codes": []}

        result = load_bars_resilient(load, ["600001"], "2026-09-01", "2026-09-05")
        self.assertFalse(result["truncated"])
        self.assertEqual([bar["date"] for bar in result["rows"]["600001"]],
                         ["2026-09-02", "2026-09-03"])
        self.assertEqual(result["bar_dates"], ["2026-09-02", "2026-09-03"])

    def test_truncated_splits_by_date_then_codes(self):
        calls: list[tuple] = []

        def load(codes, date_from, date_to):
            calls.append((tuple(codes), date_from, date_to))
            # 宽区间 / 多 code 始终截断；单 code + 单日才成功。
            if date_from != date_to or len(codes) > 1:
                return {"rows": {}, "truncated": True,
                        "bar_dates": [], "codes": []}
            return {"rows": {codes[0]: [{"date": date_from, "close": 10.0}]},
                    "truncated": False, "bar_dates": [], "codes": [codes[0]]}

        result = load_bars_resilient(
            load, ["600001", "600002"], "2026-09-01", "2026-09-04")
        self.assertFalse(result["truncated"])
        self.assertEqual(set(result["rows"]), {"600001", "600002"})
        self.assertTrue(any(date_from != date_to
                            for _, date_from, date_to in calls))

    def test_single_code_single_day_still_truncated_raises(self):
        def load(codes, date_from, date_to):
            return {"rows": {}, "truncated": True,
                    "bar_dates": [], "codes": []}

        with self.assertRaises(BarsTruncated):
            load_bars_resilient(load, ["600001"], "2026-09-01", "2026-09-01")


if __name__ == "__main__":
    unittest.main()


class TestStockNameAggregation(unittest.TestCase):
    def test_later_non_null_name_fills_stock_row(self):
        bars = {"600001": [
            _bar("2026-09-02", open_px=10.0, close=11.0),
            _bar("2026-09-04", open_px=11.0, close=12.0),
            _bar("2026-09-03", open_px=10.0, close=10.5),
            _bar("2026-09-05", open_px=10.5, close=9.0),
        ]}
        samples = [_sample("600001", name=None),
                   _sample("600001", day="2026-09-02", name="龙头A")]
        result = evaluate_accuracy(samples, bars, CALENDAR, WINDOW,
                                   cutoff="2026-09-02")
        self.assertEqual(result["stocks"][0]["name"], "龙头A")
