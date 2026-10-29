"""prep / auction 决策结果回测（``factor decision-review``）纯核心测试。

覆盖方案 ``archived_md/factor_framework/2026-10-29-decision-outcome-review-technical-design.md``
§5 分组映射、§6 账本、§9 指标、§12 边界、§14.1 单元测试。

全部离线：行情与快照均由测试注入（鸭子类型），核心模块零 I/O。
"""

from __future__ import annotations

import math
import unittest

from dragon_quant.infra.factor import decision_review, review_shadow
from dragon_quant.infra.factor import stats as fstats

DAY = "2026-05-19"
CAL = ["2026-05-19", "2026-05-20", "2026-05-21", "2026-05-22", "2026-05-25"]


# ─── helpers ──────────────────────────────────────────────────────────────


def _bar(day, open_, volume=1_000_000.0, amount=500_000_000.0, **extra):
    payload = {"trade_date": day, "open": open_, "close": open_,
               "high": open_, "low": open_, "volume": volume, "amount": amount}
    payload.update(extra)
    return payload


class _Market:
    def __init__(self, bars, calendar=None):
        self._bars = bars
        self._calendar = list(calendar) if calendar is not None else list(CAL)

    def daily(self, code, start):
        return self._bars.get(code, [])

    def calendar(self, start, end):
        return [day for day in self._calendar if start <= day <= end]


class _PrepStore:
    def __init__(self, days):
        self._days = days

    def snapshot_dates(self, date_from, date_to):
        return [day for day in sorted(self._days) if date_from <= day <= date_to]

    def load(self, snapshot_date):
        payload, created = self._days[snapshot_date]
        return payload, {"created_at": created}


class _AuctionStore:
    def __init__(self, days):
        self._days = days

    def snapshot_dates(self, date_from, date_to):
        return [day for day in sorted(self._days) if date_from <= day <= date_to]

    def load(self, snapshot_date):
        return self._days[snapshot_date]


def _cand(code, eligibility="eligible", position=4.0, score=70.0, grade="B",
          name="x", **extra):
    payload = {"code": code, "name": name, "eligibility": eligibility,
               "position_pct": position, "review_score": score,
               "review_grade": grade}
    payload.update(extra)
    return payload


def _prep_payload(candidates, *, version="0.7", trade_date=DAY, frozen_at=None):
    return {
        "version": version,
        "trade_date": trade_date,
        "review_candidates": candidates,
        "strategy_context": {"frozen_at": frozen_at or f"{trade_date}T16:40:00+08:00"},
    }


def _auction_row(code, *, version="2", is_weak=False, strength=70.0,
                 level="强", unverified=False, status="ok", error=None,
                 created_at="2026-05-19 09:25:00", trade_date=DAY):
    row = {"trade_date": trade_date, "code": code, "version": version,
           "is_weak": is_weak, "strength": strength, "level": level,
           "unverified": unverified}
    if status is not None:
        row["data_quality"] = {"status": status}
    if created_at is not None:
        row["created_at"] = created_at
    if error is not None:
        row["error"] = error
    return row


def _group_of(adapter, date, payload_or_rows, code):
    day = adapter.map_day(date, payload_or_rows)
    for obs in day.observations:
        if obs.code == code:
            return obs.group, day
    return None, day


# ─── §9 Wilson ────────────────────────────────────────────────────────────


class TestWilsonInterval(unittest.TestCase):
    def test_known_value(self):
        low, high = fstats.wilson_interval(16, 27)
        self.assertAlmostEqual(low, 0.4073, places=3)
        self.assertAlmostEqual(high, 0.7548, places=3)

    def test_empty_and_bounds(self):
        self.assertIsNone(fstats.wilson_interval(0, 0))
        self.assertIsNone(fstats.wilson_interval(1, 0))
        low, high = fstats.wilson_interval(0, 10)
        self.assertAlmostEqual(low, 0.0, places=6)
        self.assertLessEqual(high, 1.0)
        low, high = fstats.wilson_interval(10, 10)
        self.assertGreaterEqual(low, 0.0)
        self.assertAlmostEqual(high, 1.0, places=6)


# ─── §5.3 prep 分组映射 ──────────────────────────────────────────────────


