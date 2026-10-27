"""P1 因子评估测试（方案 §4.6、§8.1）：IC / NW / 分组 / PIT / 1q。"""

import io
import json
import random
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
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
        bar_days = {bar["timestamp"][:10] for series in bars.values()
                    for bar in series}
        self.days = sorted(set(days) | bar_days)

    def daily(self, code, start):
        return [bar for bar in self.bars.get(code, [])
                if bar["timestamp"][:10] >= start]

    def calendar(self, start, end):
        return [day for day in self.days if start <= day <= end]


def build_panel(*, days=30, codes=20, horizon=1, seed=7):
    """构造「因子值 = 前向收益」的完美面板；返回 (bars, factor_dates, labels)。"""
    dates = _calendar(days + horizon + 1)
    rng = random.Random(seed)
    opens = {}
    bars = {}
    for index in range(codes):
        code = f"60{index:04d}"
        price = 10.0
        series = []
        for _ in dates:
            series.append(price)
            price *= 1.0 + rng.uniform(-0.06, 0.06)
        opens[code] = series
        bars[code] = [
            {"timestamp": f"{day} 15:00:00", "open": series[pos],
             "volume": 1.0, "amount": 1.0}
            for pos, day in enumerate(dates)
        ]
    labels = {}
    for pos in range(days):
        day = dates[pos]
        labels[day] = {
            code: (opens[code][pos + 1 + horizon] / opens[code][pos + 1] - 1.0) * 100.0
            for code in opens
        }
    return bars, dates[:days], labels


