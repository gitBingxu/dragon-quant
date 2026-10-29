"""P1.5 prep / auction 因子化测试（v2：8 升级 + 9 新增）。

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

from dragon_quant.review_account.auction import registry as auction_registry
from dragon_quant.infra.factor import engine
from dragon_quant.infra.factor import storage as fstore
from dragon_quant.infra.factor.builtin import auction as factor_auction
from dragon_quant.infra.factor.decision import default_snapshot
from dragon_quant.infra.factor.registry import (
    FACTOR_SPECS, compute_version_info, get_spec, validate_registry,
)
from dragon_quant.infra.factor.types import FactorContext, FactorValue
from dragon_quant.infra.assets import db as assets_db
from dragon_quant.infra.storage import connection as dragon_db
from dragon_quant.infra.factor import sql as sqlstore

DAY = "2026-10-23"
PREP_NAMES = (
    "prep_high_days", "prep_t_pct", "prep_t_turnover",
    "prep_limit_up_suc_rate", "prep_in_pool_today",
)
RETIRED_PREP_NAMES = ("prep_verdict", "prep_composite")
AUCTION_NAMES = (
    "auction_strength", "auction_is_weak", "auction_direction", "auction_rank",
    "auction_dim_direction", "auction_dim_liquidity", "auction_dim_supply",
    "auction_dim_vol_price", "auction_dim_tail_grab",
    "auction_open_gap_pct", "auction_final_slippage_pct", "auction_turnover_pct",
    "auction_volume_ratio_5d", "auction_unmatched_matched_ratio",
    "auction_weak_to_strong", "auction_strong_to_weak",
    "auction_tail_matched_acceleration",
)
# 17 = 8 旧（4 聚合 + 4 子分，其中 liquidity 新增）+ 9 新增
assert len(AUCTION_NAMES) == 17


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


def _dims(**overrides):
    dims = {
        "direction": {"score": 70.0}, "liquidity": {"score": 55.0},
        "supply": {"score": 60.0}, "vol_price": {"score": 50.0},
        "tail_grab": {"score": 40.0},
    }
    dims.update(overrides)
    return dims


def _raw(**overrides):
    raw = {
        "official_open_gap_pct": 3.2, "final_indicative_slippage_pct": 0.2,
        "auction_turnover_pct": 0.41, "volume_ratio_5d": 1.8,
        "volume_ratio_history_n": 5, "final_unmatched_matched_ratio": 0.4,
        "tail_matched_acceleration": 1.7, "weak_to_strong": False,
        "strong_to_weak": False,
    }
    raw.update(overrides)
    return raw


def _auction_row(*, code="600001", strength=66.0, is_weak=False,
                 direction="bull", rank=1, unverified=False,
                 reject_reason=None, dims=None, version="2", status="ok",
                 raw=None):
    return {
        "code": code, "strength": strength, "is_weak": is_weak,
        "direction": direction, "rank": rank, "unverified": unverified,
        "reject_reason": reject_reason,
        "dims": _dims() if dims is None else dims,
        "raw_factors": _raw() if raw is None else raw,
        "data_quality": {"status": status},
        "version": version,
    }


def _auction_source(rows, *, version="2", created_at=None, corrupt=()) -> dict:
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
            patch("dragon_quant.infra.assets.db._connect",
                  side_effect=self._assets_connect),
            patch("dragon_quant.infra.storage.connection._connect",
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
    def test_retired_verdict_factors_are_unregistered(self):
        from dragon_quant.infra.factor.registry import FactorRegistryError
        for name in RETIRED_PREP_NAMES:
            with self.assertRaises(FactorRegistryError):
                get_spec(name)

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


# ─── 2. 缺失规则 ──────────────────────────────────────────────────────────


class TestMissingRules(unittest.TestCase):
    def test_unverified_or_missing_flag_writes_nothing(self):
        source = _auction_source({
            "A": _auction_row(code="A", unverified=True),
            "B": {k: v for k, v in _auction_row(code="B").items()
                  if k != "unverified"},
        })
        for name in AUCTION_NAMES:
            spec = get_spec(name)
            frame = spec.compute(_ctx(source, params=spec.params))
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
                              unverified=False, rank=None, reject_reason="强转弱"),
        })
        expected = {"auction_strength": {"A": 0.0}, "auction_is_weak": {"A": 1.0}}
        for name in AUCTION_NAMES:
            spec = get_spec(name)
            frame = spec.compute(_ctx(source, params=spec.params))
            self.assertEqual(frame.values, expected.get(name, {}), name)

    def test_rank_none_only_rank_missing(self):
        source = _auction_source({"A": _auction_row(code="A", rank=None)})
        flags = {"auction_weak_to_strong", "auction_strong_to_weak"}
        for name in AUCTION_NAMES:
            spec = get_spec(name)
            frame = spec.compute(_ctx(source, params=spec.params))
            if name == "auction_rank":
                self.assertEqual(frame.values, {})
            elif name in flags:
                self.assertEqual(frame.values, {}, name)   # 未发生 → 无行
            else:
                self.assertEqual(frame.values, {"A": frame.values["A"]}, name)

    def test_unavailable_status_blocks_strength_and_flag(self):
        source = _auction_source({"A": _auction_row(code="A", strength=None,
                                                    status="unavailable",
                                                    unverified=True)})
        for name in ("auction_strength", "auction_is_weak"):
            spec = get_spec(name)
            self.assertEqual(spec.compute(_ctx(source, params=spec.params)).values, {})

    def test_degraded_status_blocks_strength_and_rank_only(self):
        source = _auction_source({"A": _auction_row(code="A", status="degraded",
                                                    rank=None)})
        spec = get_spec("auction_strength")
        self.assertEqual(spec.compute(_ctx(source, params=spec.params)).values, {})
        spec = get_spec("auction_dim_supply")
        self.assertEqual(spec.compute(_ctx(source, params=spec.params)).values,
                         {"A": 60.0})


class TestNewFactors(unittest.TestCase):
    def test_raw_factors_are_published(self):
        source = _auction_source({"A": _auction_row(code="A")})
        expected = {
            "auction_open_gap_pct": 3.2,
            "auction_final_slippage_pct": 0.2,
            "auction_turnover_pct": 0.41,
            "auction_volume_ratio_5d": 1.8,
            "auction_unmatched_matched_ratio": 0.4,
            "auction_tail_matched_acceleration": 1.7,
        }
        for name, value in expected.items():
            spec = get_spec(name)
            frame = spec.compute(_ctx(source, params=spec.params))
            self.assertEqual(frame.values, {"A": value}, name)

    def test_volume_ratio_requires_history_5(self):
        source = _auction_source({"A": _auction_row(
            code="A", raw=_raw(volume_ratio_history_n=3, volume_ratio_5d=1.8))})
        spec = get_spec("auction_volume_ratio_5d")
        self.assertEqual(spec.compute(_ctx(source, params=spec.params)).values, {})

    def test_conversion_flags_write_only_true(self):
        source = _auction_source({
            "A": _auction_row(code="A", raw=_raw(weak_to_strong=True)),
            "B": _auction_row(code="B", raw=_raw(strong_to_weak=True)),
        })
        weak = get_spec("auction_weak_to_strong")
        strong = get_spec("auction_strong_to_weak")
        self.assertEqual(weak.compute(_ctx(source, params=weak.params)).values,
                         {"A": 1.0})
        self.assertEqual(strong.compute(_ctx(source, params=strong.params)).values,
                         {"B": 1.0})

    def test_official_open_missing_does_not_publish_gap(self):
        source = _auction_source({"A": _auction_row(
            code="A", raw=_raw(official_open_gap_pct=None))})
        spec = get_spec("auction_open_gap_pct")
        self.assertEqual(spec.compute(_ctx(source, params=spec.params)).values, {})


# ─── 3. derived PIT 与血缘 ────────────────────────────────────────────────


class TestDerivedPit(TempDb):
    def test_available_at_and_backfilled_lineage(self):
        self._seed_prep(_healthy_prep([
            _candidate("A", trade_date="2026-10-22")]))
        entry = engine.build_factor(get_spec("prep_t_pct"), DAY)
        self.assertEqual(entry["status"], "ok")
        self.assertEqual(entry["available_at"], f"{DAY}T16:40:00")

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


# ─── 4. 源故障不删旧行 ────────────────────────────────────────────────────


class TestSourceFailure(TempDb):
    def test_prep_json_corruption_keeps_old_rows(self):
        self._seed_prep(_healthy_prep([_candidate("A")]))
        engine.build_factor(get_spec("prep_t_pct"), DAY)
        self._exec("UPDATE prep_snapshots SET result_json = ? WHERE trade_date = ?",
                   ("{broken", DAY))
        entry = engine.build_factor(get_spec("prep_t_pct"), DAY)
        self.assertEqual(entry["status"], "missing_source")
        self.assertEqual(len(self._rows(["prep_t_pct"])), 1)

    def test_auction_missing_or_all_corrupt_keeps_old_rows(self):
        self._seed_auction({"A": _auction_row(code="A")})
        engine.build_factor(get_spec("auction_strength"), DAY)
        self.assertEqual(len(self._rows(["auction_strength"])), 1)
        self._exec("DELETE FROM auction_snapshots")
        entry = engine.build_factor(get_spec("auction_strength"), DAY)
        self.assertEqual(entry["status"], "missing_source")
        self.assertEqual(len(self._rows(["auction_strength"])), 1)

    def test_auction_partial_corrupt_keeps_good_rows(self):
        self._seed_auction({"A": _auction_row(code="A")})
        self._exec(
            "INSERT INTO auction_snapshots(trade_date, code, source, analysis_json, created_at) "
            "VALUES (?, ?, 'v2', ?, ?)", (DAY, "B", "{broken", f"{DAY} 09:26:00"))
        entry = engine.build_factor(get_spec("auction_strength"), DAY)
        self.assertEqual(entry["status"], "ok")
        self.assertEqual(entry["meta"]["counters"]["corrupt_rows"], 1)

    def test_schema_v1_rows_are_skipped_and_explained(self):
        self._seed_auction({
            "A": _auction_row(code="A", version="1"),
            "B": _auction_row(code="B", version="1"),
        })
        entry = engine.build_factor(get_spec("auction_strength"), DAY)
        self.assertEqual(entry["status"], "empty")
        self.assertEqual(entry["meta"]["counters"]["schema_v1_rows"], 2)
        self.assertNotIn("empty_unexpected", entry["warnings"])
        self.assertTrue(any("schema_v1_rows" in item for item in entry["warnings"]))


# ─── 5. 隔离性 ────────────────────────────────────────────────────────────


class TestIsolation(TempDb):
    def test_factors_do_not_import_upper_layers(self):
        root = Path(__file__).resolve().parents[2] / "dragon_quant" / "infra" / "factor"
        forbidden = ("dragon_quant.review_account", "dragon_quant.review")
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
        self._seed_prep(_healthy_prep([_candidate("A")]))
        self._seed_auction({"A": _auction_row(code="A")})
        self.assertEqual(engine.build_factor(get_spec("prep_t_pct"), DAY)["status"],
                         "ok")
        self.assertEqual(engine.build_factor(get_spec("auction_strength"), DAY)["status"],
                         "ok")
        self.assertEqual([(r["code"], r["value"]) for r in self._rows(["five_dim_drive"])],
                         [("A", 9.0)])


# ─── 6. 默认零影响 ────────────────────────────────────────────────────────


class TestDefaultSnapshot(unittest.TestCase):
    def test_default_snapshot_members_unchanged(self):
        snapshot = default_snapshot()
        self.assertFalse(any(name.startswith(("prep_", "auction_"))
                             for name in snapshot["factor_versions"]))


# ─── 7. 注册表与联动 ──────────────────────────────────────────────────────


class TestRegistry(unittest.TestCase):
    def test_registry_valid_and_metadata(self):
        validate_registry(FACTOR_SPECS)
        by_name = {spec.name: spec for spec in FACTOR_SPECS}
        for name in AUCTION_NAMES:
            spec = by_name[name]
            self.assertIs(spec.default_enabled, False)
            self.assertEqual((spec.source, spec.pit_rule, spec.available_phase),
                             ("auction", "derived", "auction"))
            self.assertTrue(spec.impl_key.endswith(".v2"), name)
            self.assertEqual(spec.params["config_version"], "auction_v2_baseline")
            self.assertEqual(spec.params["config_hash"],
                             auction_registry.CONFIG_HASH)
        self.assertEqual(by_name["auction_rank"].direction, "lower_better")
        self.assertEqual(by_name["auction_is_weak"].direction, "neutral")

    def test_config_hash_linkage_to_registry(self):
        self.assertEqual(factor_auction.CONFIG_VERSION,
                         auction_registry.CONFIG_VERSION)
        self.assertEqual(factor_auction.CONFIG_HASH, auction_registry.CONFIG_HASH)


# ─── 8. 报告与告警 ────────────────────────────────────────────────────────


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


if __name__ == "__main__":
    unittest.main()
