"""测试隔离回归：漏 patch 的写操作不得落到真实数据目录。

2026-10-23 事故：``db.save_dragons`` 新增 assets.db 镜像 fan-out 后，只 patch
``db._connect`` 的单测把假数据写进了开发者真实 assets.db。``dragon_quant/tests/conftest.py``
用 ``DQ_DATA_DIR`` 重定向兜底，本文件验证兜底确实生效。
"""

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from dragon_quant.infra.assets import db as assets_db
from dragon_quant.infra.assets import dragons_mirror
from dragon_quant.infra.storage import paths
from dragon_quant.scan.storage import dragons as db
from dragon_quant.infra.storage.connection import init_db
from dragon_quant.tests.support import patch_storage_dbs

ISOLATED = os.environ.get("DQ_TEST_ISOLATED_DATA_DIR")


@unittest.skipUnless(ISOLATED, "未启用测试隔离（需经 dragon_quant/tests/conftest.py 运行，pytest）")
class TestDataDirIsolation(unittest.TestCase):
    def test_data_dir_points_to_isolated_tmp(self):
        self.assertEqual(str(paths.DATA_DIR), ISOLATED)
        self.assertTrue(str(paths.DB_PATH).startswith(ISOLATED))
        self.assertTrue(str(assets_db.ASSETS_DB_PATH).startswith(ISOLATED))

    def test_unpatched_dragon_write_stays_inside_isolated_dir(self):
        """不 patch 任何连接直接写 dragons：两条连接都必须落在隔离目录。"""
        day = "1999-01-04"
        init_db()
        try:
            db.save_dragons(
                day, [{"code": "000001", "name": "隔离探针", "rank": 1}],
                version="test-isolation")
            mirrored = dragons_mirror.get_dragons(day)
            self.assertEqual([r["code"] for r in mirrored], ["000001"])
        finally:
            self._cleanup(day)

    def _cleanup(self, day):
        for path, table in ((paths.DB_PATH, "dragons_v2"),
                            (assets_db.ASSETS_DB_PATH, "dragons")):
            conn = sqlite3.connect(path)
            try:
                conn.execute(f"DELETE FROM {table} WHERE trade_date = ?", (day,))
                conn.commit()
            finally:
                conn.close()


class TestPatchStorageDbs(unittest.TestCase):
    """helper 契约：镜像写必须落到 ``<dragon_path>.assets``，helper 失效即变红。"""

    DAY = "1999-01-05"

    def test_mirror_write_lands_in_side_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            dragon_path = Path(tmp) / "dragon.db"
            with patch_storage_dbs(dragon_path):
                init_db()
                db.save_dragons(
                    self.DAY, [{"code": "600001", "name": "镜像探针", "rank": 1}],
                    version="test-isolation")
                mirrored = dragons_mirror.get_dragons(self.DAY)

            self.assertEqual([r["code"] for r in mirrored], ["600001"])
            side = Path(str(dragon_path) + ".assets")
            self.assertTrue(side.exists(), "镜像写没有落到 <dragon_path>.assets")
            conn = sqlite3.connect(side)
            try:
                codes = [r[0] for r in conn.execute(
                    "SELECT code FROM dragons WHERE trade_date = ?", (self.DAY,))]
            finally:
                conn.close()
            self.assertEqual(codes, ["600001"])

    def test_same_path_for_both_databases_is_rejected(self):
        with self.assertRaises(ValueError):
            patch_storage_dbs("same.db", "same.db")


if __name__ == "__main__":
    unittest.main()