class EvalBase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._db_path = str(Path(self._tmpdir.name) / "assets.db")
        self._patch = patch(
            "dragon_quant.infra.assets.db._connect",
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

    def seed_values(self, factor_dates, labels, *, name="five_dim_composite",
                    version="vtest", transform=None, meta=None):
        for trade_date in factor_dates:
            rows = []
            for code, label in labels[trade_date].items():
                value = transform(label, trade_date, code) if transform else label
                rows.append(FactorValue(
                    trade_date=trade_date, code=code, factor_name=name,
                    factor_version=version, value=value,
                    available_at=f"{trade_date}T15:05:00", source="scan",
                    source_meta_json=(json.dumps(meta) if meta else None)))
            fstore.replace_factor_day(trade_date, name, version, rows)

    def evaluate(self, data, *, name="five_dim_composite",
                 date_from=None, date_to=None, **kwargs):
        params = dict(version="vtest", min_days=10, min_cross_section=5,
                      horizon=1, groups=2)
        params.update(kwargs)
        return evaluation.evaluate_factor(
            name, date_from, date_to, data=data, **params)


class TestRankIC(EvalBase):
    def test_perfect_factor_ic_is_one(self):
        bars, dates, labels = build_panel()
        self.seed_values(dates, labels)
        result = self.evaluate(FakeData(bars, dates), date_from=dates[0],
                               date_to=dates[-1])
        self.assertEqual(result["status"], "evaluated", result.get("reason"))
        self.assertAlmostEqual(result["ic"]["mean"], 1.0, places=9)
        self.assertEqual(result["ic"]["n_days"], len(dates))
        self.assertEqual(result["observations"], len(dates) * 20)
        self.assertEqual(result["label_rule"], "next_open_to_next_open")
        self.assertEqual(result["universe"], "limit_up_candidates")
        self.assertEqual(result["nw_lag"], 0)
        self.assertEqual(result["effective_independent_days"], len(dates))
        self.assertGreater(result["groups"][1]["mean_return"],
                           result["groups"][0]["mean_return"])
        self.assertFalse(result["long_short"]["tradable"])

    def test_inverse_factor_ic_is_minus_one(self):
        bars, dates, labels = build_panel()
        self.seed_values(dates, labels, transform=lambda value, *_: -value)
        result = self.evaluate(FakeData(bars, dates), date_from=dates[0],
                               date_to=dates[-1])
        self.assertAlmostEqual(result["ic"]["mean"], -1.0, places=9)
        # 取反后：因子最低组对应最高收益（group 1 是因子值最低组）
        self.assertGreater(result["groups"][0]["mean_return"],
                           result["groups"][1]["mean_return"])

    def test_random_factor_ic_near_zero(self):
        bars, dates, labels = build_panel(days=40, seed=11)
        rng = random.Random(99)

        def shuffle(label, trade_date, code):
            codes = sorted(labels[trade_date])
            return labels[trade_date][codes[rng.randrange(len(codes))]]

        self.seed_values(dates, labels, transform=shuffle)
        result = self.evaluate(FakeData(bars, dates), date_from=dates[0],
                               date_to=dates[-1])
        self.assertLess(abs(result["ic"]["mean"]), 0.2)

    def test_high_tie_ratio_and_kendall(self):
        bars, dates, labels = build_panel()
        self.seed_values(
            dates, labels,
            transform=lambda value, trade_date, code:
                1.0 if int(code[-2:]) % 2 else 0.0)
        result = self.evaluate(FakeData(bars, dates), date_from=dates[0],
                               date_to=dates[-1])
        self.assertTrue(any("high_tie_ratio" in warning
                            for warning in result["warnings"]))
        self.assertIsNotNone(result["ic"]["kendall_tau_b"])

    def test_insufficient_data(self):
        bars, dates, labels = build_panel(days=5)
        self.seed_values(dates, labels)
        result = self.evaluate(FakeData(bars, dates), date_from=dates[0],
                               date_to=dates[-1], min_days=20)
        self.assertEqual(result["status"], "insufficient_data")
        self.assertIn("min_days", result["reason"])

    def test_labels_start_after_available_at(self):
        days = ["2026-05-19", "2026-05-20", "2026-05-21"]
        bars = {"600001": {
            days[0]: 10.0, days[1]: 20.0, days[2]: 22.0}}
        mapping = {day: {"open": bars["600001"][day], "volume": 1.0,
                         "amount": 1.0, "timestamp": f"{day} 15:00:00"}
                   for day in days}
        # 收盘后可知 → 只能用次日开盘起算：22/20-1 = 10%
        self.assertAlmostEqual(
            evaluation._forward_return("2026-05-19T15:05:00", 1, days, mapping),
            10.0)
        # 竞价可知（09:25）→ 当日开盘可成交：20/10-1 = 100%
        self.assertAlmostEqual(
            evaluation._forward_return("2026-05-19T09:25:00", 1, days, mapping),
            100.0)
        # 停牌（量额双零）→ 剔除
        mapping[days[1]]["volume"] = 0.0
        mapping[days[1]]["amount"] = 0.0
        self.assertIsNone(
            evaluation._forward_return("2026-05-19T15:05:00", 1, days, mapping))


class TestPitPolicy(EvalBase):
    def _seed_fin(self, dates, labels, *, all_backfilled=False):
        periods = ["2026-06-30" if index < len(dates) // 2 else "2026-09-30"
                   for index in range(len(dates))]
        for position, trade_date in enumerate(dates):
            basis = {}
            period = {}
            for code in labels[trade_date]:
                index = int(code[-2:])
                basis[code] = ("statutory_backfilled"
                               if all_backfilled or index >= 10 else "observed")
                period[code] = periods[position]
            meta = {
                "pit_basis": ("statutory_backfilled" if all_backfilled else "mixed"),
                "basis_by_code": basis,
                "period_by_code": period,
                "finance_data_versions": [{
                    "period": periods[position], "file_md5": "m1",
                    "first_seen_at": "2026-08-01T10:00:00",
                    "available_at": "2026-08-31T23:59:59"}],
                "retracted_count": 1,
            }
            rows = [FactorValue(
                trade_date=trade_date, code=code, factor_name="fin_roe",
                factor_version="vtest", value=label,
                available_at=f"{trade_date}T16:40:00", source="fin",
                source_meta_json=json.dumps(meta))
                for code, label in labels[trade_date].items()]
            fstore.replace_factor_day(trade_date, "fin_roe", "vtest", rows)

    def test_strict_excludes_backfilled_and_reports_both_samples(self):
        bars, dates, labels = build_panel(days=30)
        self._seed_fin(dates, labels)
        data = FakeData(bars, dates)
        strict = evaluation.evaluate_factor(
            "fin_roe", dates[0], dates[-1], version="vtest", data=data,
            min_days=5, min_cross_section=5, pit_policy="strict")
        research = evaluation.evaluate_factor(
            "fin_roe", dates[0], dates[-1], version="vtest", data=data,
            min_days=5, min_cross_section=5, pit_policy="research")
        self.assertEqual(strict["status"], "evaluated")
        self.assertEqual(research["status"], "evaluated")
        self.assertEqual(strict["pit_basis_mix"]["statutory_backfilled"], 0)
        self.assertEqual(sum(research["pit_basis_mix"].values()),
                         research["observations"])
        self.assertEqual(strict["pit_policy_samples"]["strict"], strict["observations"])
        self.assertGreater(strict["pit_policy_samples"]["research"],
                           strict["pit_policy_samples"]["strict"])
        self.assertTrue(research["restatement_unverifiable"])
        self.assertFalse(research["model_snapshot_allowed"])
        self.assertIn("by_report_period", research)
        self.assertTrue(research["report_period_mixed"])
        self.assertTrue(any("mixed_report_period" in warning
                            for warning in research["warnings"]))
        self.assertTrue(research["finance_data_versions"])
        # retracted_count 是当日因子级常量，必须按日期去重（不能被 code 数放大）
        self.assertEqual(research["retracted_count"], len(dates))

    def test_1q_extra_outputs(self):
        bars, dates, labels = build_panel(days=30)
        self._seed_fin(dates, labels)
        result = evaluation.evaluate_factor(
            "fin_roe", dates[0], dates[-1], version="vtest",
            data=FakeData(bars, dates), min_days=5, min_cross_section=5,
            pit_policy="research")
        self.assertIn("effective_independent_observations", result)
        self.assertIn("blocks", result["effective_independent_observations"])
        self.assertIn("report_period_block_t", result["ic"])
        self.assertEqual(result["ic"]["t_stat_label"], "pseudo_independent")
        self.assertTrue(any("pseudo_independent" in warning
                            for warning in result["warnings"]))

    def test_strict_with_zero_backfilled_samples_is_insufficient(self):
        bars, dates, labels = build_panel(days=12)
        self._seed_fin(dates, labels, all_backfilled=True)
        result = evaluation.evaluate_factor(
            "fin_roe", dates[0], dates[-1], version="vtest",
            data=FakeData(bars, dates), min_days=5, min_cross_section=5,
            pit_policy="strict")
        self.assertEqual(result["status"], "insufficient_data")
        self.assertIn("strict", result["reason"])

    def test_groups_five_rejected(self):
        bars, dates, labels = build_panel(days=3)
        with self.assertRaises(ValueError):
            evaluation.evaluate_factor(
                "five_dim_composite", dates[0], dates[-1], version="vtest",
                data=FakeData(bars, dates), groups=5)


class TestCliExportContract(EvalBase):
    def _run(self, argv):
        from dragon_quant import cli
        out, err = io.StringIO(), io.StringIO()
        code = 0
        with patch("sys.argv", ["dragon-quant", *argv]), \
             redirect_stdout(out), redirect_stderr(err):
            try:
                cli.main()
            except SystemExit as exc:
                code = int(exc.code or 0)
        return code, out.getvalue(), err.getvalue()

    def test_export_json_writes_sidecar(self):
        self.seed_values(["2026-05-19"],
                         {"2026-05-19": {"600001": 1.0, "600002": 2.0}})
        out = str(Path(self._tmpdir.name) / "m.csv")
        code, text, _ = self._run([
            "factor", "export", "--from", "20260519", "--to", "20260519",
            "--name", "five_dim_composite", "--version", "vtest",
            "--out", out, "--json"])
        self.assertEqual(code, 0, text)
        payload = json.loads(text)
        self.assertEqual(payload["mode"], "export")
        self.assertTrue(Path(out + ".meta.json").exists())

    def test_eval_cli_contract_real_path_exit_zero(self):
        bars, dates, labels = build_panel(days=25)
        self.seed_values(dates, labels)
        fake = FakeData(bars, dates)
        with patch("dragon_quant.review_account.main.MarketData",
                   return_value=fake):
            code, text, err = self._run([
                "factor", "eval", "--name", "five_dim_composite",
                "--from", dates[0], "--to", dates[-1],
                "--version", "vtest", "--json"])
        self.assertEqual(code, 0, err)
        payload = json.loads(text)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["status"], "evaluated")

    def test_export_missing_name_exit_2(self):
        code, _, err = self._run([
            "factor", "export", "--from", "20260519", "--to", "20260519",
            "--out", "/tmp/x.csv"])
        self.assertEqual(code, 2)
        self.assertIn("❌", err)


if __name__ == "__main__":
    unittest.main()
