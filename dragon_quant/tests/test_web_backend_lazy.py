"""B2：``web_ui`` 导入期零 fastapi / 零目录副作用（子进程观测）。"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _run(tmp_path: Path, code: str) -> subprocess.CompletedProcess:
    home = tmp_path / "home"
    data = tmp_path / "data"
    home.mkdir(parents=True, exist_ok=True)
    env = {
        **{k: v for k, v in os.environ.items() if not k.startswith("DQ_")},
        "PYTHONPATH": str(REPO_ROOT),
        "DQ_DATA_DIR": str(data),
        "HOME": str(home),
        "APPDATA": str(home / "AppData" / "Roaming"),
        "XDG_DATA_HOME": str(home / ".local" / "share"),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONIOENCODING": "utf-8",
    }
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(REPO_ROOT), env=env, capture_output=True, text=True, timeout=120,
    )


def test_import_web_ui_backend_stays_lazy(tmp_path):
    code = (
        "import sys\n"
        "import web_ui, web_ui.backend, web_ui.backend.serve, web_ui.server\n"
        "assert 'fastapi' not in sys.modules, 'fastapi imported eagerly'\n"
        "assert 'uvicorn' not in sys.modules, 'uvicorn imported eagerly'\n"
        "print('ok')\n"
    )
    result = _run(tmp_path, code)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"
    assert not (tmp_path / "data").exists()
    for name in ("dragon.db", "assets.db", "prep.db"):
        assert not (tmp_path / "data" / name).exists()


def test_dragon_quant_help_does_not_import_fastapi(tmp_path):
    result = _run(
        tmp_path,
        "import sys; import dragon_quant.cli; "
        "assert 'fastapi' not in sys.modules; print('ok')",
    )
    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "data").exists()
