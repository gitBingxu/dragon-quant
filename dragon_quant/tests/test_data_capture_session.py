"""采集会话：时窗、预算、幂等、漏采、dry-run（W0 KR2/KR8 的会话侧）。"""

from __future__ import annotations

import json
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest

from dragon_quant.data_capture import (
    build_plan_payload,
    collect_once,
    freeze_plan,
    run_session,
)
from dragon_quant.data_capture.schedule import render_schedule
from dragon_quant.infra.assets import db as assets_db
from dragon_quant.infra.assets import observations as store
from dragon_quant.infra.clock import FixedClock, reset_clock, set_clock
from dragon_quant.infra.models import KBar, Quote
from dragon_quant.tests.support import patch_storage_dbs

CST = timezone(timedelta(hours=8))
TRADE_DATE = "2026-11-18"
NOW = "2026-11-18T09:10:00+08:00"


def _moment(hour, minute):
    return datetime(2026, 11, 18, hour, minute, tzinfo=CST)


class FakeTdx:
    """最小 fake provider：只为采集契约服务，不模拟业务评分。"""

    name = "tdx"

    def __init__(self, *, auction=None, final=None, minutes=None, quotes=None,
                 daily=None, members=None):
        self._auction = auction
        self._final = final
        self._minutes = minutes
        self._quotes = quotes
        self._daily = daily
        self._members = members or {}

    def get_auction(self, code):
        if self._auction is None:
            raise RuntimeError("transport down")
        return list(self._auction.get(code, []))

    def get_auction_snapshot(self, code):
        if self._final is None:
            return None
        return self._final.get(code)

    def get_minute_kline(self, code):
        return list((self._minutes or {}).get(code, []))

    def batch_get_quotes(self, codes):
        return [self._quotes[code] for code in codes if code in (self._quotes or {})]

    def get_message_placeholder(self):  # pragma: no cover - 防误用
        raise AssertionError("unexpected call")

    def get_sector_components(self, sector_code, page=1, all_pages=False):
        return list(self._members.get(sector_code, []))


class FakeTdxWithDaily(FakeTdx):
    def get_daily_kline_unadjusted(self, code, days=120):
        return list((self._daily or {}).get(code, []))


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
        yield


def _freeze(codes=("600001",), sectors=None, topics=None):
    payload = build_plan_payload(
        trade_date=TRADE_DATE, now=NOW, codes=list(codes),
        sectors=sectors or (), topics=topics)
    return freeze_plan(payload, now=NOW)["plan"]


def _bar(hour, minute, close):
    moment = datetime(2026, 11, 18, hour, minute, tzinfo=CST)
    return KBar(timestamp=int(moment.timestamp() * 1000), volume=10.0,
                open=close - 0.05, high=close + 0.05, low=close - 0.1,
                close=close, chg=0.0, pct=0.0, turnover=0.0, amount=1000.0)


def _quote(code, price):
    return Quote(code=code, name="x", price=price, prev_close=price,
                 open_px=price, high=price, low=price, pct=0.0, chg=0.0,
                 turnover_rate=0.0, amplitude=0.0, volume=1000.0,
                 amount=10000.0)


def test_collect_once_writes_observations_and_rerun_is_idempotent(tmp_path):
    tdx = FakeTdx(
        auction={"600001": [{"time": "09:20:00", "price": 10.5,
                             "matched": 100, "unmatched": 10}]},
        final={"600001": {"price": 10.6, "amount": 123000.0,
                          "matched": 200, "unmatched": 5}},
    )
    with _sandbox(tmp_path):
        plan = _freeze()
        report = collect_once(
            plan_id=plan["plan_id"], topics=["auction_path", "auction_final"],
            providers=Bundle(tdx), clock=FixedClock(_moment(9, 26)))
        assert report["ok"] is True
        assert {item["status"] for item in report["topics"]} == {"complete"}
        assert len(store.query_observations()) == 2
        run = store.get_run(report["run_id"])
        assert run["status"] == "complete"
        # 提交即释放租约，重复提交被 fencing 拒绝。
        assert run["lease_owner"] is None

        again = collect_once(
            plan_id=plan["plan_id"], topics=["auction_path"], slot="0926",
            providers=Bundle(tdx), clock=FixedClock(_moment(9, 27)))
        assert again["status"] == "skipped"
        assert again["topics"][0]["reason"] == "already_complete"
        assert len(store.query_observations()) == 2


