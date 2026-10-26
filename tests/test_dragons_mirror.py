"""dragons 镜像（assets.db.dragons）测试：双写一致性 / 回填 / 读源切换 / 守卫。

覆盖方案 §6 的 AC3 / AC4 / AC5 主体；全部使用临时 SQLite + patch 连接，零网络。
"""

import io
import sqlite3
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import MagicMock, patch

from dragon_quant.infra.assets import db as assets_db
from dragon_quant.infra.assets import dragons_mirror
from dragon_quant.scan.storage import dragons as db
from dragon_quant.infra.storage.connection import init_db

DAY = "2026-10-20"
DAY2 = "2026-10-19"


class MirrorTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.dragon_path = root / "dragon.db"
        self.assets_path = root / "assets.db"
        self._patches = [
            patch("dragon_quant.infra.storage.connection._connect",
                  side_effect=lambda *a, **k: sqlite3.connect(self.dragon_path)),
            patch("dragon_quant.infra.assets.db._connect",
                  side_effect=self._assets_connect),
        ]
        for item in self._patches:
            item.start()

    def tearDown(self):
        for item in reversed(self._patches):
            item.stop()
        self._tmp.cleanup()

    def _assets_connect(self, create=True, path=None):
        target = Path(path) if path is not None else self.assets_path
        if not create and not target.exists():
            return None
        return sqlite3.connect(target)

    # ── helpers ───────────────────────────────────────────────────────────
    def _rows(self, table, path=None):
        conn = sqlite3.connect(path or self.assets_path)
        conn.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in conn.execute(f"SELECT * FROM {table}")]
        finally:
            conn.close()

    def _seed_source_only(self, rows_by_date):
        """只写源库（镜像 fan-out 打桩为空），用于回填/对账测试。"""
        with patch("dragon_quant.scan.storage.dragons._mirror_write"):
            for day, rows in rows_by_date.items():
                db.save_dragons(day, rows, version="1.0")

    def _sample_rows(self, code="600001"):
        return [{
            "code": code, "name": "样本", "scan_id": "v2_20261020_top25",
            "rank": 1, "composite_score": 88.5, "board_count": 2,
            "open_px": 10.0, "close_px": 11.0, "high_px": 11.0, "low_px": 9.9,
            "pct": 10.0, "turnover_rate": 8.2, "amount": 1.2e8,
            "market_cap": 5.0e9, "concepts": ["低空经济"], "report_text": "r",
            "is_true_dragon": True,
        }]


