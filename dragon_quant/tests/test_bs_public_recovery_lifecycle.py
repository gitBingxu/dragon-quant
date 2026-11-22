"""G8 公共恢复生命周期：真实 events + facts 确认 + 多交易日推进。

覆盖 main G8 验收 T1–T4：

- T1 正式配置（``fixture=False``）现金空 scope 可冷却→逐日推进→毕业；旧
  active episode 缺捕获证明仍受限（公共路径）；
- T2 有真实冻结 owner/已入场批次时，触发后合格事件产生真实减仓 sell fill，
  现金/数量/T+1 正确，恢复期新批次不被旧目标反复半仓；
- T3 逐事件断言冷却门禁、缺真实条件不升、每真实交易日最多一级、跨周末
  计数、毕业与毕业后重触发的 episode 锚；
- T4 ``freeze.load_experiment_inputs`` + ``replay_frozen`` 完整重放对比。

全部走公共 ``build_experiment_pack`` / ``run_bs_experiment``；不 patch
events/facts/recovery/rank/seed 决策，数据源边界只替换 reader。
"""

from __future__ import annotations

import math
from datetime import date, timedelta

from dragon_quant.infra.calibration import PRODUCTION_CALIBRATIONS
from dragon_quant.research import bs_advice
from dragon_quant.research.bs_advice import assembly
from dragon_quant.research.bs_advice.freeze import (
    load_experiment_inputs, replay_frozen,
)
from dragon_quant.review_account.strategy.bs_policy import teaching_fixture
from dragon_quant.tests.test_bs_advice import (
    _account_payload, _cash_account, _history, _minute,
)

DAYS = ["2026-11-18", "2026-11-19", "2026-11-20",
        "2026-11-23", "2026-11-24", "2026-11-25", "2026-11-26"]
WEEKEND = ("2026-11-21", "2026-11-22")
RECOVERY = {
    "drawdown_trigger_pct": 8.0, "loss_streak_trigger": 3,
    "cooldown_days": 2, "stages": [0.25, 0.5, 1.0],
    "conditions": ["market_regime_verified", "execution_quality_ok"]}
STAGES = tuple(RECOVERY["stages"])
COOLDOWN = int(RECOVERY["cooldown_days"])


def _config():
    return teaching_fixture(fixture=False, recovery=RECOVERY)


def _prep_reader(day):
    before = (date.fromisoformat(day) - timedelta(days=1)).isoformat()
    while date.fromisoformat(before).weekday() >= 5:
        before = (date.fromisoformat(before) - timedelta(days=1)).isoformat()
    created = f"{before}T16:50:00+08:00"
    return ({"review_candidates": [{"code": "600001", "name": "龙一",
                                    "trade_date": before,
                                    "eligibility": "eligible",
                                    "position_pct": 5.0}],
             "market": {"state": "strong", "score": 80.0, "coeff": 1.0,
                        "partial": False, "missing_inputs": [],
                        "evidence": ["prep"]}},
            {"trade_date": before, "created_at": created, "error": None})


def _cash_with_recovery(config):
    payload = _cash_account(config)
    payload.update({"peak_equity": 120000.0, "drawdown_pct": 12.0})
    return payload


def _held_with_recovery(config):
    """真实冻结 owner 的已入场可卖批次（T+1 可卖）+ 显式触发回撤。"""
    payload = _account_payload(config, cash=50000.0, mark=9.0)
    payload.update({"peak_equity": 120000.0, "drawdown_pct": 12.0,
                    "as_of": "2026-11-17T16:00:00+08:00"})
    payload["positions"][0]["mark_source"] = {
        "price": 9.0, "source": "tdx",
        "available_at": "2026-11-17T15:05:00+08:00"}
    return payload


