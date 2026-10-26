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
