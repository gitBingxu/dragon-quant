"""pytest 全局隔离：把数据目录重定向到一次性临时目录。

背景：``dragon_quant.infra.storage.paths`` 在 import 时解析 ``DATA_DIR``。测试若漏 patch
某条连接（例如 ``storage.dragons.save_dragons`` 内部往 assets.db 镜像的
fan-out 双写），写入就会落到开发者真实的 dragon.db / assets.db
（2026-10-23 发生过：单测往真实 assets.db.dragons 灌了 16 行假数据）。

本文件在任何测试模块 import 之前改写 ``DQ_DATA_DIR``，让漏 patch 最多污染临时
目录，session 结束自动删除。

显式例外：``DQ_TEST_ALLOW_REAL_DATA=1`` 时保留原数据目录（仅供人工诊断）。

**边界**：本重定向只在 pytest 加载 conftest 时生效。``python -m unittest
discover -s dragon_quant/tests`` 等入口不会执行本文件，隔离完全依赖各用例自行用
``dragon_quant.tests.support.patch_storage_dbs`` 同时 patch 两个连接点。
"""

import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path

ISOLATION_ENABLED = os.environ.get("DQ_TEST_ALLOW_REAL_DATA") != "1"

if ISOLATION_ENABLED:
    # 整个 session 共用一个临时目录：这是「漏 patch」的安全网，不是用例自己的
    # 隔离手段——各用例仍应自带临时库，避免相互残留。
    _isolated_dir = tempfile.mkdtemp(prefix="dragon-quant-tests-")
    os.environ["DQ_DATA_DIR"] = _isolated_dir
    os.environ["DQ_TEST_ISOLATED_DATA_DIR"] = _isolated_dir
    atexit.register(shutil.rmtree, _isolated_dir, ignore_errors=True)

    # Playwright 内核下载隔离：内核目录指向临时区，下载被禁用，下载源指向
    # 本机关闭端口。任何意外的浏览器下载都会快速失败，而不是触网/写用户目录。
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(
        Path(_isolated_dir) / "playwright-browsers"
    )
    os.environ["PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD"] = "1"
    os.environ["PLAYWRIGHT_DOWNLOAD_HOST"] = "http://127.0.0.1:9"

    # Python 网络 fail-closed：只允许本机回环（assets S3 单测/Web UI 单测需要），
    # 任何外部 socket/DNS 直接抛 NetworkBlocked。复用 E2E 已审计的 socket guard。
    from dragon_quant.tests.e2e.injection import netguard

    netguard.install(allow_localhost=True)
    if not netguard.is_patched():
        raise RuntimeError("单元测试 netguard 安装后自检失败：socket 入口未被接管")

# ── SQLite 真实库 open 阻断（先于任何应用模块连接）──────────────────────────
# 以真实 OS 用户 home 解析默认数据目录（不信测试 HOME/DQ_DATA_DIR），在
# sqlite3.connect 的 audit 事件里于实际 open 前拒绝；覆盖主进程/线程/Python
# 子进程（子进程需显式注入，见 test_sqlite_guard_isolation.py）。
from dragon_quant.tests import sqlite_guard  # noqa: E402

_GUARD_LOG = Path(
    (os.environ.get("DQ_TEST_ISOLATED_DATA_DIR") or tempfile.gettempdir())
) / "sqlite-guard.jsonl"
os.environ["DQ_SQLITE_GUARD_LOG"] = str(_GUARD_LOG)
sqlite_guard.install(log_path=_GUARD_LOG)
sqlite_guard.verify_selftest()


def pytest_sessionfinish(session, exitstatus):  # noqa: ARG001
    """被被测代码吞掉的真实库 open 尝试也必须让最终诊断失败。"""
    violations = sqlite_guard.log_violations(_GUARD_LOG)
    if violations:
        sample = violations[:5]
        sys.stderr.write(
            "真实数据目录 SQLite open 尝试被 guard 阻断（最终诊断失败）:\n"
            + "\n".join(f"  pid={item.get('pid')} target={item.get('target')}"
                        for item in sample) + "\n")
        session.exitstatus = 1

