"""B：``assets backfill-stock-daily`` 单测（方案 §10.1，临时库 + fake tdx，零网络）。

覆盖：dry-run 零写零 provider / 检测 missing-stale / 合并保留未涉及 code /
新建行 partial=True / 既有行作用域字段不动 / transport 失败不写库 / --codes
跳过检测 / 二次运行幂等 / 周末目标日 / 参数与环境错误 / 回填后窗口内可读（B2）。
"""

from __future__ import annotations

import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from dragon_quant.infra.assets import db as assets_db
from dragon_quant.infra.assets.stock_daily_backfill import (
    MAX_LOOKBACK_DAYS,
    backfill_stock_daily,
    normalize_codes,
)
from dragon_quant.infra.models import KBar
from dragon_quant.review_account.prep import collectors

CST = timezone(timedelta(hours=8))
DAY = "2026-10-21"
PRIOR = "2026-10-20"


def _bar(day: str, close: float = 10.0) -> KBar:
    timestamp = int(datetime.fromisoformat(day + "T15:00:00")
                    .replace(tzinfo=CST).timestamp() * 1000)
    return KBar(timestamp=timestamp, volume=10_000.0, open=close - 0.1,
                high=close + 0.1, low=close - 0.2, close=close, chg=0.0,
                pct=0.1, turnover=1.5, amount=1e7)


class FakeTdx:
    def __init__(self, bars=None, *, fail=False):
        self.bars = bars or {}
        self.fail = fail
        self.calls: list[tuple] = []

    def batch_get_daily_klines(self, codes, days=120, end_date=None):
        self.calls.append((list(codes), days, end_date))
        if self.fail:
            raise RuntimeError("daily transport down")
        return {code: self.bars.get(code, []) for code in codes}


def _args(**overrides):
    base = {"dry_run": False, "days": 10, "codes": None, "json": False}
    base.update(overrides)
    return SimpleNamespace(**base)


def _services(tdx):
    return SimpleNamespace(providers=SimpleNamespace(tdx=tdx))