class TestPrepAdapterMapping(unittest.TestCase):
    def _adapter(self):
        return decision_review.PrepSnapshotAdapter(_PrepStore({}))

    def test_recommended_requires_eligible_and_positive_position(self):
        adapter = self._adapter()
        payload = _prep_payload([_cand("600001")])
        group, _ = _group_of(adapter, DAY, payload, "600001")
        self.assertEqual(group, "recommended")

    def test_position_zero_wins_over_eligibility(self):
        adapter = self._adapter()
        payload = _prep_payload([
            _cand("600001", eligibility="not_qualified", position=0)])
        group, _ = _group_of(adapter, DAY, payload, "600001")
        self.assertEqual(group, "not_recommended")

    def test_float_zero_is_not_recommended(self):
        adapter = self._adapter()
        payload = _prep_payload([_cand("600001", position=0.0)])
        group, _ = _group_of(adapter, DAY, payload, "600001")
        self.assertEqual(group, "not_recommended")

    def test_bool_position_is_insufficient_and_counted(self):
        adapter = self._adapter()
        payload = _prep_payload([_cand("600001", position=True)])
        group, day = _group_of(adapter, DAY, payload, "600001")
        self.assertEqual(group, "data_insufficient")
        self.assertEqual(day.counters["unparsed_position_pct"], 1)

    def test_string_position_is_not_guessed(self):
        adapter = self._adapter()
        payload = _prep_payload([_cand("600001", position="4")])
        group, day = _group_of(adapter, DAY, payload, "600001")
        self.assertEqual(group, "data_insufficient")
        self.assertEqual(day.counters["unparsed_position_pct"], 1)

    def test_positive_position_but_not_eligible_is_insufficient(self):
        adapter = self._adapter()
        payload = _prep_payload([_cand("600001", eligibility="unknown",
                                       position=3.0)])
        group, day = _group_of(adapter, DAY, payload, "600001")
        self.assertEqual(group, "data_insufficient")
        self.assertEqual(day.counters["unparsed_position_pct"], 0)

    def test_null_position_is_insufficient_without_unparsed_counter(self):
        adapter = self._adapter()
        payload = _prep_payload([_cand("600001", position=None)])
        group, day = _group_of(adapter, DAY, payload, "600001")
        self.assertEqual(group, "data_insufficient")
        self.assertEqual(day.counters["unparsed_position_pct"], 0)

    def test_duplicate_code_keeps_first(self):
        adapter = self._adapter()
        payload = _prep_payload([_cand("600001", position=4.0),
                                 _cand("600001", position=0.0)])
        day = adapter.map_day(DAY, payload)
        self.assertEqual(len(day.observations), 1)
        self.assertEqual(day.observations[0].group, "recommended")
        self.assertEqual(day.counters["duplicate_code"], 1)

    def test_empty_candidates_is_empty_decision_not_malformed(self):
        adapter = self._adapter()
        day = adapter.map_day(DAY, _prep_payload([]))
        self.assertEqual(day.observations, ())
        self.assertEqual(day.counters["empty_decision"], 1)
        self.assertEqual(day.counters["malformed"], 0)

    def test_unsupported_version_produces_no_observation(self):
        adapter = self._adapter()
        day = adapter.map_day(DAY, _prep_payload([_cand("600001")], version="0.6"))
        self.assertEqual(day.observations, ())
        self.assertEqual(day.counters["unsupported_version"], 1)

    def test_missing_version_is_unsupported(self):
        adapter = self._adapter()
        payload = _prep_payload([_cand("600001")])
        del payload["version"]
        day = adapter.map_day(DAY, payload)
        self.assertEqual(day.observations, ())
        self.assertEqual(day.counters["unsupported_version"], 1)

    def test_non_array_candidates_is_malformed(self):
        adapter = self._adapter()
        payload = _prep_payload([])
        payload["review_candidates"] = {"not": "a list"}
        day = adapter.map_day(DAY, payload)
        self.assertEqual(day.observations, ())
        self.assertEqual(day.counters["malformed"], 1)

    def test_available_at_is_fixed_1640_contract(self):
        adapter = self._adapter()
        day = adapter.map_day(DAY, _prep_payload([_cand("600001")]))
        self.assertEqual(day.observations[0].available_at,
                         f"{DAY}T16:40:00+08:00")

    def test_snapshot_date_comes_from_db_key_not_payload(self):
        adapter = self._adapter()
        payload = _prep_payload([_cand("600001")], trade_date="2026-05-01")
        day = adapter.map_day(DAY, payload)
        self.assertEqual(day.observations[0].snapshot_date, DAY)

    def test_frozen_at_mismatch_is_counted(self):
        adapter = self._adapter()
        payload = _prep_payload([_cand("600001")],
                                frozen_at="2026-05-18T16:40:00+08:00")
        day = adapter.map_day(DAY, payload)
        self.assertEqual(day.counters["frozen_at_mismatch"], 1)