class TestDualWrite(MirrorTestBase):
    def test_save_dragons_writes_both_tables(self):
        db.save_dragons(DAY, self._sample_rows(), version="1.0")

        source = db.get_dragons(DAY)
        mirrored = dragons_mirror.get_dragons(DAY)
        self.assertEqual(len(source), 1)
        self.assertEqual(len(mirrored), 1)
        self.assertEqual(source[0]["code"], mirrored[0]["code"])
        self.assertEqual(mirrored[0]["source"], "v2")
        self.assertEqual(mirrored[0]["concepts"], ["低空经济"])

    def test_table_columns_match_dragons_v2(self):
        """AC3：镜像字段 probe 与 dragons_v2 的列名集合完全相等。"""
        init_db()
        db.save_dragons(DAY, self._sample_rows(), version="1.0")
        src = sqlite3.connect(self.dragon_path)
        dst = sqlite3.connect(self.assets_path)
        try:
            src_cols = {r[1] for r in src.execute("PRAGMA table_info(dragons_v2)")}
            dst_cols = {r[1] for r in dst.execute("PRAGMA table_info(dragons)")}
        finally:
            src.close()
            dst.close()
        self.assertEqual(src_cols, dst_cols)

    def test_business_columns_identical_excluding_id_and_created_at(self):
        db.save_dragons(DAY, self._sample_rows(), version="1.0")
        src = db.get_dragons(DAY)[0]
        dst = dragons_mirror.get_dragons(DAY)[0]
        for key in ("code", "name", "scan_id", "rank", "composite_score",
                    "board_count", "open_px", "close_px", "high_px", "low_px",
                    "pct", "turnover_rate", "amount", "market_cap", "concepts",
                    "report_text", "is_true_dragon", "version",
                    "review_status", "buy_date", "buy_price"):
            self.assertEqual(src[key], dst[key], key)

    def test_update_review_mirrors_row(self):
        db.save_dragons(DAY, self._sample_rows(), version="1.0")
        db.update_dragon_review(DAY, "600001", buy_date="2026-10-22",
                                buy_price=10.5, max_return_5d=12.0,
                                max_drawdown_5d=-3.0, max_return_hold_days=3)
        mirrored = dragons_mirror.get_dragons(DAY)[0]
        self.assertEqual(mirrored["review_status"], "completed")
        self.assertEqual(mirrored["buy_price"], 10.5)
        self.assertEqual(mirrored["max_return_5d"], 12.0)
        self.assertEqual(mirrored["max_return_hold_days"], 3)

    def test_update_review_missing_mirror_row_warns(self):
        db.save_dragons(DAY, self._sample_rows(), version="1.0")
        conn = sqlite3.connect(self.assets_path)
        try:
            conn.execute("DELETE FROM dragons")
            conn.commit()
        finally:
            conn.close()
        err = io.StringIO()
        with redirect_stderr(err):
            db.update_dragon_review(DAY, "600001", buy_price=10.5)
        self.assertIn("mirror_missing_row", err.getvalue())

    def test_delete_pending_fanout(self):
        db.save_dragons(DAY, self._sample_rows("600001") + self._sample_rows("600002"),
                        version="1.0")
        deleted = db.delete_pending_dragons_not_in(DAY, {"600001"})
        self.assertEqual(deleted, 1)
        self.assertEqual([r["code"] for r in dragons_mirror.get_dragons(DAY)], ["600001"])

    def test_mirror_failure_does_not_break_main_write(self):
        with patch("dragon_quant.infra.assets.dragons_mirror.save_dragons",
                   side_effect=RuntimeError("assets db locked")):
            err = io.StringIO()
            with redirect_stderr(err):
                db.save_dragons(DAY, self._sample_rows(), version="1.0")
        self.assertEqual(len(db.get_dragons(DAY)), 1)     # 主写成功，不回滚
        self.assertIn("[mirror]", err.getvalue())
        self.assertIn("backfill-dragons", err.getvalue())

    def test_slow_mirror_does_not_hold_main_lock(self):
        """锁外 fan-out：慢镜像（0.3s）不得阻塞另一路 save_dragons。"""
        def slow(*args, **kwargs):
            time.sleep(0.3)
            return 0

        with patch("dragon_quant.infra.assets.dragons_mirror.save_dragons", side_effect=slow):
            start = time.monotonic()
            threads = [
                threading.Thread(target=db.save_dragons,
                                 args=(day, self._sample_rows(), "1.0"))
                for day in (DAY, DAY2)
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            elapsed = time.monotonic() - start
        self.assertLess(elapsed, 0.55)                    # 串行锁内会是 ~0.6s
        self.assertEqual(len(db.get_dragons(DAY)), 1)
        self.assertEqual(len(db.get_dragons(DAY2)), 1)

    def test_v1_source_does_not_write_mirror(self):
        db.save_dragons(DAY, self._sample_rows(), version="1.0", source="v1")
        self.assertEqual(dragons_mirror.get_dragons(DAY), [])


class TestBackfill(MirrorTestBase):
    def test_backfill_inserts_then_idempotent(self):
        self._seed_source_only({DAY: self._sample_rows("600001") + self._sample_rows("600002")})
        first = dragons_mirror.backfill_dragons()
        self.assertTrue(first["ok"], first)
        self.assertEqual(first["inserted"], 2)
        self.assertEqual(first["rows_source"], 2)
        self.assertEqual(len(dragons_mirror.get_dragons(DAY)), 2)

        second = dragons_mirror.backfill_dragons()
        self.assertTrue(second["ok"], second)
        self.assertEqual(second["inserted"], 0)
        self.assertEqual(second["updated"], 0)
        self.assertEqual(second["unchanged"], 2)

    def test_dry_run_distinguishes_missing_and_orphan(self):
        self._seed_source_only({DAY: self._sample_rows("600001")})
        conn = sqlite3.connect(self.assets_path)
        try:
            assets_db._ensure_schema(conn)
            conn.execute(
                "INSERT INTO dragons(trade_date, code, name, review_status) "
                "VALUES (?, 'ORPHAN', '孤儿', 'pending')", (DAY,))
            conn.commit()
        finally:
            conn.close()

        result = dragons_mirror.backfill_dragons(dry_run=True)
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["missing_rows"], 1)
        self.assertEqual(result["orphan_rows"], 1)
        codes = {r["code"] for r in dragons_mirror.get_dragons(DAY)}
        self.assertEqual(codes, {"ORPHAN"})               # dry-run 不写库

    def test_prune_removes_orphan_rows_only(self):
        self._seed_source_only({DAY: self._sample_rows("600001")})
        conn = sqlite3.connect(self.assets_path)
        try:
            assets_db._ensure_schema(conn)
            conn.execute(
                "INSERT INTO dragons(trade_date, code, name, review_status) "
                "VALUES (?, 'ORPHAN', '孤儿', 'pending')", (DAY,))
            conn.commit()
        finally:
            conn.close()

        result = dragons_mirror.backfill_dragons(prune=True)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["pruned"], 1)
        self.assertEqual([r["code"] for r in dragons_mirror.get_dragons(DAY)],
                         ["600001"])

    def test_range_filter(self):
        self._seed_source_only({
            DAY: self._sample_rows("600001"),
            DAY2: self._sample_rows("600002"),
        })
        result = dragons_mirror.backfill_dragons(date_from=DAY, date_to=DAY)
        self.assertEqual(result["dates"], [DAY])
        self.assertEqual(result["inserted"], 1)
        self.assertEqual(dragons_mirror.get_dragons(DAY2), [])

    def test_review_fields_are_copied(self):
        self._seed_source_only({DAY: self._sample_rows("600001")})
        with patch("dragon_quant.scan.storage.dragons._mirror_write"):
            db.update_dragon_review(DAY, "600001", buy_date="2026-10-22",
                                    buy_price=10.5, max_return_5d=12.0,
                                    max_return_hold_days=4)
        dragons_mirror.backfill_dragons()
        mirrored = dragons_mirror.get_dragons(DAY)[0]
        self.assertEqual(mirrored["review_status"], "completed")
        self.assertEqual(mirrored["buy_date"], "2026-10-22")
        self.assertEqual(mirrored["buy_price"], 10.5)
        self.assertEqual(mirrored["max_return_5d"], 12.0)
        self.assertEqual(mirrored["max_return_hold_days"], 4)

    def test_updates_changed_business_columns(self):
        self._seed_source_only({DAY: self._sample_rows("600001")})
        dragons_mirror.backfill_dragons()
        # 源库改分（模拟 rebuild 重扫），镜像应跟随
        with patch("dragon_quant.scan.storage.dragons._mirror_write"):
            rows = self._sample_rows("600001")
            rows[0]["composite_score"] = 99.0
            db.save_dragons(DAY, rows, version="1.0")
        result = dragons_mirror.backfill_dragons()
        self.assertEqual(result["updated"], 1)
        self.assertEqual(dragons_mirror.get_dragons(DAY)[0]["composite_score"], 99.0)


