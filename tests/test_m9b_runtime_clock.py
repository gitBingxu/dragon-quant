"""M9B 聚焦测试：M9B1 review_account / live_trade / prep / auction / utils.trading，
M9B2 factors / cache / logging / storage。

每个用例用 ``FixedClock`` 把 ``runtime.clock`` 钉在一个时刻，证明当前时间取点
来自 clock 而不是真实 ``datetime.now``；时间语义（naive/aware、16:40、午休、
缓存 today、交易日 today）保持不变。
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from dragon_quant.auction.dates import _today
from dragon_quant.auction.recompute import run_auction_recompute
from dragon_quant.live_trade.trader import LiveTrader
from dragon_quant.models.types import KBar
from dragon_quant.review_account.data import MarketData, historical_day
from dragon_quant.review_account.market import SHANGHAI, at
from dragon_quant.review_account.models import StrategyConfig
from dragon_quant.review_account.prep import (
    PrepError, _collect_all, _validate_trade_date,
    rebuild_dragons_for_recompute, run_prep,
)
from dragon_quant.review_account.prep.collectors import CollectorSpec
from dragon_quant.runtime import clock
from dragon_quant.utils.trading import build_trade_calendar


def sh(day: str, hour: int, minute: int, second: int = 0) -> datetime:
    return datetime.fromisoformat(day).replace(
        hour=hour, minute=minute, second=second, tzinfo=SHANGHAI)


@contextmanager
def fixed(moment: datetime):
    previous = clock.set_clock(clock.FixedClock(moment))
    try:
        yield
    finally:
        clock.set_clock(previous)


def daily_bar(day: str, close: float = 10.0) -> KBar:
    return KBar(at(day, "15:00"), 1000, close, close + 0.2, close - 0.2,
                close, 0, 0, 1.0, 1.0e6)


class _KlineProvider:
    def __init__(self, bars):
        self.bars = list(bars)

    def get_kline(self, code, days=90, fq_type="normal"):
        return list(self.bars)


class _Cache:
    def load_for_trade_date(self, *args, **kwargs):
        return None

    def set_for_trade_date(self, *args, **kwargs):
        return None


class _TraderData:
    def __init__(self, days):
        self.days = list(days)

    def calendar(self, start, end, include_today=False):
        return [d for d in self.days if start <= d <= end]


# ─── review_account/data.py ────────────────────────────────────────────────


def test_marketdata_calendar_and_historical_day_use_runtime_clock():
    days = ["2026-09-09", "2026-09-10", "2026-09-11"]
    data = MarketData(provider=_KlineProvider([daily_bar(d) for d in days]),
                      cache=_Cache(), offline=False)
    with fixed(sh("2026-09-11", 10, 0)):
        assert historical_day("2026-09-10") is True
        assert historical_day("2026-09-11") is False
        # 10:00 < 15:05 且 include_today=False → 当日不入历
        assert data.calendar("2026-09-09", "2026-09-11") == days[:2]
        assert data.calendar("2026-09-09", "2026-09-11",
                             include_today=True) == days


# ─── live_trade/trader.py ──────────────────────────────────────────────────


def test_trader_time_gate_uses_the_runtime_clock():
    trader = LiveTrader(StrategyConfig(), source="v2",
                        data=_TraderData(["2026-09-11"]))
    with fixed(sh("2026-09-11", 12, 0)), \
            patch.object(LiveTrader, "_run_buy", return_value={}):
        with pytest.raises(ValueError) as exc:
            trader.buy("2026-09-11")
    assert "午间休市" in str(exc.value)


def test_trader_legacy_datetime_patch_still_drives_the_gate():
    """旧 patch 目标 ``trader.datetime`` 仍必须影响时间闸门（非无效别名）。"""
    trader = LiveTrader(StrategyConfig(), source="v2",
                        data=_TraderData(["2026-09-11"]))
    fake = MagicMock()
    fake.strptime = datetime.strptime
    fake.now.return_value = sh("2026-09-11", 12, 0)
    # clock 给 10:00（交易时段内），旧 patch 给 12:00：patched 值必须生效。
    with fixed(sh("2026-09-11", 10, 0)), \
            patch("dragon_quant.live_trade.trader.datetime", fake), \
            patch.object(LiveTrader, "_run_buy", return_value={}):
        with pytest.raises(ValueError) as exc:
            trader.buy("2026-09-11")
    assert "午间休市" in str(exc.value)


# ─── utils/trading.py ──────────────────────────────────────────────────────


def test_build_trade_calendar_uses_the_runtime_clock():
    provider = _KlineProvider([daily_bar("2026-09-10"), daily_bar("2026-09-11")])
    with fixed(sh("2026-09-11", 10, 0)), \
            patch("dragon_quant.providers.xueqiu.XueqiuProvider",
                  lambda: provider):
        assert build_trade_calendar("2026-09-01", "2026-09-11") == {"2026-09-10"}
    with fixed(sh("2026-09-11", 16, 0)), \
            patch("dragon_quant.providers.xueqiu.XueqiuProvider",
                  lambda: provider):
        assert build_trade_calendar("2026-09-01", "2026-09-11") == {
            "2026-09-10", "2026-09-11"}


# ─── auction/dates.py + recompute.py ───────────────────────────────────────


def test_auction_today_and_recompute_future_gate_use_the_runtime_clock():
    with fixed(sh("2026-09-11", 9, 30)):
        assert _today() == "2026-09-11"
        outcome = run_auction_recompute("20260912", today=None)
    assert outcome.exit_code == 1
    assert "未来日期不可重算" in outcome.payload["error"]
    assert "2026-09-11" in outcome.payload["error"]


# ─── review_account/prep/service.py ────────────────────────────────────────


def _fake_tdx(days):
    bars = [SimpleNamespace(timestamp=int(sh(d, 15, 0).timestamp() * 1000))
            for d in days]

    class _Tdx:
        def get_kline(self, code, days=120):
            return bars

    return _Tdx()


def test_prep_gate_uses_the_runtime_clock():
    tdx = _fake_tdx(["2026-09-10", "2026-09-11"])
    with fixed(sh("2026-09-11", 15, 30)):
        with pytest.raises(PrepError) as exc:
            _validate_trade_date("2026-09-11", tdx)
        assert "16:40" in str(exc.value)
    with fixed(sh("2026-09-11", 16, 45)):
        assert _validate_trade_date("2026-09-11", tdx) == [
            "2026-09-10", "2026-09-11"]


def test_prep_recompute_future_date_uses_the_runtime_clock():
    with fixed(sh("2026-09-11", 10, 0)):
        with pytest.raises(PrepError) as exc:
            run_prep("2026-09-12", recompute=True)
    assert "未来日期" in str(exc.value)


# ─── review_account/prep/collectors.py ─────────────────────────────────────


def test_collect_all_derives_today_from_the_runtime_clock():
    probe = CollectorSpec(
        collect=lambda ctx: {"as_of": ctx.today, "rows": [{"probe": 1}]},
        same_day_only=True)
    services = SimpleNamespace(providers=SimpleNamespace(
        tdx=object(), ths=object(), tencent=object(), xueqiu=object()))
    with fixed(sh("2026-09-11", 10, 0)), \
            patch.dict("dragon_quant.review_account.prep.ASSET_COLLECTORS",
                       {"probe": probe}, clear=False):
        payloads, report = _collect_all(
            "2026-09-11", window_trade_dates=["2026-09-11"],
            asset_types=["probe"], services=services)
    assert report["probe"]["status"] == "ok"
    assert payloads["probe"]["as_of"] == "2026-09-11"


# ─── review_account/prep/rebuild.py ────────────────────────────────────────


def test_prep_rebuild_rebuilt_at_uses_the_runtime_clock():
    stored = {"dragons": {
        "trade_date": "2026-09-11",
        "window_trade_dates": ["2026-09-11"],
        "days": [{"trade_date": "2026-09-11", "count": 0, "status": "empty"}],
        "rows": [],
    }}
    mirror = SimpleNamespace(list_dragon_trade_dates=lambda: [])
    with fixed(sh("2026-09-11", 9, 30)), \
            patch("dragon_quant.review_account.prep.dragons_mirror", mirror), \
            patch("dragon_quant.review_account.prep._read_dragon_day",
                  lambda day: []), \
            patch("dragon_quant.review_account.prep._stale_dates",
                  lambda dates: {}):
        payload = rebuild_dragons_for_recompute("2026-09-11", stored)
    assert payload is not None
    assert payload["rebuilt_at"] == "2026-09-11 09:30:00"


# ─── factors/engine.py ─────────────────────────────────────────────────────


class _DurationClock:
    """Only ``monotonic`` matters: wall calls stay on the frozen moment."""

    def __init__(self, moment: datetime, monotonic: float) -> None:
        self._moment = moment
        self._monotonic = monotonic

    def now(self, tz=None):  # noqa: ANN001 - test stub
        return self._moment if tz is None else self._moment.astimezone(tz)

    def today(self, tz=None):  # noqa: ANN001 - test stub
        return self.now(tz).date()

    def stamp(self, fmt="%Y%m%d_%H%M%S", tz=None):  # noqa: ANN001 - test stub
        return self.now(tz).strftime(fmt)

    def monotonic(self) -> float:
        return self._monotonic


def test_engine_audit_timestamps_and_timezone_probe_use_the_runtime_clock():
    from dragon_quant.factors import engine as fengine

    with fixed(sh("2026-09-11", 15, 30)):
        assert fengine._now_str() == "2026-09-11T15:30:00"
        assert fengine.timezone_warning() is None
    with fixed(datetime(2026, 9, 11, 7, 30, tzinfo=timezone.utc)):
        warning = fengine.timezone_warning()
    assert warning is not None
    assert "UTC 偏移 +0h" in warning


def test_engine_elapsed_uses_clock_monotonic_not_wall_clock():
    from dragon_quant.factors import engine as fengine

    previous = clock.set_clock(
        _DurationClock(sh("2026-09-11", 15, 30), monotonic=10.25))
    try:
        assert fengine._elapsed_ms(10.0) == 250.0
    finally:
        clock.set_clock(previous)


# ─── factors/matrix.py ─────────────────────────────────────────────────────


def test_matrix_generated_at_uses_the_runtime_clock():
    from dragon_quant.factors import matrix as fmatrix

    with fixed(sh("2026-09-11", 15, 30)):
        assert fmatrix._now() == "2026-09-11T15:30:00"


# ─── factors/service.py ────────────────────────────────────────────────────


def test_service_today_and_fin_snapshot_gate_use_the_runtime_clock():
    from dragon_quant.factors import service as fservice

    with fixed(sh("2026-09-11", 15, 30)):
        assert fservice._today() == "2026-09-11"
        with patch.object(fservice, "_candidate_codes", return_value=["600001"]), \
                patch("dragon_quant.factors.finance.sync_snapshots",
                      return_value={"status": "ok",
                                    "captured_at": "2026-09-11T15:30:00"}) as sync:
            payload = fservice.fin_sync(snapshot=True, date="20260911")
    sync.assert_called_once()
    assert payload["mode"] == "snapshot"


def test_service_build_calendar_span_uses_the_runtime_clock():
    from dragon_quant.factors import service as fservice

    captured: dict = {}

    def fake_calendar(days=180, *, services=None):
        captured["days"] = days
        return [], None

    with fixed(sh("2026-09-11", 15, 30)), \
            patch("dragon_quant.storage.finance.has_any_fin_data",
                  return_value=True), \
            patch("dragon_quant.factors.finance.load_trade_calendar",
                  side_effect=fake_calendar), \
            patch.object(fservice.engine, "build_date",
                         return_value={"ok": True, "trade_date": "2026-01-05"}):
        fservice.build(date="20260105", only=["fin_roe"])
    # 2026-01-05 → 2026-09-11 = 249 天；日历窗口 = max(180, 249 + 60)
    assert captured["days"] == 309


# ─── factors/finance.py ────────────────────────────────────────────────────


def test_finance_today_snapshot_capture_and_overdue_use_the_runtime_clock():
    from dragon_quant.factors import finance as ffinance

    class _Tdx:
        def get_finance_info(self, code):
            return {"updated_date": 20260630, "jing_zichan": 1.0}

    with fixed(sh("2026-09-11", 15, 30)):
        assert ffinance._today() == "2026-09-11"
        with patch.object(ffinance, "_match_period", return_value=None), \
                patch("dragon_quant.storage.finance.upsert_observed_snapshots",
                      return_value=1):
            payload = ffinance.sync_snapshots(
                "2026-09-11", ["600001"], tdx=_Tdx())
        assert payload["captured_at"] == "2026-09-11T15:30:00"
        # same_day_only 闸门读 clock，而不是真实今天
        with pytest.raises(ValueError):
            ffinance.sync_snapshots("2026-09-10", ["600001"], tdx=_Tdx())

    store = {
        "periods": ["2026-03-31"],
        "report_records": {"by_basis": {}},
    }
    with fixed(sh("2026-09-11", 10, 0)), \
            patch.object(ffinance.fin_store, "finance_status",
                         return_value=store), \
            patch.object(ffinance.fin_store, "list_field_calibrations",
                         return_value=[]):
        overdue = ffinance.status()
    assert overdue["next_deadline"] == "2026-08-31T23:59:59"
    assert overdue["overdue"] is True

    with fixed(sh("2026-08-01", 10, 0)), \
            patch.object(ffinance.fin_store, "finance_status",
                         return_value=store), \
            patch.object(ffinance.fin_store, "list_field_calibrations",
                         return_value=[]):
        upcoming = ffinance.status()
    assert upcoming["overdue"] is False


# ─── factors/evaluation.py（runtime 模式无全局冻结）───────────────────────


def test_evaluation_datetime_isinstance_path_works_without_a_global_freeze():
    from dragon_quant.factors.evaluation import _bar_date

    assert _bar_date(SimpleNamespace(timestamp=sh("2026-09-11", 10, 0))) == \
        "2026-09-11"
    # epoch(ms) 分支是纯解析，不得被时钟迁移影响
    assert _bar_date(SimpleNamespace(timestamp=1789092000000)) == "2026-09-11"


# ─── cache/data_cache.py ───────────────────────────────────────────────────


def test_cache_ttl_and_persisted_timestamps_use_the_wall_clock(tmp_path):
    from dragon_quant.cache.data_cache import CacheEntry, DataCache, _today_end

    with fixed(sh("2026-09-11", 15, 30)):
        now = clock.now().timestamp()
        end_of_day = int(sh("2026-09-11", 23, 59, 59).timestamp())
        assert _today_end() == end_of_day
        assert CacheEntry("x", int(now) + 60).expired is False
        assert CacheEntry("x", int(now) - 1).expired is True

        cache = DataCache(cache_dir=tmp_path)
        cache.set("k", [1, 2, 3])
        assert cache._mem["k"].ttl == end_of_day
        blob = json.loads((tmp_path / "k.json").read_text(encoding="utf-8"))
        assert blob["ts"] == now
        assert "k" in cache.snapshot()

        cache.set_for_trade_date("kline:day:A", [{"close": 1.0}], "2026-09-11")
        dated = tmp_path / "provider" / "2026-09-11" / "kline_day_A.json"
        assert json.loads(dated.read_text(encoding="utf-8"))["ts"] == now


def test_cache_load_persisted_age_uses_the_wall_clock(tmp_path):
    from dragon_quant.cache.data_cache import DataCache

    with fixed(sh("2026-09-11", 15, 30)):
        now = clock.now().timestamp()
        DataCache(cache_dir=tmp_path).set("fresh", {"a": 1})
        (tmp_path / "stale.json").write_text(
            json.dumps({"data": {"b": 2}, "ts": now - 7200}),
            encoding="utf-8")
        fresh_cache = DataCache(cache_dir=tmp_path)
        assert fresh_cache.load_persisted("fresh", max_age=3600) == {"a": 1}
        assert fresh_cache.load_persisted("stale", max_age=3600) is None
        assert not (tmp_path / "stale.json").exists()


# ─── logging/logger.py + logging/query.py ─────────────────────────────────


def test_scan_logger_timestamps_and_elapsed_use_the_runtime_clock():
    from dragon_quant.logging.logger import ScanLogger

    with fixed(sh("2026-09-11", 15, 30)):
        expected = clock.now().timestamp()
        logger = ScanLogger()
        logger.phase("A", "板块排行")
        entry = logger.query(category="phase:A")[0]
        assert entry.timestamp == expected
        assert logger.to_dicts()[0]["ts"] == expected
        assert logger.summary()["elapsed_s"] == 0.0


def test_clear_logs_cutoff_uses_the_runtime_clock():
    from dragon_quant.logging import query as log_query

    with fixed(sh("2026-09-11", 15, 30)):
        expected_cutoff = clock.now().timestamp() - 7 * 86400
        with patch.object(log_query.store, "delete_old_scan_logs",
                          return_value=3) as delete:
            result = log_query.clear_logs(days=7)
    assert delete.call_args.args[0] == expected_cutoff
    assert delete.call_args.kwargs == {"source": "v2"}
    assert result["cleared"] == 3


# ─── storage/manager.py ───────────────────────────────────────────────────


def test_clear_dir_days_uses_the_runtime_clock(tmp_path):
    from dragon_quant.storage import manager as storage_manager

    old = tmp_path / "old.txt"
    fresh = tmp_path / "fresh.txt"
    old.write_bytes(b"x")
    fresh.write_bytes(b"x")
    with fixed(sh("2026-09-11", 15, 30)):
        now = clock.now().timestamp()
        _touch(old, now - 8 * 86400)
        _touch(fresh, now - 60)
        assert storage_manager._clear_dir(tmp_path, days=7) == 1
    assert not old.exists()
    assert fresh.exists()


def test_storage_clear_logs_cutoff_uses_the_runtime_clock(tmp_path, monkeypatch):
    from dragon_quant.storage import manager as storage_manager

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    db_path = tmp_path / "dragon.db"
    db_path.write_bytes(b"")
    monkeypatch.setattr(storage_manager, "LOG_DIR", log_dir)
    monkeypatch.setattr(storage_manager, "DB_PATH", db_path)
    calls: list[tuple[float, str]] = []

    def fake_delete(cutoff, source):  # noqa: ANN001 - test stub
        calls.append((cutoff, source))
        return 0

    with fixed(sh("2026-09-11", 15, 30)):
        expected_cutoff = clock.now().timestamp() - 7 * 86400
        with patch("dragon_quant.storage.db.delete_old_scan_logs",
                   side_effect=fake_delete):
            assert storage_manager.StorageManager().clear_logs(days=7) == 0
    assert [source for _, source in calls] == ["v1", "v2"]
    assert all(cutoff == expected_cutoff for cutoff, _ in calls)


def _touch(path, epoch: float) -> None:  # noqa: ANN001 - test helper
    import os

    os.utime(path, (epoch, epoch))