# ─── §5.3 auction 分组映射 ────────────────────────────────────────────────


class TestAuctionAdapterMapping(unittest.TestCase):
    def _adapter(self):
        return decision_review.AuctionSnapshotAdapter(_AuctionStore({}))

    def test_unavailable_beats_vetoed(self):
        adapter = self._adapter()
        row = _auction_row("600001", status="unavailable", is_weak=True)
        group, _ = _group_of(adapter, DAY, [row], "600001")
        self.assertEqual(group, "unavailable")

    def test_vetoed_beats_level(self):
        adapter = self._adapter()
        row = _auction_row("600001", is_weak=True, level="弱")
        group, _ = _group_of(adapter, DAY, [row], "600001")
        self.assertEqual(group, "vetoed")

    def test_null_level_without_weak_is_unavailable(self):
        adapter = self._adapter()
        row = _auction_row("600001", is_weak=False, level=None)
        group, day = _group_of(adapter, DAY, [row], "600001")
        self.assertEqual(group, "unavailable")
        self.assertEqual(day.counters["unmapped_level"], 0)

    def test_degraded_status_still_maps_by_level(self):
        adapter = self._adapter()
        row = _auction_row("600001", status="degraded", level="强")
        group, _ = _group_of(adapter, DAY, [row], "600001")
        self.assertEqual(group, "strong")

    def test_missing_data_quality_falls_through_to_level(self):
        adapter = self._adapter()
        row = _auction_row("600001", status=None, level="中")
        group, _ = _group_of(adapter, DAY, [row], "600001")
        self.assertEqual(group, "medium")

    def test_missing_unverified_key_fails_closed(self):
        adapter = self._adapter()
        row = _auction_row("600001")
        del row["unverified"]
        group, _ = _group_of(adapter, DAY, [row], "600001")
        self.assertEqual(group, "unavailable")

    def test_missing_is_weak_is_not_vetoed(self):
        adapter = self._adapter()
        row = _auction_row("600001", level="中")
        del row["is_weak"]
        group, _ = _group_of(adapter, DAY, [row], "600001")
        self.assertEqual(group, "medium")

    def test_null_strength_is_unavailable(self):
        adapter = self._adapter()
        row = _auction_row("600001", strength=None, level="中")
        group, _ = _group_of(adapter, DAY, [row], "600001")
        self.assertEqual(group, "unavailable")

    def test_unknown_level_is_unavailable_and_counted(self):
        adapter = self._adapter()
        row = _auction_row("600001", level="超强")
        group, day = _group_of(adapter, DAY, [row], "600001")
        self.assertEqual(group, "unavailable")
        self.assertEqual(day.counters["unmapped_level"], 1)

    def test_v1_row_is_unsupported(self):
        adapter = self._adapter()
        row = _auction_row("600001", version="1")
        day = adapter.map_day(DAY, [row])
        self.assertEqual(day.observations, ())
        self.assertEqual(day.counters["unsupported_version"], 1)
        self.assertEqual(day.counters["usable_rows"], 0)

    def test_corrupt_row_is_counted(self):
        adapter = self._adapter()
        row = _auction_row("600001", error="JSONDecodeError: bad")
        day = adapter.map_day(DAY, [row])
        self.assertEqual(day.observations, ())
        self.assertEqual(day.counters["corrupt_rows"], 1)

    def test_duplicate_code_keeps_first(self):
        adapter = self._adapter()
        rows = [_auction_row("600001", level="强"),
                _auction_row("600001", level="弱")]
        day = adapter.map_day(DAY, rows)
        self.assertEqual(len(day.observations), 1)
        self.assertEqual(day.observations[0].group, "strong")
        self.assertEqual(day.counters["duplicate_code"], 1)

    def test_available_at_is_fixed_0925_contract(self):
        adapter = self._adapter()
        day = adapter.map_day(DAY, [_auction_row("600001")])
        self.assertEqual(day.observations[0].available_at,
                         f"{DAY}T09:25:00+08:00")