class TestMirrorReads(MirrorTestBase):
    def test_get_dragons_by_date_raises_when_source_has_rows(self):
        self._seed_source_only({DAY: self._sample_rows("600001")})
        with self.assertRaises(dragons_mirror.DragonsMirrorStale) as cm:
            dragons_mirror.get_dragons_by_date(DAY)
        self.assertIn("backfill-dragons", str(cm.exception))

    def test_get_dragons_by_date_empty_is_fine_when_source_empty(self):
        self.assertEqual(dragons_mirror.get_dragons_by_date(DAY), [])

    def test_list_dragon_trade_dates_from_mirror(self):
        db.save_dragons(DAY2, self._sample_rows("600002"), version="1.0")
        db.save_dragons(DAY, self._sample_rows("600001"), version="1.0")
        self.assertEqual(dragons_mirror.list_dragon_trade_dates(), [DAY2, DAY])

    def test_stale_dates_only_reports_source_present_mirror_missing(self):
        self._seed_source_only({DAY: self._sample_rows("600001")})
        db.save_dragons(DAY2, self._sample_rows("600002"), version="1.0")
        self.assertEqual(dragons_mirror.stale_dates([DAY, DAY2, "2026-10-18"]),
                         {DAY: 1})

    def test_ensure_populated_raises_with_fix_command(self):
        self._seed_source_only({DAY: self._sample_rows("600001")})
        with self.assertRaises(dragons_mirror.DragonsMirrorEmpty) as cm:
            dragons_mirror.ensure_populated()
        self.assertIn("assets backfill-dragons", str(cm.exception))

    def test_ensure_populated_ok_after_backfill(self):
        self._seed_source_only({DAY: self._sample_rows("600001")})
        dragons_mirror.backfill_dragons()
        dragons_mirror.ensure_populated()      # 不抛

    def test_source_must_be_v2(self):
        with self.assertRaises(dragons_mirror.DragonsMirrorSourceError):
            dragons_mirror.get_dragons(DAY, source="v1")


