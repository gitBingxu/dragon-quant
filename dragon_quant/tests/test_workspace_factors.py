"""workspace 用户因子：命名空间、版本、依赖校验与 PIT/as-of 读取。"""

from __future__ import annotations

import json

import pytest

from dragon_quant.strategy_sdk import (
    FACTOR_STATUS_MISSING, FACTOR_STATUS_OK, FACTOR_STATUS_UNAVAILABLE,
)
from dragon_quant.workspace import config, factors, main

FACTOR_MODULE = '''
from dragon_quant.infra.factor.types import FactorFrame, FactorSpec
from dragon_quant.strategy_sdk import FACTOR_STATUS_OK  # noqa: F401

def compute(ctx):
    return FactorFrame(values={c: 1.0 for c in ctx.codes})

SPECS = [
    FactorSpec(name="sentiment_x", label="自定义", frequency="1d", source="computed",
               compute=compute, impl_key="custom_v1"),
]
'''

CYCLIC_MODULE = '''
from dragon_quant.infra.factor.types import FactorFrame, FactorSpec

def compute(ctx):
    return FactorFrame()

SPECS = [
    FactorSpec(name="a", label="a", frequency="1d", source="computed", compute=compute,
               impl_key="a", depends_on=("b",)),
    FactorSpec(name="b", label="b", frequency="1d", source="computed", compute=compute,
               impl_key="b", depends_on=("a",)),
]
'''


def _workspace_with_factor(tmp_path, source=FACTOR_MODULE):
    root = tmp_path / "ws"
    main.init_workspace(str(root))
    (root / "factors" / "custom.py").write_text(source, encoding="utf-8")
    cfg = config.load_workspace(root)
    cfg.factors = ["factors/custom.py"]
    config.write_workspace(cfg)
    return config.load_workspace(root)


def test_user_factor_is_namespaced(tmp_path):
    workspace = _workspace_with_factor(tmp_path)
    specs = factors.load_workspace_factor_specs(workspace)
    assert len(specs) == 1
    name = specs[0].name
    assert name.startswith("ws_") and name.endswith(".sentiment_x")
    combined = factors.combined_specs(workspace)
    assert name in {spec.name for spec in combined}


def test_helpers_change_bumps_user_factor_version(tmp_path):
    workspace = _workspace_with_factor(tmp_path)
    v1 = factors.load_workspace_factor_specs(workspace)[0].version_override
    (workspace.root / "helpers" / "shared.py").write_text("VALUE = 1\n", encoding="utf-8")
    v2 = factors.load_workspace_factor_specs(workspace)[0].version_override
    assert v1 != v2


def test_cycle_is_rejected(tmp_path):
    workspace = _workspace_with_factor(tmp_path, CYCLIC_MODULE)
    with pytest.raises(config.WorkspaceError) as exc:
        factors.combined_specs(workspace)
    assert "成环" in str(exc.value)


def test_dangling_dependency_is_rejected(tmp_path):
    source = CYCLIC_MODULE.replace('depends_on=("a",)', 'depends_on=("missing",)')
    workspace = _workspace_with_factor(tmp_path, source)
    with pytest.raises(config.WorkspaceError):
        factors.combined_specs(workspace)


def test_duplicate_local_factor_name_is_rejected(tmp_path):
    workspace = _workspace_with_factor(tmp_path, FACTOR_MODULE + "\nSPECS = SPECS + SPECS\n")
    with pytest.raises(config.WorkspaceError):
        factors.load_workspace_factor_specs(workspace)


def test_factor_reader_pit_and_missing(monkeypatch):
    payload = {
        "values": {"600001": {"f": 3.0}},
        "available_at": {"600001": {"f": "2026-11-11 15:00:00"}},
        "resolved_versions": {"f": "v1"},
    }
    monkeypatch.setattr("dragon_quant.infra.factor.storage.get_day_values",
                        lambda *a, **k: payload)
    reader = factors.FactorReader(["f"])
    # 决策时点在 15:00 之前 → 未来数据不可读
    early = reader.values_for(trade_date="2026-11-11", code="600001",
                              as_of_ms=1_760_000_000_000, phase="open")
    assert early["f"].status == FACTOR_STATUS_UNAVAILABLE
    assert early["f"].value is None
    # 时点之后 → 可读
    late = reader.values_for(trade_date="2026-11-11", code="600001",
                             as_of_ms=2_000_000_000_000, phase="close")
    assert late["f"].status == FACTOR_STATUS_OK
    assert late["f"].value == 3.0
    # 缺失不补零
    missing = reader.values_for(trade_date="2026-11-11", code="600999",
                                as_of_ms=2_000_000_000_000)
    assert missing["f"].status == FACTOR_STATUS_MISSING
    assert missing["f"].value is None