# ─── §6 账本 + §9 指标 ────────────────────────────────────────────────────


def _prep_run(store, bars, *, horizons=(1,), cost_bps=13.0, min_days=1,
              min_group_samples=1, include_samples=False, calendar=None,
              date_from=None, date_to=None):
    adapter = decision_review.PrepSnapshotAdapter(store)
    return decision_review.run_decision_review(
        adapter, market=_Market(bars, calendar or CAL),
        date_from=date_from or DAY, date_to=date_to or "2026-05-25",
        horizons=horizons, cost_bps=cost_bps, min_days=min_days,
        min_group_samples=min_group_samples, include_samples=include_samples)


def _three_group_setup():
    payload = _prep_payload([
        _cand("600001"),                                  # recommended
        _cand("600002", eligibility="not_qualified", position=0),  # not_recommended
        _cand("600003", position=None),                   # data_insufficient
    ])
    store = _PrepStore({DAY: (payload, "2026-05-19 17:02:11")})
    bars = {
        "600001": [_bar("2026-05-20", 10.0), _bar("2026-05-21", 11.0)],
        "600002": [_bar("2026-05-20", 10.0), _bar("2026-05-21", 9.0)],
        "600003": [_bar("2026-05-20", 10.0), _bar("2026-05-21", 10.0)],
    }
    return store, bars


