"""P1 纯统计 / 矩阵导出测试（方案 §4.7、§8.1）。"""

import csv
import json
import math
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dragon_quant.factors import stats as fstats
from dragon_quant.factors.matrix import (
    CleanConfig, export_matrix, identity_clean,
)
from dragon_quant.factors.types import FactorValue
from dragon_quant.factors import storage as fstore


class TestStats(unittest.TestCase):
    def test_rank_average_ties(self):
        self.assertEqual(fstats.rankdata([3.0, 1.0, 1.0, 2.0]),
                         [4.0, 1.5, 1.5, 3.0])

    def test_spearman_perfect_and_inverse(self):
        x = [1, 2, 3, 4, 5]
        self.assertAlmostEqual(fstats.spearman(x, [10, 20, 30, 40, 50]), 1.0)
        self.assertAlmostEqual(fstats.spearman(x, [50, 40, 30, 20, 10]), -1.0)

    def test_kendall_tau_b_with_ties(self):
        self.assertAlmostEqual(fstats.kendall_tau_b([1, 2, 3, 4], [1, 2, 3, 4]), 1.0)
        # 全部并列 → 分母为 0 → None（不返回 0）
        self.assertIsNone(fstats.kendall_tau_b([1, 1, 1], [1, 2, 3]))

    def test_newey_west_matches_closed_form(self):
        series = [0.1, -0.2, 0.3, 0.05, -0.1, 0.2]
        lag = 2
        n = len(series)
        centre = sum(series) / n
        residuals = [value - centre for value in series]
        variance = sum(r * r for r in residuals) / n
        for offset in range(1, lag + 1):
            gamma = sum(residuals[t] * residuals[t - offset]
                        for t in range(offset, n)) / n
            variance += 2.0 * (1.0 - offset / (lag + 1.0)) * gamma
        expected = centre / math.sqrt(variance / n)
        self.assertAlmostEqual(fstats.newey_west_t(series, lag=lag), expected, places=12)

    def test_ar1_newy_west_is_smaller_than_naive(self):
        values = []
        shock = 0.0
        seed = 1234567
        for index in range(80):
            # 确定性伪随机（LCG），避免依赖 random 版本差异
            seed = (1103515245 * seed + 12345) % (2 ** 31)
            shock = ((seed % 2001) - 1000) / 10000.0
            values.append(0.8 * (values[-1] if values else 0.0) + shock)
        naive = fstats.mean(values) / fstats.stdev(values) * math.sqrt(len(values))
        adjusted = fstats.newey_west_t(values, lag=1)
        self.assertIsNotNone(adjusted)
        self.assertLess(abs(adjusted), abs(naive))

    def test_block_bootstrap_requires_two_blocks(self):
        self.assertIsNone(fstats.block_bootstrap_t([1, 2, 3], ["a", "a", "a"]))
        result = fstats.block_bootstrap_t([1, 2, 3, 4], ["a", "a", "b", "b"])
        self.assertIsNotNone(result)


class MatrixBase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._db_path = str(Path(self._tmpdir.name) / "assets.db")
        self._patch = patch(
            "dragon_quant.storage.assets_db._connect",
            side_effect=self._connect)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self._tmpdir.cleanup()

    def _connect(self, *args, create=True, path=None, **kwargs):
        target = path or self._db_path
        if not create and not Path(target).exists():
            return None
        return sqlite3.connect(target)

    def seed(self, trade_date, values, name="five_dim_composite",
             version="vtest", meta=None):
        """按「日期 × 因子」一次性写入（replace 是整日整因子替换）。"""
        rows = [FactorValue(
            trade_date=trade_date, code=code, factor_name=name,
            factor_version=version, value=value,
            available_at=f"{trade_date}T15:05:00", source="scan",
            source_meta_json=(json.dumps(meta) if meta else None))
            for code, value in values.items()]
        fstore.replace_factor_day(trade_date, name, version, rows)


