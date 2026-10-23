"""P1.5 prep / auction 因子化测试（方案 §15.8 九类用例）。

全部无网络：临时 dragon.db + assets.db，patch 两个 ``_connect`` 完全隔离。
"""

import ast
import dataclasses
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dragon_quant.auction import registry as auction_registry
from dragon_quant.factors import engine
from dragon_quant.factors import storage as fstore
from dragon_quant.factors.builtin import auction as factor_auction
from dragon_quant.factors.builtin import prep as factor_prep
from dragon_quant.factors.decision import default_snapshot
from dragon_quant.factors.registry import (
    FACTOR_SPECS, compute_version_info, get_spec, validate_registry,
)
from dragon_quant.factors.types import FactorContext, FactorSpec, FactorValue
from dragon_quant.storage import assets_db
from dragon_quant.storage import db as dragon_db
from dragon_quant.storage import factors as sqlstore

DAY = "2026-10-23"
PREP_NAMES = (
    "prep_verdict", "prep_composite", "prep_high_days", "prep_t_pct",
    "prep_t_turnover", "prep_limit_up_suc_rate", "prep_in_pool_today",
)
AUCTION_NAMES = (
    "auction_strength", "auction_is_weak", "auction_direction", "auction_rank",
    "auction_dim_direction", "auction_dim_supply", "auction_dim_vol_price",
    "auction_dim_tail_grab",
)


# ─── 纯 compute 夹具 ──────────────────────────────────────────────────────


def _ctx(source_rows, params=None, codes=None, trade_date=DAY) -> FactorContext:
    return FactorContext(
        trade_date=trade_date,
        codes=tuple(codes if codes is not None else source_rows.get("codes") or ()),
        source_rows=source_rows,
        as_of="",
        params=params or {},
    )


def _prep_source(candidates, *, version=0.5, created_at=None) -> dict:
    return {
        "status": "ok",
        "snapshot_version": version,
        "snapshot_created_at": created_at,
        "candidates": candidates,
        "codes": tuple(sorted(c["code"] for c in candidates if c.get("code"))),
    }


def _auction_row(*, code="600001", strength=66.0, is_weak=False,
                 direction="bull", rank=1, unverified=False,
                 reject_reason=None, dims=None):
    if dims is None:
        dims = {
            "direction": {"score": 70.0},
            "supply": {"score": 60.0},
            "vol_price": {"score": 50.0},
            "tail_grab": {"score": 40.0},
        }
    return {
        "code": code, "strength": strength, "is_weak": is_weak,
        "direction": direction, "rank": rank, "unverified": unverified,
        "reject_reason": reject_reason, "dims": dims, "version": "1",
    }


def _auction_source(rows, *, version="1", created_at=None, corrupt=()) -> dict:
    return {
        "status": "ok",
        "snapshot_version": version,
        "snapshot_created_at": created_at,
        "rows_by_code": rows,
        "codes": tuple(sorted(rows)),
        "corrupt_rows": list(corrupt),
    }


def _compute(name, ctx):
    return get_spec(name).compute(ctx)


# ─── 临时库基类 ───────────────────────────────────────────────────────────