class TestLedgerAndMetrics(unittest.TestCase):
    def test_groups_directions_and_spreads(self):
        store, bars = _three_group_setup()
        payload = _prep_run(store, bars, horizons=(1,))
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["source"], "prep")
        groups = {group["group"]: group for group in payload["groups"]}
        self.assertEqual(list(groups), ["recommended", "not_recommended",
                                        "data_insufficient"])
        rec = groups["recommended"]["horizons"]["1"]
        self.assertEqual(rec["up"], 1)
        self.assertEqual(rec["valid"], 1)
        self.assertAlmostEqual(rec["direction_hit_rate"], 1.0)
        self.assertAlmostEqual(rec["mean_net_return_pct"],
                               (11.0 / 10.0 - 1.0) * 100 - 0.13)
        nr = groups["not_recommended"]["horizons"]["1"]
        self.assertEqual(nr["down"], 1)
        self.assertAlmostEqual(nr["direction_hit_rate"], 1.0)
        ins = groups["data_insufficient"]["horizons"]["1"]
        self.assertFalse(ins["direction_evaluable"])
        self.assertIsNone(ins["direction_hit_rate"])
        self.assertIsNone(ins["wilson95"])
        spread = payload["spreads"]["1"]
        self.assertEqual(spread["pair"], ["recommended", "not_recommended"])
        self.assertAlmostEqual(
            spread["value"],
            rec["mean_net_return_pct"] - nr["mean_net_return_pct"])

    def test_flat_is_net_zero(self):
        payload = _prep_payload([_cand("600001")])
        store = _PrepStore({DAY: (payload, None)})
        # gross == 0.13 -> net == 0 -> flat
        bars = {"600001": [_bar("2026-05-20", 10.0),
                           _bar("2026-05-21", 10.013)]}
        result = _prep_run(store, bars, horizons=(1,))
        horizon = result["groups"][0]["horizons"]["1"]
        self.assertEqual(horizon["flat"], 1)
        self.assertEqual(horizon["up"], 0)
        self.assertEqual(horizon["direction_hit_rate"], 0.0)
        sample = result["samples"] if "samples" in result else None
        self.assertIsNone(sample)

    def test_gross_is_not_used_for_direction(self):
        payload = _prep_payload([_cand("600001")])
        store = _PrepStore({DAY: (payload, None)})
        # gross == 0.10 < 13bp cost -> net negative -> down
        bars = {"600001": [_bar("2026-05-20", 10.0),
                           _bar("2026-05-21", 10.01)]}
        result = _prep_run(store, bars, horizons=(1,))
        horizon = result["groups"][0]["horizons"]["1"]
        self.assertEqual(horizon["down"], 1)

    def test_include_samples_ledger_fields(self):
        store, bars = _three_group_setup()
        result = _prep_run(store, bars, horizons=(1,), include_samples=True)
        samples = result["samples"]
        self.assertEqual(len(samples), 3)
        self.assertEqual([s["code"] for s in samples],
                         ["600001", "600002", "600003"])
        sample = samples[0]
        self.assertEqual(sample["sample_id"], f"prep:{DAY}:600001:1")
        self.assertEqual(sample["entry_date"], "2026-05-20")
        self.assertEqual(sample["exit_date"], "2026-05-21")
        self.assertEqual(sample["cost_bps"], 13.0)
        self.assertEqual(sample["outcome"], "up")
        self.assertIsNone(sample["exclusion_reason"])
        self.assertAlmostEqual(
            sample["gross_return_pct"], (11.0 / 10.0 - 1.0) * 100)
        self.assertAlmostEqual(
            sample["net_return_pct"], sample["gross_return_pct"] - 0.13)

    def test_include_samples_absent_by_default(self):
        store, bars = _three_group_setup()
        result = _prep_run(store, bars, horizons=(1,))
        self.assertNotIn("samples", result)

    def test_exclusion_expands_per_horizon(self):
        payload = _prep_payload([_cand("600001")])
        store = _PrepStore({DAY: (payload, None)})
        # only the available_at day -> no entry day for prep 16:40
        result = _prep_run(store, { "600001": []}, horizons=(1, 3, 5),
                           calendar=[DAY], date_to=DAY)
        self.assertEqual(result["exclusions"]["sample"]["no_entry_day"], 3)
        self.assertEqual(result["groups"][0]["horizons"]["1"]["observations"], 1)
        self.assertEqual(result["groups"][0]["horizons"]["3"]["observations"], 1)

    def test_insufficient_horizon_is_per_horizon(self):
        payload = _prep_payload([_cand("600001")])
        store = _PrepStore({DAY: (payload, None)})
        bars = {"600001": [_bar("2026-05-20", 10.0)]}
        result = _prep_run(store, bars, horizons=(1, 3),
                           calendar=["2026-05-19", "2026-05-20"],
                           date_to="2026-05-20")
        sample_excl = result["exclusions"]["sample"]
        self.assertEqual(sample_excl["insufficient_horizon"], 2)

    def test_empty_group_isolated(self):
        payload = _prep_payload([_cand("600001")])
        store = _PrepStore({DAY: (payload, None)})
        bars = {"600001": [_bar("2026-05-20", 10.0),
                           _bar("2026-05-21", 11.0)]}
        result = _prep_run(store, bars, horizons=(1,))
        groups = {group["group"]: group for group in result["groups"]}
        self.assertEqual(groups["recommended"]["horizons"]["1"]["quality"], "ok")
        empty = groups["not_recommended"]["horizons"]["1"]
        self.assertEqual(empty["observations"], 0)
        self.assertEqual(empty["quality"], "not_evaluable")
        self.assertIsNone(empty["coverage"])

    def test_quality_thresholds_and_above_reference(self):
        payload = _prep_payload([_cand("600001")])
        store = _PrepStore({DAY: (payload, None)})
        bars = {"600001": [_bar("2026-05-20", 10.0),
                           _bar("2026-05-21", 11.0)]}
        low = _prep_run(store, bars, horizons=(1,), min_days=20,
                        min_group_samples=30)
        horizon = low["groups"][0]["horizons"]["1"]
        self.assertEqual(horizon["quality"], "low_confidence")
        self.assertIsNone(horizon["above_reference"])
        self.assertEqual(low["status"], "insufficient_data")
        ok = _prep_run(store, bars, horizons=(1,))
        horizon = ok["groups"][0]["horizons"]["1"]
        self.assertEqual(horizon["quality"], "ok")
        self.assertTrue(horizon["above_reference"])

    def test_no_snapshot_in_range(self):
        store = _PrepStore({})
        result = _prep_run(store, {}, horizons=(1,))
        self.assertEqual(result["status"], "insufficient_data")
        self.assertEqual(result["reason"], "no_snapshot_in_range")
        self.assertEqual(result["snapshots"]["with_snapshot"], 0)

    def test_calendar_unavailable(self):
        store = _PrepStore({DAY: (_prep_payload([_cand("600001")]), None)})

        class _NoCalendar(_Market):
            def calendar(self, start, end):
                raise RuntimeError("boom")

        payload = decision_review.run_decision_review(
            decision_review.PrepSnapshotAdapter(store), market=_NoCalendar({}),
            date_from=DAY, date_to=DAY, horizons=(1,))
        self.assertEqual(payload["status"], "insufficient_data")
        self.assertEqual(payload["reason"], "calendar_unavailable")
        self.assertIsNone(payload["snapshots"]["requested_days"])

    def test_deterministic_and_no_wall_clock(self):
        store, bars = _three_group_setup()
        first = _prep_run(store, bars, horizons=(1, 3))
        second = _prep_run(store, bars, horizons=(1, 3))
        self.assertEqual(first, second)
        self.assertNotIn("generated_at", first)

    def test_parameter_validation(self):
        adapter = decision_review.PrepSnapshotAdapter(_PrepStore({}))
        market = _Market({})
        cases = [
            dict(date_from="2026-05-25", date_to="2026-05-19"),
            dict(date_from=DAY, date_to="2026-05-25", horizons=(0,)),
            dict(date_from=DAY, date_to="2026-05-25", horizons=(-1,)),
            dict(date_from=DAY, date_to="2026-05-25", cost_bps=-1),
            dict(date_from=DAY, date_to="2026-05-25", cost_bps=math.inf),
            dict(date_from=DAY, date_to="2026-05-25", min_days=0),
            dict(date_from=DAY, date_to="2026-05-25", min_group_samples=0),
        ]
        for kwargs in cases:
            with self.assertRaises(ValueError, msg=str(kwargs)):
                decision_review.run_decision_review(adapter, market=market,
                                                   **kwargs)
        with self.assertRaises(ValueError):
            decision_review.run_decision_review(adapter, market=None,
                                               date_from=DAY, date_to="2026-05-25")

    def test_snapshot_counters_and_versions(self):
        payload = _prep_payload([_cand("600001")])
        unsupported = _prep_payload([], version="0.6")
        store = _PrepStore({
            "2026-05-18": (unsupported, None),
            DAY: (payload, None),
        })
        bars = {"600001": [_bar("2026-05-20", 10.0),
                           _bar("2026-05-21", 11.0)]}
        result = _prep_run(store, bars, horizons=(1,), date_from="2026-05-18")
        self.assertEqual(result["snapshots"]["with_snapshot"], 2)
        self.assertEqual(result["snapshots"]["unsupported_version"], 1)
        self.assertEqual(result["snapshots"]["usable"], 1)
        self.assertEqual(result["versions"]["accepted"], ["0.7", "0.8"])
        self.assertEqual(result["versions"]["seen"], {"0.6": 1, "0.7": 1})
        self.assertEqual(result["versions"]["unsupported_units"], 1)
        self.assertTrue(any("unsupported_snapshot_version" in warning
                            for warning in result["warnings"]))


