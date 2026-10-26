"""M7 + Task 7: ``infra.factor`` 公共门面（13 个批准出口 + 25 个 lazy 旧名）。

本文件只钉结构契约（不改任何因子行为）：

* ``infra.factor.main.__all__`` 恰好是用户批准的 13 个名字且顺序固定；
* 包根 ``infra.factor`` 是 ``main`` 的惰性代理：13 个公共名 + 25 个旧名运行期
  解析到 ``infra.factor.<owner>`` 的既有实现（因此
  ``patch("...infra.factor.service.build")`` 仍然生效，命令层不需要深引内部模块）；
* 旧名不出现在 ``main`` 模块 ``__dict__``，解析后也不写回；
* unknown name 仍抛 ``AttributeError``；
* 导入包根不 eager import 任何应用子模块。
"""

from __future__ import annotations

import importlib
import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import dragon_quant.infra.factor as factor
from dragon_quant.infra.factor import main as factor_main

ROOT = Path(__file__).resolve().parents[2]

# 用户批准的唯一公共出口（顺序即 __all__ 顺序）。
PUBLIC_EXPORTS = [
    "build",
    "list_factors",
    "query",
    "status",
    "export_matrix",
    "evaluate_factor",
    "fin_sync",
    "fin_calibrate",
    "fin_status",
    "parse_only",
    "FactorModel",
    "annotate_pool",
    "CleanConfig",
]

# public name -> (owner submodule, attribute name)
PUBLIC_SOURCES = {
    **{
        name: ("service", name)
        for name in (
            "build",
            "list_factors",
            "query",
            "status",
            "fin_sync",
            "fin_calibrate",
            "fin_status",
            "parse_only",
        )
    },
    # B3 只批准收窄门面，不批准改语义：这两个保持收窄前的原始实现身份。
    "export_matrix": ("matrix", "export_matrix"),
    "evaluate_factor": ("evaluation", "evaluate_factor"),
    "FactorModel": ("decision", "FactorModel"),
    "annotate_pool": ("decision", "annotate_pool"),
    "CleanConfig": ("matrix", "CleanConfig"),
}

# 旧包根名：保留一轮，M11 删除。
LEGACY_EXPORTS = {
    "FACTOR_SPECS": ("registry", "FACTOR_SPECS"),
    "AsOfViolation": ("types", "AsOfViolation"),
    "BackfilledSource": ("types", "BackfilledSource"),
    "EqualWeightRankModel": ("decision", "EqualWeightRankModel"),
    "FactorContext": ("types", "FactorContext"),
    "FactorFrame": ("types", "FactorFrame"),
    "FactorRegistryError": ("registry", "FactorRegistryError"),
    "FactorSpec": ("types", "FactorSpec"),
    "FactorValue": ("types", "FactorValue"),
    "PoolInput": ("decision", "PoolInput"),
    "PoolItem": ("decision", "PoolItem"),
    "build_completion_rate": ("decision", "build_completion_rate"),
    "build_date": ("engine", "build_date"),
    "build_factor": ("engine", "build_factor"),
    "canonical_scan": ("engine", "canonical_scan"),
    "candidate_factor_date": ("decision", "candidate_factor_date"),
    "compute_version": ("registry", "compute_version"),
    "compute_version_info": ("registry", "compute_version_info"),
    "default_snapshot": ("decision", "default_snapshot"),
    "get_spec": ("registry", "get_spec"),
    "phase_ts": ("engine", "phase_ts"),
    "resolve_available_at": ("engine", "resolve_available_at"),
    "resolve_specs": ("registry", "resolve_specs"),
    "save_default_model_snapshot": ("decision", "save_default_model_snapshot"),
    "spec_names": ("registry", "spec_names"),
}


def _owner(module_name: str):
    return importlib.import_module(f"dragon_quant.infra.factor.{module_name}")


