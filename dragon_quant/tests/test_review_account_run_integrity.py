"""账户回测落库完整性：date_to ↔ 最后快照日、钳制留痕、原子落库。

三类防护对应真实故障：全量回测在收盘前把 ``date_to`` 记成「今天」，模拟器
只跑到上一交易日；落库分两次提交，子表失败留下空 run；增量入口随即以
「基准记录快照与结束日期不一致」拒绝续算。本文件在临时 SQLite + FakeData
上验证修复，不触网、不写真实库。
"""

import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from dragon_quant.infra import clock as runtime_clock
from dragon_quant.infra.clock import FixedClock
from dragon_quant.infra.storage.connection import init_db
from dragon_quant.review_account import storage as account_store
from dragon_quant.review_account.market import SHANGHAI
from dragon_quant.review_account.models import Snapshot
from dragon_quant.review_account.service import (
    _persist_result,
    resolve_persisted_date_to,
    restore_incremental_seed,
    run_review_account,
)
from dragon_quant.tests.test_review_account_incremental import (
    CAND,
    DAYS,
    ChainData,
)


class TruncatedData(ChainData):
    """交易日历只到 2026-09-10：模拟「请求结束日当天尚未收盘」。"""

    def __init__(self):
        super().__init__(days=DAYS[:-1])


class _IntegrityBase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._db_path = str(Path(self._tmpdir.name) / "test.db")
        self._conn = sqlite3.connect(self._db_path)
        self._patch = patch("dragon_quant.infra.storage.connection._connect",
                            side_effect=lambda: sqlite3.connect(self._db_path))
        self._patch.start()
        init_db()
        self._previous_clock = runtime_clock.set_clock(
            FixedClock(datetime(2026, 9, 11, 15, 30, tzinfo=SHANGHAI)))
        patchers = [
            patch("dragon_quant.review_account.simulator.MarketData",
                  side_effect=lambda provider=None: TruncatedData()),
            patch("dragon_quant.review_account.service.MarketData",
                  side_effect=lambda provider=None: TruncatedData()),
            patch("dragon_quant.infra.assets.dragons_mirror.get_dragons_by_date",
                  return_value=[CAND]),
            patch("dragon_quant.review_account.market.prep_snapshot_for",
                  return_value=(None, None, None)),
        ]
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def tearDown(self):
        runtime_clock.set_clock(self._previous_clock)
        self._patch.stop()
        self._conn.close()
        self._tmpdir.cleanup()

    @staticmethod
    def _snapshot(trade_date):
        return Snapshot(trade_date, 100_000.0, 0.0, 100_000.0,
                        0.0, 0.0, 0.0)


class TestFullRunClamp(_IntegrityBase):
    def test_clamps_date_to_to_last_snapshot_and_records_request(self):
        result = run_review_account("2026-09-04", "2026-09-11",
                                    verbose=False, display_name="全量")

        self.assertEqual(result["date_to"], "2026-09-10")
        runs = account_store.query_review_account_runs(source="v2")
        self.assertEqual(runs[0]["date_to"], "2026-09-10")
        self.assertEqual(runs[0]["run_meta"]["requested_date_to"], "2026-09-11")
        self.assertIs(runs[0]["run_meta"]["date_to_clamped"], True)
        snapshots = account_store.query_review_account_snapshots(result["run_id"])
        self.assertEqual(snapshots[-1]["trade_date"], "2026-09-10")

    def test_resolve_persisted_date_to_rejects_empty_snapshots(self):
        with self.assertRaisesRegex(ValueError, "没有生成任何每日快照"):
            resolve_persisted_date_to({"snapshots": []}, "2026-09-11")

    def test_clamped_default_display_name_uses_effective_end(self):
        run_review_account("2026-09-04", "2026-09-11", verbose=False,
                           display_name="2026-09-04 ~ 2026-09-11")
        runs = account_store.query_review_account_runs(source="v2")
        self.assertEqual(runs[0]["display_name"], "2026-09-04 ~ 2026-09-10")

    def test_clamped_run_seed_restores_without_mismatch(self):
        # 修复前：run.date_to=09-11、最后快照=09-10 → 种子恢复直接抛
        # 「快照与结束日期不一致」，增量入口不可用。
        base = run_review_account("2026-09-04", "2026-09-11", verbose=False)
        run = account_store.get_review_account_run(base["run_id"])
        snapshots = account_store.query_review_account_snapshots(base["run_id"])
        seed = restore_incremental_seed(run, snapshots, [])
        self.assertEqual(seed["base_equity"], run["final_equity"])