class TestAuctionRun(unittest.TestCase):
    def _store(self):
        rows = [
            _auction_row("600001", level="强"),            # strong (up)
            _auction_row("600002", level="中"),            # medium
            _auction_row("600003", level="弱"),            # weak (down)
            _auction_row("600004", is_weak=True, level="弱"),  # vetoed (down)
            _auction_row("600005", status="unavailable"),  # unavailable
            _auction_row("600006", version="1"),           # unsupported row
        ]
        return _AuctionStore({DAY: rows})

    def _bars(self):
        return {
            "600001": [_bar(DAY, 10.0), _bar("2026-05-20", 11.0)],
            "600002": [_bar(DAY, 10.0), _bar("2026-05-20", 10.5)],
            "600003": [_bar(DAY, 10.0), _bar("2026-05-20", 9.0)],
            "600004": [_bar(DAY, 10.0), _bar("2026-05-20", 9.5)],
            "600005": [_bar(DAY, 10.0), _bar("2026-05-20", 10.2)],
        }

    def test_groups_order_and_spread(self):
        payload = decision_review.run_decision_review(
            decision_review.AuctionSnapshotAdapter(self._store()),
            market=_Market(self._bars()),
            date_from=DAY, date_to="2026-05-20", horizons=(1,),
            min_days=1, min_group_samples=1)
        self.assertEqual(payload["coverage_basis"], "persisted_snapshots")
        self.assertEqual([group["group"] for group in payload["groups"]],
                         ["strong", "medium", "weak", "vetoed", "unavailable"])
        groups = {group["group"]: group for group in payload["groups"]}
        self.assertEqual(groups["strong"]["horizons"]["1"]["up"], 1)
        self.assertEqual(groups["weak"]["horizons"]["1"]["down"], 1)
        self.assertEqual(groups["vetoed"]["horizons"]["1"]["down"], 1)
        self.assertFalse(groups["medium"]["horizons"]["1"]["direction_evaluable"])
        spread = payload["spreads"]["1"]
        self.assertEqual(spread["pair"], ["strong", "weak"])
        self.assertAlmostEqual(
            spread["value"],
            groups["strong"]["horizons"]["1"]["mean_net_return_pct"]
            - groups["weak"]["horizons"]["1"]["mean_net_return_pct"])
        self.assertEqual(payload["versions"]["unsupported_units"], 1)
        self.assertEqual(payload["snapshots"]["unsupported_version"], 1)
        self.assertEqual(payload["snapshots"]["usable"], 1)
        self.assertIn("coverage_basis=persisted_snapshots: 未持久化的空盘口/"
                      "失败候选不在分母内（无 run manifest）", payload["warnings"])
        self.assertIn("auction 分母仅为已持久化快照，不含未持久化的空盘口/失败候选",
                      payload["caveats"])

    def test_auction_entry_uses_same_day_open(self):
        payload = decision_review.run_decision_review(
            decision_review.AuctionSnapshotAdapter(self._store()),
            market=_Market(self._bars()),
            date_from=DAY, date_to="2026-05-20", horizons=(1,),
            min_days=1, min_group_samples=1, include_samples=True)
        strong = next(s for s in payload["samples"] if s["code"] == "600001")
        self.assertEqual(strong["entry_date"], DAY)
        self.assertEqual(strong["exit_date"], "2026-05-20")


