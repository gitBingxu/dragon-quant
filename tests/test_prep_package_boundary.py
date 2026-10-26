"""M5B：prep 包内 collectors / service 职责边界与旧 patch 兼容（正式门禁）。

M5A 把 ``review_account.prep`` 转为包，M5B 再把 8 个采集器、分类/健康判定与
collection_report 合并从过渡 service 迁到 ``collectors.py``。本模块用正式 pytest
固化拆分后必须成立的兼容契约（替代一次性 inline 取证）：

* 门面与子模块导出**同一实现**（不是同名副本）；
* 旧 patch 目标（provider 类、``ASSET_COLLECTORS`` 重绑定/原地改、
  ``dragons_mirror``、``collect_macro``、``_read_dragon_day``、``_stale_dates``、
  ``_validate_trade_date``/``plan_prep``/``_run_collection``）在调用期真实生效；
* ``legacy`` 对未知名字 fail loud（不静默回退到静态全局）；
* ``--recompute`` 零 provider、零规划、零采集；
* collectors / service 各只有一份实现，职责不重叠。
"""

from __future__ import annotations

import ast
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from dragon_quant.review_account import prep
from dragon_quant.review_account.prep import collectors, compute, rebuild, service

PREP_DIR = Path(prep.__file__).resolve().parent
DAY = "2026-10-19"

COLLECTOR_FUNCTIONS = (
    "_collect_dragons",
    "_collect_ths_limit_up_pool",
    "_collect_ths_block_top",
    "_collect_market_breadth",
    "_collect_fg_boards",
    "_collect_fund_flow",
    "_collect_cninfo",
    "_collect_macro",
)
SUSPICIOUS_FUNCTIONS = (
    "_pool_suspicious",
    "_breadth_suspicious",
    "_fg_suspicious",
    "_fund_flow_suspicious",
    "_macro_suspicious",
)
HEALTH_FUNCTIONS = (
    "asset_health",
    "collection_health",
    "is_collectable",
    "plan_repairs",
    "merge_report_entries",
    "_prepare_assets",
    "_window_degradations",
    "_classify_payload",
    "_report_status",
    "_collectors",
    "_asset_codes",
    "_asset_dragon_rows",
    "_dragon_codes",
)
ORCHESTRATION_FUNCTIONS = (
    "run_prep",
    "plan_prep",
    "_run_collection",
    "_compute_all",
    "resolve_window_dates",
    "_validate_trade_date",
    "_trading_dates",
)


def _now() -> datetime:
    return datetime(2026, 10, 19, 16, 0, tzinfo=compute.SHANGHAI)


def _ctx(**overrides) -> collectors.PrepContext:
    base = dict(
        trade_date=DAY,
        today=DAY,
        tdx=MagicMock(name="tdx"),
        ths=MagicMock(name="ths"),
        window_trade_dates=[DAY],
        window_days=collectors.WINDOW_TRADING_DAYS,
    )
    base.update(overrides)
    return collectors.PrepContext(**base)


