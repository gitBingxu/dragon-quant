"""`dragon_quant.scan` 可调用包：导入顺序与调用期解析（Task 2）。

批准语义（2026-10-25）：`dragon_quant.scan` 从包根惰性函数改为**可调用包**，
接受函数身份变化。需要守住两条不变量：

1. 任意导入顺序下 `dragon_quant.scan` 都是同一个包对象且 `callable(...)`；
2. `from dragon_quant import scan; scan(...)` 在**调用时**解析到
   `scan.orchestrator.main.scan`，导入期不加载 orchestrator（B1）。

跨进程用例使用一次性 `DQ_DATA_DIR` / `HOME`，并在子进程结束后断言数据目录
没有被创建；进程内用例只做属性解析与打桩，绝不执行真实扫描。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _run_python(code: str, tmp_path: Path) -> subprocess.CompletedProcess:
    """在干净环境里跑一段 Python，断言导入期零数据目录副作用（B1）。"""

    data_root = tmp_path / "data"
    home = tmp_path / "home"
    (tmp_path / "tmp").mkdir(parents=True, exist_ok=True)
    home.mkdir(parents=True, exist_ok=True)
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("DQ_")}
    env.update({
        "DQ_DATA_DIR": str(data_root),
        "HOME": str(home),
        "PYTHONPATH": str(REPO_ROOT),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONIOENCODING": "utf-8",
    })
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert not data_root.exists(), (
        f"导入/解析 scan 入口时创建了数据目录: {data_root}\n{result.stderr}")
    return result


def test_scan_is_callable_under_both_import_orders(tmp_path: Path):
    # 子模块先导入：importlib 会把 dragon_quant.scan 挂到包根上。
    code = """
import importlib
import dragon_quant
pkg = importlib.import_module("dragon_quant.scan.scores")
importlib.import_module("dragon_quant.scan")
assert dragon_quant.scan is importlib.import_module("dragon_quant.scan")
assert callable(dragon_quant.scan), dragon_quant.scan
print("ok")
"""
    result = _run_python(code, tmp_path)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


def test_scan_is_callable_when_root_imported_first(tmp_path: Path):
    # 包根先取属性、再导入子模块：两者必须解析到同一个包对象。
    code = """
import importlib
import dragon_quant
from dragon_quant import scan as entry
assert callable(entry), entry
assert entry is importlib.import_module("dragon_quant.scan")
import dragon_quant.scan.scores
assert dragon_quant.scan is entry
print("ok")
"""
    result = _run_python(code, tmp_path)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


def test_importing_the_package_does_not_load_the_orchestrator(tmp_path: Path):
    code = """
import sys
import dragon_quant.scan
assert "dragon_quant.scan.orchestrator.main" not in sys.modules
assert callable(dragon_quant.scan)
print("ok")
"""
    result = _run_python(code, tmp_path)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


def test_scan_package_exposes_the_real_function():
    import dragon_quant.scan as pkg
    from dragon_quant.scan.orchestrator.main import scan as real

    assert pkg.scan is real


def test_scan_package_call_uses_the_real_orchestrator():
    """Task 11：canonical 实现必须定义在 ``scan.orchestrator.main``。"""
    import inspect

    import dragon_quant.scan as pkg
    from dragon_quant.scan.orchestrator.main import run_scan, scan as real

    assert pkg.scan is real
    assert real.__module__ == "dragon_quant.scan.orchestrator.main"
    assert run_scan.__module__ == "dragon_quant.scan.orchestrator.main"
    assert pkg.__call__.__func__  # module subclass owns __call__
    assert list(inspect.signature(pkg.scan).parameters)[:2] == [
        "top_n", "candidates_n"]
    assert list(inspect.signature(real).parameters) == [
        "top_n", "candidates_n", "workers", "verbose", "force", "scorers",
        "refresh_provider_cache", "services"]


def test_scan_attribute_is_not_written_back_to_package_globals():
    import dragon_quant.scan as pkg

    assert "scan" not in vars(pkg), "包属性 scan 必须惰性解析，不得预先绑定"
    first = pkg.scan
    assert pkg.scan is first
    assert "scan" not in vars(pkg), "scan.__getattr__ 不得写回 globals()（避免缓存漂移）"


def test_call_resolves_orchestrator_main_at_call_time(monkeypatch):
    import dragon_quant.scan as pkg
    import dragon_quant.scan.orchestrator.main as main_module

    calls = []

    def fake_scan(*args, **kwargs):
        calls.append((args, kwargs))
        return {"ok": True}

    monkeypatch.setattr(main_module, "scan", fake_scan)

    assert pkg(top_n=3, candidates_n=2) == {"ok": True}
    assert calls == [((), {"top_n": 3, "candidates_n": 2})]