def test_service_list_and_query_accept_workspace_specs(tmp_path):
    from dragon_quant.infra.factor import service

    workspace = _workspace_with_factor(tmp_path)
    specs = factors.combined_specs(workspace)
    names = {entry["name"] for entry in service.list_factors(specs=specs)}
    assert any(name.startswith("ws_") and name.endswith(".sentiment_x") for name in names)
    # query 用同一注册表校验名字；未知名字明确失败。
    with pytest.raises(ValueError):
        service.query(date="20261111", name="ws_nope.sentiment_x", specs=specs)


def test_factor_list_uses_metadata_without_importing_user_code(tmp_path, capsys):
    import io
    from types import SimpleNamespace
    from contextlib import redirect_stdout
    from dragon_quant.commands import factor as factor_cmd

    root = tmp_path / "ws"
    main.init_workspace(str(root))
    (root / "factors" / "boom.py").write_text(
        "raise RuntimeError('must not import')\n"
        "SPECS = [FactorSpec(name='boom_factor', label='b', frequency='1d',"
        " source='computed', compute=None, impl_key='b')]\n", encoding="utf-8")
    cfg = config.load_workspace(root)
    cfg.factors = ["factors/boom.py"]
    config.write_workspace(cfg)
    buf = io.StringIO()
    args = SimpleNamespace(factor_action="list", json=True, workspace=str(root))
    with redirect_stdout(buf):
        factor_cmd.handle(args, services=None)
    payload = json.loads(buf.getvalue())
    names = {entry["name"] for entry in payload["factors"]}
    assert any(name.startswith("ws_") for name in names)


def test_metadata_only_specs_marks_workspace_owner(tmp_path):
    workspace = _workspace_with_factor(tmp_path)
    entries = factors.metadata_only_specs(workspace)
    assert len(entries) == 1
    entry = entries[0]
    assert entry["name"].startswith("ws_")
    assert entry["owner"] == "workspace"
    assert entry["version"].startswith("v")
    # 展示字段 owner 不得改变静态版本公式：与执行路径逐位一致。
    execution = factors.load_workspace_factor_specs(workspace)[0]
    assert entry["version"] == execution.version_override


def test_main_facade_lists_static_factor_metadata(tmp_path):
    root = tmp_path / "ws"
    assert main.list_factor_metadata(str(root)) == []  # 未初始化 workspace
    _workspace_with_factor(tmp_path)
    entries = main.list_factor_metadata(str(root))
    assert [entry["name"].split(".", 1)[1] for entry in entries] == ["sentiment_x"]
    assert entries[0]["owner"] == "workspace"


def test_main_facade_fails_explicitly_on_corrupt_workspace(tmp_path):
    root = tmp_path / "ws"
    main.init_workspace(str(root))
    (root / "workspace.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(config.WorkspaceError):
        main.list_factor_metadata(str(root))


def test_service_query_accepts_known_names_without_importing_code(tmp_path, monkeypatch):
    from dragon_quant.infra.factor import service, storage as fstore

    root = tmp_path / "ws"
    main.init_workspace(str(root))
    (root / "factors" / "custom.py").write_text(
        "raise RuntimeError('must not import')\n"
        "SPECS = [FactorSpec(name='sentiment_x', label='自定义', frequency='1d',"
        " source='computed', compute=None, impl_key='custom', owner='workspace')]\n",
        encoding="utf-8")
    cfg = config.load_workspace(root)
    cfg.factors = ["factors/custom.py"]
    config.write_workspace(cfg)
    names = [entry["name"] for entry in main.list_factor_metadata(str(root))]
    assert names
    monkeypatch.setattr(fstore, "get_day_values", lambda *a, **k: {
        "trade_date": "2026-11-11", "rows": 0, "resolved_versions": {},
        "missing_versions": [], "values": {}, "available_at": {},
        "source_scan_id": {},
    })
    # 静态名字通过校验，进入存储层（无值 → 空集，而非 ValueError）
    payload = service.query(date="20261111", name=names[0], known_names=names)
    assert payload["ok"] is True and payload["rows"] == 0
    with pytest.raises(ValueError):
        service.query(date="20261111", name="ws_nope.sentiment_x",
                      known_names=names)
