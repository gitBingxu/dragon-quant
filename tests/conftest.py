"""pytest 全局隔离：把数据目录重定向到一次性临时目录。

背景：``dragon_quant.storage.paths`` 在 import 时解析 ``DATA_DIR``。测试若漏 patch
某条连接（例如 ``storage.dragons.save_dragons`` 内部往 assets.db 镜像的
fan-out 双写），写入就会落到开发者真实的 dragon.db / assets.db
（2026-10-23 发生过：单测往真实 assets.db.dragons 灌了 16 行假数据）。

本文件在任何测试模块 import 之前改写 ``DQ_DATA_DIR``，让漏 patch 最多污染临时
目录，session 结束自动删除。

显式例外：``DQ_TEST_ALLOW_REAL_DATA=1`` 时保留原数据目录（仅供人工诊断）。

**边界**：本重定向只在 pytest 加载 conftest 时生效。``python -m unittest
discover -s tests`` 等入口不会执行本文件，隔离完全依赖各用例自行用
``tests.support.patch_storage_dbs`` 同时 patch 两个连接点。
"""

import atexit
import os
import shutil
import tempfile

if os.environ.get("DQ_TEST_ALLOW_REAL_DATA") != "1":
    # 整个 session 共用一个临时目录：这是「漏 patch」的安全网，不是用例自己的
    # 隔离手段——各用例仍应自带临时库，避免相互残留。
    _isolated_dir = tempfile.mkdtemp(prefix="dragon-quant-tests-")
    os.environ["DQ_DATA_DIR"] = _isolated_dir
    os.environ["DQ_TEST_ISOLATED_DATA_DIR"] = _isolated_dir
    atexit.register(shutil.rmtree, _isolated_dir, ignore_errors=True)
