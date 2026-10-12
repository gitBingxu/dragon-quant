"""盘后复盘快照（prep）单元测试。"""

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from dragon_quant.review_account.models import StrategyConfig


class TestPrepSnapshotStorage(unittest.TestCase):
    """storage.prep 快照读写 + 幂等 UPSERT。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._db_path = str(Path(self._tmpdir.name) / "test.db")
        with patch("dragon_quant.storage.db._connect",
                   side_effect=lambda: sqlite3.connect(self._db_path)):
            from dragon_quant.storage import db
            db.init_db()

    def tearDown(self):
        self._tmpdir.cleanup()

    def _db(self):
        from dragon_quant.storage import db
        return db

    def test_roundtrip_and_upsert(self):
        result = {"trade_date": "2026-10-09",
                  "sentiment": {"limit_up_cnt": 72, "position_advice": "正常"},
                  "candidates": [{"code": "600001", "trade_date": "2026-10-09"}]}
        with patch("dragon_quant.storage.db._connect",
                   side_effect=lambda: sqlite3.connect(self._db_path)):
            db = self._db()
            db.save_prep_snapshot("2026-10-09", result)
            self.assertTrue(db.has_prep_snapshot("2026-10-09"))
            got = db.get_prep_snapshot("2026-10-09")
            self.assertEqual(got["sentiment"]["limit_up_cnt"], 72)
            self.assertEqual(got["candidates"][0]["code"], "600001")
            # UPSERT 覆盖
            db.save_prep_snapshot("2026-10-09", {"trade_date": "2026-10-09", "v2": True})
            self.assertEqual(db.get_prep_snapshot("2026-10-09")["v2"], True)

    def test_missing_returns_none(self):
        with patch("dragon_quant.storage.db._connect",
                   side_effect=lambda: sqlite3.connect(self._db_path)):
            db = self._db()
            self.assertIsNone(db.get_prep_snapshot("1999-01-01"))
            self.assertFalse(db.has_prep_snapshot("1999-01-01"))

    def test_v1_v2_do_not_overwrite(self):
        with patch("dragon_quant.storage.db._connect",
                   side_effect=lambda: sqlite3.connect(self._db_path)):
            db = self._db()
            db.save_prep_snapshot("2026-10-09", {"src": "v2"}, source="v2")
            db.save_prep_snapshot("2026-10-09", {"src": "v1"}, source="v1")
            self.assertEqual(db.get_prep_snapshot("2026-10-09", source="v2")["src"], "v2")
            self.assertEqual(db.get_prep_snapshot("2026-10-09", source="v1")["src"], "v1")


class TestPrepPureHelpers(unittest.TestCase):
    """review_account.prep 的纯函数。"""

    def test_position_advice_bands(self):
        from dragon_quant.review_account.prep import position_advice
        self.assertEqual(position_advice(90, 2), ("重仓", 0.8))
        self.assertEqual(position_advice(50, 40), ("空仓", 0.0))
        self.assertEqual(position_advice(30, 15), ("轻仓", 0.3))
        self.assertEqual(position_advice(50, 3), ("正常", 0.5))

    def test_compute_main_boards_counts_concepts(self):
        from dragon_quant.review_account.prep import compute_main_boards
        dragons = [{"concepts": ["A", "B"]}, {"concepts": ["A"]}, {"concepts": ["C"]}]
        boards = compute_main_boards(dragons)
        self.assertEqual(boards[0], {"board_name": "A", "dragon_count": 2})
        self.assertEqual(len(boards), 3)

    def test_compute_candidates_adds_trade_date(self):
        from dragon_quant.review_account.prep import compute_candidates
        cands = compute_candidates([{"code": "600001", "name": "样本"}], "2026-10-09")
        self.assertEqual(cands[0]["trade_date"], "2026-10-09")
        self.assertEqual(cands[0]["code"], "600001")


class TestCollectCandidatesSnapshot(unittest.TestCase):
    """collect_candidates 快照消费 + 回退。"""

    def test_uses_snapshot_when_present(self):
        from dragon_quant.review_account.market import collect_candidates
        snap = {"candidates": [
            {"code": "A", "composite_score": 90, "is_true_dragon": True},
            {"code": "B", "composite_score": 70, "is_true_dragon": True},
            {"code": "C", "composite_score": 40, "is_true_dragon": True},  # 低分剔除
            {"code": "D", "composite_score": 80, "is_true_dragon": False},  # 否决剔除
        ]}
        loader = MagicMock(return_value=[])
        with patch("dragon_quant.storage.db.get_prep_snapshot", return_value=snap):
            got = collect_candidates(["2026-09-04"], "2026-09-07", StrategyConfig(), loader)
        self.assertEqual([c["code"] for c in got], ["A", "B"])
        loader.assert_not_called()

    def test_falls_back_when_no_snapshot(self):
        from dragon_quant.review_account.market import collect_candidates
        loader = MagicMock(return_value=[])
        with patch("dragon_quant.storage.db.get_prep_snapshot", return_value=None):
            collect_candidates(
                ["2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04", "2026-09-07"],
                "2026-09-07", StrategyConfig(), loader)
        self.assertEqual([c.args[0] for c in loader.call_args_list],
                         ["2026-09-02", "2026-09-03", "2026-09-04"])

    def test_falls_back_on_snapshot_read_error(self):
        from dragon_quant.review_account.market import collect_candidates
        loader = MagicMock(return_value=[{"code": "X", "composite_score": 60,
                                          "is_true_dragon": True}])
        with patch("dragon_quant.storage.db.get_prep_snapshot",
                   side_effect=RuntimeError("db down")):
            got = collect_candidates(["2026-09-04"], "2026-09-07", StrategyConfig(), loader)
        self.assertEqual([c["code"] for c in got], ["X"])

    def test_v1_source_skips_snapshot(self):
        from dragon_quant.review_account.market import collect_candidates
        loader = MagicMock(return_value=[])
        with patch("dragon_quant.storage.db.get_prep_snapshot") as gp:
            collect_candidates(["2026-09-04"], "2026-09-07", StrategyConfig(source="v1"), loader)
        gp.assert_not_called()

    def test_snapshot_all_filtered_falls_back(self):
        from dragon_quant.review_account.market import collect_candidates
        snap = {"candidates": [{"code": "LOW", "composite_score": 10, "is_true_dragon": True}]}
        loader = MagicMock(return_value=[{"code": "X", "composite_score": 60,
                                          "is_true_dragon": True}])
        with patch("dragon_quant.storage.db.get_prep_snapshot", return_value=snap):
            got = collect_candidates(["2026-09-04"], "2026-09-07", StrategyConfig(), loader)
        self.assertEqual([c["code"] for c in got], ["X"])


if __name__ == "__main__":
    unittest.main()
