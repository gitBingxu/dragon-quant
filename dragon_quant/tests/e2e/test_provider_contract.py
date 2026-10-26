"""M1A provider / backend contract and fail-closed scenario tests.

Two layers:

* **Static (parent, no application import):** the fake provider classes must
  define every public method that ``StockProvider`` or a concrete provider
  defines, with an identical argument/default signature.  This is an AST
  comparison against the real sources, so a production method added later
  cannot silently fall through to a real provider.
* **Runtime (child, injection active):** construct the fake providers through
  ``dragon_quant.infra.provider.create_providers``, prove fixture-driven responses
  return real model instances, prove an unconfigured scenario raises
  ``FakeScenarioMissing`` (never a real transport fallback), and prove the
  assets backend adapter round-trips and logs every operation.

The tests never execute a business CLI path whose fake is incomplete.  G4/G5
(default scan / hidden alias) belong to M1B.
"""

from __future__ import annotations

import ast
import copy
import json
from pathlib import Path

import pytest

from dragon_quant.tests.e2e.harness.sandbox import EnvSandbox

REPO_ROOT = Path(__file__).resolve().parents[3]
PROVIDERS = REPO_ROOT / "dragon_quant" / "infra" / "provider"
E2E_ROOT = Path(__file__).resolve().parent
FAKE_SOURCE = E2E_ROOT / "injection" / "fake_providers.py"
SCENARIO = E2E_ROOT / "fixtures" / "scenario_providers.json"

NOW = "2026-09-11T15:30:00+08:00"

#: Concretely routed providers and the fake class that stands in for each.
CONCRETE = (
    ("tdx", "TdxProvider"),
    ("ths", "THSProvider"),
    ("xueqiu", "XueqiuProvider"),
    ("tencent", "TencentProvider"),
)

#: Methods the real provider does *not* implement (inherits the
#: ``NotImplementedError`` default).  The fake must preserve that behavior.
NOT_IMPLEMENTED = {
    "tdx": ("get_5min_kline_for", "get_limit_up_pool", "get_block_top"),
    "ths": (
        "get_kline",
        "get_5min_kline",
        "get_quote",
        "get_minute_kline",
        "get_5min_kline_for",
        "batch_get_quotes",
        "get_market_breadth_rows",
        "get_fg_boards",
        "get_announcements",
        "get_macro_quotes",
    ),
    "xueqiu": (
        "get_sector_ranking",
        "get_sector_components",
        "get_sector_5min_kline",
        "get_sector_1min_kline",
        "get_sector_5min_kline_history",
        "batch_get_quotes",
        "get_market_breadth_rows",
        "get_fg_boards",
        "get_limit_up_pool",
        "get_block_top",
        "get_announcements",
    ),
    "tencent": (
        "get_sector_ranking",
        "get_sector_components",
        "get_sector_5min_kline",
        "get_sector_1min_kline",
        "get_sector_5min_kline_history",
        "get_minute_kline",
        "get_5min_kline_for",
        "get_market_breadth_rows",
        "get_fg_boards",
        "get_limit_up_pool",
        "get_block_top",
        "get_announcements",
    ),
}

#: Dummy call arguments per provider method, used to exercise every configured
#: scenario entry.
CALL_ARGS = {
    "get_sector_ranking": "()",
    "get_sector_components": "('881167',)",
    "get_sector_5min_kline": "('881167',)",
    "get_sector_1min_kline": "('881167',)",
    "get_sector_5min_kline_history": "('881167',)",
    "get_kline": "('600172',)",
    "get_5min_kline": "('600172',)",
    "get_quote": "('600172',)",
    "get_minute_kline": "('600172',)",
    "get_5min_kline_for": "('600172', 1757575800000)",
    "batch_get_quotes": "(['600172'],)",
    "get_market_breadth_rows": "()",
    "get_fg_boards": "(['测试风格'],)",
    "get_limit_up_pool": "('20260911',)",
    "get_block_top": "('20260911',)",
    "get_announcements": "('600172',)",
    "get_macro_quotes": "([], '2026-09-11')",
    "get_finance_info": "('600172',)",
    "get_financial_files": "()",
    "get_financial_records": "('gpcw20260630.zip',)",
    "get_financial_file": "('gpcw20260630.zip',)",
    "get_xdxr_info": "('600172',)",
    "get_fg_board_members": "('测试风格',)",
    "get_auction": "('600172',)",
}


