"""P1 门禁登记的三个低成本测试缺口（P2 顺修）。"""

import json
import random
import sqlite3
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from dragon_quant.infra.factor import evaluation, storage as fstore
from dragon_quant.infra.factor.types import FactorValue


def _calendar(length):
    start = date(2026, 1, 1)
    return [(start + timedelta(days=index)).isoformat() for index in range(length)]


class FakeData:
    def __init__(self, bars, days):
        self.bars = bars
        self.days = sorted(set(days) | {bar["timestamp"][:10]
                                        for series in bars.values()
                                        for bar in series})

    def daily(self, code, start):
        return [bar for bar in self.bars.get(code, [])
                if bar["timestamp"][:10] >= start]

    def calendar(self, start, end):
        return [day for day in self.days if start <= day <= end]


def build_panel(*, days=20, codes=10, seed=3):
    dates = _calendar(days + 2)
    rng = random.Random(seed)
    bars, opens = {}, {}
    for index in range(codes):
        code = f"60{index:04d}"
        price, series = 10.0, []
        for _ in dates:
            series.append(price)
            price *= 1.0 + rng.uniform(-0.05, 0.05)
        opens[code] = series
        bars[code] = [{"timestamp": f"{day} 15:00:00", "open": series[pos],
                       "volume": 1.0, "amount": 1.0}
                      for pos, day in enumerate(dates)]
    labels = {day: {code: (opens[code][pos + 2] / opens[code][pos + 1] - 1) * 100
                    for code in opens}
              for pos, day in enumerate(dates[:days])}
    return bars, dates[:days], labels


class Base(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._db_path = str(Path(self._tmpdir.name) / "assets.db")
        self._patch = patch("dragon_quant.infra.assets.db._connect",
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

    def seed(self, dates, labels, *, name="five_dim_composite", version="vtest",
             meta=None, transform=None):
        for trade_date in dates:
            rows = [FactorValue(
                trade_date=trade_date, code=code, factor_name=name,
                factor_version=version,
                value=transform(label) if transform else label,
                available_at=f"{trade_date}T15:05:00", source="scan",
                source_meta_json=json.dumps(meta) if meta else None)
                for code, label in labels[trade_date].items()]
            fstore.replace_factor_day(trade_date, name, version, rows)


class TestExplicitVersionWithCodeFilter(Base):
    def test_no_rows_for_code_marks_missing_version(self):
        self.seed(["2026-05-19"], {"2026-05-19": {"600001": 80.0}})
        payload = fstore.get_day_values(
            "2026-05-19", names=["five_dim_composite"], codes=["600002"],
            version="vtest")
        self.assertEqual(payload["rows"], 0)
        self.assertEqual(payload["resolved_versions"], {})
        self.assertIn("five_dim_composite", payload["missing_versions"])
        # 有行的 code 仍正常解析
        good = fstore.get_day_values(
            "2026-05-19", names=["five_dim_composite"], codes=["600001"],
            version="vtest")
        self.assertEqual(good["missing_versions"], [])
        self.assertEqual(good["resolved_versions"], {"five_dim_composite": "vtest"})


class TestCoverageUniverseUnavailable(Base):
    def test_empty_scan_db_does_not_fake_coverage(self):
        bars, dates, labels = build_panel()
        self.seed(dates, labels)
        result = evaluation.evaluate_factor(
            "five_dim_composite", dates[0], dates[-1], data=FakeData(bars, dates),
            version="vtest", min_days=5, min_cross_section=3)
        self.assertEqual(result["status"], "evaluated", result.get("reason"))
        self.assertTrue(any("coverage_universe_unavailable" in note
                            for note in result["degraded"]))
        self.assertIsNone(result["coverage"]["mean"])


class TestStrictMixedReportPeriod(Base):
    def test_strict_keeps_observed_samples_and_reports_mixed_periods(self):
        bars, dates, labels = build_panel()
        codes = sorted(labels[dates[0]])
        for position, trade_date in enumerate(dates):
            period = "2026-06-30" if position < len(dates) // 2 else "2026-09-30"
            meta = {
                "pit_basis": "observed",
                "basis_by_code": {code: "observed" for code in codes},
                "period_by_code": {code: period for code in codes},
                "finance_data_versions": [],
            }
            rows = [FactorValue(
                trade_date=trade_date, code=code, factor_name="fin_roe",
                factor_version="vtest", value=labels[trade_date][code],
                available_at=f"{trade_date}T16:40:00", source="fin",
                source_meta_json=json.dumps(meta)) for code in codes]
            fstore.replace_factor_day(trade_date, "fin_roe", "vtest", rows)
        result = evaluation.evaluate_factor(
            "fin_roe", dates[0], dates[-1], data=FakeData(bars, dates),
            version="vtest", min_days=5, min_cross_section=3,
            pit_policy="strict")
        self.assertEqual(result["status"], "evaluated", result.get("reason"))
        self.assertTrue(result["report_period_mixed"])
        self.assertGreater(result["pit_basis_mix"]["observed"], 0)
        self.assertEqual(result["pit_basis_mix"]["statutory_backfilled"], 0)
        self.assertEqual(sum(result["pit_basis_mix"].values()),
                         result["observations"])


if __name__ == "__main__":
    unittest.main()
