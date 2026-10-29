"""因子注册表测试：版本哈希稳定性 / 源码指纹 / 注册表校验（方案 §8.1）。"""

import unittest
from unittest.mock import patch

from dragon_quant.infra.factor.registry import (
    FactorRegistryError,
    canonical_json,
    compute_version,
    compute_version_info,
    resolve_specs,
    spec_hash,
    validate_registry,
)
from dragon_quant.infra.factor.types import FactorFrame, FactorSpec


def _spec(**overrides) -> FactorSpec:
    base = dict(
        name="demo_factor",
        label="示例",
        frequency="1d",
        source="computed",
        compute=_compute_a,
        impl_key="dragon_quant.tests.demo.v1",
        direction="higher_better",
        unit="score",
        available_phase="close",
        pit_rule="recorded",
        params={"window": 5},
    )
    base.update(overrides)
    return FactorSpec(**base)


def _compute_a(ctx):
    # 语义实现：窗口求和
    total = sum(ctx.source_rows.get("values") or [])
    return FactorFrame(values={"600001": float(total)}, available_at=None, source="computed")


def _compute_b(ctx):
    total = sum(ctx.source_rows.get("values") or []) * 2
    return FactorFrame(values={"600001": float(total)}, available_at=None, source="computed")


class TestVersionHash(unittest.TestCase):
    def test_same_spec_same_version(self):
        self.assertEqual(compute_version(_spec()), compute_version(_spec()))

    def test_semantic_fields_change_version(self):
        base = compute_version(_spec())
        for overrides in (
            {"params": {"window": 10}},
            {"direction": "lower_better"},
            {"pit_rule": "derived"},
            {"available_phase": "prep"},
            {"unit": "pct"},
            {"frequency": "5m"},
            {"source": "scan"},
            {"lookback": 3},
            {"scope": ("candidate", "dragon")},
        ):
            with self.subTest(overrides=overrides):
                self.assertNotEqual(base, compute_version(_spec(**overrides)))

    def test_label_description_owner_do_not_change_version(self):
        base = compute_version(_spec())
        self.assertEqual(base, compute_version(_spec(
            label="另一个名字", description="改了文案", owner="someone")))

    def test_comment_only_change_keeps_version(self):
        """token 级规范化：仅注释不同的实现 → 同一版本。"""
        with patch("dragon_quant.infra.factor.registry.inspect.getsource",
                   side_effect=[
                       "def f(ctx):\n    # 只改注释\n    return 1\n",
                       "def f(ctx):\n    return 1\n",
                   ]):
            left = compute_version(_spec(compute=_compute_a))
            right = compute_version(_spec(compute=_compute_b))
        self.assertEqual(left, right)

    def test_hash_string_literal_is_not_stripped(self):
        """含 # 的字符串字面量必须保留：正则剥注释会误删，导致伪版本。"""
        from dragon_quant.infra.factor.registry import _normalize_tokens
        kept = _normalize_tokens("x = 'a#b'\n")
        self.assertIn("#", kept)
        with patch("dragon_quant.infra.factor.registry.inspect.getsource",
                   side_effect=[
                       "def f(ctx):\n    return 'a#b'\n",
                       "def f(ctx):\n    return 'ab'\n",
                   ]):
            left = compute_version(_spec(compute=_compute_a))
            right = compute_version(_spec(compute=_compute_b))
        self.assertNotEqual(left, right)

    def test_getsource_failure_falls_back_to_impl_key(self):
        with patch("dragon_quant.infra.factor.registry.inspect.getsource",
                   side_effect=OSError("no source")):
            version, source = compute_version_info(_spec(compute=_compute_b))
        self.assertEqual(source, "impl_key")
        self.assertTrue(version.startswith("v"))
        self.assertEqual(len(version), 13)

    def test_version_override(self):
        version, source = compute_version_info(
            _spec(compute=_compute_b, version_override="legacy_v0"))
        self.assertEqual((version, source), ("legacy_v0", "override"))

    def test_canonical_json_is_deterministic(self):
        left = canonical_json({"b": [1, 2], "a": {"y": 1, "x": 2}})
        right = canonical_json({"a": {"x": 2, "y": 1}, "b": [1, 2]})
        self.assertEqual(left, right)

    def test_canonical_json_rejects_nan(self):
        with self.assertRaises(ValueError):
            canonical_json({"x": float("nan")})

    def test_spec_hash_is_full_sha256(self):
        self.assertEqual(len(spec_hash(_spec())), 64)

    def test_all_builtin_factors_have_source_fingerprints(self):
        from dragon_quant.infra.factor.registry import FACTOR_SPECS
        for spec in FACTOR_SPECS:
            with self.subTest(spec=spec.name):
                _, source = compute_version_info(spec)
                self.assertEqual(source, "source")


class TestRegistryValidation(unittest.TestCase):
    def test_builtin_registry_is_valid(self):
        from dragon_quant.infra.factor.registry import FACTOR_SPECS
        validate_registry(FACTOR_SPECS)

    def test_duplicate_name_rejected(self):
        with self.assertRaises(FactorRegistryError):
            validate_registry([_spec(), _spec(compute=_compute_b)])

    def test_invalid_enum_values_rejected(self):
        for overrides in (
            {"frequency": "1h"},
            {"source": "unknown"},
            {"direction": "up"},
            {"unit": "usd"},
            {"available_phase": "noon"},
            {"pit_rule": "magic"},
            {"scope": ("pool",)},
            {"lookback": -1},
        ):
            with self.subTest(overrides=overrides):
                with self.assertRaises(FactorRegistryError):
                    validate_registry([_spec(**overrides)])

    def test_dangling_dependency_rejected(self):
        with self.assertRaises(FactorRegistryError):
            validate_registry([_spec(depends_on=("not_exists",))])

    def test_resolve_specs_unknown_name(self):
        with self.assertRaises(FactorRegistryError):
            resolve_specs(["not_exists"])

    def test_resolve_specs_enabled_only(self):
        specs = resolve_specs(enabled_only=True)
        self.assertTrue(specs)
        self.assertTrue(all(spec.default_enabled for spec in specs))
        self.assertEqual([s.name for s in specs], sorted(s.name for s in specs))

    def test_builtin_count_and_names(self):
        from dragon_quant.infra.factor.registry import spec_names
        names = set(spec_names())
        # 18 = 五维 7 + vpa 5 + 财务 6；P1.5 prep 5 + auction 17；M6 review 18。
        self.assertEqual(len(names), 58)
        fin_names = {"fin_bps", "fin_roe", "fin_net_margin", "fin_net_profit_yoy",
                     "fin_revenue_yoy", "fin_net_assets"}
        self.assertEqual(names & fin_names, fin_names)
        prep_auction = {name for name in names
                        if name.startswith(("prep_", "auction_"))}
        self.assertEqual(len(prep_auction), 40)
        # 财务 / prep / auction 因子默认关：build without --only 不得静默计算
        from dragon_quant.infra.factor.registry import get_spec
        self.assertTrue(all(not get_spec(name).default_enabled
                            for name in fin_names | prep_auction))


if __name__ == "__main__":
    unittest.main()