def _experiment(tmp_path, monkeypatch, *, payload, prep_reader=None):
    """公共入口：临时本地资产 + 逐日 T-1 prep + pack/experiment。"""
    monkeypatch.setattr(
        assembly, "current_tdx_provenance",
        lambda: PRODUCTION_CALIBRATIONS[0].provenance)
    db_path = tmp_path / "assets.db"
    history = _history(db_path)
    for day in DAYS:
        _minute(db_path, day, start="09:59:00", end="10:00:00", close=10.0)
        _minute(db_path, day, start="10:00:00", end="10:01:00", close=10.1)
    config = _config()
    account = bs_advice.parse_account_file(payload)
    policy = bs_advice.load_policy_payload(config.to_dict())
    calendar = history + DAYS + ["2026-11-27"]
    prep = prep_reader or _prep_reader
    readers = {
        "dragons_reader": lambda a, b: [{
            "code": "600001", "name": "龙一", "trade_date": history[-1],
            "scan_id": "s", "is_true_dragon": True}],
        "prep_reader": lambda day: (None, {"error": None})
        if day not in DAYS else prep(day),
        "calendar_reader": lambda as_of: calendar,
        "plan_reader": lambda day: None,
    }
    pack = bs_advice.build_experiment_pack(
        account=account, policy=policy, date_from=DAYS[0], date_to=DAYS[-1],
        db_path=db_path, calendar=calendar, **readers)
    result = bs_advice.run_bs_experiment(
        account=account, policy=policy, date_from=DAYS[0], date_to=DAYS[-1],
        db_path=db_path, calendar=calendar, pack=pack)
    return pack, result["variants"]["bs"]


def _rows(variant):
    """逐事件投影：锁存态、决策时恢复条件、意图、成交、期末状态。"""
    rows = []
    for event in variant["events"]:
        record = event.get("decision_record") or {}
        rows.append({
            "day": event["trade_date"],
            "ts": int(event["timestamp"]),
            "latch": dict(event.get("recovery") or {}),
            "decision": dict(((record.get("bundle") or {}).get("recovery"))
                             or {}),
            "intents": [dict(item) for item in (record.get("intents") or ())],
            "fills": [dict(item) for item in (event.get("fills") or ())],
            "state": dict(event.get("state") or {}),
        })
    return rows


def _episodes(rows):
    """按真实触发 episode 切分（毕业保留原锚，重触发开启新 episode）。"""
    episodes = []
    for row in rows:
        trigger = row["latch"].get("trigger_trade_date") or ""
        if not episodes or episodes[-1]["trigger"] != trigger:
            episodes.append({"trigger": trigger, "rows": []})
        episodes[-1]["rows"].append(row)
    return episodes


def _day_index(day):
    return DAYS.index(day)


def _assert_episode_contract(episode):
    """T3：冷却/逐日一级/跨周末计数/毕业锁存。"""
    rows = episode["rows"]
    trigger = episode["trigger"]
    assert trigger in DAYS, rows[0]["latch"]
    first = rows[0]
    assert first["latch"].get("active") is True
    assert first["latch"]["stage"] == 0
    assert first["latch"]["days_since_trigger"] == 0
    assert first["ts"] == min(row["ts"] for row in rows)
    per_day = {}
    for row in rows:
        latch = row["latch"]
        assert latch["days_since_trigger"] == \
            _day_index(row["day"]) - _day_index(trigger), (row["day"], latch)
        if latch.get("active"):
            # 已捕获（可能为空）的撮合前 scope，不因空目标反复 fail closed。
            assert "target_snapshot_missing" not in row["decision"]["reasons"], \
                row["decision"]
            assert row["decision"]["target_snapshot_trade_date"] == trigger
            assert row["decision"]["scale"] == STAGES[row["decision"]["stage"]]
        per_day.setdefault(row["day"], []).append(latch["stage"])
    # 跨周末：周末不得出现在事件时间线里，且计数按交易日索引（上面已断言）。
    for weekend in WEEKEND:
        assert weekend not in per_day
    ordered = [per_day[day] for day in DAYS if day in per_day]
    for stages in ordered:
        assert stages == sorted(stages), "同日多事件不得重复抬升阶段"
        assert max(stages) - min(stages) <= 1
    maxima = [max(stages) for stages in ordered]
    for prev, cur in zip(maxima, maxima[1:]):
        assert 0 <= cur - prev <= 1, "每真实交易日最多升一级"
    return rows


def _assert_frozen_replay(variant, pack):
    """T4：真实冻结解码 + 重放，逐事件完整对比而非仅 stage 列表。"""
    loaded = load_experiment_inputs(pack["freeze"])
    replayed = replay_frozen(pack["freeze"])
    assert len(loaded.events) == len(variant["events"])
    assert len(replayed["events"]) == len(variant["events"])
    for original, frozen in zip(variant["events"], replayed["events"]):
        assert frozen["recovery"] == original["recovery"]
        assert frozen["fills"] == original["fills"]
        assert frozen["state"] == original["state"]
        original_decision = ((original.get("decision_record") or {})
                             .get("bundle") or {}).get("recovery")
        frozen_decision = ((frozen.get("decision_record") or {})
                           .get("bundle") or {}).get("recovery")
        assert frozen_decision == original_decision
    assert replayed["final_state"] == variant["final_state"]