def test_budget_truncates_scope_and_reports_partial(tmp_path):
    tdx = FakeTdx(auction={
        "600001": [{"time": "09:20:00", "price": 10.0}],
        "600002": [{"time": "09:20:00", "price": 20.0}],
        "600003": [{"time": "09:20:00", "price": 30.0}],
    })
    with _sandbox(tmp_path):
        plan = _freeze(codes=("600001", "600002", "600003"))
        report = collect_once(
            plan_id=plan["plan_id"], topics=["auction_path"], budget=2,
            providers=Bundle(tdx), clock=FixedClock(_moment(9, 26)))
        topic = report["topics"][0]
        assert topic["status"] == "partial"
        assert topic["expected"] == 2
        assert any(gap["reason"] == "budget_exhausted" for gap in topic["gaps"])
        # 被截断的代码进入缺口，不谎称已采。
        run = store.get_run(report["run_id"])
        assert run["status"] == "partial"
        assert run["report"]["topics"][0]["gaps"][0]["dropped"] == 1


def test_window_guard_recovery_and_history_refusal(tmp_path):
    tdx = FakeTdx(auction={"600001": [{"time": "09:20:00", "price": 10.0}]})
    with _sandbox(tmp_path):
        plan = _freeze()
        early = collect_once(plan_id=plan["plan_id"], topics=["auction_path"],
                             providers=Bundle(tdx),
                             clock=FixedClock(_moment(9, 0)))
        assert early["topics"][0]["reason"] == "window_not_open"

        recovered = collect_once(
            plan_id=plan["plan_id"], topics=["auction_path"],
            providers=Bundle(tdx), clock=FixedClock(_moment(10, 0)),
            slot="1000")
        assert recovered["topics"][0]["status"] == "complete"
        assert recovered["topics"][0]["quality"] == "same_day_recovered"

        past_payload = build_plan_payload(trade_date="2026-11-17", now=NOW,
                                          codes=["600001"])
        past = freeze_plan(past_payload, now=NOW)["plan"]
        refused = collect_once(plan_id=past["plan_id"],
                               topics=["auction_path"], providers=Bundle(tdx),
                               clock=FixedClock(_moment(10, 0)), slot="1000")
        assert refused["topics"][0]["reason"] == "history_not_recoverable"


def test_minute_bars_store_only_finished_windows(tmp_path):
    tdx = FakeTdx(minutes={"600001": [_bar(9, 34, 10.05), _bar(9, 40, 10.10)]})
    with _sandbox(tmp_path):
        plan = _freeze()
        report = collect_once(plan_id=plan["plan_id"], topics=["minute_bars"],
                              providers=Bundle(tdx),
                              clock=FixedClock(_moment(9, 35)))
        assert report["topics"][0]["status"] == "complete"
        rows = store.query_bars()
        assert len(rows) == 1
        assert rows[0]["interval_end"] == "2026-11-18T09:34:00+08:00"
        assert rows[0]["completeness"] == "final"
        assert rows[0]["adjustment"] == "none"
        notes = report["topics"][0]["notes"]
        assert any("unfinished" in note for note in notes)


def test_daily_bars_need_an_explicit_unadjusted_adapter(tmp_path):
    with _sandbox(tmp_path):
        plan = _freeze()
        no_adapter = collect_once(
            plan_id=plan["plan_id"], topics=["daily_bars"],
            providers=Bundle(FakeTdx()), clock=FixedClock(_moment(15, 30)))
        assert no_adapter["topics"][0]["status"] == "partial"
        assert any(gap["reason"] == "unadjusted_daily_unavailable"
                   for gap in no_adapter["topics"][0]["gaps"])
        assert store.query_bars() == []

        daily = {"600001": [_bar(15, 0, 10.0)]}
        with_adapter = collect_once(
            plan_id=plan["plan_id"], topics=["daily_bars"],
            providers=Bundle(FakeTdxWithDaily(daily=daily)),
            clock=FixedClock(_moment(15, 31)), slot="1531")
        assert with_adapter["topics"][0]["status"] == "complete"
        rows = store.query_bars()
        assert len(rows) == 1
        assert rows[0]["period"] == "daily"
        assert rows[0]["trade_date"] == TRADE_DATE


def test_sector_membership_scope_and_observation(tmp_path):
    class Member:
        def __init__(self, code):
            self.code = code

    tdx = FakeTdx(members={"hy001": [Member("600001"), Member("600002")]})
    with _sandbox(tmp_path):
        empty = _freeze()
        skipped = collect_once(plan_id=empty["plan_id"],
                               topics=["sector_membership"],
                               providers=Bundle(tdx),
                               clock=FixedClock(_moment(9, 0)))
        assert skipped["topics"][0]["reason"] == "scope_empty"

        payload = build_plan_payload(trade_date=TRADE_DATE, now=NOW,
                                     codes=["600001"], sectors=["hy001"])
        plan = freeze_plan(payload, now=NOW)["plan"]
        report = collect_once(plan_id=plan["plan_id"],
                              topics=["sector_membership"],
                              providers=Bundle(tdx),
                              clock=FixedClock(_moment(9, 1)))
        assert report["topics"][0]["status"] == "complete"
        rows = [row for row in store.query_observations()
                if row["obs_type"] == "sector_membership"]
        assert rows[0]["payload"]["codes"] == ["600001", "600002"]


