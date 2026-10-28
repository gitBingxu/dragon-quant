"""M6 prep review 因子族 + 评估/影子纯函数测试（技术方案 §13–§14）。

全部离线：临时 dragon.db + assets.db，patch 两个 ``_connect`` 完全隔离。

覆盖（brief Step 1）：
  - 全部默认关；``null`` / ``unknown`` 无行；``position_pct=0`` 是真实值；
  - ``params`` 含 ``review_config_version`` / ``review_config_hash`` 且改 hash → 版本 bump；
  - 常量与 ``review`` 配置单一真源一致（测试强制，防止第二份真相）；
  - 新族分母 = ``len(review_candidates)``，新代码可落因子行（不受旧 ``ctx.codes`` 过滤）；
  - ``review`` 缺失 / disabled / compute_error → ``missing_source`` 且不删旧行；
  - 可成交标签 / 消融 / 滚动 OOS / 影子指标：历史不足返回 ``insufficient_data``。
"""

import dataclasses
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dragon_quant.infra.assets import db as assets_db
from dragon_quant.infra.factor import engine
from dragon_quant.infra.factor import review_shadow
from dragon_quant.infra.factor import sql as sqlstore
from dragon_quant.infra.factor import storage as fstore
from dragon_quant.infra.factor.builtin import prep_review as factor_review
from dragon_quant.infra.factor.decision import default_snapshot
from dragon_quant.infra.factor.registry import (
    FACTOR_SPECS, compute_version_info, get_spec, validate_registry,
)
from dragon_quant.infra.factor.types import FactorContext, FactorValue, SourceUnavailable
from dragon_quant.infra.storage import connection as dragon_db

DAY = "2026-10-28"