def test_public_cash_recovery_lifecycle_advances_and_graduates(
        tmp_path, monkeypatch):
    """T1 正式配置现金空 scope：真实触发→冷却→逐日推进→毕业→重触发。"""
    pack, variant = _experiment(
        tmp_path, monkeypatch, payload=_cash_with_recovery(_config()))
    rows = _rows(variant)
    assert rows
    first = rows[0]
    assert first["latch"]["active"] is True
    assert "drawdown_trigger" in first["decision"]["reasons"]
    assert first["latch"]["trigger_trade_date"] == DAYS[0]
    episodes = _episodes(rows)
    assert len(episodes) >= 2, "毕业后仍超阈值应重触发新 episode"
    for episode in episodes:
        _assert_episode_contract(episode)
    # 冷却未满：次日 days=1 < cooldown，不恢复、不升阶段。
    day_one = [row for row in rows if row["day"] == DAYS[1]]
    assert day_one and all(row["latch"]["days_since_trigger"] == 1
                           for row in day_one)
    assert all(row["latch"]["stage"] == 0 for row in day_one)
    assert all(row["decision"]["can_resume"] is False for row in day_one)
    # 冷却满足且条件齐全：确有一次真实恢复推进。
    resumed = [row for row in rows
               if row["decision"].get("can_resume") is True]
    assert resumed
    assert all(row["latch"]["days_since_trigger"] >= COOLDOWN
               for row in resumed)
    # 毕业：末阶段且回正常，旧目标快照清空。
    graduated = [row for row in rows
                 if row["latch"].get("graduated_trade_date")]
    assert graduated
    for row in graduated:
        assert row["latch"]["active"] is False
        assert row["latch"]["stage"] == len(STAGES) - 1
        assert dict(row["latch"]["contraction_targets"]) == {}
        assert row["latch"]["graduated_trade_date"] == row["day"]
    # 毕业后重触发：新 trigger_event/日期，不沿用初始 stage0 作为证据。
    for prev, nxt in zip(episodes, episodes[1:]):
        prior_grads = [row for row in prev["rows"]
                       if row["latch"].get("graduated_trade_date")]
        assert prior_grads, "每个 episode 应在窗口内毕业后才重触发"
        grad = prior_grads[-1]
        assert nxt["rows"][0]["ts"] > grad["ts"]
        assert nxt["trigger"] >= grad["latch"]["graduated_trade_date"]
        assert nxt["rows"][0]["latch"]["trigger_event"] > \
            prev["rows"][0]["latch"]["trigger_event"]
    _assert_frozen_replay(variant, pack)


def test_public_recovery_waits_for_missing_real_condition(tmp_path,
                                                          monkeypatch):
    """T3：冷却已满但缺真实 T-1 市场支持仍不升；条件恢复后逐日一级。"""
    def prep(day):
        if day == "2026-11-20":
            return (None, {"error": None})
        return _prep_reader(day)

    pack, variant = _experiment(
        tmp_path, monkeypatch, payload=_cash_with_recovery(_config()),
        prep_reader=prep)
    rows = _rows(variant)
    blocked = [row for row in rows if row["day"] == "2026-11-20"]
    assert blocked and all(row["latch"]["days_since_trigger"] == COOLDOWN
                           for row in blocked)
    for row in blocked:
        decision = row["decision"]
        assert "market_regime_verified" in decision["conditions_missing"]
        assert decision["can_resume"] is False
        assert row["latch"]["stage"] == 0
    # 条件恢复后的首个真实交易日只升一级，同日第二事件不再连续抬升。
    resumed = [row for row in rows if row["day"] == "2026-11-23"]
    assert resumed
    assert resumed[0]["decision"]["can_resume"] is True
    assert resumed[0]["decision"]["conditions_missing"] == []
    assert resumed[0]["latch"]["stage"] == 1
    assert resumed[0]["latch"]["days_since_trigger"] == 3
    assert all(row["latch"]["stage"] == 1 for row in resumed)
    # 末阶段在后一交易日毕业。
    graduation = next(row for row in rows
                      if row["latch"].get("graduated_trade_date"))
    assert graduation["day"] == "2026-11-24"
    assert graduation["latch"]["stage"] == len(STAGES) - 1
    _assert_frozen_replay(variant, pack)