class TestPersistInvariant(_IntegrityBase):
    def test_rejects_mismatched_snapshot_end_without_writing_run(self):
        result = {
            "initial_cash": 100_000.0, "final_equity": 100_000.0,
            "total_return": 0.0, "max_drawdown": 0.0, "trade_count": 0,
            "win_rate": None, "snapshots": [self._snapshot("2026-09-10")],
            "trades": [], "positions": [], "events": [],
            "realized_pnl_total": 0.0,
        }
        with self.assertRaisesRegex(ValueError, "快照终点"):
            _persist_result(result, source="v2", strategy_name="dragon_pullback_daily",
                            strategy_params_json="{}", date_from="2026-09-04",
                            date_to="2026-09-11", display_name="越界")
        self.assertEqual(account_store.query_review_account_runs(source="v2"), [])


class TestAtomicPersist(_IntegrityBase):
    def test_child_failure_rolls_back_run_row(self):
        with patch("dragon_quant.review_account.storage._insert_children",
                   side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                account_store.create_review_account_run(
                    source="v2", strategy_name="dragon_pullback_daily",
                    strategy_params_json="{}", date_from="2026-09-04",
                    date_to="2026-09-10", initial_cash=100_000.0,
                    final_equity=100_000.0, total_return=0.0,
                    max_drawdown=0.0, trade_count=0, win_rate=None,
                    children={"snapshots": [], "trades": [],
                              "positions": [], "events": []},
                )
        self.assertEqual(account_store.query_review_account_runs(source="v2"), [])


class TestAtomicAppend(_IntegrityBase):
    def _seed(self):
        return run_review_account("2026-09-04", "2026-09-10",
                                  verbose=False)["run_id"]

    def test_append_rejects_overlapping_snapshot(self):
        run_id = self._seed()
        with self.assertRaisesRegex(ValueError, "重叠"):
            account_store.append_review_account_results(
                run_id, date_to="2026-09-11", final_equity=1.0,
                total_return=0.0, max_drawdown=0.0, trade_count=0,
                win_rate=None, run_meta_json="{}",
                snapshots=[{"trade_date": "2026-09-10"}],
                trades=[], positions=[], events=[])

    def test_append_failure_rolls_back_inserted_snapshots(self):
        run_id = self._seed()
        before = account_store.query_review_account_snapshots(run_id)

        def boom(conn, run_id, **_kwargs):
            conn.execute(
                "INSERT INTO review_account_snapshots(run_id, trade_date) VALUES (?, ?)",
                (run_id, "2026-09-11"),
            )
            raise RuntimeError("boom")

        with patch("dragon_quant.review_account.storage._insert_children",
                   side_effect=boom):
            with self.assertRaises(RuntimeError):
                account_store.append_review_account_results(
                    run_id, date_to="2026-09-11", final_equity=999.0,
                    total_return=9.0, max_drawdown=0.0, trade_count=0,
                    win_rate=None, run_meta_json="{}",
                    snapshots=[{"trade_date": "2026-09-11"}],
                    trades=[], positions=[], events=[])

        after = account_store.query_review_account_snapshots(run_id)
        self.assertEqual([s["trade_date"] for s in after],
                         [s["trade_date"] for s in before])
        self.assertEqual(account_store.get_review_account_run(run_id)["date_to"],
                         "2026-09-10")


if __name__ == "__main__":
    unittest.main()