class _Base:
    def setup_method(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "assets.db"
        self._patches = []

    def teardown_method(self):
        for patcher in reversed(self._patches):
            patcher.stop()
        self._tmp.cleanup()

    def _connect(self, create=True, path=None, *args, **kwargs):
        target = Path(path) if path else self.db_path
        if create is False and not target.exists():
            return None
        return sqlite3.connect(target)

    def _install(self, now: str = "2026-10-21T18:30:00+08:00"):
        self._patches.append(patch(
            "dragon_quant.infra.assets.db._connect",
            side_effect=self._connect))
        self._patches[-1].start()
        moment = datetime.fromisoformat(now)
        self._patches.append(patch(
            "dragon_quant.infra.assets.stock_daily_backfill.clock.now",
            side_effect=lambda tz=None: moment.astimezone(tz) if tz else moment))
        self._patches[-1].start()

    def _seed_stock_daily(self, trade_date=DAY, *, rows=None,
                          requested=("600009",), coverage=None):
        rows = rows if rows is not None else {
            "600009": [{"date": "2026-10-20", "open": 9.0, "high": 9.2,
                        "low": 8.9, "close": 9.1, "volume": 100.0,
                        "amount": 1e6, "turnover": 1.0}],
        }
        requested = list(requested)
        assets_db.save_prep_assets(trade_date, {"stock_daily": {
            "as_of": trade_date,
            "trade_date": trade_date,
            "requested_codes": requested,
            "returned_codes": [code for code in requested if code in rows],
            "missing_codes": [code for code in requested if code not in rows],
            "rows": rows,
            "coverage": coverage or {"requested": len(requested),
                                     "returned": len(requested),
                                     "missing": [], "ratio": 1.0},
            "truncated": False,
            "review_codes": [],
            "review_health": {"status": "ok", "note": None, "missing": []},
        }})

    def _seed_snapshot(self, trade_date=PRIOR, codes=()):
        assets_db.save_prep_snapshot(trade_date, {
            "review_candidates": [{"code": code, "position_pct": 5}
                                  for code in codes]})


class TestBackfillStockDaily(_Base):
    def test_dry_run_reports_gap_without_write_or_provider(self):
        self._install()
        self._seed_stock_daily()
        self._seed_snapshot(codes=["600001", "600002"])
        before = assets_db.get_prep_asset(DAY, "stock_daily")
        tdx = FakeTdx()
        payload = backfill_stock_daily(_args(dry_run=True), None)

        assert payload["ok"] is True and payload["dry_run"] is True
        assert payload["target_date"] == DAY
        assert payload["latest_bar_date"] == "2026-10-20"
        assert payload["detected"] == ["600001", "600002"]
        assert payload["missing"] == ["600001", "600002"]
        assert payload["fetched"] == [] and payload["updated"] == []
        assert tdx.calls == []
        assert assets_db.get_prep_asset(DAY, "stock_daily") == before

    def test_backfill_merges_and_preserves_unrelated_code(self):
        self._install()
        self._seed_stock_daily()
        self._seed_snapshot(codes=["600001", "600002"])
        before = assets_db.get_prep_asset(DAY, "stock_daily")
        tdx = FakeTdx({"600001": [_bar("2026-10-20"), _bar("2026-10-21")],
                       "600002": [_bar("2026-10-21")]})
        payload = backfill_stock_daily(_args(), _services(tdx))

        assert payload["ok"] is True
        assert payload["fetched"] == ["600001", "600002"]
        assert payload["updated"] == ["600001", "600002"]
        assert payload["skipped"] == []
        assert payload["failed"] == []
        assert tdx.calls == [(["600001", "600002"], 120, DAY)]

        after = assets_db.get_prep_asset(DAY, "stock_daily")
        assert set(after["rows"]) == {"600009", "600001", "600002"}
        # 未涉及 code 原样保留。
        assert after["rows"]["600009"] == before["rows"]["600009"]
        # 既有行的 review 作用域字段一律不动。
        for key in ("requested_codes", "returned_codes", "missing_codes",
                    "coverage", "review_health", "as_of", "trade_date"):
            assert after[key] == before[key], key
        assert after["backfill"]["returned"] == ["600001", "600002"]
        assert "partial" not in after

        # B2：回填后缺 bar 候选在评估窗口内可读。
        window = assets_db.list_stock_daily_bars(
            ["600001"], "2026-01-01", "2026-12-31")
        assert [bar["date"] for bar in window["rows"]["600001"]] == [
            "2026-10-20", "2026-10-21"]

    def test_new_row_is_partial_and_flagged_by_asset_health(self):
        self._install()
        self._seed_snapshot(codes=["600001"])
        tdx = FakeTdx({"600001": [_bar("2026-10-21")]})
        payload = backfill_stock_daily(_args(), _services(tdx))

        assert payload["target_date"] == DAY
        created = assets_db.get_prep_asset(DAY, "stock_daily")
        assert created["partial"] is True
        assert created["requested_codes"] == ["600001"]
        assert created["rows"]["600001"][0]["date"] == "2026-10-21"
        assert created["backfill"]["lookback_days"] == 10
        assert collectors.asset_health(
            "stock_daily", created, "ok", DAY) == "partial_payload"

    def test_corrupt_existing_payload_is_replaced_with_audit(self):
        self._install()
        # 先建库，再把目标行改成损坏 JSON（save_prep_assets 不接受非法 JSON）。
        self._seed_stock_daily()
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                "UPDATE prep_assets SET payload_json='{broken' "
                "WHERE trade_date=? AND asset_type='stock_daily'", (DAY,))
            conn.commit()
        finally:
            conn.close()
        self._seed_snapshot(codes=["600001"])
        payload = backfill_stock_daily(
            _args(), _services(FakeTdx({"600001": [_bar("2026-10-21")]})))

        assert payload["ok"] is True
        assert payload["warnings"] == ["existing_payload_corrupt"]
        created = assets_db.get_prep_asset(DAY, "stock_daily")
        assert created["partial"] is True
        assert "previous_corrupt" in created["backfill"]

    def test_partial_row_never_grants_pool_bar_limit(self):
        self._install()
        # partial 行的 requested_codes 是补采 codes，不是池清单 → 仍只存 20 根。
        assets_db.save_prep_assets(DAY, {"stock_daily": {
            "as_of": DAY, "trade_date": DAY,
            "requested_codes": ["600001"],
            "rows": {"600009": [{"date": "2026-10-21", "close": 9.1}]},
            "coverage": {"requested": 1, "returned": 1, "missing": [],
                         "ratio": 1.0},
            "review_codes": [], "partial": True,
            "review_health": {"status": "ok", "note": None, "missing": []},
        }})
        self._seed_snapshot(codes=["600001"])
        bars = [_bar((datetime(2026, 10, 21) - timedelta(days=24 - index))
                     .strftime("%Y-%m-%d")) for index in range(25)]
        payload = backfill_stock_daily(
            _args(), _services(FakeTdx({"600001": bars})))

        assert payload["updated"] == ["600001"]
        after = assets_db.get_prep_asset(DAY, "stock_daily")
        assert len(after["rows"]["600001"]) == 20
        assert after["partial"] is True

    def test_transport_failure_writes_nothing(self):
        self._install()
        self._seed_stock_daily()
        self._seed_snapshot(codes=["600001"])
        before = assets_db.get_prep_asset(DAY, "stock_daily")
        tdx = FakeTdx(fail=True)
        payload = backfill_stock_daily(_args(), _services(tdx))

        assert payload["ok"] is False
        assert payload["failed"][0]["kind"] == "transport"
        assert payload["updated"] == []
        assert assets_db.get_prep_asset(DAY, "stock_daily") == before

    def test_explicit_codes_skip_detection(self):
        self._install()
        self._seed_snapshot()          # 建库但窗口为空：证明 --codes 不依赖检测
        tdx = FakeTdx({"600123": [_bar("2026-10-21")],
                       "000456": [_bar("2026-10-21")]})
        args = _args(codes="SH600123, sz000456,600123")
        payload = backfill_stock_daily(args, _services(tdx))

        assert payload["window"]["codes"] == []
        assert payload["detected"] == ["600123", "000456"]
        assert payload["updated"] == ["600123", "000456"]
        assert tdx.calls[0][0] == ["600123", "000456"]

    def test_second_run_is_idempotent(self):
        self._install()
        self._seed_stock_daily()
        self._seed_snapshot(codes=["600001", "600002"])
        bars = {"600001": [_bar("2026-10-21")],
                "600002": [_bar("2026-10-21")]}
        first = backfill_stock_daily(_args(), _services(FakeTdx(bars)))
        assert first["detected"] == ["600001", "600002"]
        assert first["updated"] == ["600001", "600002"]

        second = backfill_stock_daily(_args(), _services(FakeTdx(bars)))
        assert second["detected"] == []
        assert second["updated"] == []
        assert second["window"]["codes"] == ["600001", "600002"]

    def test_skipped_when_no_new_bars(self):
        self._install()
        self._seed_stock_daily()
        self._seed_snapshot(codes=["600001"])
        tdx = FakeTdx({"600001": []})
        payload = backfill_stock_daily(_args(), _services(tdx))

        assert payload["fetched"] == [] and payload["updated"] == []
        assert payload["skipped"] == ["600001"]
        assert payload["ok"] is True
        # 空结果不覆盖：既有行保持原样，不追加 600001。
        kept = assets_db.get_prep_asset(DAY, "stock_daily")
        assert set(kept["rows"]) == {"600009"}
        assert "backfill" not in kept

    def test_weekend_targets_latest_local_bar_date(self):
        self._install(now="2026-10-24T10:00:00+08:00")
        self._seed_stock_daily("2026-10-23", rows={
            "600009": [{"date": "2026-10-23", "close": 9.1}],
        })
        self._seed_snapshot("2026-10-22", codes=["600001"])
        tdx = FakeTdx({"600001": [_bar("2026-10-23")]})
        payload = backfill_stock_daily(_args(), _services(tdx))

        assert payload["target_date"] == "2026-10-23"
        assert tdx.calls[0][2] == "2026-10-23"
        assert assets_db.get_prep_asset("2026-10-24", "stock_daily") is None
        assert assets_db.get_prep_asset("2026-10-23", "stock_daily") is not None

    def test_argument_and_environment_errors_are_classified(self):
        self._install()
        self._seed_stock_daily()
        self._seed_snapshot(codes=["600001"])

        bad_code = backfill_stock_daily(_args(codes="ABC"), None)
        assert bad_code["failed"][0]["kind"] == "args"
        too_large = backfill_stock_daily(
            _args(days=MAX_LOOKBACK_DAYS + 1), None)
        assert too_large["failed"][0]["kind"] == "args"
        too_small = backfill_stock_daily(_args(days=0), None)
        assert too_small["failed"][0]["kind"] == "args"

        # 无 assets.db：env 错误。
        self._tmp.cleanup()
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "assets.db"
        missing_db = backfill_stock_daily(_args(), None)
        assert missing_db["failed"][0]["kind"] == "env"

    def test_no_detected_returns_ok_without_provider_call(self):
        self._install()
        self._seed_stock_daily()
        self._seed_snapshot(codes=["600009"])
        tdx = FakeTdx()
        payload = backfill_stock_daily(_args(), _services(tdx))
        assert payload["ok"] is True
        assert payload["detected"] == []
        assert tdx.calls == []


def test_normalize_codes_rejects_non_six_digit():
    try:
        normalize_codes("60001")
    except ValueError as exc:
        assert "非法代码" in str(exc)
    else:  # pragma: no cover - 断言失败路径
        raise AssertionError("expected ValueError")


if __name__ == "__main__":
    import unittest

    unittest.main()