def test_public_recovery_sells_held_batch_and_spares_new_batch(
        tmp_path, monkeypatch):
    """T2：真实冻结 owner 存量在收缩期产生 sell fill；现金/数量/T+1 正确。"""
    pack, variant = _experiment(
        tmp_path, monkeypatch, payload=_held_with_recovery(_config()))
    rows = _rows(variant)
    episodes = _episodes(rows)
    first = episodes[0]
    _assert_episode_contract(first)
    trigger_row = first["rows"][0]
    target = dict(trigger_row["latch"]["contraction_targets"])["600001"]
    assert list(target["batch_ids"]) == ["orig-b1:sellable"]
    assert target["target_qty"] == 500
    assert dict(target["remaining"]) == {"orig-b1:sellable": 1000}
    # 触发后同一交易日后续合格事件，真实减仓 500 股。
    sell_rows = [(index, fill) for index, row in enumerate(first["rows"])
                 for fill in row["fills"]
                 if fill["side"] == "sell"
                 and str(fill["batch_id"]).startswith("orig-b1")]
    assert len(sell_rows) == 1, sell_rows
    index, fill = sell_rows[0]
    assert fill["qty"] == 500
    assert first["rows"][index]["day"] == DAYS[0]
    previous_cash = first["rows"][index - 1]["state"]["cash"]
    expected_cash = previous_cash + fill["qty"] * fill["price"] - fill["fee"]
    assert math.isclose(first["rows"][index]["state"]["cash"], expected_cash,
                        rel_tol=1e-9, abs_tol=1e-6)
    remaining = {batch["batch_id"]: batch["qty"]
                 for batch in first["rows"][index]["state"]["batches"]}
    assert remaining["orig-b1:sellable"] == 500
    # 恢复期新买入批次：入场日不可卖（T+1），下一真实交易日可卖。
    new_ids = [batch["batch_id"]
               for row in first["rows"]
               for batch in row["state"]["batches"]
               if batch["entry_date"] == "2026-11-20"]
    assert new_ids, "恢复期应有新买入批次"
    new_batch = new_ids[0]
    entry_row = next(row for row in first["rows"]
                     if any(batch["batch_id"] == new_batch
                            for batch in row["state"]["batches"]))
    entry_batch = next(batch for batch in entry_row["state"]["batches"]
                       if batch["batch_id"] == new_batch)
    assert entry_batch["sellable"] == 0, "当日新批次不得卖出（T+1）"
    next_day_row = next(row for row in first["rows"]
                        if row["day"] == "2026-11-23")
    next_day_batch = next(batch for batch in next_day_row["state"]["batches"]
                          if batch["batch_id"] == new_batch)
    assert next_day_batch["sellable"] == 300
    # 旧 episode 只按触发时快照处理存量：不因新批次反复半仓，且目标不被超卖。
    old_sold = sum(fill["qty"] for row in first["rows"]
                   for fill in row["fills"]
                   if str(fill["batch_id"]).startswith("orig-b1"))
    assert old_sold == 500
    for row in first["rows"]:
        for intent in row["intents"]:
            if intent.get("action") == "sell":
                assert new_batch not in (intent.get("batch_ids") or [])
        for fill in row["fills"]:
            if fill["side"] == "sell":
                assert fill["batch_id"] != new_batch
    # 毕业后再触发按新存量重建快照（旧 episode 目标已清空，非旧目标复用）：
    # 新目标 = 原始量之半并按整手取整（(1000+300)/2 → 600）。
    assert len(episodes) >= 2
    rebuilt = dict(episodes[1]["rows"][0]["latch"]["contraction_targets"])
    assert set(rebuilt["600001"]["batch_ids"]) == {
        "orig-b1:sellable", new_batch}
    assert rebuilt["600001"]["target_qty"] == 600
    _assert_frozen_replay(variant, pack)


def test_public_legacy_episode_without_capture_stays_restricted(
        tmp_path, monkeypatch):
    """T1：旧 active episode 缺捕获证明仍受限，不用当前持仓重建过去 scope。"""
    payload = _held_with_recovery(_config())
    payload["recovery"] = {"active": True, "stage": 0,
                           "days_since_trigger": 5, "trigger_event": 1,
                           "trigger_trade_date": "2026-11-10"}
    pack, variant = _experiment(tmp_path, monkeypatch, payload=payload)
    rows = _rows(variant)
    assert rows
    for row in rows:
        decision = row["decision"]
        assert "target_snapshot_missing" in decision["reasons"]
        assert decision["can_resume"] is False
        assert dict(decision["contraction_targets"]) == {}
        assert decision["scale"] < 1.0
        assert row["latch"]["stage"] == 0
        assert not row["latch"].get("graduated_trade_date")
    _assert_frozen_replay(variant, pack)
