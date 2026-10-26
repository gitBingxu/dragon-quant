"""Task 9: Phase F 收口 ``scan.storage.persist_scan_result`` 与仓储边界。

这些测试只使用 mock / 临时目录，绝不打开真实 DB、网络或 Cookie：

- 写入顺序固定 ``save_scan_logs → count_scan_logs → write_scan_report →
  save_scan → save_dragons``；
- 日志 / DB 失败隔离进 ``PersistResult.errors``，报告文件失败上抛；
- 5 日去重、raw_output 序列化时点、legacy ``storage.db`` patch 传播保持旧语义；
- ``scan.storage`` 不 import logger / orchestrator / scores / cache，也不直接
  打开 SQLite；``infra.factor`` 只经函数内 lazy 读 ``scan.storage`` / ``vpa.storage``。
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from dragon_quant.scan.storage import main

REPO_ROOT = Path(__file__).resolve().parents[1]


def _payload(**overrides) -> dict:
    """最小 Phase F payload；与 orchestrator 的真实组包字段一一对应。"""
    payload = {
        "scan_id": "v2_20260911_153000_5",
        "timestamp": "20260911_153000",
        "scan_date_fmt": "2026-09-11",
        "elapsed_s": 1.5,
        "top_n": 5,
        "candidates_n": 3,
        "workers": 1,
        "results": [{"code": "600001", "rank": 1, "composite_score": 90.0}],
        "raw_output": {
            "timestamp": "20260911_153000",
            "source": "v2",
            "log_count": 0,
            "report_path": "",
        },
        "log_entries": [{"message": "a"}, {"message": "b"}],
        "report_text": "summary\n\nreport",
        "dragon_rows": [
            {"code": "600001", "name": "龙头A", "rank": 1, "scan_id": "x"},
        ],
        "source": "v2",
        "trade_calendar": {"2026-09-10", "2026-09-11"},
        "version": "9.9.9",
    }
    payload.update(overrides)
    return payload


def _install_success_stubs(monkeypatch, calls, *, report_path):
    monkeypatch.setattr(
        main, "save_scan_logs", lambda *a, **k: calls.append("logs"))
    monkeypatch.setattr(main, "count_scan_logs", lambda *a, **k: 3)
    monkeypatch.setattr(
        main, "write_scan_report",
        lambda **k: calls.append("report") or report_path)
    monkeypatch.setattr(main, "save_scan", lambda *a, **k: calls.append("scan"))
    monkeypatch.setattr(main, "save_dragons", lambda *a, **k: calls.append("dragons"))
    monkeypatch.setattr(main, "get_last_entry_with_rank", lambda *a, **k: None)


def test_persist_order_and_result_fields(monkeypatch, tmp_path):
    calls: list[str] = []
    report_path = tmp_path / "scan_report_v2_20260911_153000.txt"
    _install_success_stubs(monkeypatch, calls, report_path=report_path)

    result = main.persist_scan_result(**_payload())

    assert calls == ["logs", "report", "scan", "dragons"]
    assert result.log_count == 3
    assert result.report_path == report_path
    assert result.saved_dragons == 1
    assert result.skipped_dragons == 0
    assert result.updated_dragons == 0
    assert result.errors == []


def test_log_failure_is_isolated(monkeypatch, tmp_path):
    calls: list[str] = []
    _install_success_stubs(
        monkeypatch, calls, report_path=tmp_path / "r.txt")

    def boom(*args, **kwargs):
        raise RuntimeError("log down")

    monkeypatch.setattr(main, "save_scan_logs", boom)

    result = main.persist_scan_result(**_payload())

    assert result.log_count == 2          # 回退到 len(log_entries)
    assert len(result.errors) == 1
    assert "日志持久化失败" in result.errors[0]
    assert "log down" in result.errors[0]
    assert calls == ["report", "scan", "dragons"]


def test_report_failure_propagates_and_skips_database(monkeypatch, tmp_path):
    calls: list[str] = []
    _install_success_stubs(
        monkeypatch, calls, report_path=tmp_path / "r.txt")

    def boom(**kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(main, "write_scan_report", boom)

    with pytest.raises(OSError, match="disk full"):
        main.persist_scan_result(**_payload())
    assert "scan" not in calls
    assert "dragons" not in calls


def test_scan_failure_is_isolated_and_skips_dragons(monkeypatch, tmp_path):
    """与旧 Phase F 一致：save_scan 失败时不再尝试 save_dragons。"""
    calls: list[str] = []
    _install_success_stubs(
        monkeypatch, calls, report_path=tmp_path / "r.txt")

    def boom(*args, **kwargs):
        raise RuntimeError("db locked")

    monkeypatch.setattr(main, "save_scan", boom)

    result = main.persist_scan_result(**_payload())

    assert calls == ["logs", "report"]
    assert result.saved_dragons == 0
    assert len(result.errors) == 1
    assert "SQLite 持久化失败" in result.errors[0]
    assert "db locked" in result.errors[0]


def test_dragon_failure_is_isolated_after_scan(monkeypatch, tmp_path):
    calls: list[str] = []
    _install_success_stubs(
        monkeypatch, calls, report_path=tmp_path / "r.txt")

    def boom(*args, **kwargs):
        raise RuntimeError("mirror dead")

    monkeypatch.setattr(main, "save_dragons", boom)

    result = main.persist_scan_result(**_payload())

    assert calls == ["logs", "report", "scan"]
    assert result.saved_dragons == 0
    assert result.skipped_dragons == 0
    assert result.updated_dragons == 0
    assert len(result.errors) == 1
    assert "SQLite 持久化失败" in result.errors[0]


def test_five_day_dedup_skips_and_counts_rank_improvement(monkeypatch, tmp_path):
    calls: list[str] = []
    _install_success_stubs(
        monkeypatch, calls, report_path=tmp_path / "r.txt")

    def last_entry(code, **kwargs):
        # A 旧 rank=1，新 rank=2（更差）→ 跳过；B 旧 rank=5，新 rank=3 → 更新。
        return {"A": ("2026-09-09", 1), "B": ("2026-09-09", 5)}.get(code)

    monkeypatch.setattr(main, "get_last_entry_with_rank", last_entry)
    saved: dict = {}
    monkeypatch.setattr(
        main, "save_dragons",
        lambda date, rows, **k: saved.update(date=date, rows=rows, kwargs=k))

    payload = _payload(dragon_rows=[
        {"code": "A", "rank": 2},
        {"code": "B", "rank": 3},
        {"code": "C", "rank": 1},
    ])
    result = main.persist_scan_result(**payload)

    assert result.skipped_dragons == 1
    assert result.updated_dragons == 1
    assert result.saved_dragons == 2
    assert [row["code"] for row in saved["rows"]] == ["B", "C"]
    assert saved["date"] == "2026-09-11"
    assert saved["kwargs"] == {"version": "9.9.9", "source": "v2"}


def test_same_day_entry_is_not_deduped(monkeypatch, tmp_path):
    calls: list[str] = []
    _install_success_stubs(
        monkeypatch, calls, report_path=tmp_path / "r.txt")
    monkeypatch.setattr(
        main, "get_last_entry_with_rank",
        lambda code, **k: ("2026-09-11", 1))

    result = main.persist_scan_result(**_payload())

    assert result.skipped_dragons == 0
    assert result.saved_dragons == 1


def test_missing_calendar_skips_the_gate(monkeypatch, tmp_path):
    calls: list[str] = []
    _install_success_stubs(
        monkeypatch, calls, report_path=tmp_path / "r.txt")

    def forbidden(*args, **kwargs):
        raise AssertionError("无日历时不得读取历史入选或计算交易日间隔")

    monkeypatch.setattr(main, "get_last_entry_with_rank", forbidden)
    result = main.persist_scan_result(**_payload(trade_calendar=None))

    assert result.saved_dragons == 1
    assert result.skipped_dragons == 0


def test_raw_output_is_serialized_after_report_and_log_count(monkeypatch, tmp_path):
    calls: list[str] = []
    report_path = tmp_path / "r.txt"
    captured: dict = {}
    _install_success_stubs(monkeypatch, calls, report_path=report_path)
    monkeypatch.setattr(
        main, "save_scan", lambda *a, **k: captured.update(k))
    raw = _payload()["raw_output"]
    ordered_keys = list(raw)

    main.persist_scan_result(**_payload(raw_output=raw))

    stored = json.loads(captured["raw_output"])
    assert stored["report_path"] == str(report_path)
    assert stored["log_count"] == 3
    assert list(stored) == ordered_keys, "raw_output 键顺序必须保持旧实现"
    assert captured["stocks"] == _payload()["results"]
    assert captured["scan_id"] == "v2_20260911_153000_5"
    assert captured["scan_date"] == "2026-09-11"
    assert captured["elapsed_s"] == 1.5
    assert captured["source"] == "v2"


def test_write_scan_report_uses_canonical_results_dir(monkeypatch, tmp_path):
    from dragon_quant.infra.storage import paths

    results_dir = tmp_path / "results"
    monkeypatch.setattr(paths, "RESULTS_DIR", results_dir)

    path = main.write_scan_report(
        source="v2", timestamp="20260911_153000", text="hello\n\nworld")

    assert path == results_dir / "scan_report_v2_20260911_153000.txt"
    assert path.read_text(encoding="utf-8") == "hello\n\nworld"


def test_canonical_log_patch_reaches_persist(monkeypatch, tmp_path):
    """``scan.storage.logs`` 的 patch 必须穿透到 main 的子模块转发层。"""
    calls: list[str] = []
    monkeypatch.setattr(main, "count_scan_logs", lambda *a, **k: 3)
    monkeypatch.setattr(
        main, "write_scan_report",
        lambda **k: calls.append("report") or tmp_path / "r.txt")
    monkeypatch.setattr(main, "save_scan", lambda *a, **k: calls.append("scan"))
    monkeypatch.setattr(main, "save_dragons", lambda *a, **k: calls.append("dragons"))
    monkeypatch.setattr(main, "get_last_entry_with_rank", lambda *a, **k: None)

    with patch("dragon_quant.scan.storage.logs.save_scan_logs",
               side_effect=RuntimeError("canonical seam")):
        result = main.persist_scan_result(**_payload())

    assert len(result.errors) == 1
    assert "canonical seam" in result.errors[0]
    assert result.log_count == 2
    assert calls == ["report", "scan", "dragons"]


# ─── 失败路径可观察顺序（Phase F verbose parity）───────────────────────────


def test_log_error_is_emitted_before_report_failure(monkeypatch, tmp_path):
    """log failure + report failure：日志错误必须已可见后才上抛报告错误。"""
    events: list = []

    def log_boom(*args, **kwargs):
        raise RuntimeError("log down")

    def report_boom(**kwargs):
        events.append("report")
        raise OSError("disk full")

    monkeypatch.setattr(main, "save_scan_logs", log_boom)
    monkeypatch.setattr(main, "write_scan_report", report_boom)

    with pytest.raises(OSError, match="disk full"):
        main.persist_scan_result(
            **_payload(), on_error=lambda message: events.append(("error", message)))

    assert [event[0] if isinstance(event, tuple) else event for event in events] == [
        "error", "report"]
    assert events[0] == ("error", "日志持久化失败: log down")


def test_dedupe_event_precedes_database_error(monkeypatch, tmp_path):
    """成功日志 → 去重信息 → save_dragons 失败：顺序与旧实现一致。"""
    events: list = []
    _install_success_stubs(monkeypatch, [], report_path=tmp_path / "r.txt")
    monkeypatch.setattr(
        main, "get_last_entry_with_rank",
        lambda code, **k: {"A": ("2026-09-09", 1)}.get(code))

    def dragon_boom(*args, **kwargs):
        raise RuntimeError("mirror dead")

    monkeypatch.setattr(main, "save_dragons", dragon_boom)
    payload = _payload(dragon_rows=[
        {"code": "A", "rank": 2},
        {"code": "B", "rank": 1},
    ])

    result = main.persist_scan_result(
        **payload,
        on_error=lambda message: events.append(("error", message)),
        on_dedupe=lambda skipped, updated: events.append(("dedupe", skipped, updated)),
    )

    assert result.skipped_dragons == 1
    assert result.updated_dragons == 0
    assert events == [
        ("dedupe", 1, 0),
        ("error", "SQLite 持久化失败: mirror dead"),
    ]


def test_log_error_then_dedupe_then_database_error(monkeypatch, tmp_path):
    """log failure + DB failure：日志错误 → 去重信息 → DB 错误。"""
    events: list = []
    _install_success_stubs(monkeypatch, [], report_path=tmp_path / "r.txt")
    monkeypatch.setattr(
        main, "save_scan_logs",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("log down")))
    monkeypatch.setattr(
        main, "get_last_entry_with_rank",
        lambda code, **k: {"A": ("2026-09-09", 1)}.get(code))
    monkeypatch.setattr(
        main, "save_dragons",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("mirror dead")))
    payload = _payload(dragon_rows=[
        {"code": "A", "rank": 2},
        {"code": "B", "rank": 1},
    ])

    main.persist_scan_result(
        **payload,
        on_error=lambda message: events.append(("error", message)),
        on_dedupe=lambda skipped, updated: events.append(("dedupe", skipped, updated)),
    )

    assert events == [
        ("error", "日志持久化失败: log down"),
        ("dedupe", 1, 0),
        ("error", "SQLite 持久化失败: mirror dead"),
    ]


# ─── 仓储边界 ──────────────────────────────────────────────────────────────


def _module_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_scan_storage_does_not_import_logger_orchestrator_scores_cache():
    forbidden = (
        "dragon_quant.scan.logger",
        "dragon_quant.logging",
        "dragon_quant.scan.orchestrator",
        "dragon_quant.orchestrator",
        "dragon_quant.scan.scores",
        "dragon_quant.scan.cache",
        "dragon_quant.cache",
    )
    for path in sorted((REPO_ROOT / "dragon_quant" / "scan" / "storage").rglob("*.py")):
        for module in _module_imports(path):
            for prefix in forbidden:
                assert not module.startswith(prefix), f"{path.name} 违规 import {module}"


def test_scan_storage_never_opens_sqlite_directly():
    for path in sorted((REPO_ROOT / "dragon_quant" / "scan" / "storage").rglob("*.py")):
        imports = _module_imports(path)
        assert "sqlite3" not in imports, f"{path.name} 不得直接 import sqlite3"


def test_orchestrator_uses_canonical_scan_logger_and_cache():
    """Task 11：编排器只经新树装配 scan logger/cache，不回落旧路径。"""
    imports = _module_imports(
        REPO_ROOT / "dragon_quant" / "scan" / "orchestrator" / "main.py")
    assert "dragon_quant.scan.cache.main" in imports
    assert "dragon_quant.scan.logger.events" in imports
    assert "dragon_quant.scan.logger.report" in imports
    legacy = {
        "dragon_quant.logging.logger",
        "dragon_quant.logging.reporter",
        "dragon_quant.cache.data_cache",
        "dragon_quant.orchestrator",
    }
    assert not (imports & legacy), f"编排器仍在 import 旧路径: {sorted(imports & legacy)}"


def test_factor_reads_only_lazy_canonical_storage():
    from dragon_quant.infra.factor import engine, evaluation, service
    from dragon_quant.scan.storage import main as scan_store
    from dragon_quant.vpa import storage as vpa_store

    assert engine._scan_store() is scan_store
    assert service._scan_store() is scan_store
    assert evaluation._scan_store() is scan_store
    assert engine._vpa_store() is vpa_store
    for path in (REPO_ROOT / "dragon_quant" / "infra" / "factor").rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert "from dragon_quant.storage import db" not in source, path.name


def test_vpa_storage_is_the_only_owner_of_vpa_analysis():
    import dragon_quant.scan.storage.dragons as dragon_store
    from dragon_quant.vpa import storage as vpa_store

    assert hasattr(vpa_store, "upsert_vpa")
    assert hasattr(vpa_store, "list_vpa_analysis")
    assert not hasattr(dragon_store, "upsert_vpa")
    assert not hasattr(dragon_store, "list_vpa_analysis")