def test_dry_run_never_writes_or_needs_providers(tmp_path):
    with _sandbox(tmp_path):
        plan = _freeze()
        report = collect_once(plan_id=plan["plan_id"], topics=["auction_path"],
                              providers=None, dry_run=True,
                              clock=FixedClock(_moment(9, 26)))
        assert report["ok"] is True
        assert report["status"] == "planned"
        assert report["topics"][0]["scope"] == ["600001"]
        assert store.list_runs() == []
        assert store.query_observations() == []


def test_missing_providers_fail_explicitly_and_never_call_network(tmp_path):
    with _sandbox(tmp_path):
        plan = _freeze()
        report = collect_once(plan_id=plan["plan_id"], topics=["auction_path"],
                              providers=None, clock=FixedClock(_moment(9, 26)))
        assert report["ok"] is False
        assert report["status"] == "failed"
        assert report["topics"][0]["errors"][0]["reason"] == "missing_api_key"
        assert store.list_runs() == []


def test_session_marks_passed_slot_as_missed(tmp_path):
    tdx = FakeTdx(auction={"600001": [{"time": "09:20:00", "price": 10.0}]})
    with _sandbox(tmp_path):
        plan = _freeze()
        payload = run_session(plan_id=plan["plan_id"], phase="auction",
                              providers=Bundle(tdx),
                              clock=FixedClock(_moment(11, 0)),
                              sleep=lambda seconds: None, max_slots=1)
        assert payload["ok"] is False
        assert payload["slots"][0]["status"] == "missed"
        assert payload["slots"][0]["reason"] == "slot_window_passed"
        run = store.get_run(f"{plan['plan_id']}:auction:0926")
        assert run["status"] == "missed"
        assert run["report"]["reason"] == "slot_window_passed"


def test_schedule_template_is_output_only():
    template = render_schedule("cron", workdir="/repo")
    assert template["installed"] is False
    assert "assets capture session" in template["content"]
    assert "/repo" in template["content"]
    with pytest.raises(ValueError):
        render_schedule("windows-service")


def test_capture_cli_plan_and_coverage_are_offline(tmp_path, capsys, monkeypatch):
    from dragon_quant import cli

    def _run(argv):
        monkeypatch.setattr(sys, "argv", ["dragon-quant", *argv])
        with pytest.raises(SystemExit) as exit_info:
            cli.main()
        return exit_info.value.code

    with _sandbox(tmp_path):
        conn = assets_db._connect()
        conn.execute(
            "INSERT INTO dragons(trade_date, code, name, review_status, source) "
            "VALUES ('2026-11-17', '600001', '甲', 'pending', 'v2')")
        conn.commit()
        conn.close()
        set_clock(FixedClock(_moment(9, 10)))
        try:
            assert _run(["assets", "capture", "plan", "--date", "20261118",
                         "--dry-run", "--json"]) == 0
            plan_payload = json.loads(capsys.readouterr().out)
            assert plan_payload["dry_run"] is True
            assert plan_payload["plan_id"] is None
            assert [item["code"] for item in plan_payload["plan"]["members"]] \
                == ["600001"]

            assert _run(["assets", "capture", "coverage", "--json"]) == 0
            coverage = json.loads(capsys.readouterr().out)
            assert coverage["plans"] == []
        finally:
            reset_clock()


def test_lost_lease_during_fetch_blocks_the_write(tmp_path, monkeypatch):
    """取数期间被抢占：结果不落库，运行记 lease_lost。"""
    from dragon_quant.infra.assets import observations as store
    from dragon_quant.data_capture import collectors

    class StealingCollector(collectors.AuctionPathCollector):
        def collect(self, request, *, provider, plan):
            # 模拟另一 worker 在租约过期后抢到同一时槽。
            store.acquire_lease(
                f"{plan['plan_id']}:manual:0926", owner="other",
                ttl_seconds=60, now="2026-11-18T09:30:00+08:00")
            return super().collect(request, provider=provider, plan=plan)

    monkeypatch.setattr(collectors, "COLLECTORS",
                        (StealingCollector(),) + collectors.COLLECTORS[1:])
    monkeypatch.setattr(collectors, "_REGISTRY",
                        {c.topic: c for c in collectors.COLLECTORS})

    tdx = FakeTdx(auction={"600001": [{"time": "09:20:00", "price": 10.0}]})
    with _sandbox(tmp_path):
        plan = _freeze()
        report = collect_once(plan_id=plan["plan_id"], topics=["auction_path"],
                              providers=Bundle(tdx),
                              clock=FixedClock(_moment(9, 26)))
        assert report["ok"] is False
        assert report["status"] == "failed"
        assert report["error"]["reason"] == "lease_lost"
        assert store.query_observations() == []
