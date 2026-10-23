"""M3 state / 进程锁测试。"""

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from dragon_quant.assets_sync.errors import LockBusyError
from dragon_quant.assets_sync.state import (
    LOCK_STALE_SECONDS,
    ProcessLock,
    default_paths,
    load_state,
    new_state,
    now_iso,
    save_state,
)


class StateTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()


class TestStateFile(StateTestBase):
    def test_roundtrip_and_atomic_replace(self):
        path = self.root / "assets_sync" / "state.json"
        state = new_state(hostname="host")
        state["partitions"]["2026-10-21"] = {"manifest_id": "x", "kind": "date"}
        save_state(path, state)
        loaded = load_state(path)
        self.assertEqual(loaded["schema"], 2)
        self.assertEqual(loaded["partitions"]["2026-10-21"]["manifest_id"], "x")
        self.assertEqual([p.name for p in path.parent.iterdir()], ["state.json"])

    def test_missing_and_corrupt_reset(self):
        self.assertEqual(load_state(self.root / "missing.json")["partitions"], {})
        path = self.root / "state.json"
        path.write_text("{bad", encoding="utf-8")
        self.assertEqual(load_state(path)["schema"], 2)
        path.write_text(json.dumps({"schema": 99, "dates": {"x": {}}}), encoding="utf-8")
        self.assertEqual(load_state(path)["partitions"], {})

    def test_schema1_migrates_dates_to_partitions(self):
        path = self.root / "state.json"
        path.write_text(json.dumps({
            "schema": 1,
            "hostname": "old-host",
            "updated_at": "2026-10-21T20:00:00+08:00",
            "last_success_trade_date": "2026-10-21",
            "dates": {"2026-10-21": {
                "manifest_id": "m1", "codec": 1,
                "sources": {"prep": {"watermark": "w|1|2"}}}},
        }), encoding="utf-8")
        loaded = load_state(path)
        self.assertEqual(loaded["schema"], 2)
        self.assertEqual(loaded["hostname"], "old-host")
        self.assertEqual(loaded["last_success_trade_date"], "2026-10-21")
        entry = loaded["partitions"]["2026-10-21"]
        self.assertEqual(entry["kind"], "date")
        self.assertEqual(entry["codec"], 1)
        self.assertEqual(entry["sources"]["prep"]["watermark"], "w|1|2")
        # 迁移后保存一律写 schema 2，不再写 dates
        save_state(path, loaded)
        raw = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(raw["schema"], 2)
        self.assertNotIn("dates", raw)

    def test_now_iso_has_shanghai_offset(self):
        self.assertTrue(now_iso().endswith("+08:00"))


class TestProcessLock(StateTestBase):
    def _lock(self, **kwargs):
        return ProcessLock(self.root / "assets_sync.lock", hostname="host", **kwargs)

    def test_mutual_exclusion(self):
        first = self._lock()
        first.acquire()
        try:
            with self.assertRaises(LockBusyError):
                self._lock().acquire()
        finally:
            first.release()
        self.assertFalse(first.path.exists())

    def test_dead_pid_is_stale(self):
        path = self.root / "assets_sync.lock"
        path.mkdir()
        (path / "owner.json").write_text(json.dumps({
            "pid": 12345, "hostname": "host", "started_at": now_iso()}), encoding="utf-8")
        with patch("dragon_quant.assets_sync.state._pid_alive", return_value=False):
            lock = self._lock()
            lock.acquire()
            self.assertTrue((path / "owner.json").exists())
            lock.release()

    def test_live_pid_is_not_stale_even_if_old(self):
        path = self.root / "assets_sync.lock"
        path.mkdir()
        old = datetime.now(timezone.utc) - timedelta(seconds=LOCK_STALE_SECONDS * 4)
        (path / "owner.json").write_text(json.dumps({
            "pid": os.getpid(), "hostname": "host", "started_at": old.isoformat()}),
            encoding="utf-8")
        with patch("dragon_quant.assets_sync.state._pid_alive", return_value=True):
            with self.assertRaises(LockBusyError):
                self._lock().acquire()

    def test_cross_host_uses_age(self):
        path = self.root / "assets_sync.lock"
        path.mkdir()
        old = datetime.now(timezone.utc) - timedelta(seconds=LOCK_STALE_SECONDS * 2)
        (path / "owner.json").write_text(json.dumps({
            "pid": 1, "hostname": "other-host", "started_at": old.isoformat()}),
            encoding="utf-8")
        lock = self._lock()
        lock.acquire()
        lock.release()

    def test_win32_never_calls_os_kill(self):
        path = self.root / "assets_sync.lock"
        path.mkdir()
        old = datetime.now(timezone.utc) - timedelta(seconds=LOCK_STALE_SECONDS * 2)
        (path / "owner.json").write_text(json.dumps({
            "pid": os.getpid(), "hostname": "host", "started_at": old.isoformat()}),
            encoding="utf-8")
        with patch("dragon_quant.assets_sync.state.sys.platform", "win32"), \
             patch("dragon_quant.assets_sync.state.os.kill",
                   side_effect=AssertionError("win32 不得调用 os.kill")):
            lock = self._lock()
            lock.acquire()
            lock.release()


class TestPaths(StateTestBase):
    def test_default_paths_under_data_dir(self):
        paths = default_paths(self.root)
        self.assertEqual(paths.state_path, self.root / "assets_sync" / "state.json")
        self.assertEqual(paths.lock_path, self.root / "assets_sync.lock")
        self.assertEqual(paths.tmp_dir, self.root / "tmp" / "assets_sync")


if __name__ == "__main__":
    unittest.main()
