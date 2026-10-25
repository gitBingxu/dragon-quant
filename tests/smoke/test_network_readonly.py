"""Real-network, read-only smoke (opt-in, non-gating).

Run only with ``DQ_RUN_NETWORK_SMOKE=1``.  Every case:

* uses a throwaway ``DQ_DATA_DIR`` and ``HOME`` (no real DB/cookie access);
* runs a read-only CLI command in a subprocess;
* reports real provider failures as test failures, but never affects a gate.

``assets doctor`` additionally requires ``DQ_RUN_ASSETS_SMOKE=1`` and a complete
``DQ_ASSETS_*`` credential set, because it performs a remote write probe.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TIMEOUT = 180


def _run(tmp_path: Path, *args: str, keep_assets: bool = False) -> subprocess.CompletedProcess:
    data_dir = tmp_path / "data"
    home = tmp_path / "home"
    data_dir.mkdir(exist_ok=True)
    home.mkdir(exist_ok=True)

    env = dict(os.environ)
    env["DQ_DATA_DIR"] = str(data_dir)
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)
    env["XDG_DATA_HOME"] = str(home / ".local" / "share")
    env.pop("DQ_TEST_ALLOW_REAL_DATA", None)
    if not keep_assets:
        for name in [key for key in env if key.startswith("DQ_ASSETS_")]:
            env.pop(name, None)

    return subprocess.run(
        [sys.executable, "-m", "dragon_quant", *args],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=TIMEOUT,
    )


def _assert_clean(result: subprocess.CompletedProcess, label: str) -> None:
    assert result.returncode == 0, (
        f"{label} failed: exit={result.returncode}\n"
        f"stdout:\n{result.stdout[-2000:]}\nstderr:\n{result.stderr[-2000:]}"
    )
    assert "Traceback" not in result.stderr, result.stderr[-2000:]


def test_data_sector_reads_a_real_ranking(tmp_path):
    result = _run(tmp_path, "data", "sector")
    _assert_clean(result, "data sector")
    assert result.stdout.strip(), "data sector returned no rows"


def test_data_quote_reads_one_symbol(tmp_path):
    result = _run(tmp_path, "data", "quote", "--code", "600519")
    _assert_clean(result, "data quote")
    assert "获取行情失败" not in result.stdout
    assert '"code"' in result.stdout, result.stdout[-2000:]


def test_data_minute_tencent_reads_real_bars(tmp_path):
    result = _run(tmp_path, "data", "minute", "--code", "600519", "--source", "tencent")
    _assert_clean(result, "data minute --source tencent")
    assert result.stdout.strip(), "data minute returned no bars"


def test_data_kline_tencent_path_is_read_only_and_does_not_crash(tmp_path):
    # Tencent daily K-line is documented future work (returns []); this pins the
    # CLI path/exit contract without pretending a real daily fetch happened.
    result = _run(tmp_path, "data", "kline", "--code", "600519", "--source", "tencent", "--days", "5")
    _assert_clean(result, "data kline --source tencent")


def test_assets_doctor_requires_explicit_credentials_and_opt_in(tmp_path):
    if os.environ.get("DQ_RUN_ASSETS_SMOKE") != "1":
        pytest.skip("assets doctor 远端写探针需 DQ_RUN_ASSETS_SMOKE=1")
    required = (
        "DQ_ASSETS_ENDPOINT",
        "DQ_ASSETS_REGION",
        "DQ_ASSETS_BUCKET",
        "DQ_ASSETS_ACCESS_KEY_ID",
        "DQ_ASSETS_SECRET_ACCESS_KEY",
    )
    if not all(os.environ.get(name) for name in required):
        pytest.skip("缺少完整 DQ_ASSETS_* 凭据；assets doctor 跳过")

    result = _run(tmp_path, "assets", "doctor", "--samples", "5", keep_assets=True)
    _assert_clean(result, "assets doctor")