def _top_level_functions(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return {
        node.name for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _top_level_defs(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return {
        node.name for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }


# ─── 门面 = 子模块同一实现 ─────────────────────────────────────────────────


def test_facade_and_submodules_share_one_implementation():
    """门面导出的每个名字必须就是子模块里的那个对象（禁止同名双份实现）。"""
    owners = {
        collectors: (
            "PrepContext", "CollectorSpec", "ASSET_COLLECTORS",
            "_collect_dragons", "_collect_all", "_collect_cninfo",
            "_collect_fund_flow", "_collect_macro", "_collect_market_breadth",
            "_breadth_suspicious", "_fund_flow_suspicious",
            "_window_degradations", "asset_health", "collection_health",
            "is_collectable", "merge_report_entries", "plan_repairs",
        ),
        service: (
            "PrepError", "PrepPlan", "run_prep", "plan_prep", "_run_collection",
            "_compute_all", "resolve_window_dates", "_validate_trade_date",
        ),
        compute: (
            "SHANGHAI", "position_advice", "compute_sentiment",
            "compute_main_boards", "compute_candidates", "compute_macro",
            "_lianban_count",
        ),
        rebuild: (
            "WINDOW_TRADING_DAYS", "build_dragons_payload",
            "rebuild_dragons_for_recompute", "_read_dragon_day", "_stale_dates",
        ),
    }
    for module, names in owners.items():
        for name in names:
            assert getattr(prep, name) is getattr(module, name), (
                f"prep.{name} 与 {module.__name__}.{name} 不是同一实现")


def test_facade_keeps_only_the_required_compatibility_surface():
    """M5B 不扩大门面：新增的 collector/health 内部 helper 只经 ``prep.collectors``。"""
    for name in ("_classify_payload", "_collectors", "_prepare_assets",
                 "_report_status", "_stored_asset_healthy", "_uncovered_assets",
                 "_providers_from_services", "_collect_ths_block_top",
                 "_collect_ths_limit_up_pool", "_collect_fg_boards",
                 "_pool_suspicious", "_fg_suspicious", "_macro_suspicious",
                 "_dragon_codes", "_asset_codes", "_asset_dragon_rows"):
        assert not hasattr(prep, name), f"门面不应新增导出 {name}"
    assert prep.collectors._classify_payload is collectors._classify_payload


def test_collectors_own_collection_and_service_owns_orchestration():
    """8 个采集器 / 分类 / 健康 / report 合并只在 collectors；编排只在 service。"""
    collector_defs = _top_level_functions(PREP_DIR / "collectors.py")
    service_defs = _top_level_functions(PREP_DIR / "service.py")

    owned_by_collectors = set(COLLECTOR_FUNCTIONS + SUSPICIOUS_FUNCTIONS
                              + HEALTH_FUNCTIONS) | {
        "_collect_all", "_providers_from_services"}
    assert owned_by_collectors <= collector_defs, (
        f"collectors.py 缺少职责: {sorted(owned_by_collectors - collector_defs)}")
    assert not service_defs & owned_by_collectors, (
        f"service.py 仍定义采集/健康职责: {sorted(service_defs & owned_by_collectors)}")

    owned_by_service = set(ORCHESTRATION_FUNCTIONS)
    assert owned_by_service <= service_defs, (
        f"service.py 缺少编排职责: {sorted(owned_by_service - service_defs)}")
    assert not collector_defs & owned_by_service, (
        f"collectors.py 含编排职责: {sorted(collector_defs & owned_by_service)}")

    assert {"PrepContext", "CollectorSpec"} <= _top_level_defs(
        PREP_DIR / "collectors.py")
    assert "PrepPlan" in _top_level_defs(PREP_DIR / "service.py")


def test_moved_helpers_have_exactly_one_definition_site():
    """迁移后的每个 helper 全包只允许一处 def，避免复制实现。"""
    package_files = sorted(PREP_DIR.glob("*.py"))
    expected = {
        **{name: "collectors.py" for name in
           COLLECTOR_FUNCTIONS + SUSPICIOUS_FUNCTIONS + HEALTH_FUNCTIONS
           + ("_collect_all", "_providers_from_services",
              "PrepContext", "CollectorSpec")},
        **{name: "service.py" for name in ORCHESTRATION_FUNCTIONS + ("PrepPlan",)},
    }
    for name, filename in expected.items():
        sites = sorted(path.name for path in package_files
                       if name in _top_level_defs(path))
        assert sites == [filename], f"{name} 的定义位置应唯一: {sites}"


def test_unused_bar_date_helper_is_removed():
    """``_bar_date`` 无调用方（M11A 死代码清理），不得再回到 service。"""
    assert "_bar_date" not in _top_level_functions(PREP_DIR / "service.py")


# ─── 旧 patch 目标必须真实改变运行时行为 ───────────────────────────────────


def test_registry_rebinding_on_the_facade_drives_planning_and_collection():
    """``patch.object(prep, "ASSET_COLLECTORS", ...)`` 重绑定必须影响计划与采集。"""
    probe = collectors.CollectorSpec(lambda ctx: {"total": 1}, same_day_only=False)
    with patch.object(prep, "ASSET_COLLECTORS", {"probe": probe}), \
            patch.object(prep, "TdxProvider"), \
            patch.object(prep, "THSProvider"), \
            patch.object(prep, "TencentProvider"), \
            patch.object(prep, "XueqiuProvider"):
        plan = prep.plan_prep({}, DAY, DAY, force=True)
        payloads, report = prep._collect_all(
            DAY, window_trade_dates=[DAY], asset_types=["probe"], now=_now())

    assert plan.collect == ["probe"]
    assert payloads == {"probe": {"total": 1}}
    assert report["probe"]["status"] == "ok"


def test_registry_in_place_mutation_on_the_facade_is_visible():
    """``patch.dict(prep.ASSET_COLLECTORS, ...)`` 原地改必须被注册表读到。"""
    probe = collectors.CollectorSpec(lambda ctx: {"total": 1}, same_day_only=False)
    with patch.dict(prep.ASSET_COLLECTORS, clear=True):
        prep.ASSET_COLLECTORS["probe"] = probe
        assert list(collectors._collectors()) == ["probe"]
        assert collectors.is_collectable("probe", DAY, DAY) is True


def test_provider_patches_on_the_facade_reach_the_collectors():
    """采集器构造 provider 必须走门面运行期解析，而不是静态类引用。"""
    captured: dict = {}

    def probe(ctx):
        captured["ctx"] = ctx
        return {"total": 1}

    with patch.dict(prep.ASSET_COLLECTORS, clear=True):
        prep.ASSET_COLLECTORS["probe"] = collectors.CollectorSpec(probe, False)
        with patch.object(prep, "TdxProvider") as tdx_cls, \
                patch.object(prep, "THSProvider") as ths_cls, \
                patch.object(prep, "TencentProvider") as tencent_cls, \
                patch.object(prep, "XueqiuProvider") as xueqiu_cls:
            collectors._collect_all(
                DAY, window_trade_dates=[DAY], asset_types=["probe"], now=_now())

    ctx = captured["ctx"]
    assert ctx.tdx is tdx_cls.return_value
    assert ctx.ths is ths_cls.return_value
    assert ctx.tencent is tencent_cls.return_value
    assert ctx.xueqiu is xueqiu_cls.return_value
    for cls in (tdx_cls, ths_cls, tencent_cls, xueqiu_cls):
        cls.assert_called_once_with()


def test_dragons_mirror_patch_on_the_facade_reaches_the_collector():
    """dragons 采集经 ``_read_dragon_day``/``_stale_dates`` 反查门面 ``dragons_mirror``。"""
    mirror = MagicMock()
    mirror.get_dragons.return_value = [
        {"code": "600001", "scan_id": "v2_20261019_top25", "version": "1.0"}]
    mirror.stale_dates.return_value = {}
    ctx = _ctx()

    with patch.object(prep, "dragons_mirror", mirror):
        payload = collectors._collect_dragons(ctx)

    mirror.get_dragons.assert_called_once_with(DAY)
    assert ctx.dragons_ready is True
    assert [row["code"] for row in payload["rows"]] == ["600001"]
    assert payload["count"] == 1


def test_macro_patch_on_the_facade_reaches_the_collector():
    """宏观采集必须经门面解析 ``collect_macro`` / ``YahooMacroSource``。"""
    ctx = _ctx()
    fake_payload = {"instruments": {"jp_nikkei": {}}}

    with patch.object(prep, "collect_macro", return_value=fake_payload) as collect, \
            patch.object(prep, "YahooMacroSource") as yahoo:
        payload = collectors._collect_macro(ctx)

    assert payload == fake_payload
    sources = collect.call_args.args[0]
    assert sources["tdx"] is ctx.tdx
    yahoo.assert_called_once_with()
    assert collect.call_args.kwargs["as_of"] == DAY


def test_run_prep_uses_facade_patch_targets_for_gate_plan_and_collection():
    """``run_prep`` 的日期门禁 / 计划 / 采集入口都必须运行期解析（patch 生效）。"""
    seen: dict = {}

    def fake_validate(trade_date, tdx, now=None):
        seen["validate"] = trade_date
        return [DAY]

    def fake_plan(stored, trade_date, today, *, force=False):
        seen["plan"] = (trade_date, force)
        return prep.PrepPlan(["dragons"], {})

    def fake_collect(trade_date, tdx, calendar, stored, plan, today, now,
                     services=None):
        seen["collect"] = list(plan.collect)
        return {"collection_report": {}}

    with patch.object(prep, "_validate_trade_date", side_effect=fake_validate), \
            patch.object(prep, "plan_prep", side_effect=fake_plan), \
            patch.object(prep, "_run_collection", side_effect=fake_collect), \
            patch.object(prep, "TdxProvider") as tdx_cls, \
            patch("dragon_quant.infra.assets.db.get_prep_assets",
                  return_value={}), \
            patch("dragon_quant.infra.assets.db.save_prep_snapshot"):
        prep.run_prep(DAY, now=_now())

    tdx_cls.assert_called_once_with()
    assert seen == {
        "validate": DAY,
        "plan": (DAY, False),
        "collect": ["dragons"],
    }


# ─── legacy 查找与 recompute 离线 ─────────────────────────────────────────


def test_prep_facade_points_to_main_and_keeps_legacy():
    """Task 12: the public entries live on ``prep.main``; the package is a proxy."""
    from dragon_quant.infra.module_proxy import NameProxyModule
    from dragon_quant.review_account import prep
    from dragon_quant.review_account.prep import main

    assert isinstance(prep, NameProxyModule)
    assert prep.run_prep is main.run_prep
    assert prep.PrepError is main.PrepError
    assert callable(prep.legacy) and prep.legacy is main.legacy


def test_prep_package_proxy_forwards_facade_patches_but_keeps_submodules_local():
    """Patch targets on the package must reach ``main``; submodules stay local."""
    from dragon_quant.review_account import prep
    from dragon_quant.review_account.prep import main

    assert prep.service is service
    assert prep.collectors is collectors

    sentinel = object()
    with patch.object(prep, "TdxProvider", sentinel):
        assert main.TdxProvider is sentinel
        assert prep.TdxProvider is sentinel
    assert prep.TdxProvider is main.TdxProvider


def test_legacy_lookup_returns_runtime_facade_bindings():
    sentinel = object()
    with patch.object(prep, "_m5b_probe_target", sentinel, create=True):
        assert rebuild.legacy("_m5b_probe_target") is sentinel


def test_legacy_lookup_fails_loudly_for_unknown_names():
    with pytest.raises(AttributeError, match="__missing_patch_target__"):
        rebuild.legacy("__missing_patch_target__")


def test_recompute_is_offline_and_skips_planning_and_collection():
    assets = {"dragons": {"trade_date": DAY, "count": 0, "rows": []}}
    with patch("dragon_quant.infra.assets.db.get_prep_assets",
               return_value=assets), \
            patch("dragon_quant.infra.assets.db.save_prep_snapshot") as save_snap, \
            patch.object(prep, "TdxProvider") as tdx_cls, \
            patch.object(prep, "THSProvider") as ths_cls, \
            patch.object(prep, "plan_prep") as plan_mock, \
            patch.object(prep, "_collect_all") as collect_mock:
        result = prep.run_prep(DAY, recompute=True)

    tdx_cls.assert_not_called()
    ths_cls.assert_not_called()
    plan_mock.assert_not_called()
    collect_mock.assert_not_called()
    save_snap.assert_called_once()
    assert result["version"] == "0.6"