class TestPublicFacade(unittest.TestCase):
    def test_all_is_exactly_the_thirteen_approved_names(self):
        self.assertEqual(factor_main.__all__, PUBLIC_EXPORTS)
        self.assertEqual(factor.__all__, PUBLIC_EXPORTS)
        self.assertEqual(len(factor_main.__all__), 13)
        self.assertEqual(sorted(PUBLIC_EXPORTS), sorted(PUBLIC_SOURCES))

    def test_public_names_resolve_to_their_existing_implementations(self):
        for name, (module_name, attribute) in PUBLIC_SOURCES.items():
            with self.subTest(name=name):
                self.assertIs(getattr(factor, name), getattr(_owner(module_name), attribute))

    def test_public_resolution_keeps_monkeypatch_targets_live(self):
        sentinel = object()
        with patch("dragon_quant.infra.factor.service.build", sentinel):
            self.assertIs(factor.build, sentinel)
        # 未缓存：patch 退出后立即回到真实实现（命令层运行期解析依赖这一点）。
        self.assertIs(factor.build, _owner("service").build)

    def test_star_import_uses_exactly_the_public_all(self):
        namespace: dict = {}
        exec("from dragon_quant.infra.factor import *", namespace)  # noqa: S102
        exported = {name for name in namespace if not name.startswith("__")}
        self.assertEqual(exported, set(PUBLIC_EXPORTS))

    def test_export_and_evaluate_keep_their_pre_m7_identities(self):
        """B3 只批准收窄门面：包根不得把原始实现换成 service 用例包装。"""
        matrix = _owner("matrix")
        evaluation = _owner("evaluation")
        service = _owner("service")
        self.assertIs(factor.export_matrix, matrix.export_matrix)
        self.assertIs(factor.evaluate_factor, evaluation.evaluate_factor)
        self.assertIsNot(factor.export_matrix, service.export_matrix)
        self.assertIsNot(factor.evaluate_factor, service.evaluate_factor)

    def test_from_import_of_export_and_evaluate_does_not_route_to_service(self):
        from dragon_quant.infra.factor import evaluate_factor, export_matrix  # noqa: F401

        self.assertIs(export_matrix, _owner("matrix").export_matrix)
        self.assertIs(evaluate_factor, _owner("evaluation").evaluate_factor)

    def test_lazy_mapping_uses_package_not_module_name(self):
        """Task 7: owner 路径必须用 ``__package__``，不能解析成 factor.main.service。"""
        source = (ROOT / "dragon_quant" / "infra" / "factor" / "main.py").read_text(
            encoding="utf-8")
        self.assertIn("_PACKAGE = __package__", source)
        self.assertIn('f"{_PACKAGE}.{owner}"', source)
        self.assertNotIn('f"{__name__}.{owner}"', source)
        self.assertEqual(factor.build.__module__, "dragon_quant.infra.factor.service")
        self.assertEqual(factor.canonical_scan.__module__, "dragon_quant.infra.factor.engine")


class TestLegacyCompatibility(unittest.TestCase):
    def test_legacy_names_stay_importable_lazily(self):
        for name, (module_name, attribute) in LEGACY_EXPORTS.items():
            with self.subTest(name=name):
                self.assertNotIn(name, vars(factor_main), "旧名必须 lazy，不得 eager 绑定")
                self.assertIs(getattr(factor, name), getattr(_owner(module_name), attribute))
                self.assertNotIn(name, vars(factor_main), "解析后也不得写回模块 __dict__")

    def test_legacy_from_import_still_works(self):
        from dragon_quant.infra.factor import canonical_scan, compute_version_info  # noqa: F401

        self.assertIs(canonical_scan, _owner("engine").canonical_scan)
        self.assertIs(compute_version_info, _owner("registry").compute_version_info)

    def test_unknown_attribute_raises_attribute_error(self):
        with self.assertRaises(AttributeError) as ctx:
            factor.definitely_not_a_factor_export
        self.assertIn("definitely_not_a_factor_export", str(ctx.exception))

    def test_documented_submodules_stay_importable(self):
        # 现有调用方（review_account / 单测）从包根取子模块；lazy 门面不得破坏它。
        from dragon_quant.infra.factor import (  # noqa: F401
            decision,
            engine,
            evaluation,
            finance,
            service,
            stats,
            storage,
        )

        self.assertTrue(hasattr(service, "build"))
        self.assertTrue(hasattr(engine, "build_date"))


class TestLazyInitialization(unittest.TestCase):
    def test_importing_the_facade_loads_no_factor_submodules(self):
        code = (
            "import sys\n"
            "import dragon_quant.infra.factor  # noqa: F401\n"
            "print(','.join(sorted(\n"
            "    name for name in sys.modules\n"
            "    if name.startswith('dragon_quant.infra.factor.'))))\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=180,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "", result.stdout)


class TestCommandUsesServiceWrappers(unittest.TestCase):
    """CLI 的日期归一 / ok 退出码语义属于 infra.factor.service，不属于包根原函数。"""

    def test_export_uses_the_service_wrapper(self):
        from dragon_quant.commands import factor as command

        args = SimpleNamespace(
            factor_action="export", from_date="2026-05-01", to_date="2026-05-31",
            names="five_dim_composite", codes=None, version=None, clean=False,
            missing=None, winsorize=None, normalize=None, limits="0.05,0.95",
            fmt="csv", out_path="/tmp/matrix.csv", json=False)
        with patch("dragon_quant.infra.factor.service.export_matrix",
                   return_value={"ok": True}) as export, \
                patch("dragon_quant.commands.factor._print_factor_human"):
            command.handle(args, None)

        export.assert_called_once()
        self.assertIsInstance(export.call_args.kwargs["clean"], _owner("matrix").CleanConfig)

    def test_eval_uses_the_service_wrapper(self):
        from dragon_quant.commands import factor as command

        args = SimpleNamespace(
            factor_action="eval", name="five_dim_composite",
            from_date="2026-05-01", to_date="2026-08-31", horizon=1, groups=2,
            version=None, min_days=20, min_cross_section=10,
            pit_policy="strict", json=False)
        with patch("dragon_quant.infra.factor.service.evaluate_factor",
                   return_value={"ok": True}) as evaluate, \
                patch("dragon_quant.review_account.main.MarketData") as market_data, \
                patch("dragon_quant.commands.factor._print_factor_human"):
            command.handle(args, None)

        evaluate.assert_called_once()
        market_data.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
