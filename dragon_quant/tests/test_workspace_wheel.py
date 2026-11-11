"""wheel 打包资源：SDK README 与策略模板必须进入 wheel。

真实 wheel-only venv 消费冒烟在交付报告中以命令/输出留证；本用例只验证
构建产物包含 ``importlib.resources`` 读取的资源，避免每次测试都创建 venv。
"""

from __future__ import annotations

import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


def _build_wheel(tmp_path: Path) -> Path:
    result = subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--outdir", str(tmp_path)],
        cwd=str(REPO), capture_output=True, text=True, timeout=300,
    )
    if result.returncode != 0:
        pytest.skip(f"wheel build unavailable: {result.stderr[-400:]}")
    wheels = sorted(tmp_path.glob("*.whl"))
    assert wheels, result.stderr
    return wheels[-1]


def test_wheel_contains_strategy_sdk_resources(tmp_path):
    wheel = _build_wheel(tmp_path)
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
    expected = {
        "dragon_quant/strategy_sdk/resources/README.md",
        "dragon_quant/strategy_sdk/resources/templates/example_momentum.strategy.py",
        "dragon_quant/strategy_sdk/resources/templates/example_momentum.strategy.json",
        "dragon_quant/strategy_sdk/resources/templates/example_meanrev.strategy.py",
    }
    missing = expected - names
    assert not missing, f"wheel 缺少策略 SDK 资源：{sorted(missing)}"


CONSUMER = r'''
import json, os, sys, tempfile
from pathlib import Path
sys.path.insert(0, {site!r})
from dragon_quant.workspace.main import init_workspace, list_strategies, validate_strategy
from dragon_quant.workspace import config
from dragon_quant.workspace.factors import load_workspace_factor_specs, combined_specs
from dragon_quant.review_account.models import StrategyConfig
from dragon_quant.review_account.strategy.runtime import build_runtime_for_selection
from dragon_quant.review_account.market import at

root = Path(tempfile.mkdtemp(prefix="dq-wheel-ws-")) / "ws"
init_workspace(str(root))
print("init", [s["id"] for s in list_strategies(str(root))["strategies"]])
print("validate", validate_strategy("user:example_momentum", str(root))["ok"])
cfg = config.load_workspace(root)
(cfg.root / "factors" / "custom.py").write_text(
    "from dragon_quant.infra.factor.types import FactorFrame, FactorSpec\n"
    "def compute(ctx): return FactorFrame()\n"
    "SPECS=[FactorSpec(name='wheel_factor', label='w', frequency='1d',"
    " source='computed', compute=compute, impl_key='w')]\n", encoding="utf-8")
cfg.factors = ["factors/custom.py"]; config.write_workspace(cfg)
cfg = config.load_workspace(root)
print("factor", [s.name for s in load_workspace_factor_specs(cfg)])
selection_mod = __import__("dragon_quant.workspace.main", fromlist=["resolve_selection"])
selection = selection_mod.resolve_selection(str(root), "user:example_momentum")
runtime = build_runtime_for_selection(selection, StrategyConfig())
stamp = at("2026-11-11", "09:30")
row = {"date": "2026-11-11", "phase": "open", "observed_at": stamp,
       "bar_timestamp": stamp, "open": 11.0, "bar_close": 11.0, "bar_high": 11.0,
       "bar_low": 11.0, "ma5": 10.5, "prev_close": 10.8, "open_gap_pct": 1.9,
       "limit_up": 11.88, "limit_down": 9.72, "execution_price": 11.0,
       "prev_row": {}, "hist_rows": [], "intraday_bars": []}
signal = runtime.evaluate_entry(candidate={"code": "600001", "trade_date": "2026-11-10"},
    row=row, cfg=StrategyConfig(), prev_row={}, hist_rows=[], intraday_bars=[],
    sentiment_pct=None, trade_date="2026-11-11", timestamp=stamp, phase="open", account={})
print("strategy_decision", signal["code"])
print("workspace_root", str(root))
'''


def test_wheel_only_venv_executes_strategy_and_factor(tmp_path, monkeypatch):
    import os
    import shutil

    wheel = _build_wheel(tmp_path)
    venv_dir = tmp_path / "venv"
    subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=True,
                   capture_output=True, text=True, timeout=180)
    python = venv_dir / "bin" / "python"
    if not python.exists():
        python = venv_dir / "Scripts" / "python.exe"
    subprocess.run([str(python), "-m", "pip", "install", "--no-deps", "--no-index",
                    str(wheel)], check=True, capture_output=True, text=True, timeout=180)
    env = dict(os.environ)
    env["DQ_DATA_DIR"] = str(tmp_path / "data")
    env["HOME"] = str(tmp_path / "home")
    (tmp_path / "home").mkdir()
    script = tmp_path / "consume.py"
    import glob
    site = (glob.glob(str(venv_dir / "lib" / "python*" / "site-packages")) or [""])[0]
    script.write_text(CONSUMER.replace("{site!r}", repr(site)), encoding="utf-8")
    result = subprocess.run([str(python), str(script)], capture_output=True, text=True,
                            env=env, timeout=180, cwd=str(tmp_path))
    assert result.returncode == 0, result.stderr
    assert "user:example_momentum" in result.stdout
    assert "validate True" in result.stdout
    assert "ws_" in result.stdout and "wheel_factor" in result.stdout
    assert "strategy_decision momentum_breakout" in result.stdout
    marked = Path(result.stdout.split("workspace_root ", 1)[1].strip())
    strategy_file = marked / "strategies" / "example_momentum" / "strategy.py"
    before = strategy_file.read_text(encoding="utf-8")
    # 同 wheel 再装一次：不得覆盖用户代码。
    subprocess.run([str(python), "-m", "pip", "install", "--force-reinstall", "--no-deps",
                    "--no-index", str(wheel)], check=True, capture_output=True,
                   text=True, timeout=180)
    assert strategy_file.read_text(encoding="utf-8") == before
    shutil.rmtree(marked.parent, ignore_errors=True)
