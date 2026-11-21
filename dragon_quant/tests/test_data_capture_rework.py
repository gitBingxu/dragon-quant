"""W0 返工回归（M1–M5）：真实时钟、原子 fencing、读错语义、bar 校验、区间覆盖、边界。

每个用例都在临时 assets.db 上运行，绝不触碰真实数据目录。
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest

from dragon_quant.data_capture import (
    build_coverage,
    build_plan_payload,
    collect_once,
    freeze_plan,
)
from dragon_quant.data_capture import session as capture_session
from dragon_quant.data_capture.collectors import (
    DailyBarCollector,
    MinuteBarCollector,
)
from dragon_quant.data_capture.types import CaptureRequest
from dragon_quant.infra.assets import db as assets_db
from dragon_quant.infra.assets import observations as store
from dragon_quant.infra.clock import FixedClock
from dragon_quant.infra.models import KBar
from dragon_quant.tests.support import patch_storage_dbs

CST = timezone(timedelta(hours=8))
TRADE_DATE = "2026-11-18"
NOW = "2026-11-18T09:10:00+08:00"


def _moment(hour, minute, day=18):
    return datetime(2026, 11, day, hour, minute, tzinfo=CST)


class SequenceClock:
    """按调用顺序返回预设时刻（固定 clock 无法模拟慢请求）。"""

    def __init__(self, moments):
        self._moments = list(moments)
        self._index = 0

    def now(self, tz=None):
        moment = self._moments[min(self._index, len(self._moments) - 1)]
        self._index += 1
        return moment

    def today(self, tz=None):
        return self.now().date()

    def stamp(self, fmt="%Y%m%d_%H%M%S", tz=None):
        return self.now().strftime(fmt)

    def monotonic(self) -> float:
        return 0.0


class FakeTdx:
    name = "tdx"

    def __init__(self, *, auction=None, minutes=None, daily=None):
        self._auction = auction or {}
        self._minutes = minutes or {}
        self._daily = daily or {}

    def get_auction(self, code):
        return list(self._auction.get(code, []))

    def get_minute_kline(self, code):
        return list(self._minutes.get(code, []))

    def get_daily_kline_unadjusted(self, code, days=120):
        return list(self._daily.get(code, []))


class Bundle:
    def __init__(self, tdx):
        self.tdx = tdx


@contextmanager
def _sandbox(tmp_path):
    with patch_storage_dbs(str(tmp_path / "dragon.db"),
                           str(tmp_path / "assets.db")):
        conn = assets_db._connect()
        assets_db._ensure_schema(conn)
        conn.close()
        yield str(tmp_path / "assets.db")


def _plan(topics=None, codes=("600001",)):
    payload = build_plan_payload(trade_date=TRADE_DATE, now=NOW,
                                 codes=list(codes), topics=topics)
    return freeze_plan(payload, now=NOW)["plan"]


def _bar(hour, minute, close=10.0, **overrides):
    moment = _moment(hour, minute)
    values = dict(timestamp=int(moment.timestamp() * 1000), volume=10.0,
                  open=close - 0.05, high=close + 0.05, low=close - 0.1,
                  close=close, chg=0.0, pct=0.0, turnover=0.0, amount=1000.0)
    values.update(overrides)
    return KBar(**values)


def _add_bar(code, end, *, complete="final", time="unverified",
             revision_close=10.0, trade_date=TRADE_DATE):
    start = (datetime.fromisoformat(end) - timedelta(minutes=1)).isoformat()
    return store.append_bar(
        trade_date=trade_date, market="sh", code=code, period="1min",
        interval_start=start, interval_end=end, open=10.0, high=10.2, low=9.9,
        close=revision_close, volume=1.0, amount=10.0, source="tdx",
        observed_at=NOW, available_at=NOW, completeness=complete,
        time_semantics=time)


# ─── M1：真实时钟与原子 fencing ─────────────────────────────────────────────


def test_received_time_is_read_after_the_network_request(tmp_path):
    tdx = FakeTdx(auction={"600001": [{"time": "09:20:00", "price": 10.0}]})
    with _sandbox(tmp_path):
        plan = _plan()
        clock = SequenceClock([_moment(9, 26), _moment(9, 29), _moment(9, 29)])
        report = collect_once(plan_id=plan["plan_id"], topics=["auction_path"],
                              providers=Bundle(tdx), clock=clock)
        assert report["status"] == "complete"
        row = store.query_observations()[0]
        # cutoff 是请求开始；received/available 是网络之后的真实时刻。
        assert report["cutoff"] == "2026-11-18T09:26:00+08:00"
        assert row["received_at"] == "2026-11-18T09:29:00+08:00"
        assert row["available_at"] == "2026-11-18T09:29:00+08:00"


def test_slow_request_past_lease_expiry_renews_with_real_clock(tmp_path):
    tdx = FakeTdx(auction={"600001": [{"time": "09:20:00", "price": 10.0}]})
    with _sandbox(tmp_path):
        plan = _plan()
        # 请求开始 09:26（租约 30s），收到 09:28（已过期）→ 续租拿到新 token。
        clock = SequenceClock([_moment(9, 26), _moment(9, 28), _moment(9, 28)])
        report = collect_once(plan_id=plan["plan_id"], topics=["auction_path"],
                              providers=Bundle(tdx), clock=clock, lease_ttl=30)
        assert report["status"] == "complete"
        assert len(store.query_observations()) == 1
        run = store.get_run(report["run_id"])
        assert run["status"] == "complete"


def test_takeover_during_request_leaves_no_raw_data(tmp_path):
    from dragon_quant.data_capture import collectors

    class StealingCollector(collectors.AuctionPathCollector):
        def collect(self, request, *, provider, plan):
            # 另一 worker 在租约过期后抢到同一时槽（真实时钟更晚）。
            store.acquire_lease(f"{plan['plan_id']}:manual:0926",
                                owner="thief", ttl_seconds=60,
                                now="2026-11-18T09:30:00+08:00")
            return super().collect(request, provider=provider, plan=plan)

    original = collectors._REGISTRY
    collectors._REGISTRY = {**original,
                            "auction_path": StealingCollector()}
    try:
        tdx = FakeTdx(auction={"600001": [{"time": "09:20:00", "price": 10.0}]})
        with _sandbox(tmp_path):
            plan = _plan()
            clock = SequenceClock([_moment(9, 26), _moment(9, 26),
                                   _moment(9, 26)])
            report = collect_once(plan_id=plan["plan_id"],
                                  topics=["auction_path"],
                                  providers=Bundle(tdx), clock=clock)
            assert report["ok"] is False
            assert report["error"]["reason"] == "lease_lost"
            assert store.query_observations() == []
            assert store.query_bars() == []
    finally:
        collectors._REGISTRY = original


def test_commit_topic_rejects_wrong_owner_without_writing(tmp_path):
    with _sandbox(tmp_path):
        plan = _plan()
        run = store.ensure_run(plan_id=plan["plan_id"], trade_date=TRADE_DATE,
                               phase="manual", slot="0926",
                               deadline_at=None, created_at=NOW)
        token = store.acquire_lease(run["run_id"], owner="owner-a",
                                    ttl_seconds=60, now=NOW)
        intents = [{
            "trade_date": TRADE_DATE, "market": "sh", "code": "600001",
            "obs_type": "auction_path", "source": "tdx", "payload": {"x": 1},
            "received_at": NOW, "available_at": NOW, "units": {},
            "quality": "semantics_unverified",
        }]
        with pytest.raises(store.LeaseLostError):
            store.commit_topic(run_id=run["run_id"], owner="owner-b",
                               token=token, now=NOW, observations=intents,
                               bars=[], topic_report={"topic": "auction_path"})
        assert store.query_observations() == []
        store.commit_topic(run_id=run["run_id"], owner="owner-a",
                           token=token, now=NOW, observations=intents, bars=[],
                           topic_report={"topic": "auction_path"})
        assert len(store.query_observations()) == 1


def test_deadline_is_checked_before_and_after_the_request(tmp_path):
    tdx = FakeTdx(auction={"600001": [{"time": "09:20:00", "price": 10.0}]})
    with _sandbox(tmp_path):
        plan = _plan()
        # 截止在请求之后：照样落库，但不得声称 on-time complete。
        late_clock = SequenceClock([_moment(9, 26), _moment(9, 30),
                                    _moment(9, 30)])
        late = collect_once(plan_id=plan["plan_id"], topics=["auction_path"],
                            providers=Bundle(tdx), clock=late_clock,
                            deadline_at="2026-11-18T09:28:00+08:00",
                            slot="0926")
        assert late["status"] == "partial"
        assert late["topics"][0]["late_after_deadline"] is True
        assert len(store.query_observations()) == 1

        # 截止在请求之前：一个请求都不发。
        early_clock = SequenceClock([_moment(10, 0), _moment(10, 0)])
        early = collect_once(plan_id=plan["plan_id"], topics=["auction_path"],
                             providers=Bundle(tdx), clock=early_clock,
                             deadline_at="2026-11-18T09:30:00+08:00",
                             slot="1000")
        assert early["topics"][0]["reason"] == "deadline_passed"
        assert len(store.query_observations()) == 1


def test_worker_identity_is_unique_per_call():
    assert capture_session.new_worker_id() != capture_session.new_worker_id()


# ─── M2：读错语义 ───────────────────────────────────────────────────────────


def test_missing_database_is_not_an_error_and_creates_nothing(tmp_path):
    def _no_create(create=True, path=None):
        if not create:
            return None
        raise AssertionError("只读路径不得创建 assets.db")

    from unittest.mock import patch
    with patch("dragon_quant.infra.assets.db._connect", side_effect=_no_create):
        assert store.store_state() == store.MISSING_DATABASE
        assert store.previous_plan(TRADE_DATE) is None
        payload = build_coverage()
    assert payload["store_state"] == store.MISSING_DATABASE
    assert payload["plans"] == []
    assert not (tmp_path / "assets.db").exists()


def test_uninitialized_tables_are_structured_and_not_created_by_reads(tmp_path):
    assets_path = str(tmp_path / "assets.db")
    with patch_storage_dbs(str(tmp_path / "dragon.db"), assets_path):
        conn = sqlite3.connect(assets_path)
        conn.execute("CREATE TABLE prep_assets (trade_date TEXT, asset_type TEXT, "
                     "payload_json TEXT)")
        conn.commit()
        conn.close()
        assert store.store_state() == store.UNINITIALIZED
        with pytest.raises(store.StoreUninitialized):
            store.get_plan("cp_20261118_r1")
        assert store.previous_plan(TRADE_DATE) is None
        payload = build_coverage()
        assert payload["store_state"] == store.UNINITIALIZED
        # 读路径不建表。
        conn = sqlite3.connect(assets_path)
        names = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        conn.close()
        assert "capture_plans" not in names


def test_corrupt_plan_json_is_an_explicit_failure_not_empty(tmp_path):
    with _sandbox(tmp_path):
        plan = _plan()
        conn = assets_db._connect()
        conn.execute("UPDATE capture_plans SET members_json = '{bad' "
                     "WHERE plan_id = ?", (plan["plan_id"],))
        conn.commit()
        conn.close()
        with pytest.raises(store.CorruptEvidence):
            store.get_plan(plan["plan_id"])
        payload = build_coverage()
        assert payload["store_state"] == "error"
        assert payload["error"]["reason"] == "corrupt_json"


def test_legitimate_empty_store_reads_as_empty(tmp_path):
    with _sandbox(tmp_path):
        assert store.store_state() == store.READY
        assert store.get_plan("cp_20261118_r1") is None
        assert store.list_plans() == []
        payload = build_coverage()
        assert payload["store_state"] == store.READY
        assert payload["coverage_complete"] is True


# ─── M3：bar 校验与 final 语义 ──────────────────────────────────────────────


def _request(scope=("600001",), cutoff="2026-11-18T09:35:00+08:00"):
    return CaptureRequest(plan_id="cp", topic="minute_bars",
                          trade_date=TRADE_DATE, scope=scope, cutoff=cutoff,
                          request_budget=100)


def test_minute_bars_reject_nan_zero_and_inconsistent_ohlc(tmp_path):
    bad = [
        _bar(9, 31, close=10.0, open=float("nan")),
        _bar(9, 32, close=0.0),
        _bar(9, 33, close=10.0, high=9.5),
        _bar(9, 35, close=10.0, open=None),
    ]
    good = _bar(9, 34, close=10.0)
    collector = MinuteBarCollector()
    result = collector.collect(
        _request(), provider=FakeTdx(minutes={"600001": bad + [good]}),
        plan={"members": [], "task_config": {}})
    assert len(result.bars) == 1
    assert result.bars[0].interval_end == "2026-11-18T09:34:00+08:00"
    reasons = {gap["reason"] for gap in result.gaps}
    assert "invalid_ohlc" in reasons
    assert all(bar.close > 0 for bar in result.bars)


def test_minute_bars_outside_session_are_not_stored(tmp_path):
    collector = MinuteBarCollector()
    bars = [_bar(12, 30, close=10.0), _bar(9, 34, close=10.0)]
    result = collector.collect(
        _request(), provider=FakeTdx(minutes={"600001": bars}),
        plan={"members": [], "task_config": {}})
    assert [bar.interval_end for bar in result.bars] == [
        "2026-11-18T09:34:00+08:00"]


def test_daily_bar_today_is_not_final_before_close(tmp_path):
    collector = DailyBarCollector()
    today_bar = _bar(15, 0, close=10.0)
    plan = {"members": [], "task_config": {"daily_days": 5}}

    before_close = collector.collect(
        CaptureRequest(plan_id="cp", topic="daily_bars",
                       trade_date=TRADE_DATE, scope=("600001",),
                       cutoff="2026-11-18T14:00:00+08:00", request_budget=10),
        provider=FakeTdx(daily={"600001": [today_bar]}), plan=plan)
    assert before_close.bars == ()
    assert any(gap["reason"] == "unfinished_daily_bar"
               for gap in before_close.gaps)

    after_close = collector.collect(
        CaptureRequest(plan_id="cp", topic="daily_bars",
                       trade_date=TRADE_DATE, scope=("600001",),
                       cutoff="2026-11-18T15:30:00+08:00", request_budget=10),
        provider=FakeTdx(daily={"600001": [today_bar]}), plan=plan)
    assert len(after_close.bars) == 1
    assert after_close.bars[0].completeness == "final"
    assert after_close.bars[0].time_semantics == "verified"
    assert after_close.bars[0].unit_semantics == "unverified"


def test_weekend_daily_bar_is_rejected(tmp_path):
    collector = DailyBarCollector()
    saturday = KBar(timestamp=int(datetime(2026, 11, 14, 15, 0,
                                           tzinfo=CST).timestamp() * 1000),
                    volume=1.0, open=10.0, high=10.2, low=9.9, close=10.1,
                    chg=0.0, pct=0.0, turnover=0.0, amount=10.0)
    result = collector.collect(
        CaptureRequest(plan_id="cp", topic="daily_bars",
                       trade_date="2026-11-18", scope=("600001",),
                       cutoff="2026-11-18T15:30:00+08:00", request_budget=10),
        provider=FakeTdx(daily={"600001": [saturday]}),
        plan={"members": [], "task_config": {}})
    assert result.bars == ()
    assert any(gap["reason"] == "weekend_bar" for gap in result.gaps)


# ─── M4 / M5：区间与质量覆盖、边界 ──────────────────────────────────────────


def _coverage_at(moment, **kwargs):
    return build_coverage(clock=FixedClock(moment), **kwargs)


def test_minute_coverage_counts_missing_intervals_not_just_any_bar(tmp_path):
    with _sandbox(tmp_path):
        plan = _plan(topics=["minute_bars"])
        _add_bar("600001", "2026-11-18T09:31:00+08:00")
        _add_bar("600001", "2026-11-18T09:33:00+08:00")
        payload = _coverage_at(_moment(16, 0), topics=["minute_bars"])
        entry = payload["interval_coverage"][0]
        assert entry["expected_intervals"] == 240
        assert entry["present_intervals"] == 2
        assert entry["missing_count"] == 238
        assert "2026-11-18T09:32:00+08:00" in entry["missing_sample"]
        assert entry["status"] == "partial"
        assert plan["plan_id"] == payload["plans"][0]["plan_id"]


def test_minute_coverage_does_not_count_future_windows_as_missed(tmp_path):
    with _sandbox(tmp_path):
        _plan(topics=["minute_bars"])
        _add_bar("600001", "2026-11-18T09:31:00+08:00")
        payload = _coverage_at(_moment(10, 0), topics=["minute_bars"])
        entry = payload["interval_coverage"][0]
        assert entry["expected_intervals"] == 30   # 09:31–10:00
        assert entry["missing_count"] == 29
        assert not any(
            "15:00:00" in sample for sample in entry["missing_sample"])


def test_multiple_revisions_of_one_interval_count_once(tmp_path):
    with _sandbox(tmp_path):
        _plan(topics=["minute_bars"])
        _add_bar("600001", "2026-11-18T09:31:00+08:00")
        _add_bar("600001", "2026-11-18T09:31:00+08:00", revision_close=10.05)
        payload = _coverage_at(_moment(10, 0), topics=["minute_bars"])
        entry = payload["interval_coverage"][0]
        assert entry["present_intervals"] == 1
        assert entry["revised_intervals"] == 1
        assert entry["missing_count"] == 29


def test_unverified_minute_bars_are_not_counted_as_usable(tmp_path):
    with _sandbox(tmp_path):
        _plan(topics=["minute_bars"])
        start = datetime(2026, 11, 18, 9, 31, tzinfo=CST)
        ends = [(start + timedelta(minutes=offset)).isoformat()
                for offset in range(30)]
        for end in ends:
            _add_bar("600001", end)
        payload = _coverage_at(_moment(10, 0), topics=["minute_bars"])
        entry = payload["interval_coverage"][0]
        assert entry["missing_count"] == 0
        assert entry["usable_intervals"] == 0
        assert entry["unusable_count"] == 30
        assert entry["status"] == "partial"
        assert any(gap["reason"] == "semantics_unverified"
                   for gap in payload["gaps"])


def test_topic_filter_is_pushed_down_to_bar_groups(tmp_path):
    with _sandbox(tmp_path):
        _plan(topics=["minute_bars", "daily_bars"])
        _add_bar("600001", "2026-11-18T09:31:00+08:00")
        store.append_bar(
            trade_date=TRADE_DATE, market="sh", code="600001", period="daily",
            interval_start="2026-11-18T09:30:00+08:00",
            interval_end="2026-11-18T15:00:00+08:00",
            open=10.0, high=10.2, low=9.9, close=10.1, volume=1.0, amount=10.0,
            source="tdx", observed_at=NOW, available_at=NOW,
            completeness="final", time_semantics="verified")
        payload = _coverage_at(_moment(16, 0), topics=["daily_bars"])
        assert {item["period"] for item in payload["bar_groups"]} == {"daily"}
        assert payload["interval_coverage"] == []


def test_daily_bar_gap_is_not_reported_before_close(tmp_path):
    with _sandbox(tmp_path):
        _plan(topics=["daily_bars"])
        before = _coverage_at(_moment(14, 0), topics=["daily_bars"])
        assert not any(gap["reason"] == "daily_bar_missing"
                       for gap in before["gaps"])
        after = _coverage_at(_moment(16, 0), topics=["daily_bars"])
        assert any(gap["reason"] == "daily_bar_missing"
                   for gap in after["gaps"])


def test_coverage_truncation_is_explicit_and_never_claims_complete(tmp_path):
    with _sandbox(tmp_path):
        first = _plan(topics=["auction_path"], codes=("600001",))
        second_payload = build_plan_payload(
            trade_date="2026-11-17", now=NOW, codes=["600002"],
            topics=["auction_path"])
        freeze_plan(second_payload, now=NOW)
        payload = _coverage_at(_moment(16, 0), limit=1)
        assert payload["truncated"] is True
        assert payload["coverage_complete"] is False
        assert payload["totals"]["plans"] >= 2
        assert any(gap["reason"] == "coverage_truncated"
                   for gap in payload["gaps"])
        shown = {plan["plan_id"] for plan in payload["plans"]}
        assert len(shown) == 1
        assert shown <= {first["plan_id"], "cp_20261117_r1"}


def test_committed_batches_survive_a_later_lease_loss_and_mark_partial(tmp_path):
    from dragon_quant.data_capture import collectors

    class StealingFinalCollector(collectors.AuctionFinalCollector):
        def collect(self, request, *, provider, plan):
            store.acquire_lease(f"{plan['plan_id']}:manual:0926",
                                owner="thief", ttl_seconds=60,
                                now="2026-11-18T09:30:00+08:00")
            return super().collect(request, provider=provider, plan=plan)

    original = collectors._REGISTRY
    collectors._REGISTRY = {**original,
                            "auction_final": StealingFinalCollector()}
    try:
        class MultiProvider:
            class tdx:
                name = "tdx"

                @staticmethod
                def get_auction(code):
                    return [{"time": "09:20:00", "price": 10.0}]

                @staticmethod
                def get_auction_snapshot(code):
                    return {"price": 10.1, "amount": 1000.0}

        with _sandbox(tmp_path):
            plan = _plan()
            clock = SequenceClock([_moment(9, 26), _moment(9, 27),
                                   _moment(9, 28), _moment(9, 28)])
            report = collect_once(plan_id=plan["plan_id"],
                                  topics=["auction_path", "auction_final"],
                                  providers=MultiProvider(), clock=clock)
            assert report["status"] == "partial"
            assert report["ok"] is True
            assert report["error"]["reason"] == "lease_lost"
            assert report["committed_topics"] == ["auction_path"]
            rows = store.query_observations()
            assert [row["obs_type"] for row in rows] == ["auction_path"]
    finally:
        collectors._REGISTRY = original


# ─── E2 回归：竞价终态不伪造 09:25 来源时间与终态来源 ───────────────────────


def test_auction_final_keeps_real_source_time_and_terminal_source(tmp_path):
    class FinalProvider:
        name = "tdx"

        @staticmethod
        def get_auction(code):
            return []

        @staticmethod
        def get_auction_snapshot(code):
            return {"price": 10.1, "amount": 1000.0, "matched": 100,
                    "unmatched": 5, "time": "09:24:57",
                    "terminal_source": "last_tick_virtual_match"}

    with _sandbox(tmp_path):
        plan = _plan(topics=["auction_final"])
        clock = SequenceClock([_moment(9, 26), _moment(9, 26)])
        report = collect_once(plan_id=plan["plan_id"], topics=["auction_final"],
                              providers=Bundle(FinalProvider()), clock=clock)
        assert report["status"] == "complete"
        row = store.query_observations()[0]
        assert row["source_event_time"] == "2026-11-18T09:24:57+08:00"
        assert row["payload"]["terminal_source"] == "last_tick_virtual_match"
        assert row["payload"]["source_time"] == "09:24:57"


def test_auction_final_without_source_time_stays_null(tmp_path):
    class FinalProvider:
        name = "tdx"

        @staticmethod
        def get_auction(code):
            return []

        @staticmethod
        def get_auction_snapshot(code):
            return {"price": 10.1, "amount": 1000.0}

    with _sandbox(tmp_path):
        plan = _plan(topics=["auction_final"])
        clock = SequenceClock([_moment(9, 26), _moment(9, 26)])
        report = collect_once(plan_id=plan["plan_id"], topics=["auction_final"],
                              providers=Bundle(FinalProvider()), clock=clock)
        assert report["status"] == "complete"
        row = store.query_observations()[0]
        assert row["source_event_time"] is None
        assert row["payload"]["terminal_source"] == "unverified"