class TestCandidateLoaderDispatch(unittest.TestCase):
    def test_v2_returns_mirror_loader(self):
        from dragon_quant.review_account.market import candidate_loader
        self.assertIs(candidate_loader("v2"), dragons_mirror.get_dragons_by_date)

    def test_v1_returns_source_loader(self):
        from dragon_quant.review_account.market import candidate_loader
        self.assertIs(candidate_loader("v1"), db.get_dragons_by_date)

    def test_default_is_v2(self):
        from dragon_quant.review_account.market import candidate_loader
        self.assertIs(candidate_loader(), dragons_mirror.get_dragons_by_date)


class TestGuardPropagation(MirrorTestBase):
    """消费方守卫：池构造失败必须显式上抛，不得静默减少买点。"""

    def test_pool_loader_stale_propagates_through_collect_candidates(self):
        from dragon_quant.review_account import market
        from dragon_quant.review_account.models import StrategyConfig

        # 池 loader 只读 T 之前的交易日（DAY2）；该日源库有行而镜像缺行 → fail-fast
        self._seed_source_only({DAY2: self._sample_rows("600001")})
        with self.assertRaises(dragons_mirror.DragonsMirrorStale):
            market.collect_candidates(
                [DAY2, DAY], DAY, StrategyConfig(), dragons_mirror.get_dragons_by_date)

    def test_sell_path_degrades_with_explicit_reason(self):
        """sell 有 try：候选池失败降级为仅持仓判定，文案带 candidate_pool_unavailable。"""
        from dragon_quant.review_account.live_trade import trader as trader_mod

        class _Cfg:
            candidate_lookback_days = 5
            source = "v2"

            def to_json_dict(self):
                from dragon_quant.review_account.models import StrategyConfig
                return StrategyConfig().to_json_dict()

        data = MagicMock()
        data.calendar.return_value = [DAY2, DAY]
        stale_loader = MagicMock(
            side_effect=dragons_mirror.DragonsMirrorStale("源库有行而镜像缺行"))
        with patch.object(trader_mod, "candidate_loader",
                          return_value=stale_loader), \
             patch.object(trader_mod, "build_shadow_state") as shadow, \
             patch.object(trader_mod, "historical_events", return_value=[]), \
             patch.object(trader_mod.signal_store, "list_all_signals", return_value=[]):
            shadow.return_value.positions = []
            shadow.return_value.cash = 100_000.0
            result = trader_mod.LiveTrader(_Cfg(), data=data)._run_sell(
                DAY, 0, None, [DAY2, DAY], 0, False)
        self.assertTrue(any("candidate_pool_unavailable" in d
                            for d in result["degraded"]), result["degraded"])