# ─── static surface comparison ─────────────────────────────────────────────


def _definition_signature(node: ast.FunctionDef) -> str:
    """Argument names + defaults, annotations stripped."""

    args = copy.deepcopy(node.args)
    targets = list(args.posonlyargs) + list(args.args) + list(args.kwonlyargs)
    targets += [args.vararg, args.kwarg]
    for arg in targets:
        if arg is not None:
            arg.annotation = None
    return ast.unparse(args)


def _class_methods(path: Path, class_name: str) -> dict[str, str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return {
                item.name: _definition_signature(item)
                for item in node.body
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                and not item.name.startswith("_")
            }
    raise AssertionError(f"{path.name} 缺少 class {class_name}")


def _module_functions(path: Path) -> dict[str, str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return {
        node.name: _definition_signature(node)
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and not node.name.startswith("_")
    }


def test_fake_base_covers_every_stockprovider_method_with_same_signature():
    base = _class_methods(PROVIDERS / "base.py", "StockProvider")
    fake = _class_methods(FAKE_SOURCE, "_FakeProvider")
    assert base, "StockProvider surface must not be empty"
    for method, signature in base.items():
        assert method in fake, f"_FakeProvider 缺少 StockProvider.{method}"
        assert fake[method] == signature, (
            f"_FakeProvider.{method} 签名漂移: {fake[method]!r} != {signature!r}"
        )


@pytest.mark.parametrize("module,class_name", CONCRETE)
def test_fake_concrete_override_surface_matches_production(module, class_name):
    real = _class_methods(PROVIDERS / f"{module}.py", class_name)
    fake = _class_methods(FAKE_SOURCE, class_name)
    fake_base = _class_methods(FAKE_SOURCE, "_FakeProvider")
    for method, signature in real.items():
        target = fake.get(method, fake_base.get(method))
        assert target is not None, f"fake {class_name} 缺少生产方法 {method}"
        assert target == signature, (
            f"fake {class_name}.{method} 签名漂移: {target!r} != {signature!r}"
        )


def test_fake_module_helpers_match_production_module_helpers():
    expected = {}
    expected.update(_module_functions(PROVIDERS / "tdx.py"))
    expected.update(_module_functions(PROVIDERS / "ths.py"))
    fake = _module_functions(FAKE_SOURCE)
    for name, signature in expected.items():
        assert name in fake, f"fake_providers 缺少模块级 helper {name}"
        assert fake[name] == signature, (
            f"fake_providers.{name} 签名漂移: {fake[name]!r} != {signature!r}"
        )


def test_pinned_not_implemented_table_matches_production_ast():
    """The manual Not-Implemented table must be derived, not guessed."""

    def raising(path: Path, class_name: str) -> set[str]:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and node.name == class_name:
                found = set()
                for item in node.body:
                    if not isinstance(item, ast.FunctionDef) or item.name.startswith("_"):
                        continue
                    body = list(item.body)
                    if (
                        body
                        and isinstance(body[0], ast.Expr)
                        and isinstance(body[0].value, ast.Constant)
                        and isinstance(body[0].value.value, str)
                    ):
                        body = body[1:]
                    if len(body) != 1 or not isinstance(body[0], ast.Raise):
                        continue
                    exc = body[0].exc
                    name = (
                        exc.id
                        if isinstance(exc, ast.Name)
                        else exc.func.id
                        if isinstance(exc, ast.Call) and isinstance(exc.func, ast.Name)
                        else None
                    )
                    if name == "NotImplementedError":
                        found.add(item.name)
                return found
        raise AssertionError(f"{path.name} 缺少 class {class_name}")

    base_raising = raising(PROVIDERS / "base.py", "StockProvider")
    for module, class_name in CONCRETE:
        real = _class_methods(PROVIDERS / f"{module}.py", class_name)
        inherited = base_raising - set(real)
        expected = raising(PROVIDERS / f"{module}.py", class_name) | inherited
        assert expected == set(NOT_IMPLEMENTED[module]), (
            f"{module} 的 NotImplementedError 面与生产不一致: "
            f"{sorted(expected)} != {sorted(NOT_IMPLEMENTED[module])}"
        )


def test_scenario_fixture_is_pinned_to_the_frozen_clock():
    data = json.loads(SCENARIO.read_text(encoding="utf-8"))
    assert data["now"] == NOW
    assert data["trade_date"] == "2026-09-11"
    assert set(data["providers"]) == {name for name, _ in CONCRETE}


# ─── runtime: signatures, name, NotImplemented parity ──────────────────────


def test_runtime_surface_matches_pinned_contract(sandbox):
    not_implemented = json.dumps(NOT_IMPLEMENTED, ensure_ascii=False)
    call_args = json.dumps(
        {name: args for name, args in CALL_ARGS.items()}, ensure_ascii=False
    )
    code = f"""
import json
import fake_providers as fp

NOT_IMPLEMENTED = {not_implemented}
CALL_ARGS = {call_args}
providers = {{name: cls() for name, cls in fp.PROVIDER_CLASSES.items()}}
report = {{}}
for name, instance in providers.items():
    report[name] = {{"name": instance.name}}
    for method in NOT_IMPLEMENTED[name]:
        args = eval(CALL_ARGS[method], {{"__builtins__": {{}}}})
        try:
            getattr(instance, method)(*args)
        except NotImplementedError:
            report[name][method] = "NotImplementedError"
        except Exception as exc:  # noqa: BLE001 - contract mismatch is the point
            report[name][method] = type(exc).__name__
        else:
            report[name][method] = "ALLOWED"
print(json.dumps(report, ensure_ascii=False, sort_keys=True))
"""
    result = sandbox_runner().run_python(code, sandbox=sandbox)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    for name, _ in CONCRETE:
        assert report[name]["name"] == name
        for method in NOT_IMPLEMENTED[name]:
            assert report[name][method] == "NotImplementedError", (
                f"{name}.{method} must mirror production NotImplementedError"
            )


def test_unconfigured_scenario_fails_closed_without_real_transport(sandbox):
    code = (
        "from dragon_quant.infra.provider import create_providers\n"
        "provider = create_providers()['tdx']\n"
        "provider.get_kline('600172')\n"
    )
    result = sandbox_runner().run_python(code, sandbox=sandbox)
    assert result.returncode != 0
    assert "FakeScenarioMissing" in result.stderr
    assert "fake_providers" in result.stderr

    calls = _call_log(sandbox)
    assert any(
        record["provider"] == "tdx" and record["method"] == "get_kline"
        for record in calls
    )


def test_configured_scenario_returns_real_model_instances(sandbox):
    code = f"""
import json
import os
from dragon_quant.infra.provider import create_providers

CALL_ARGS = {CALL_ARGS!r}
scenario_names = json.load(open(os.environ["DQ_E2E_SCENARIO"], encoding="utf-8"))["providers"]
providers = create_providers()
report = {{}}
for provider_name, methods in scenario_names.items():
    provider = providers[provider_name]
    for method in methods:
        args = eval(CALL_ARGS[method], {{"__builtins__": {{}}}})  # fixture-owned literals only
        try:
            value = getattr(provider, method)(*args)
            item = None
            if isinstance(value, list) and value:
                item = type(value[0]).__name__
            elif isinstance(value, dict) and value:
                first_list = next((entry for entry in value.values() if isinstance(entry, list) and entry), None)
                if first_list is not None:
                    item = type(first_list[0]).__name__
                else:
                    item = type(next(iter(value.values()))).__name__
            report[provider_name + "." + method] = {{
                "ok": True,
                "type": type(value).__name__,
                "item_type": item,
                "len": len(value) if isinstance(value, (list, dict, bytes)) else None,
            }}
        except Exception as exc:  # noqa: BLE001 - report every fixture gap
            report[provider_name + "." + method] = {{
                "ok": False, "error": type(exc).__name__ + ": " + str(exc)
            }}
print(json.dumps(report, ensure_ascii=False, sort_keys=True))
"""
    result = sandbox_runner().run_python(
        code, sandbox=sandbox, env_override={"DQ_E2E_SCENARIO": str(SCENARIO)}
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    failed = {key: value for key, value in report.items() if not value["ok"]}
    assert not failed, failed
    assert len(report) >= 20, report

    expected = {
        "tdx.get_kline": ("list", "KBar"),
        "tdx.get_quote": ("Quote", None),
        "tdx.get_sector_ranking": ("list", "SectorPerformance"),
        "tdx.get_sector_components": ("list", "StockInfo"),
        "tdx.get_market_breadth_rows": ("list", "dict"),
        "tdx.get_macro_quotes": ("dict", "dict"),
        "tdx.get_auction": ("list", "dict"),
        "tdx.get_financial_file": ("bytes", None),
        "ths.get_limit_up_pool": ("dict", "dict"),
        "xueqiu.get_kline": ("list", "KBar"),
        "tencent.batch_get_quotes": ("list", "Quote"),
    }
    for key, (kind, item_type) in expected.items():
        assert report[key]["type"] == kind, (key, report[key])
        if item_type is not None:
            assert report[key]["item_type"] == item_type, (key, report[key])

    calls = _call_log(sandbox)
    called = {(record["provider"], record["method"]) for record in calls}
    assert ("tdx", "get_kline") in called
    assert ("tencent", "batch_get_quotes") in called


# ─── assets backend adapter contract ────────────────────────────────────────


def test_fake_assets_backend_round_trip_and_call_log(sandbox):
    code = """
import json
from dragon_quant.infra.assets.sync.backends.s3 import S3Backend

backend = S3Backend(object())
backend.put("probe/a.txt", b"payload", metadata={"k": "v"}, content_md5=None)
body = backend.get("probe/a.txt")
head = backend.head("probe/a.txt")
keys = [item.key for item in backend.list("probe/")]
backend.delete("probe/a.txt")
after = backend.head("probe/a.txt")
print(json.dumps({
    "module": S3Backend.__module__,
    "body": body.decode("utf-8"),
    "head_size": None if head is None else head.size,
    "head_metadata": None if head is None else head.metadata,
    "keys": keys,
    "after_delete": after is None,
}))
"""
    result = sandbox_runner().run_python(code, sandbox=sandbox)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert "fake_assets_backend" in payload["module"]
    assert payload["body"] == "payload"
    assert payload["head_size"] == 7
    assert payload["head_metadata"] == {"k": "v"}
    assert payload["keys"] == ["probe/a.txt"]
    assert payload["after_delete"] is True

    calls = _call_log(sandbox)
    operations = [
        record["method"] for record in calls if record["provider"] == "assets_backend"
    ]
    assert {"put", "get", "head", "list", "delete"} <= set(operations)


def test_fake_assets_backend_error_mode_never_touches_network(sandbox):
    code = (
        "from dragon_quant.infra.assets.sync.backends.s3 import S3Backend\n"
        "try:\n"
        "    S3Backend(object()).put('probe/k.txt', b'x')\n"
        "except Exception as exc:\n"
        "    print(type(exc).__name__)\n"
        "else:\n"
        "    print('ALLOWED')\n"
    )
    result = sandbox_runner().run_python(
        code, sandbox=sandbox, env_override={"DQ_E2E_BACKEND_MODE": "error"}
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "StorageError"


# ─── helpers ────────────────────────────────────────────────────────────────


def sandbox_runner():
    from dragon_quant.tests.e2e.harness import runner as module

    return module


def _call_log(sandbox: EnvSandbox) -> list[dict]:
    if not sandbox.call_log.exists():
        return []
    return [
        json.loads(line)
        for line in sandbox.call_log.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


@pytest.fixture
def sandbox():
    box = EnvSandbox.for_case("provider-contract", now=NOW)
    try:
        yield box
    finally:
        box.cleanup()