class TempDb(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.assets_path = str(Path(self._tmpdir.name) / "assets.db")
        self.dragon_path = str(Path(self._tmpdir.name) / "dragon.db")
        self._patches = [
            patch("dragon_quant.storage.assets_db._connect",
                  side_effect=self._assets_connect),
            patch("dragon_quant.storage.db._connect",
                  side_effect=self._dragon_connect),
        ]
        for item in self._patches:
            item.start()
        dragon_db.init_db()

    def tearDown(self):
        for item in reversed(self._patches):
            item.stop()
        self._tmpdir.cleanup()

    def _assets_connect(self, *args, create=True, path=None, **kwargs):
        target = path or self.assets_path
        if not create and not Path(target).exists():
            return None
        return sqlite3.connect(target)

    def _dragon_connect(self, *args, create=True, path=None, **kwargs):
        target = path or self.dragon_path
        if not create and not Path(target).exists():
            return None
        return sqlite3.connect(target)

    def _exec(self, sql, params=(), path=None):
        conn = sqlite3.connect(path or self.assets_path)
        try:
            conn.execute(sql, params)
            conn.commit()
        finally:
            conn.close()

    def _seed_prep(self, payload, created_at=None):
        assets_db.save_prep_snapshot(DAY, payload)
        self._exec("UPDATE prep_snapshots SET created_at = ? WHERE trade_date = ?",
                   (created_at or f"{DAY} 17:00:00", DAY))

    def _seed_auction(self, payloads, created_at=None):
        assets_db.replace_auction_snapshots(DAY, payloads, source="v2")
        self._exec("UPDATE auction_snapshots SET created_at = ? WHERE trade_date = ?",
                   (created_at or f"{DAY} 09:26:00", DAY))

    def _rows(self, names):
        return sqlstore.get_day_values(DAY, names=list(names))


def _healthy_prep(candidates):
    return {
        "version": 0.5,
        "sentiment": {"degraded": []},
        "dragon_source": {
            "window_days": 3,
            "dates": [{"status": "ok"}, {"status": "ok"}, {"status": "ok"}],
        },
        "candidates": candidates,
    }


def _candidate(code, **overrides):
    base = {"code": code, "verdict": "strong", "composite_score": 80.0,
            "facts": {"pool_available": True, "in_pool_today": True,
                      "t_pct": 10.0, "t_turnover": 12.5, "high_days": "3天3板",
                      "limit_up_suc_rate": 0.75}}
    base.update(overrides)
    return base


# ─── 1. 映射正确性 ────────────────────────────────────────────────────────


class TestMapping(unittest.TestCase):
    def test_verdict_mapping_and_unknown(self):
        source = _prep_source([
            _candidate("A", verdict="strong"),
            _candidate("B", verdict="light"),
            _candidate("C", verdict="ignore"),
            _candidate("D", verdict="unrated"),
            _candidate("E", verdict="sideways"),
            _candidate("F", verdict=None),
        ])
        frame = _compute("prep_verdict", _ctx(source,
                                             params=get_spec("prep_verdict").params))
        self.assertEqual(frame.values, {"A": 3.0, "B": 2.0, "C": 0.0})
        self.assertEqual(frame.meta["counters"]["unrated"], 1)
        self.assertEqual(frame.meta["counters"]["unknown_enum"], 1)
        self.assertEqual(frame.meta["counters"]["missing"], 1)

    def test_direction_mapping_and_unknown(self):
        source = _auction_source({
            "A": _auction_row(code="A", direction="bull"),
            "B": _auction_row(code="B", direction="neutral"),
            "C": _auction_row(code="C", direction="bear"),
            "D": _auction_row(code="D", direction="sideways"),
        })
        frame = _compute("auction_direction",
                         _ctx(source, params=get_spec("auction_direction").params))
        self.assertEqual(frame.values, {"A": 1.0, "B": 0.0, "C": -1.0})
        self.assertEqual(frame.meta["counters"]["unknown_enum"], 1)

    def test_mapping_participates_in_version_and_compute_reads_params(self):
        spec = get_spec("prep_verdict")
        changed = dataclasses.replace(
            spec, params={"mapping": {"strong": 9.0, "light": 2.0, "ignore": 0.0},
                          "unrated_policy": "skip"})
        self.assertNotEqual(compute_version_info(spec)[0],
                            compute_version_info(changed)[0])

        source = _prep_source([_candidate("A", verdict="strong")])
        original = _compute("prep_verdict", _ctx(source, params=spec.params))
        modified = spec.compute(_ctx(source, params=changed.params))
        self.assertEqual(original.values["A"], 3.0)
        self.assertEqual(modified.values["A"], 9.0)


# ─── 2. unrated 行为 ─────────────────────────────────────────────────────


class TestUnrated(unittest.TestCase):
    def test_unrated_skipped_not_zero(self):
        source = _prep_source([_candidate("A", verdict="unrated")])
        frame = _compute("prep_verdict", _ctx(source,
                                             params=get_spec("prep_verdict").params))
        self.assertEqual(frame.values, {})
        self.assertEqual(frame.meta["counters"]["unrated"], 1)


# ─── 3. 缺失规则 ──────────────────────────────────────────────────────────


class TestMissingRules(unittest.TestCase):
    def test_unverified_or_missing_flag_writes_nothing(self):
        source = _auction_source({
            "A": _auction_row(code="A", unverified=True),
            "B": {k: v for k, v in _auction_row(code="B").items()
                  if k != "unverified"},
        })
        for name in AUCTION_NAMES:
            params = get_spec(name).params
            frame = get_spec(name).compute(_ctx(source, params=params))
            self.assertEqual(frame.values, {}, f"{name} 不应为未校准行写值")
    def test_sentinel_literal_matches_producer(self):
        self.assertEqual(factor_auction._AUCTION_ITEMS_MISSING,
                         auction_registry.AUCTION_ITEMS_MISSING)

    def test_items_missing_sentinel_writes_nothing(self):
        source = _auction_source({
            "A": _auction_row(code="A", strength=0.0, is_weak=True,
                              unverified=False, dims={},
                              reject_reason=factor_auction._AUCTION_ITEMS_MISSING),
        })
        for name in AUCTION_NAMES:
            spec = get_spec(name)
            frame = spec.compute(_ctx(source, params=spec.params))
            self.assertEqual(frame.values, {}, f"{name} 不应把缺失写成 0/否决")

    def test_real_reject_path_keeps_only_strength_and_flag(self):
        source = _auction_source({
            "A": _auction_row(code="A", strength=0.0, is_weak=True,
                              unverified=False, dims={}, rank=None,
                              reject_reason="高位"),
        })
        expected = {"auction_strength": {"A": 0.0}, "auction_is_weak": {"A": 1.0}}
        for name in AUCTION_NAMES:
            spec = get_spec(name)
            frame = spec.compute(_ctx(source, params=spec.params))
            self.assertEqual(frame.values, expected.get(name, {}), name)

    def test_rank_none_only_rank_missing(self):
        source = _auction_source({"A": _auction_row(code="A", rank=None)})
        for name in AUCTION_NAMES:
            spec = get_spec(name)
            frame = spec.compute(_ctx(source, params=spec.params))
            if name == "auction_rank":
                self.assertEqual(frame.values, {})
            else:
                self.assertEqual(frame.values, {"A": frame.values["A"]}, name)

    def test_prep_missing_fields(self):
        source = _prep_source([
            _candidate("A", facts={"pool_available": False, "in_pool_today": False,
                                   "high_days": "首板"}),
            _candidate("B", facts={"pool_available": False, "in_pool_today": True,
                                   "high_days": None}),
        ])
        in_pool = _compute("prep_in_pool_today", _ctx(source))
        self.assertEqual(in_pool.values, {})
        self.assertEqual(in_pool.meta["counters"]["pool_unavailable"], 2)

        height = _compute("prep_high_days", _ctx(source))
        self.assertEqual(height.values, {})
        self.assertEqual(height.meta["counters"]["unparsed_high_days"], 1)
        self.assertEqual(height.meta["counters"]["missing"], 1)

    def test_legacy_snapshot_without_facts(self):
        source = _prep_source([{"code": "A", "verdict": "strong",
                                "composite_score": 80.0}])
        self.assertEqual(_compute("prep_verdict", _ctx(
            source, params=get_spec("prep_verdict").params)).values, {"A": 3.0})
        self.assertEqual(_compute("prep_composite", _ctx(source)).values, {"A": 80.0})
        for name in PREP_NAMES[2:]:
            self.assertEqual(_compute(name, _ctx(source)).values, {}, name)


# ─── 4. derived PIT 与血缘 ────────────────────────────────────────────────


class TestDerivedPit(TempDb):
    def test_available_at_and_backfilled_lineage(self):
        self._seed_prep(_healthy_prep([
            _candidate("A", trade_date="2026-10-22")]))
        entry = engine.build_factor(get_spec("prep_verdict"), DAY)
        self.assertEqual(entry["status"], "ok")
        self.assertEqual(entry["available_at"], f"{DAY}T16:40:00")
        meta = json.loads(self._rows(["prep_verdict"])[0]["source_meta_json"])
        self.assertEqual(meta["candidate_trade_date"], {"A": "2026-10-22"})

        self._exec("UPDATE prep_snapshots SET created_at = ? WHERE trade_date = ?",
                   ("2026-10-26 09:00:00", DAY))
        entry = engine.build_factor(get_spec("prep_verdict"), DAY)
        self.assertEqual(entry["available_at"], f"{DAY}T16:40:00")
        row = self._rows(["prep_verdict"])[0]
        meta = json.loads(row["source_meta_json"])
        self.assertEqual(meta["snapshot_version"], 0.5)
        self.assertEqual(meta["snapshot_created_at"], "2026-10-26 09:00:00")
        self.assertTrue(meta["snapshot_backfilled"])
        self.assertTrue(meta["counters"]["snapshot_backfilled"])

    def test_auction_phase_and_idempotent_rerun(self):
        self._seed_auction({"A": _auction_row(code="A")})
        first = engine.build_factor(get_spec("auction_strength"), DAY)
        self.assertEqual(first["available_at"], f"{DAY}T09:25:00")
        before = self._rows(["auction_strength"])
        engine.build_factor(get_spec("auction_strength"), DAY)
        after = self._rows(["auction_strength"])
        self.assertEqual(
            [(r["code"], r["value"], r["available_at"]) for r in before],
            [(r["code"], r["value"], r["available_at"]) for r in after])


# ─── 5. 源故障不删旧行 ────────────────────────────────────────────────────


class TestSourceFailure(TempDb):
    def test_prep_json_corruption_keeps_old_rows(self):
        self._seed_prep(_healthy_prep([_candidate("A")]))
        engine.build_factor(get_spec("prep_verdict"), DAY)
        self.assertEqual(len(self._rows(["prep_verdict"])), 1)

        self._exec("UPDATE prep_snapshots SET result_json = ? WHERE trade_date = ?",
                   ("{broken", DAY))
        entry = engine.build_factor(get_spec("prep_verdict"), DAY)
        self.assertEqual(entry["status"], "missing_source")
        self.assertEqual(len(self._rows(["prep_verdict"])), 1)

    def test_prep_table_missing_keeps_old_rows(self):
        self._seed_prep(_healthy_prep([_candidate("A")]))
        engine.build_factor(get_spec("prep_verdict"), DAY)
        self._exec("DROP TABLE prep_snapshots")
        entry = engine.build_factor(get_spec("prep_verdict"), DAY)
        self.assertEqual(entry["status"], "missing_source")
        self.assertEqual(len(self._rows(["prep_verdict"])), 1)

    def test_degraded_empty_keeps_old_rows(self):
        self._seed_prep(_healthy_prep([_candidate("A")]))
        engine.build_factor(get_spec("prep_composite"), DAY)
        degraded = _healthy_prep([])
        degraded["dragon_source"]["window_days"] = 0
        self._seed_prep(degraded)
        entry = engine.build_factor(get_spec("prep_composite"), DAY)
        self.assertEqual(entry["status"], "missing_source")
        self.assertIn("empty_degraded_source", entry["reason"])
        self.assertEqual(len(self._rows(["prep_composite"])), 1)

    def test_degraded_empty_markers_keep_old_rows(self):
        self._seed_prep(_healthy_prep([_candidate("A")]))
        engine.build_factor(get_spec("prep_composite"), DAY)
        for mutate in (
            lambda payload: payload["sentiment"].update(degraded=["dragons_partial"]),
            lambda payload: payload["dragon_source"]["dates"][0].update(status="empty"),
            lambda payload: payload["sentiment"].update(degraded=["dragons"]),
        ):
            payload = _healthy_prep([])
            mutate(payload)
            self._seed_prep(payload)
            entry = engine.build_factor(get_spec("prep_composite"), DAY)
            self.assertEqual(entry["status"], "missing_source")
            self.assertEqual(len(self._rows(["prep_composite"])), 1)

    def test_healthy_empty_deletes_old_rows(self):
        self._seed_prep(_healthy_prep([_candidate("A")]))
        engine.build_factor(get_spec("prep_composite"), DAY)
        self._seed_prep(_healthy_prep([]))
        entry = engine.build_factor(get_spec("prep_composite"), DAY)
        self.assertEqual(entry["status"], "empty")
        self.assertIn("empty_unexpected", entry["warnings"])
        self.assertEqual(self._rows(["prep_composite"]), [])

    def test_auction_missing_or_all_corrupt_keeps_old_rows(self):
        self._seed_auction({"A": _auction_row(code="A")})
        engine.build_factor(get_spec("auction_strength"), DAY)
        self.assertEqual(len(self._rows(["auction_strength"])), 1)

        self._exec("DELETE FROM auction_snapshots")
        entry = engine.build_factor(get_spec("auction_strength"), DAY)
        self.assertEqual(entry["status"], "missing_source")
        self.assertEqual(len(self._rows(["auction_strength"])), 1)

        self._exec(
            "INSERT INTO auction_snapshots(trade_date, code, source, analysis_json, created_at) "
            "VALUES (?, ?, 'v2', ?, ?)", (DAY, "A", "{broken", f"{DAY} 09:26:00"))
        entry = engine.build_factor(get_spec("auction_strength"), DAY)
        self.assertEqual(entry["status"], "missing_source")
        self.assertIn("all_corrupt", entry["reason"])
        self.assertEqual(len(self._rows(["auction_strength"])), 1)

    def test_auction_partial_corrupt_keeps_good_rows(self):
        self._seed_auction({"A": _auction_row(code="A")})
        self._exec(
            "INSERT INTO auction_snapshots(trade_date, code, source, analysis_json, created_at) "
            "VALUES (?, ?, 'v2', ?, ?)", (DAY, "B", "{broken", f"{DAY} 09:26:00"))
        entry = engine.build_factor(get_spec("auction_strength"), DAY)
        self.assertEqual(entry["status"], "ok")
        self.assertEqual(entry["meta"]["counters"]["corrupt_rows"], 1)
        rows = self._rows(["auction_strength"])
        self.assertEqual([row["code"] for row in rows], ["A"])


# ─── 6. 隔离性 ────────────────────────────────────────────────────────────


class TestIsolation(TempDb):
    def test_factors_do_not_import_upper_layers(self):
        root = Path(__file__).resolve().parents[1] / "dragon_quant" / "factors"
        forbidden = ("dragon_quant.review_account", "dragon_quant.auction")
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                modules = []
                if isinstance(node, ast.ImportFrom) and node.module:
                    modules.append(node.module)
                elif isinstance(node, ast.Import):
                    modules.extend(alias.name for alias in node.names)
                for module in modules:
                    for prefix in forbidden:
                        self.assertFalse(
                            module == prefix or module.startswith(prefix + "."),
                            f"{path} 违规 import {module}")

    def test_cross_family_build_and_other_factor_rows_untouched(self):
        fstore.replace_factor_day(DAY, "five_dim_drive", "v0", [
            FactorValue(trade_date=DAY, code="A", factor_name="five_dim_drive",
                        factor_version="v0", value=9.0,
                        available_at=f"{DAY}T15:05:00", source="scan"),
        ])
        fstore.replace_factor_day(DAY, "vpa_breakout", "v0", [
            FactorValue(trade_date=DAY, code="A", factor_name="vpa_breakout",
                        factor_version="v0", value=7.0,
                        available_at=f"{DAY}T15:05:00", source="vpa"),
        ])
        self._seed_prep(_healthy_prep([_candidate("A")]))
        self._seed_auction({"A": _auction_row(code="A")})
        self.assertEqual(engine.build_factor(get_spec("prep_composite"), DAY)["status"],
                         "ok")
        self.assertEqual(engine.build_factor(get_spec("auction_strength"), DAY)["status"],
                         "ok")
        self.assertEqual([(r["code"], r["value"]) for r in self._rows(["five_dim_drive"])],
                         [("A", 9.0)])
        self.assertEqual([(r["code"], r["value"]) for r in self._rows(["vpa_breakout"])],
                         [("A", 7.0)])

    def test_build_prep_without_auction_and_vice_versa(self):
        self._seed_prep(_healthy_prep([_candidate("A")]))
        self.assertEqual(engine.build_factor(get_spec("prep_verdict"), DAY)["status"],
                         "ok")
        self._exec("DELETE FROM prep_snapshots")
        self.assertEqual(engine.build_factor(get_spec("prep_verdict"), DAY)["status"],
                         "missing_source")
        self._seed_auction({"A": _auction_row(code="A")})
        self.assertEqual(engine.build_factor(get_spec("auction_strength"), DAY)["status"],
                         "ok")

    def test_high_days_parsing_matches_producers(self):
        from dragon_quant.review_account.prep import _lianban_count
        from dragon_quant.review_account.verdicts import _board_height

        for text in ("3天3板", "2天2板"):
            source = _prep_source([_candidate("A", facts={"high_days": text})])
            value = _compute("prep_high_days", _ctx(source)).values["A"]
            self.assertEqual(int(value), _board_height(text))
            self.assertEqual(int(value), _lianban_count(text))

        source = _prep_source([_candidate("A", facts={"high_days": "首板"})])
        self.assertEqual(_compute("prep_high_days", _ctx(source)).values, {})
        self.assertIsNone(_board_height("首板"))
        self.assertEqual(_lianban_count("首板"), 0)


# ─── 7. 默认零影响 ────────────────────────────────────────────────────────


class TestDefaultSnapshot(unittest.TestCase):
    def test_default_snapshot_members_unchanged(self):
        snapshot = default_snapshot()
        expected = {
            "five_dim_absorption", "five_dim_anti_drop", "five_dim_drive",
            "five_dim_is_true_dragon", "five_dim_leadership", "five_dim_liquidity",
            "vpa_breakout", "vpa_divergence", "vpa_health_score",
            "vpa_trend_verify", "vpa_vol_amount",
        }
        self.assertEqual(set(snapshot["factor_versions"]), expected)
        self.assertEqual(set(snapshot["params"]["weights"]), expected)
        self.assertEqual(snapshot["params"]["neutral"], ["five_dim_is_true_dragon"])
        self.assertEqual(snapshot["params"]["lower_better"], [])
        for name, weight in snapshot["params"]["weights"].items():
            self.assertEqual(weight, 0.0 if name == "five_dim_is_true_dragon" else 1.0,
                             name)
        self.assertFalse(any(name.startswith(("prep_", "auction_"))
                             for name in snapshot["factor_versions"]))


# ─── 8. 注册表 ────────────────────────────────────────────────────────────


class TestRegistry(unittest.TestCase):
    def test_registry_valid_and_metadata(self):
        validate_registry(FACTOR_SPECS)
        by_name = {spec.name: spec for spec in FACTOR_SPECS}
        for name in PREP_NAMES:
            spec = by_name[name]
            self.assertIs(spec.default_enabled, False)
            self.assertEqual((spec.source, spec.pit_rule, spec.available_phase),
                             ("prep", "derived", "prep"))
        for name in AUCTION_NAMES:
            spec = by_name[name]
            self.assertIs(spec.default_enabled, False)
            self.assertEqual((spec.source, spec.pit_rule, spec.available_phase),
                             ("auction", "derived", "auction"))
        self.assertEqual(by_name["prep_limit_up_suc_rate"].unit, "ratio")
        self.assertEqual(by_name["auction_rank"].direction, "lower_better")
        self.assertEqual(by_name["prep_in_pool_today"].direction, "neutral")
        self.assertEqual(by_name["auction_is_weak"].direction, "neutral")


# ─── 9. 报告与告警 ────────────────────────────────────────────────────────


class TestReportCounters(TempDb):
    def test_all_unverified_suppresses_empty_warning(self):
        self._seed_auction({
            "A": _auction_row(code="A", unverified=True),
            "B": _auction_row(code="B", unverified=True),
        })
        entry = engine.build_factor(get_spec("auction_strength"), DAY)
        self.assertEqual(entry["status"], "empty")
        self.assertEqual(entry["meta"]["counters"]["unverified_rows"], 2)
        self.assertNotIn("empty_unexpected", entry["warnings"])
        self.assertIn("unverified_rows=2", entry["warnings"])

        report = engine.build_date(DAY, names=["auction_strength"])
        self.assertFalse(any("empty_unexpected" in item for item in report["warnings"]))
        self.assertTrue(any("unverified_rows" in item for item in report["warnings"]))

    def test_all_unrated_suppresses_empty_warning(self):
        self._seed_prep(_healthy_prep([
            _candidate("A", verdict="unrated"),
            _candidate("B", verdict="unrated"),
        ]))
        entry = engine.build_factor(get_spec("prep_verdict"), DAY)
        self.assertEqual(entry["status"], "empty")
        self.assertEqual(entry["meta"]["counters"]["unrated"], 2)
        self.assertNotIn("empty_unexpected", entry["warnings"])
        self.assertTrue(any("expected_empty" in item for item in entry["warnings"]))

        report = engine.build_date(DAY, names=["prep_verdict"])
        self.assertFalse(any("empty_unexpected" in item for item in report["warnings"]))

    def test_counters_land_in_meta(self):
        self._seed_prep(_healthy_prep([
            _candidate("A", verdict="weird"),
            _candidate("B", facts={"pool_available": True, "in_pool_today": True,
                                   "high_days": "首板"}),
        ]))
        entry = engine.build_factor(get_spec("prep_verdict"), DAY)
        self.assertEqual(entry["meta"]["counters"]["unknown_enum"], 1)
        entry = engine.build_factor(get_spec("prep_high_days"), DAY)
        self.assertEqual(entry["meta"]["counters"]["unparsed_high_days"], 1)


if __name__ == "__main__":
    unittest.main()