class TestExportMatrix(MatrixBase):
    def test_sidecar_contract_and_csv(self):
        self.seed("2026-05-19", {"600001": 81.5, "600002": 55.0})
        self.seed("2026-05-20", {"600001": 70.0})
        out = str(Path(self._tmpdir.name) / "matrix.csv")
        payload = export_matrix(
            "20260519", "20260520", names=["five_dim_composite"],
            version="vtest", clean=identity_clean(), fmt="csv", out_path=out)
        self.assertEqual(payload["dates"], ["2026-05-19", "2026-05-20"])
        self.assertEqual(payload["codes"], ["600001", "600002"])
        self.assertEqual(payload["resolved_versions"],
                         {"five_dim_composite": "vtest"})
        self.assertIn("missing_rate_by_factor", payload)
        self.assertIn("coverage_by_date", payload)
        self.assertIn("build_ids", payload)
        self.assertIn("pipeline_version", payload)
        meta = json.loads(Path(out + ".meta.json").read_text(encoding="utf-8"))
        for key in ("dates", "codes", "resolved_versions", "clean",
                    "missing_rate_by_factor", "coverage_by_date", "build_ids",
                    "generated_at", "pipeline_version"):
            self.assertIn(key, meta)
        with open(out, newline="", encoding="utf-8") as handle:
            rows = list(csv.reader(handle))
        self.assertEqual(rows[0], ["date", "code", "five_dim_composite"])
        self.assertEqual(rows[1], ["2026-05-19", "600001", "81.5"])

    def test_mad_zero_skips_without_dividing(self):
        self.seed("2026-05-19", {"600001": 50.0, "600002": 50.0,
                                 "600003": 50.0})
        payload = export_matrix(
            "2026-05-19", "2026-05-19", names=["five_dim_composite"],
            version="vtest",
            clean=CleanConfig(winsorize="mad", normalize="none"))
        self.assertEqual(payload["winsorize_skipped"], "mad_zero")
        self.assertEqual(payload["matrix"]["2026-05-19"]["600001"]
                         ["five_dim_composite"], 50.0)

    def test_rank_normalization_uses_average_ties(self):
        self.seed("2026-05-19", {"600001": 10.0, "600002": 20.0,
                                 "600003": 20.0, "600004": 30.0})
        payload = export_matrix(
            "2026-05-19", "2026-05-19", names=["five_dim_composite"],
            version="vtest",
            clean=CleanConfig(winsorize="none", normalize="rank"))
        row = payload["matrix"]["2026-05-19"]
        self.assertAlmostEqual(row["600001"]["five_dim_composite"],
                               (1 / 4 - 0.5) * 3.46)
        self.assertAlmostEqual(row["600002"]["five_dim_composite"],
                               (2.5 / 4 - 0.5) * 3.46)
        self.assertAlmostEqual(row["600004"]["five_dim_composite"],
                               (4 / 4 - 0.5) * 3.46)

    def test_cleaning_is_per_day_not_full_sample(self):
        # 两天分布完全不同：若用全样本缩尾，第二天会被第一天拉偏
        self.seed("2026-05-19", {"600001": 0.0, "600002": 1.0, "600003": 2.0})
        self.seed("2026-05-20", {"600001": 100.0, "600002": 101.0,
                                 "600003": 102.0})
        payload = export_matrix(
            "2026-05-19", "2026-05-20", names=["five_dim_composite"],
            version="vtest",
            clean=CleanConfig(winsorize="quantile", limits=(0.05, 0.95),
                              normalize="none"))
        # 第二天原始量级必须保留（没有被第一天的全样本分位压扁）
        self.assertGreaterEqual(
            payload["matrix"]["2026-05-20"]["600003"]["five_dim_composite"], 100.0)

    def test_missing_drop_is_complete_case(self):
        self.seed("2026-05-19", {"600001": 80.0, "600002": 60.0})
        self.seed("2026-05-19", {"600001": 70.0}, name="vpa_breakout")
        payload = export_matrix(
            "2026-05-19", "2026-05-19",
            names=["five_dim_composite", "vpa_breakout"], version="vtest",
            clean=CleanConfig(missing="drop", winsorize="none", normalize="none"))
        self.assertNotIn("600002", payload["matrix"]["2026-05-19"])
        self.assertEqual(payload["dropped_rows"], 1)

    def test_rank_single_name_day_skips_normalization(self):
        self.seed("2026-05-19", {"600001": 10.0})
        payload = export_matrix(
            "2026-05-19", "2026-05-19", names=["five_dim_composite"],
            version="vtest",
            clean=CleanConfig(winsorize="none", normalize="rank"))
        self.assertEqual(payload["normalize_skipped"], "insufficient_cross_section")
        self.assertEqual(payload["matrix"]["2026-05-19"]["600001"]
                         ["five_dim_composite"], 10.0)

    def test_explicit_version_without_rows_is_marked_missing(self):
        payload = export_matrix(
            "2026-05-19", "2026-05-19", names=["five_dim_composite"],
            version="v-nonexistent", clean=identity_clean())
        self.assertIn("five_dim_composite", payload["missing_versions"])
        self.assertEqual(payload["matrix"], {})

    def test_unknown_factor_rejected(self):
        with self.assertRaises(Exception):
            export_matrix("2026-05-19", "2026-05-19", names=["nope"],
                          version="vtest", clean=identity_clean())


if __name__ == "__main__":
    unittest.main()