# ── 真实数据目录 stat 哨兵 ────────────────────────────────────────────────
# 只读 os.stat 元数据，绝不打开/读取/写入真实库。测试进程若把漏 patch 的写
# 操作落到真实 dragon.db / assets.db，本哨兵在用例结束时 fail closed。

_REAL_DATA_FILES = (
    "dragon.db",
    "dragon.db-wal",
    "dragon.db-shm",
    "assets.db",
    "assets.db-wal",
    "assets.db-shm",
    "prep.db",
)


def _real_data_dir() -> Path:
    """平台默认数据目录，刻意忽略 DQ_DATA_DIR（那是被测进程的覆写值）。"""

    home = Path(os.environ.get("HOME") or Path.home()).resolve()
    if sys.platform == "win32":
        appdata = Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming")
        return (appdata / "dragon-quant").resolve()
    if sys.platform == "darwin":
        return (home / "Library" / "Application Support" / "dragon-quant").resolve()
    xdg = Path(os.environ.get("XDG_DATA_HOME") or home / ".local" / "share")
    return (xdg / "dragon-quant").resolve()


def _real_data_snapshot() -> dict[str, tuple]:
    data = _real_data_dir()
    snapshot: dict[str, tuple] = {}
    try:
        stat = os.stat(data)
    except FileNotFoundError:
        snapshot["__dir__"] = (False, 0, 0)
    except OSError as exc:
        snapshot["__dir__"] = (True, -1, type(exc).__name__)
    else:
        snapshot["__dir__"] = (True, stat.st_mtime_ns, stat.st_mode)

    for name in _REAL_DATA_FILES:
        path = data / name
        try:
            stat = os.stat(path)
        except FileNotFoundError:
            snapshot[name] = (False, 0, 0, 0)
        except OSError as exc:
            snapshot[name] = (True, -1, -1, type(exc).__name__)
        else:
            snapshot[name] = (True, stat.st_size, stat.st_mtime_ns, stat.st_mode)
    return snapshot


import pytest  # noqa: E402  (must run after the environment is rewritten)


@pytest.fixture(autouse=True)
def isolated_workspace_registry(tmp_path):
    """本机 workspace 登记逐用例隔离。

    ``init_workspace(register=True)`` / ``select_strategy`` 会写
    ``DQ_DATA_DIR/workspace-selection.json``。session 共用的临时数据目录下，一个
    用例登记的临时 workspace 会被后续用例（CLI / Web 不显式传 workspace 时）
    消费，制造顺序相关的假失败。重定向到用例私有文件：真实读写语义不变，
    但不再把一次性路径登记到全局状态。

    这里不用 ``monkeypatch``：它会改变 fixture 创建顺序，导致 ``HOME`` 类用例的
    环境恢复晚于 ``real_data_metadata_guard`` 的 teardown，从而误报真实数据目录变化。
    """
    from dragon_quant.workspace import config as workspace_config

    original = workspace_config.selection_path
    path = tmp_path / "workspace-selection.json"
    workspace_config.selection_path = lambda: path
    try:
        yield path
    finally:
        workspace_config.selection_path = original


@pytest.fixture(autouse=True)
def real_data_metadata_guard():
    """单元测试逐用例观察真实数据目录元数据；任何差异立即报错。"""

    if not ISOLATION_ENABLED:
        yield
        return
    before = _real_data_snapshot()
    try:
        yield
    finally:
        after = _real_data_snapshot()
        differences = [
            f"  {name}: {before.get(name)!r} -> {after.get(name)!r}"
            for name in ("__dir__", *_REAL_DATA_FILES)
            if before.get(name) != after.get(name)
        ]
        if differences:
            raise AssertionError(
                "真实数据目录元数据发生变化（单元测试不得读写真实库）:\n"
                f"  dir: {_real_data_dir()}\n" + "\n".join(differences)
            )