# ─── §8 JSON 契约键序 ────────────────────────────────────────────────────


class TestJsonContract(unittest.TestCase):
    def test_top_level_key_order(self):
        store, bars = _three_group_setup()
        payload = _prep_run(store, bars, horizons=(1, 3))
        self.assertEqual(list(payload), [
            "schema_version", "ok", "status", "source", "date_from", "date_to",
            "universe", "coverage_basis", "config", "snapshots", "versions",
            "groups", "spreads", "exclusions", "caveats", "warnings"])
        self.assertEqual(list(payload["config"]), [
            "horizons", "cost_bps", "min_days", "min_group_samples",
            "include_samples", "outcome_basis", "net_return_basis",
            "entry_rule", "exit_rule", "calendar_basis"])
        self.assertEqual(payload["schema_version"], "dr_v1")
        self.assertEqual(payload["config"]["outcome_basis"],
                         "net_open_to_open_after_cost")
        self.assertEqual(payload["config"]["net_return_basis"],
                         "gross_minus_cost")
        self.assertEqual(payload["universe"], "persisted_decision_snapshots")
        self.assertEqual(payload["coverage_basis"], "persisted_snapshots")

    def test_samples_key_appended_when_requested(self):
        store, bars = _three_group_setup()
        payload = _prep_run(store, bars, horizons=(1,), include_samples=True)
        self.assertEqual(list(payload)[-1], "samples")


# ─── §14.2 build_tradable_labels 兼容 ───────────────────────────────────


class TestLabelCompatibility(unittest.TestCase):
    def test_returns_old_keys_plus_exclusion_items(self):
        market = _Market({"A": [_bar("2026-05-20", 10.0),
                               _bar("2026-05-21", 11.0)]})
        result = review_shadow.build_tradable_labels(
            [{"code": "A", "available_at": f"{DAY}T16:40:00"}], market, CAL,
            horizons=(1,))
        self.assertEqual(set(result), {
            "status", "labels", "exclusions", "cost_bps", "reason",
            "exclusion_items"})
        self.assertEqual(result["exclusion_items"], [])

    def test_early_exclusion_expands_per_horizon_but_legacy_count_does_not(self):
        market = _Market({"L": [_bar("2026-05-20", 11.0,
                                     amount=100_000_000.0)]})
        result = review_shadow.build_tradable_labels(
            [{"code": "L", "available_at": f"{DAY}T16:40:00"}], market, CAL,
            horizons=(1, 3, 5))
        self.assertEqual(result["exclusions"]["illiquid"], 1)
        self.assertEqual(len(result["exclusion_items"]), 3)
        self.assertEqual([item["horizon"] for item in result["exclusion_items"]],
                         [1, 3, 5])
        self.assertTrue(all(item["reason"] == "illiquid"
                            for item in result["exclusion_items"]))
        self.assertEqual(set(result["exclusion_items"][0]),
                         {"code", "trade_date", "horizon", "reason"})


if __name__ == "__main__":
    unittest.main()