REVIEW_NAMES = (
    "prep_review_score", "prep_review_grade", "prep_position_pct",
    "prep_market_state", "prep_mainline_rank", "prep_role",
    "prep_dim_d1", "prep_dim_d2", "prep_dim_d3", "prep_dim_d4",
    "prep_dim_d5", "prep_dim_d6", "prep_seal_ratio", "prep_main_net_ratio",
    "prep_turnover_health", "prep_distance_high60", "prep_chip_concentration",
    "prep_feedback_state",
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


def _review_source(candidates, *, market_state="strong", feedback=None,
                   enabled=True, config_hash=factor_review.REVIEW_CONFIG_HASH,
                   config_version=factor_review.REVIEW_CONFIG_VERSION,
                   include_review=True, version=0.7) -> dict:
    source = {
        "status": "ok",
        "snapshot_version": version,
        "snapshot_created_at": f"{DAY} 17:00:00",
        "candidates": [],
        "review_candidates": candidates,
        "codes": tuple(),
    }
    if include_review:
        source["review"] = {
            "enabled": enabled,
            "review_version": "1.0",
            "config_version": config_version,
            "config_hash": config_hash,
            "market": {"state": market_state},
            "pool": {"count": len(candidates), "truncated": False},
            "degraded": [],
        }
    if feedback is not None:
        source["feedback"] = feedback
    return source


def _candidate(code, *, score=80.0, grade="A", position=5.0,
               mainline_rank=1, role="leader",
               d1=70.0, d2=71.0, d3=72.0, d4=73.0, d5=74.0, d6=75.0,
               seal_ratio=0.4, main_net_ratio=0.06, turnover_health=80.0,
               distance_high60=3.0, chip_concentration=0.02) -> dict:
    dims = {
        "D1": {"score": d1, "weight": 0.3, "sub": {}},
        "D2": {"score": d2, "weight": 0.2, "sub": {}},
        "D3": {"score": d3, "weight": 0.2, "sub": {}},
        "D4": {"score": d4, "weight": 0.2, "sub": {"Q": turnover_health}},
        "D5": {"score": d5, "weight": 0.05, "sub": {}},
        "D6": {"score": d6, "weight": 0.05, "sub": {}},
    }
    contributions = [
        {"dim": "D3", "sub_factors": [{"key": "R", "raw": seal_ratio}]},
        {"dim": "D4", "sub_factors": [{"key": "F", "raw": main_net_ratio}]},
        {"dim": "D5", "sub_factors": [{"key": "P", "raw": distance_high60},
                                      {"key": "C", "raw": chip_concentration}]},
    ]
    return {
        "code": code,
        "role": role,
        "mainline": (
            {"board_name": "机器人", "rank": mainline_rank, "state": "stable"}
            if mainline_rank is not None else None
        ),
        "review_score": score,
        "review_grade": grade,
        "position_pct": position,
        "dimensions": dims,
        "contributions": contributions,
    }


def _compute(name, ctx):
    spec = get_spec(name)
    return spec.compute(dataclasses.replace(ctx, params=spec.params))


def _values(name, source, **kwargs):
    return _compute(name, _ctx(source, **kwargs)).values


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

    def _exec(self, sql, params=()):
        conn = sqlite3.connect(self.assets_path)
        try:
            conn.execute(sql, params)
            conn.commit()
        finally:
            conn.close()

    def _seed_prep(self, payload, created_at=None):
        assets_db.save_prep_snapshot(DAY, payload)
        self._exec("UPDATE prep_snapshots SET created_at = ? WHERE trade_date = ?",
                   (created_at or f"{DAY} 17:00:00", DAY))

    def _rows(self, names):
        return sqlstore.get_day_values(DAY, names=list(names))


def _healthy_snapshot(candidates, **kwargs):
    payload = {
        "version": 0.7,
        "sentiment": {"degraded": []},
        "dragon_source": {
            "window_days": 5,
            "dates": [{"status": "ok"}, {"status": "ok"}, {"status": "ok"}],
        },
        "candidates": [],
    }
    review_source = _review_source(candidates, **kwargs)
    for key in ("review", "review_candidates", "feedback"):
        if key in review_source:
            payload[key] = review_source[key]
    return payload


# ─── 1. 映射正确性 ────────────────────────────────────────────────────────


class TestMappings(unittest.TestCase):
    def test_grade_mapping_and_unknown(self):
        source = _review_source([
            _candidate("A", grade="S"),
            _candidate("B", grade="A"),
            _candidate("C", grade="B"),
            _candidate("D", grade="C"),
            _candidate("E", grade="D"),
            _candidate("F", grade="X"),
            _candidate("G", grade=None),
        ])
        frame = _compute("prep_review_grade", _ctx(source))
        self.assertEqual(frame.values, {"A": 4.0, "B": 3.0, "C": 2.0,
                                        "D": 1.0, "E": 0.0})
        self.assertEqual(frame.meta["counters"]["unknown_enum"], 1)
        self.assertEqual(frame.meta["counters"]["missing"], 1)

    def test_grade_mapping_reads_params(self):
        spec = get_spec("prep_review_grade")
        changed = dataclasses.replace(
            spec, params={**spec.params,
                          "mapping": {"S": 9.0, "A": 3.0, "B": 2.0,
                                      "C": 1.0, "D": 0.0}})
        source = _review_source([_candidate("A", grade="S")])
        self.assertEqual(
            spec.compute(_ctx(source, params=spec.params)).values["A"], 4.0)
        self.assertEqual(changed.compute(_ctx(
            source, params=changed.params)).values["A"], 9.0)

    def test_role_mapping_and_null_role(self):
        source = _review_source([
            _candidate("A", role="leader"),
            _candidate("B", role="vanguard"),
            _candidate("C", role="mainstay"),
            _candidate("D", role="capacity"),
            _candidate("E", role="follower"),
            _candidate("F", role=None),
            _candidate("G", role="diagnostic"),
        ])
        frame = _compute("prep_role", _ctx(source))
        self.assertEqual(frame.values, {"A": 5.0, "B": 4.0, "C": 3.0,
                                        "D": 2.0, "E": 1.0})
        self.assertEqual(frame.meta["counters"]["role_missing"], 1)
        self.assertEqual(frame.meta["counters"]["unknown_enum"], 1)

    def test_market_mapping_and_unknown(self):
        for state, expected in (("strong", 3.0), ("neutral", 2.0),
                                ("weak", 1.0), ("ice", 0.0)):
            source = _review_source([_candidate("A"), _candidate("B")],
                                    market_state=state)
            self.assertEqual(_values("prep_market_state", source),
                             {"A": expected, "B": expected})
        unknown = _review_source([_candidate("A")], market_state="unknown")
        frame = _compute("prep_market_state", _ctx(unknown))
        self.assertEqual(frame.values, {})
        self.assertEqual(frame.meta["counters"]["market_unknown"], 1)
        self.assertTrue(frame.meta.get("expected_empty"))

    def test_mainline_rank_and_non_mainline(self):
        source = _review_source([
            _candidate("A", mainline_rank=1),
            _candidate("B", mainline_rank=3),
            _candidate("C", mainline_rank=None),
            _candidate("D", mainline_rank="x"),
        ])
        frame = _compute("prep_mainline_rank", _ctx(source))
        self.assertEqual(frame.values, {"A": 1.0, "B": 3.0})
        self.assertEqual(frame.meta["counters"]["not_mainline"], 1)
        self.assertEqual(frame.meta["counters"]["unknown_rank"], 1)

    def test_feedback_mapping_and_unknown(self):
        feedback = {"items": [
            {"code": "A", "state": "confirmed"},
            {"code": "B", "state": "weakened"},
            {"code": "C", "state": "invalidated"},
            {"code": "D", "state": "unknown", "unknown_reason": "auction_backfill_failed"},
            {"code": "E", "state": "bogus"},
            {"code": "F"},
        ]}
        source = _review_source([_candidate("A")], feedback=feedback)
        frame = _compute("prep_feedback_state", _ctx(source))
        self.assertEqual(frame.values, {"A": 2.0, "B": 1.0, "C": 0.0})
        self.assertEqual(frame.meta["counters"]["unknown_state"], 1)
        self.assertEqual(frame.meta["counters"]["unknown_enum"], 1)
        self.assertEqual(frame.meta["counters"]["missing"], 1)

    def test_feedback_phase_is_close(self):
        spec = get_spec("prep_feedback_state")
        self.assertEqual(spec.available_phase, "close")


# ─── 2. 缺失 / null / 零 语义 ─────────────────────────────────────────────


class TestMissingRules(unittest.TestCase):
    def test_all_null_candidate_writes_nothing(self):
        blank = _candidate(
            "A", score=None, grade=None, position=None, mainline_rank=None,
            role=None, d1=None, d2=None, d3=None, d4=None, d5=None, d6=None,
            seal_ratio=None, main_net_ratio=None, turnover_health=None,
            distance_high60=None, chip_concentration=None)
        source = _review_source([blank])
        for name in REVIEW_NAMES:
            if name == "prep_market_state":
                continue  # 市场状态是截面值，不随候选缺失
            frame = _compute(name, _ctx(source))
            self.assertEqual(frame.values, {}, name)

    def test_position_zero_is_real_value_but_null_is_skipped(self):
        source = _review_source([
            _candidate("A", position=0.0),
            _candidate("B", position=12.5),
            _candidate("C", position=None),
            _candidate("D", position=-1.0),
        ])
        frame = _compute("prep_position_pct", _ctx(source))
        self.assertEqual(frame.values, {"A": 0.0, "B": 12.5})
        self.assertEqual(frame.meta["counters"]["missing"], 1)
        self.assertEqual(frame.meta["counters"]["invalid"], 1)

    def test_review_missing_raises_source_unavailable(self):
        source = _review_source([_candidate("A")], include_review=False)
        with self.assertRaises(SourceUnavailable):
            _compute("prep_review_score", _ctx(source))

    def test_review_disabled_raises_source_unavailable(self):
        source = _review_source([_candidate("A")], enabled=False)
        with self.assertRaises(SourceUnavailable):
            _compute("prep_review_score", _ctx(source))

    def test_feedback_absent_is_expected_empty_not_source_failure(self):
        source = _review_source([_candidate("A")])
        frame = _compute("prep_feedback_state", _ctx(source))
        self.assertEqual(frame.values, {})
        self.assertTrue(frame.meta.get("expected_empty"))
        self.assertEqual(frame.meta["denominator"], 0)

    def test_feedback_compute_error_is_source_unavailable(self):
        source = _review_source([_candidate("A")])
        source["sentiment"] = {"factor_errors": {"feedback": "boom"}}
        with self.assertRaises(SourceUnavailable):
            _compute("prep_feedback_state", _ctx(source))

    def test_feedback_plan_code_outside_review_pool_not_diluted(self):
        # 计划代码 Z 不在当日详细池（只有 A）：分母仍 = 计划集合（1），不是 1/1 之外的池。
        source = _review_source(
            [_candidate("A")],
            feedback={"items": [{"code": "Z", "state": "confirmed"}]})
        frame = _compute("prep_feedback_state", _ctx(source))
        self.assertEqual(frame.values, {"Z": 2.0})
        self.assertEqual(frame.meta["denominator"], 1)

    def test_feedback_pool_much_larger_than_plan(self):
        candidates = [_candidate(code) for code in ("A", "B", "C", "D", "E")]
        source = _review_source(candidates, feedback={"items": [
            {"code": "A", "state": "confirmed"},
            {"code": "B", "state": "weakened"},
        ]})
        frame = _compute("prep_feedback_state", _ctx(source))
        self.assertEqual(frame.values, {"A": 2.0, "B": 1.0})
        self.assertEqual(frame.meta["denominator"], 2)


# ─── 3. 版本 / params 单一真源 ────────────────────────────────────────────


class TestVersionAndParams(unittest.TestCase):
    def test_every_spec_has_config_identity_params(self):
        for name in REVIEW_NAMES:
            params = get_spec(name).params
            self.assertEqual(params.get("review_config_version"),
                             factor_review.REVIEW_CONFIG_VERSION, name)
            self.assertEqual(params.get("review_config_hash"),
                             factor_review.REVIEW_CONFIG_HASH, name)

    def test_config_identity_matches_single_source(self):
        from dragon_quant.review_account.prep.review.config import (
            CONFIG_VERSION, config_hash,
        )
        self.assertEqual(factor_review.REVIEW_CONFIG_VERSION, CONFIG_VERSION)
        self.assertEqual(factor_review.REVIEW_CONFIG_HASH, config_hash())

    def test_config_hash_change_bumps_version(self):
        spec = get_spec("prep_review_score")
        changed = dataclasses.replace(
            spec, params={**spec.params, "review_config_hash": "deadbeefcafe"})
        self.assertNotEqual(compute_version_info(spec)[0],
                            compute_version_info(changed)[0])

    def test_config_identity_lands_in_source_meta(self):
        frame = _compute(
            "prep_review_score",
            _ctx(_review_source([_candidate("A")])))
        self.assertEqual(frame.meta["review_config_hash"],
                         factor_review.REVIEW_CONFIG_HASH)
        self.assertEqual(frame.meta["source_config_hash"],
                         factor_review.REVIEW_CONFIG_HASH)

    def test_config_hash_mismatch_fails_closed(self):
        source = _review_source([_candidate("A")], config_hash="deadbeefcafe")
        with self.assertRaises(SourceUnavailable) as raised:
            _compute("prep_review_score", _ctx(source))
        self.assertIn("config_hash_mismatch", str(raised.exception))


# ─── 4. 源适配 / 分母 / 新代码落行 ────────────────────────────────────────


class TestSourceAdaptation(TempDb):
    def test_review_candidates_outside_ctx_codes_are_written(self):
        # 旧 candidates 为空（ctx.codes=()），新族仍按 review_candidates 落行。
        self._seed_prep(_healthy_snapshot([_candidate("600777")]))
        entry = engine.build_factor(get_spec("prep_review_score"), DAY)
        self.assertEqual(entry["status"], "ok")
        self.assertEqual(entry["codes"], 0)
        self.assertEqual(entry["coverage"], 1.0)  # 分母 = len(review_candidates)
        rows = self._rows(["prep_review_score"])
        self.assertEqual([(row["code"], row["value"]) for row in rows],
                         [("600777", 80.0)])
        self.assertEqual(entry["available_at"], f"{DAY}T16:40:00")

    def test_coverage_denominator_is_review_candidate_count(self):
        self._seed_prep(_healthy_snapshot([
            _candidate("A"), _candidate("B", score=None)]))
        entry = engine.build_factor(get_spec("prep_review_score"), DAY)
        self.assertEqual(entry["coverage"], 0.5)

    def test_review_missing_keeps_old_rows(self):
        self._seed_prep(_healthy_snapshot([_candidate("A")]))
        self.assertEqual(
            engine.build_factor(get_spec("prep_review_score"), DAY)["status"], "ok")
        self.assertEqual(len(self._rows(["prep_review_score"])), 1)

        self._seed_prep(_healthy_snapshot([_candidate("A")], include_review=False))
        entry = engine.build_factor(get_spec("prep_review_score"), DAY)
        self.assertEqual(entry["status"], "missing_source")
        self.assertEqual(len(self._rows(["prep_review_score"])), 1)

    def test_review_disabled_keeps_old_rows(self):
        self._seed_prep(_healthy_snapshot([_candidate("A")]))
        engine.build_factor(get_spec("prep_review_score"), DAY)
        self._seed_prep(_healthy_snapshot([_candidate("A")], enabled=False))
        entry = engine.build_factor(get_spec("prep_review_score"), DAY)
        self.assertEqual(entry["status"], "missing_source")
        self.assertEqual(len(self._rows(["prep_review_score"])), 1)

    def test_feedback_available_at_close_phase(self):
        feedback = {"items": [{"code": "A", "state": "confirmed"}]}
        self._seed_prep(_healthy_snapshot([_candidate("A")], feedback=feedback))
        entry = engine.build_factor(get_spec("prep_feedback_state"), DAY)
        self.assertEqual(entry["status"], "ok")
        self.assertEqual(entry["available_at"], f"{DAY}T15:05:00")

    def test_feedback_coverage_uses_plan_universe(self):
        candidates = [_candidate(code) for code in ("A", "B", "C", "D", "E")]
        feedback = {"items": [{"code": "A", "state": "confirmed"},
                              {"code": "B", "state": "weakened"}]}
        self._seed_prep(_healthy_snapshot(candidates, feedback=feedback))
        entry = engine.build_factor(get_spec("prep_feedback_state"), DAY)
        self.assertEqual(entry["status"], "ok")
        self.assertEqual(entry["coverage"], 1.0)
        self.assertEqual(entry["meta"]["denominator"], 2)

    def test_config_hash_match_writes_rows(self):
        self._seed_prep(_healthy_snapshot([_candidate("A")]))
        entry = engine.build_factor(get_spec("prep_review_score"), DAY)
        self.assertEqual(entry["status"], "ok")
        self.assertEqual(
            [(row["code"], row["value"])
             for row in self._rows(["prep_review_score"])],
            [("A", 80.0)])

    def test_config_hash_mismatch_writes_no_rows_and_keeps_old_rows(self):
        self._seed_prep(_healthy_snapshot([_candidate("A")]))
        engine.build_factor(get_spec("prep_review_score"), DAY)
        self.assertEqual(len(self._rows(["prep_review_score"])), 1)

        self._seed_prep(_healthy_snapshot(
            [_candidate("B")], config_hash="deadbeefcafe"))
        entry = engine.build_factor(get_spec("prep_review_score"), DAY)
        self.assertEqual(entry["status"], "missing_source")
        self.assertIn("config_hash_mismatch", entry["reason"])
        self.assertEqual(
            [(row["code"], row["value"])
             for row in self._rows(["prep_review_score"])],
            [("A", 80.0)])

    def test_legacy_prep_rows_not_touched(self):
        fstore.replace_factor_day(DAY, "prep_composite", "v0", [
            FactorValue(trade_date=DAY, code="A", factor_name="prep_composite",
                        factor_version="v0", value=88.0,
                        available_at=f"{DAY}T16:40:00", source="prep"),
        ])
        self._seed_prep(_healthy_snapshot([_candidate("A")]))
        self.assertEqual(
            engine.build_factor(get_spec("prep_review_score"), DAY)["status"], "ok")
        self.assertEqual(
            [(row["code"], row["value"]) for row in self._rows(["prep_composite"])],
            [("A", 88.0)])


# ─── 5. 注册表与默认零影响 ────────────────────────────────────────────────


class TestRegistry(unittest.TestCase):
    def test_registry_valid_and_metadata(self):
        validate_registry(FACTOR_SPECS)
        by_name = {spec.name: spec for spec in FACTOR_SPECS}
        for name in REVIEW_NAMES:
            spec = by_name[name]
            self.assertIs(spec.default_enabled, False, name)
            self.assertEqual(spec.source, "prep", name)
            self.assertEqual(spec.pit_rule, "derived", name)
            expected_phase = "close" if name == "prep_feedback_state" else "prep"
            self.assertEqual(spec.available_phase, expected_phase, name)
        self.assertEqual(by_name["prep_review_score"].unit, "score")
        self.assertEqual(by_name["prep_position_pct"].unit, "pct")
        self.assertEqual(by_name["prep_mainline_rank"].direction, "lower_better")
        self.assertEqual(by_name["prep_chip_concentration"].direction, "lower_better")
        self.assertEqual(by_name["prep_role"].direction, "neutral")
        self.assertEqual(by_name["prep_feedback_state"].direction, "neutral")

    def test_default_snapshot_unchanged(self):
        snapshot = default_snapshot()
        self.assertFalse(any(name.startswith("prep_review_")
                             for name in snapshot["factor_versions"]))


# ─── 6. 可成交标签 / 消融 / 滚动 OOS / 影子 ──────────────────────────────


class _Market:
    def __init__(self, bars):
        self._bars = bars

    def daily(self, code, start):
        return self._bars.get(code, [])


def _bar(day, open_=10.0, close=10.0, volume=1_000_000.0,
         amount=500_000_000.0, **extra):
    payload = {"trade_date": day, "open": open_, "close": close,
               "high": extra.get("high", close), "low": extra.get("low", close),
               "volume": volume, "amount": amount}
    payload.update(extra)
    return payload


class TestTradableLabels(unittest.TestCase):
    CAL = ["2026-10-26", "2026-10-27", "2026-10-28", "2026-10-29", "2026-11-02"]

    def test_entry_is_next_open_after_available_at(self):
        bars = {"A": [_bar("2026-10-28", 10.0), _bar("2026-10-29", 11.0),
                      _bar("2026-11-02", 12.0)]}
        result = review_shadow.build_tradable_labels(
            [{"code": "A", "available_at": f"{DAY}T16:40:00"}],
            _Market(bars), self.CAL, horizons=(1,))
        self.assertEqual(result["status"], "ok")
        label = result["labels"][0]
        self.assertEqual(label["entry_date"], "2026-10-29")
        self.assertEqual(label["exit_date"], "2026-11-02")
        self.assertAlmostEqual(label["value"], (12.0 / 11.0 - 1.0) * 100.0 - 0.13)

    def test_one_word_and_suspended_and_illiquid_are_excluded(self):
        bars = {
            "W": [_bar("2026-10-28"), _bar("2026-10-29", 11.0, volume=0.0,
                                           amount=0.0, limit_up_price=11.0)],
            "S": [_bar("2026-10-28"), _bar("2026-10-29", 0.0, volume=0.0,
                                           amount=0.0)],
            "L": [_bar("2026-10-28"), _bar("2026-10-29", 11.0,
                                           amount=100_000_000.0)],
        }
        result = review_shadow.build_tradable_labels(
            [{"code": "W", "available_at": f"{DAY}T16:40:00"},
             {"code": "S", "available_at": f"{DAY}T16:40:00"},
             {"code": "L", "available_at": f"{DAY}T16:40:00"}],
            _Market(bars), self.CAL, horizons=(1,))
        self.assertEqual(result["labels"], [])
        self.assertEqual(result["exclusions"]["unfillable_one_word"], 1)
        self.assertEqual(result["exclusions"]["suspended"], 1)
        self.assertEqual(result["exclusions"]["illiquid"], 1)
        self.assertEqual(result["status"], "insufficient_data")

    def test_auction_price_used_when_fillable(self):
        bars = {"A": [_bar("2026-10-28"), _bar("2026-10-29", 11.0),
                      _bar("2026-11-02", 12.0)]}
        result = review_shadow.build_tradable_labels(
            [{"code": "A", "available_at": f"{DAY}T16:40:00",
              "auction_price": 10.5, "auction_fillable": True}],
            _Market(bars), self.CAL, horizons=(1,))
        self.assertEqual(result["labels"][0]["entry_price"], 10.5)


class TestAblationAndOos(unittest.TestCase):
    def _days(self, n=20):
        candidates, labels = {}, {}
        for index in range(n):
            day = f"2026-10-{index + 1:02d}"
            candidates[day] = []
            labels[day] = {}
            for rank, code in enumerate(("A", "B", "C", "D")):
                score = 90.0 - rank * 20.0 + index * 0.1
                candidates[day].append({
                    "code": code,
                    "dimensions": {
                        "D1": {"score": score, "weight": 0.3},
                        "D2": {"score": 50.0, "weight": 0.2},
                        "D3": {"score": 50.0, "weight": 0.2},
                        "D4": {"score": 50.0, "weight": 0.2},
                        "D5": {"score": 50.0, "weight": 0.05},
                        "D6": {"score": 50.0, "weight": 0.05},
                    },
                })
                labels[day][code] = 5.0 - rank * 3.0 + index * 0.01
        return candidates, labels

    def test_ablation_and_oos_are_finite_when_history_sufficient(self):
        candidates, labels = self._days()
        ablation = review_shadow.ablation(candidates, labels)
        self.assertEqual(ablation["status"], "evaluated")
        self.assertIsNotNone(ablation["full"]["mean_rank_ic"])
        self.assertIn("D1", ablation["leave_one_out"])
        oos = review_shadow.walk_forward_ic(candidates, labels)
        self.assertEqual(oos["status"], "evaluated")
        self.assertEqual(len(oos["windows"]), 4)

    def test_insufficient_history_never_claims_validity(self):
        candidates, labels = self._days(2)
        ablation = review_shadow.ablation(candidates, labels)
        self.assertEqual(ablation["status"], "insufficient_data")
        self.assertNotIn("mean_rank_ic", ablation.get("full", {}))
        oos = review_shadow.walk_forward_ic(candidates, labels)
        self.assertEqual(oos["status"], "insufficient_data")


class TestShadowMetrics(unittest.TestCase):
    def _day(self, index, state="confirmed", close_pct=1.0, **kwargs):
        return {
            "trade_date": f"2026-10-{index + 1:02d}",
            "feedback_items": [{"code": "A", "state": state,
                                "close_pct": close_pct,
                                "unknown_reason": kwargs.get("unknown_reason")}],
            "pool": {"count": 4, "truncated": kwargs.get("truncated", False)},
            "planned_codes": kwargs.get("planned", ["A"]),
            "auction_covered_codes": kwargs.get("covered", ["A"]),
            "http_requests": kwargs.get("http_requests", 2),
        }

    def test_insufficient_history_is_not_a_validity_claim(self):
        report = review_shadow.shadow_metrics([self._day(0)])
        self.assertEqual(report["status"], "insufficient_data")
        self.assertEqual(report["days"], 1)
        self.assertIn("不构成有效性结论", report["note"])

    def test_metrics_at_twenty_days(self):
        days = [self._day(i) for i in range(20)]
        report = review_shadow.shadow_metrics(days)
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["confirmed"]["hit_rate"], 1.0)
        self.assertIsNone(report["invalidated"]["correct_avoidance_rate"])
        self.assertEqual(report["unknown"]["ratio"], 0.0)
        self.assertEqual(report["http_requests"], 40)
        self.assertEqual(report["truncation"]["ratio"], 0.0)
        self.assertEqual(report["auction_coverage"]["ratio"], 1.0)

    def test_unknown_reasons_and_invalidated(self):
        days = [self._day(0, state="invalidated", close_pct=-2.0)]
        days += [self._day(i, state="unknown", close_pct=None,
                           unknown_reason="auction_backfill_failed")
                 for i in range(1, 20)]
        report = review_shadow.shadow_metrics(days)
        self.assertEqual(report["invalidated"]["correct_avoidance_rate"], 1.0)
        self.assertEqual(report["unknown"]["by_reason"],
                         {"auction_backfill_failed": 19})


if __name__ == "__main__":
    unittest.main()
