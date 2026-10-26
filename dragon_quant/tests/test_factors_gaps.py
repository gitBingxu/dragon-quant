"""P0 遗留低成本分支补测（方案 §11 P0 test_gaps）。"""

import unittest
from unittest.mock import patch

from dragon_quant.infra.factor import engine
from dragon_quant.infra.factor.builtin import five_dim, vpa
from dragon_quant.infra.factor.registry import get_spec
from dragon_quant.infra.factor.types import FactorContext


def _ctx(rows=None, **extra):
    source = {"rows": rows or [], **extra}
    return FactorContext(trade_date="2026-05-19", codes=("600001",),
                         source_rows=source, as_of="2026-05-19 15:30:00")


class TestVpaMalformed(unittest.TestCase):
    def test_missing_name_is_skipped(self):
        frame = vpa.compute_breakout(_ctx([{
            "code": "600001", "factors": [{"score": 88.0}]}]))
        self.assertEqual(frame.values, {})
        self.assertEqual(frame.meta["missing"], 1)

    def test_duplicate_name_is_skipped(self):
        frame = vpa.compute_breakout(_ctx([{
            "code": "600001", "factors": [
                {"name": "breakout", "score": 88.0},
                {"name": "breakout", "score": 99.0}]}]))
        self.assertEqual(frame.values, {})
        self.assertEqual(frame.meta["duplicate_name"], 1)


class TestRawOutput(unittest.TestCase):
    def test_invalid_raw_output_does_not_write(self):
        source = {"status": "ok", "raw_output": "{bad", "scan_id": "s1"}
        with patch("dragon_quant.infra.factor.engine._load_scan_source",
                   return_value=source), \
                patch("dragon_quant.infra.factor.engine.fstore.replace_sector_day") as write:
            result = engine.write_sector_snapshot("2026-05-19")
        self.assertEqual(result["status"], "missing_raw_output")
        write.assert_not_called()


class TestScanSourceFailure(unittest.TestCase):
    def test_read_exception_keeps_old_rows(self):
        spec = get_spec("five_dim_composite")
        with patch("dragon_quant.infra.factor.engine.canonical_scan",
                   side_effect=RuntimeError("db locked")), \
                patch("dragon_quant.infra.factor.engine.fstore.replace_factor_day") as write:
            entry = engine.build_factor(spec, "2026-05-19")
        self.assertEqual(entry["status"], "missing_source")
        self.assertIn("db locked", entry["reason"])
        write.assert_not_called()


class TestTrueDragonFlag(unittest.TestCase):
    @staticmethod
    def _scan_ctx(row):
        return FactorContext(
            trade_date="2026-05-19", codes=("600001",),
            source_rows={"rows_by_code": {"600001": row},
                         "scan_id": "s1", "created_at": "2026-05-19 15:30:00"},
            as_of="2026-05-19 15:30:00")

    def test_false_writes_zero_not_missing(self):
        frame = five_dim.compute_is_true_dragon(
            self._scan_ctx({"code": "600001", "is_true_dragon": False}))
        self.assertEqual(frame.values, {"600001": 0.0})

    def test_null_writes_no_row(self):
        frame = five_dim.compute_is_true_dragon(
            self._scan_ctx({"code": "600001", "is_true_dragon": None}))
        self.assertEqual(frame.values, {})


if __name__ == "__main__":
    unittest.main()
