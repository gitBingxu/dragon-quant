"""G5 公共存量生命周期：趋势持有/回吐/时间失效与 scope（真实本地源装配）。

覆盖 main G5 验收 G5-1~G5-4：

- G5-1 趋势上行 + 回吐预算内 → 合法续持（无固定盈利整仓卖出）；趋势 down 与
  回吐预算破坏分别具名；价格/缺口/迟到/无合格当前价时趋势 ``unknown`` 并带
  原因，账户 as_of 的裸趋势不授权；
- G5-2 时间失效按**逐批**真实交易日持有期生效（混合批次/同 code 不同 owner
  不互相误退出），当前 state 的后续买入批次由 runtime 补全 held_days；
- G5-3 账户主题/压力按 as_of 保留并在公共审计中给出本地/账户来源，压力取
  保守最大值；
- G5-4 公共 G5 时间线 freeze → typed decode → replay 完整一致。

全部经公共 ``build_experiment_pack`` / ``run_bs_experiment``；只替换数据源
reader 与本地 raw 资产，不 patch facts/events/selection/runtime。
"""

from __future__ import annotations

import math
from datetime import date, timedelta

from dragon_quant.infra.assets import observations
from dragon_quant.infra.calibration import PRODUCTION_CALIBRATIONS, binding_for
from dragon_quant.research import bs_advice
from dragon_quant.research.bs_advice import assembly
from dragon_quant.research.bs_advice.freeze import (
    load_experiment_inputs, replay_frozen,
)
from dragon_quant.review_account.strategy.bs_policy import teaching_fixture
from dragon_quant.workspace import snapshots

DAYS = ["2026-11-18", "2026-11-19", "2026-11-20",
        "2026-11-23", "2026-11-24", "2026-11-25",
        "2026-11-26", "2026-11-27", "2026-11-30"]


def _calendar(days=DAYS) -> list[str]:
    cursor = date(2026, 10, 20)
    end = max(date.fromisoformat(day) for day in days) + timedelta(days=2)
    result = []
    while cursor <= end:
        if cursor.weekday() < 5:
            result.append(cursor.isoformat())
        cursor += timedelta(days=1)
    return result


def _config(**kwargs):
    return teaching_fixture(fixture=False, **kwargs)


def _daily(db_path, *, code="600001", skip=None, late_day=None,
           late_at=None, start="2026-10-20", start_price=9.0, end_price=9.9):
    """真实日 K：默认 2026-10-20..11-17 线性上行（6 个连续窗口用于 MA5）。"""
    days = [day for day in _calendar(["2026-11-17"])
            if start <= day <= "2026-11-17"]
    cid = PRODUCTION_CALIBRATIONS[0].calibration_id
    span = end_price - start_price
    closes = [start_price + span * index / (len(days) - 1)
              for index in range(len(days))]
    for index, day in enumerate(days):
        if day == skip:
            continue
        close = round(closes[index], 4)
        stamp = (late_at if day == late_day else f"{day}T15:05:00+08:00")
        observations.append_bar(
            trade_date=day, market="SH", code=code, period="daily",
            interval_start=f"{day}T09:30:00+08:00",
            interval_end=f"{day}T15:00:00+08:00", open=close, high=close,
            low=close, close=close, volume=1_000_000,
            amount=close * 1_000_000, source="tdx", observed_at=stamp,
            available_at=stamp, completeness="final",
            time_semantics="verified", unit_semantics="verified",
            adjustment="none", db_path=db_path,
            calibration=binding_for(cid, calibrated_at=stamp, source="tdx",
                                    period="daily"))


def _minute(db_path, day, code, close):
    """当日两条严格可执行 1min 观测（策略在第二事件撮合首事件下的单）。"""
    from datetime import datetime
    for start, end in (("09:59:00", "10:00:00"), ("10:00:00", "10:01:00")):
        end_moment = datetime.fromisoformat(f"{day}T{end}+08:00")
        available = (end_moment + timedelta(seconds=30)).isoformat()
        observations.append_bar(
            trade_date=day, market="SH", code=code, period="1min",
            interval_start=f"{day}T{start}+08:00",
            interval_end=f"{day}T{end}+08:00", open=close, high=close,
            low=close, close=close, volume=1_000_000,
            amount=close * 1_000_000, source="tdx",
            observed_at=available, available_at=available,
            completeness="final", time_semantics="verified",
            unit_semantics="verified", adjustment="none", db_path=db_path,
            calibration=binding_for(
                PRODUCTION_CALIBRATIONS[0].calibration_id,
                calibrated_at=available, source="tdx", period="1min"))


def _prep_reader(day, codes):
    before = (date.fromisoformat(day) - timedelta(days=1)).isoformat()
    while date.fromisoformat(before).weekday() >= 5:
        before = (date.fromisoformat(before) - timedelta(days=1)).isoformat()
    created = f"{before}T16:50:00+08:00"
    return ({"review_candidates": [{"code": code, "name": code,
                                    "trade_date": before,
                                    "eligibility": "eligible",
                                    "position_pct": 5.0} for code in codes],
             "market": {"state": "strong", "score": 80.0, "coeff": 1.0,
                        "partial": False, "missing_inputs": [],
                        "evidence": ["prep"]}},
            {"trade_date": before, "created_at": created, "error": None})


def _ref(config):
    return snapshots.builtin_ref(strategy_id="builtin:bs_account_decision",
                                 params={"bs_policy": config.to_dict()})


def _batch(batch_id, *, entry, qty=1000, cost=9.0, ref=None, plan="p1",
           family="f1", sellable=None):
    return {"batch_id": batch_id, "qty": qty,
            "sellable_qty": qty if sellable is None else sellable,
            "entry_date": entry, "cost_price": cost,
            "original_qty": qty, "initial_risk": 300.0,
            "plan_id": plan, "family_id": family, "strategy_ref": ref}


def _payload(config, batches, *, cash=50000.0, mark=10.1,
             trend="up", held_days=1, pressure=0.5, theme="themeA"):
    return {"schema_version": 1, "currency": "CNY", "shares_unit": "share",
            "as_of": "2026-11-17T16:00:00+08:00", "cash": cash,
            "positions": [{
                "code": "600001", "mark": mark, "trend": trend,
                "held_days": held_days, "pressure_pct": pressure,
                "theme_ids": [theme] if theme else [],
                "mark_source": {"price": mark, "source": "tdx",
                                "available_at": "2026-11-17T15:05:00+08:00"},
                "batches": list(batches)}]}


def _experiment(tmp_path, monkeypatch, *, config, payload, minutes,
                days=DAYS, daily=None, daily_options=None,
                prep_codes=("600001",)):
    """公共入口：临时本地 raw 日K/分钟 + 逐日严格 T-1 prep + pack/experiment。"""
    monkeypatch.setattr(
        assembly, "current_tdx_provenance",
        lambda: PRODUCTION_CALIBRATIONS[0].provenance)
    db_path = tmp_path / "assets.db"
    for option in (daily_options or (("600001", {}),)):
        code, kwargs = option
        if daily is None or daily:
            _daily(db_path, code=code, **(kwargs or {}))
    for day, rows in minutes.items():
        for code, close in rows.items():
            _minute(db_path, day, code, close)
    calendar = _calendar(days)
    account = bs_advice.parse_account_file(payload)
    policy = bs_advice.load_policy_payload(config.to_dict())
    readers = {
        "dragons_reader": lambda a, b: [{
            "code": "600001", "name": "龙一", "trade_date": "2026-11-17",
            "scan_id": "s", "is_true_dragon": True}],
        "prep_reader": lambda day: (None, {"error": None})
        if day not in days else _prep_reader(day, prep_codes),
        "calendar_reader": lambda as_of: calendar,
        "plan_reader": lambda day: None,
    }
    pack = bs_advice.build_experiment_pack(
        account=account, policy=policy, date_from=days[0], date_to=days[-1],
        db_path=db_path, calendar=calendar, **readers)
    result = bs_advice.run_bs_experiment(
        account=account, policy=policy, date_from=days[0], date_to=days[-1],
        db_path=db_path, calendar=calendar, pack=pack)
    return pack, result["variants"]["bs"]


def _rows(variant):
    rows = []
    for event in variant["events"]:
        record = event.get("decision_record") or {}
        rows.append({
            "day": event["trade_date"], "ts": int(event["timestamp"]),
            "bundle": dict(record.get("bundle") or {}),
            "intents": [dict(item) for item in (record.get("intents") or ())],
            "audit": dict(record.get("runtime_audit") or {}),
            "fills": [dict(item) for item in (event.get("fills") or ())],
            "state": dict(event.get("state") or {})})
    return rows


def _hold(row, code="600001"):
    return next((plan for plan in row["bundle"].get("plans") or ()
                 if plan.get("plan_kind") == "hold"
                 and plan.get("code") == code), None)


def _trend(row, code="600001"):
    return dict((row["audit"].get("trend_audit") or {}).get(code) or {})


def _obligations(row):
    return list((row["bundle"].get("audit") or {})
                .get("protection_obligations") or ())


def _assert_frozen_replay(variant, pack):
    """G5-4：真实冻结解码 + 重放，逐事件完整对比（非仅阶段列表）。"""
    loaded = load_experiment_inputs(pack["freeze"])
    digest = str(pack["freeze"]["digest"] if "digest" in pack["freeze"]
                 else pack.get("digest") or "")
    replayed = replay_frozen(pack["freeze"],
                             ledger_id=f"bs-exp:{digest[:16]}")
    assert len(loaded.events) == len(variant["events"])
    assert len(replayed["events"]) == len(variant["events"])
    for original, frozen in zip(variant["events"], replayed["events"]):
        assert frozen["decision_record"] == original["decision_record"]
        assert frozen["execution_record"] == original["execution_record"]
        assert frozen["fills"] == original["fills"]
        assert frozen["state"] == original["state"]
    assert replayed["final_state"] == variant["final_state"]


def test_g5_public_trend_supports_hold_and_down_blocks_trend_invalid(
        tmp_path, monkeypatch):
    """G5-1：趋势上行 + 回吐预算内合法续持；趋势 down 具名阻断。"""
    config = _config()
    payload = _payload(config, [_batch("old-a", entry="2026-11-17",
                                       ref=_ref(config))])
    minutes = {day: {"600001": 9.0} for day in DAYS}
    minutes["2026-11-18"] = {"600001": 10.1}
    pack, variant = _experiment(tmp_path, monkeypatch, config=config,
                                payload=payload, minutes=minutes,
                                days=DAYS[:5])
    rows = _rows(variant)
    first = rows[0]
    # 上行趋势：6 个连续完整日收盘 + 当前价在上升 MA5 之上。
    trend = _trend(first)
    assert trend["trend"] == "up"
    assert trend["reason"] == "price_above_rising_ma5"
    assert trend["price"] == 10.1
    assert len(trend["days"]) == 6
    assert trend["ma5"] > trend["prev_ma5"]
    hold = _hold(first)
    assert hold["executable"] is True and not hold["blocked_reasons"]
    assert first["bundle"]["selected_plan_id"] == "hold:600001"
    assert not [fill for row in rows if row["day"] == "2026-11-18"
                for fill in row["fills"] if fill["side"] == "sell"], \
        "趋势/预算正常时不得固定盈利数额自动整仓卖出"
    # 失去趋势：down 单独具名，回吐预算仍获授权（两者不混为同一授权）。
    second_day = [row for row in rows if row["day"] == "2026-11-19"]
    assert second_day
    for row in second_day:
        decision = _trend(row)
        assert decision["trend"] == "down"
        assert decision["reason"] == "price_below_ma5"
        blocked = list(_hold(row)["blocked_reasons"])
        assert "trend_invalid" in blocked
        assert not [reason for reason in blocked if reason.startswith("giveback")]
        assert row["bundle"]["audit"]["giveback"]["authorized"] is True
        reduce_plan = next(plan for plan in row["bundle"]["plans"]
                           if plan["plan_kind"] == "partial_reduce")
        assert reduce_plan["executable"] is True
    _assert_frozen_replay(variant, pack)


def test_g5_public_giveback_breach_is_named_and_fills(
        tmp_path, monkeypatch):
    """G5-1：回吐预算破坏单独具名（趋势仍 up），真实退出 fill 与现金核对。"""
    config = _config(budgets={
        "per_trade": 1000.0, "account": 3000.0, "per_theme": 1500.0,
        "unknown_group": 1000.0, "giveback": 1.0, "held_principal": 2000.0})
    payload = _payload(config, [_batch("old-a", entry="2026-11-17",
                                       ref=_ref(config))])
    minutes = {day: {"600001": 10.1} for day in DAYS[:2]}
    pack, variant = _experiment(tmp_path, monkeypatch, config=config,
                                payload=payload, minutes=minutes,
                                days=DAYS[:2])
    rows = _rows(variant)
    first = rows[0]
    giveback = first["bundle"]["audit"]["giveback"]
    assert giveback["authorized"] is False
    assert giveback["reasons"] == ["giveback_budget_exceeded"]
    assert giveback["required"] == 550.0 and giveback["remaining"] == 1.0
    assert _trend(first)["trend"] == "up", "趋势与回吐分别判定"
    assert list(_hold(first)["blocked_reasons"]) == ["giveback_budget_exceeded"]
    obligations = [item for item in _obligations(first)
                   if item["kind"] == "giveback"]
    assert obligations and obligations[0]["action"] == "reduce"
    assert obligations[0]["qty"] == 500
    assert obligations[0]["batch_ids"] == ["old-a:sellable"]
    # 真实成交：回吐预算义务 500 股减仓，费用/现金与剩余量可核对。
    fill_row = next(row for row in rows
                    if any(fill["side"] == "sell" for fill in row["fills"]))
    previous_cash = rows[rows.index(fill_row) - 1]["state"]["cash"]
    proceeds = sum(fill["qty"] * fill["price"] - fill["fee"]
                   for fill in fill_row["fills"] if fill["side"] == "sell")
    assert math.isclose(fill_row["state"]["cash"], previous_cash + proceeds,
                        rel_tol=1e-9, abs_tol=1e-6)
    sells = [fill for fill in fill_row["fills"] if fill["side"] == "sell"]
    assert sells and all(fill["batch_id"] == "old-a:sellable"
                         for fill in sells)
    remaining = {batch["batch_id"]: batch["qty"]
                 for batch in fill_row["state"]["batches"]}
    assert remaining == {}
    _assert_frozen_replay(variant, pack)


def test_g5_public_time_invalidation_is_batch_scoped(tmp_path, monkeypatch):
    """G5-2/M2：必须保护按 batch scope；自主 full_exit 表达剩余 scope 全退。"""
    config = _config()
    payload = _payload(config, [
        _batch("old-a", entry="2026-11-09", ref=_ref(config)),
        _batch("new-b", entry="2026-11-17", cost=10.0, ref=_ref(config),
               plan="p2", family="f2")])
    minutes = {"2026-11-18": {"600001": 10.1}}
    pack, variant = _experiment(tmp_path, monkeypatch, config=config,
                                payload=payload, minutes=minutes,
                                days=["2026-11-18"])
    rows = _rows(variant)
    assert rows
    held = rows[0]["audit"]["held_days_by_batch"]
    assert held == {"old-a:sellable": 7, "new-b:sellable": 1}
    obligations = _obligations(rows[0])
    # 必需保护只覆盖到期旧批，且按 batch scope/数量。
    assert [(item["kind"], item["batch_ids"], item["qty"])
            for item in obligations] == [
        ("time_invalidation", ["old-a:sellable"], 1000)]
    plans = {plan["plan_kind"]: plan for plan in rows[0]["bundle"]["plans"]
             if plan.get("code") == "600001"}
    # 自主方案只作用于抵扣后的剩余 scope（new-b）；不冒充 code 聚合全退。
    def autonomous(plan):
        return [a for a in plan["actions"] if not a["obligation_id"]]

    hold = plans["hold"]
    assert hold["executable"] is True and not hold["blocked_reasons"]
    assert [(a["action"], a["batch_ids"]) for a in autonomous(hold)] == [
        ("hold", ["new-b:sellable"])]
    full_exit = plans["full_exit"]
    assert full_exit["executable"] is True
    assert [(a["action"], a["delta_qty"], a["batch_ids"])
            for a in autonomous(full_exit)] == [
        ("exit", -1000, ["new-b:sellable"])]
    assert [(leg["reason"], leg["qty"], leg["batch_ids"])
            for leg in full_exit["legs"]] == [
        ("forced_exit", 1000, ["new-b:sellable"])]
    partial = plans["partial_reduce"]
    assert [(a["delta_qty"], a["batch_ids"]) for a in autonomous(partial)] == [
        (-500, ["new-b:sellable"])]
    # 本数据场景比较选中 full_exit：旧批由必需保护卖出，新批由自主全退
    # 卖出（独立计划理由/scope，不是旧批到期聚合误退）。
    assert rows[0]["bundle"]["selected_plan_id"] == "full_exit:600001"
    intents = [(i["action"], i["qty"], i["plan_id"], i["obligation_id"],
                i["batch_ids"]) for i in rows[0]["intents"]
               if i["action"] == "sell"]
    assert intents == [
        ("sell", 1000, "p1", "time_invalidation:600001:old-a:sellable",
         ["old-a:sellable"]),
        ("sell", 1000, "full_exit:600001", "", ["new-b:sellable"])]
    assert all(i[4] for i in intents), "不得出现无 batch scope 的聚合卖单"
    fills = [fill for row in rows for fill in row["fills"]
             if fill["side"] == "sell"]
    assert sorted((fill["qty"], fill["batch_id"]) for fill in fills) == [
        (1000, "new-b:sellable"), (1000, "old-a:sellable")]
    assert not rows[-1]["state"]["batches"]
    # 仿真与账本同 scope：full_exit 的每个场景 outcome 都真正退出活动 owner
    # 全部余量（必需保护旧批 + 自主 remaining scope），terminal=exited。
    matrix = rows[0]["bundle"]["outcome_matrix"]["full_exit:600001"]
    assert matrix
    for outcome in matrix.values():
        assert outcome["terminal_status"] == "exited", outcome
        timeline = outcome.get("timeline") or ()
        sold = [fill["batch_id"] for step in timeline
                for fill in step.get("fills") or ()
                if fill.get("side") == "sell"]
        assert sorted(sold) == ["new-b:sellable", "old-a:sellable"], outcome
        reasons = {exit_["reason"] for step in timeline
                   for exit_ in (step.get("policy_state") or {})
                   .get("exits") or () if exit_.get("reason")}
        assert "full_exit:600001" in reasons, outcome
    _assert_frozen_replay(variant, pack)


def test_g5_public_same_code_owner_scope_does_not_cross(tmp_path, monkeypatch):
    """G5-2/M1：同 code 不同冻结 owner 逐批保护，不串卖其它归属批次。"""
    config = _config()
    other = _config(budgets={
        "per_trade": 1000.0, "account": 3000.0, "per_theme": 1500.0,
        "unknown_group": 1000.0, "giveback": 700.0, "held_principal": 2000.0})
    payload = _payload(config, [
        _batch("old-a", entry="2026-11-09", ref=_ref(config)),
        _batch("own-b", entry="2026-11-17", cost=10.0, ref=_ref(other),
               plan="p2", family="f2")])
    minutes = {"2026-11-18": {"600001": 10.1}}
    pack, variant = _experiment(tmp_path, monkeypatch, config=config,
                                payload=payload, minutes=minutes,
                                days=["2026-11-18"])
    rows = _rows(variant)
    obligations = _obligations(rows[0])
    assert [(item["kind"], item["batch_ids"]) for item in obligations] == [
        ("time_invalidation", ["old-a:sellable"])]
    assert obligations[0]["owner"]["params_hash"] == _ref(config)["params_hash"]
    plans = {plan["plan_kind"]: plan for plan in rows[0]["bundle"]["plans"]
             if plan.get("code") == "600001"}
    # 活动 owner 剩余 scope 已被必需保护覆盖：hold 无自主余量；减仓无自主目标；
    # 全退可执行但只由逐批必需保护表达（无自主全退动作），不卖他人批次。
    assert plans["hold"]["executable"] is False
    assert "protection_obligation_requires_exit" in plans["hold"]["blocked_reasons"]
    assert plans["partial_reduce"]["executable"] is False
    assert "remaining_scope_covered_by_protection" in \
        plans["partial_reduce"]["blocked_reasons"]
    assert plans["full_exit"]["executable"] is True
    assert [a for a in plans["full_exit"]["actions"] if not a["obligation_id"]] == []
    assert any("no autonomous full exit" in note
               for note in plans["full_exit"]["notes"])
    sell_intents = [intent for intent in rows[0]["intents"]
                    if intent["action"] == "sell"]
    assert [(intent["qty"], intent["batch_ids"]) for intent in sell_intents] == [
        (1000, ["old-a:sellable"])]
    assert all(intent["batch_ids"] for intent in sell_intents)
    fill_row = next(row for row in rows if row["fills"])
    remaining = {batch["batch_id"]: batch["owner"]["params_hash"]
                 for batch in fill_row["state"]["batches"]}
    assert set(remaining) == {"own-b:sellable"}
    assert remaining["own-b:sellable"] == _ref(other)["params_hash"]
    _assert_frozen_replay(variant, pack)


def test_g5_public_other_owner_protection_does_not_block_active_hold(
        tmp_path, monkeypatch):
    """M1：other owner 旧批必需保护不阻断 active 合格新批的自主 hold。"""
    config = _config()
    other = _config(budgets={
        "per_trade": 1000.0, "account": 3000.0, "per_theme": 1500.0,
        "unknown_group": 1000.0, "giveback": 700.0, "held_principal": 2000.0})
    payload = _payload(config, [
        _batch("old-other", entry="2026-11-09", ref=_ref(other)),
        _batch("new-active", entry="2026-11-17", cost=10.0,
               ref=_ref(config), plan="p2", family="f2")])
    minutes = {"2026-11-18": {"600001": 10.1}}
    pack, variant = _experiment(
        tmp_path, monkeypatch, config=config, payload=payload,
        minutes=minutes, days=["2026-11-18"],
        daily_options=(("600001", {"start_price": 9.8,
                                   "end_price": 10.0}),))
    rows = _rows(variant)
    obligations = _obligations(rows[0])
    assert [(item["kind"], item["batch_ids"]) for item in obligations] == [
        ("time_invalidation", ["old-other:sellable"])]
    hold = _hold(rows[0])
    # active 新批的 hold 不被他人义务阻断，且自主 hold scope 只含自身批次
    # （他人必需保护动作仍注入并可执行）。
    assert hold["executable"] is True and not hold["blocked_reasons"]
    autonomous = [a for a in hold["actions"] if not a["obligation_id"]]
    assert [(a["action"], a["batch_ids"]) for a in autonomous] == [
        ("hold", ["new-active:sellable"])]
    injected = [a for a in hold["actions"] if a["obligation_id"]]
    assert [a["batch_ids"] for a in injected] == [["old-other:sellable"]]
    assert rows[0]["bundle"]["selected_plan_id"] == "hold:600001"
    sell_intents = [intent for intent in rows[0]["intents"]
                    if intent["action"] == "sell"]
    assert [(intent["qty"], intent["batch_ids"]) for intent in sell_intents] == [
        (1000, ["old-other:sellable"])]
    fill_row = next(row for row in rows if row["fills"])
    remaining = {batch["batch_id"]: batch["owner"]["params_hash"]
                 for batch in fill_row["state"]["batches"]}
    assert set(remaining) == {"new-active:sellable"}
    assert remaining["new-active:sellable"] == _ref(config)["params_hash"]
    _assert_frozen_replay(variant, pack)


def test_g5_public_later_batch_held_days_come_from_current_state(
        tmp_path, monkeypatch):
    """G5-2：期初账户不含后来买入批次时，runtime 按当前 state 补全持有期。"""
    config = _config(budgets={
        "per_trade": 1000.0, "account": 3000.0, "per_theme": 1500.0,
        "unknown_group": 1000.0, "giveback": 3000.0, "held_principal": 2000.0})
    payload = _payload(config, [_batch("old-a", entry="2026-11-13",
                                       ref=_ref(config))], pressure=0.05,
                       theme=None)
    minutes = {day: {"600001": 9.0} for day in DAYS}
    minutes["2026-11-18"] = {"600001": 10.1}
    for day in ("2026-11-24", "2026-11-25", "2026-11-26",
                "2026-11-27", "2026-11-30"):
        minutes[day] = {"600001": 10.1}
    pack, variant = _experiment(tmp_path, monkeypatch, config=config,
                                payload=payload, minutes=minutes)
    rows = _rows(variant)
    assert [batch["batch_id"] for batch in payload["positions"][0]["batches"]] \
        == ["old-a"]
    bought = next(row for row in rows
                  if any(fill["side"] == "buy" for fill in row["fills"]))
    new_batch = next(fill["batch_id"] for fill in bought["fills"]
                     if fill["side"] == "buy")
    assert new_batch not in {"old-a:sellable"}
    entry = next(batch for batch in bought["state"]["batches"]
                 if batch["batch_id"] == new_batch)
    assert entry["sellable"] == 0, "新买入当日不可卖（T+1）"
    # 后续事件由当前 state 补全持有期并随真实交易日历递增；as-of 卖出才释放。
    later = [row for row in rows if row["day"] >= "2026-11-24"]
    entry_index = DAYS.index("2026-11-23")
    for row in later:
        held = row["audit"]["held_days_by_batch"]
        batch = next((item for item in row["state"]["batches"]
                      if item["batch_id"] == new_batch), None)
        if batch is None:
            continue
        expected = DAYS.index(row["day"]) - entry_index
        assert held[new_batch] == expected, (row["day"], held)
        if expected == 1:
            assert batch["sellable"] == batch["qty"], "次真实交易日释放"
    # 达到配置上限后同一新批次被逐批时间失效真实退出。
    expired = [row for row in rows
               if any(item["kind"] == "time_invalidation"
                      and item["batch_ids"] == [new_batch]
                      for item in _obligations(row))]
    assert expired
    final = expired[0]
    assert final["day"] == "2026-11-30"
    assert final["audit"]["held_days_by_batch"][new_batch] == 5
    assert "protection_obligation_requires_exit" in \
        _hold(final)["blocked_reasons"]
    exit_fill = next(fill for row in rows for fill in row["fills"]
                     if fill["side"] == "sell" and fill["batch_id"] == new_batch)
    assert exit_fill["qty"] == 1500
    _assert_frozen_replay(variant, pack)


def test_g5_public_unknown_trend_reasons_keep_existing_protection(
        tmp_path, monkeypatch):
    """G5-1 负例：缺日K/非连续/迟到/无合格当前价 → unknown 带原因，保护仍生效。"""
    config = _config()
    ref = _ref(config)
    # 缺日 K：账户裸 trend=up 不授权；旧批时间失效保护仍真实退出。
    payload = _payload(config, [_batch("old-a", entry="2026-11-09",
                                       ref=ref)])
    minutes = {day: {"600001": 10.1} for day in DAYS[:2]}
    pack, variant = _experiment(tmp_path, monkeypatch, config=config,
                                payload=payload, minutes=minutes,
                                days=DAYS[:2], daily=False)
    rows = _rows(variant)
    first = rows[0]
    trend = _trend(first)
    assert trend["trend"] == "unknown"
    assert trend["reason"] == "insufficient_daily_closes"
    assert "trend_unverified" in _hold(first)["blocked_reasons"]
    assert [item["kind"] for item in _obligations(first)] == [
        "time_invalidation"]
    fill_row = next(row for row in rows if row["fills"])
    assert [fill["batch_id"] for fill in fill_row["fills"]
            if fill["side"] == "sell"] == ["old-a:sellable"]
    _assert_frozen_replay(variant, pack)

    # 非连续窗口：缺一个交易日的收盘 → 明确原因，不拼凑 MA5。
    payload = _payload(config, [_batch("old-a", entry="2026-11-17",
                                       ref=ref)])
    minutes = {"2026-11-18": {"600001": 10.1}}
    pack, variant = _experiment(tmp_path / "gap", monkeypatch, config=config,
                                payload=payload, minutes=minutes,
                                days=["2026-11-18"],
                                daily_options=(("600001", {"skip":
                                                           "2026-11-12"}),))
    trend = _trend(_rows(variant)[0])
    assert trend["trend"] == "unknown"
    assert trend["reason"] == "non_consecutive_trading_days"
    assert "2026-11-12" not in trend["days"]

    # 迟到（未来）日收盘：事件时不可得 → 不进入窗口；余下不足 6 根即 unknown。
    payload = _payload(config, [_batch("old-a", entry="2026-11-17",
                                       ref=ref)])
    pack, variant = _experiment(tmp_path / "late", monkeypatch, config=config,
                                payload=payload, minutes=minutes,
                                days=["2026-11-18"],
                                daily_options=(("600001", {
                                    "start": "2026-11-10",
                                    "late_day": "2026-11-17",
                                    "late_at": "2026-11-18T15:05:00+08:00"}),))
    trend = _trend(_rows(variant)[0])
    assert trend["trend"] == "unknown"
    assert trend["reason"] == "insufficient_daily_closes"
    assert "2026-11-17" not in (trend.get("days") or ())

    # 没有合格当前价：事件由其它代码触发时，持仓代码趋势必须 unknown。
    payload = _payload(config, [_batch("old-a", entry="2026-11-17",
                                       ref=ref)])
    minutes = {"2026-11-18": {"600001": 10.1, "600002": 10.0},
               "2026-11-19": {"600002": 10.0}}
    pack, variant = _experiment(tmp_path / "noprice", monkeypatch,
                                config=config, payload=payload,
                                minutes=minutes, days=DAYS[:2],
                                prep_codes=("600001", "600002"),
                                daily_options=(("600001", {}),
                                               ("600002", {})))
    rows = _rows(variant)
    second = [row for row in rows if row["day"] == "2026-11-19"]
    assert second
    trend = _trend(second[0])
    assert trend["trend"] == "unknown"
    assert trend["reason"] == "current_price_missing"
    assert "trend_unverified" in _hold(second[0])["blocked_reasons"]
    _assert_frozen_replay(variant, pack)


def test_g5_public_theme_pressure_audit_is_conservative_and_public(
        tmp_path, monkeypatch):
    """G5-3：账户主题/压力按 as_of 保留，审计给出本地/账户来源，压力取最大。"""
    config = _config()
    payload = _payload(config, [_batch("old-a", entry="2026-11-17",
                                       ref=_ref(config))])
    minutes = {"2026-11-18": {"600001": 10.1}}
    pack, variant = _experiment(tmp_path, monkeypatch, config=config,
                                payload=payload, minutes=minutes,
                                days=["2026-11-18"])
    row = _rows(variant)[0]
    trend = _trend(row)
    themes = trend["themes"]
    assert themes["merged"] == ["themeA"]
    assert themes["account_checkpoint"] == ["themeA"]
    assert themes["source"] == "account_input_checkpoint"
    pressure = trend["pressure"]
    assert pressure["account_checkpoint"] == 0.5
    assert pressure["effective"] == 0.5
    assert pressure["source"] == "max_local_and_account_checkpoint"
    assert pressure["local"] is not None and pressure["local"] < 0.5
    checks = {check["check"]: check for check in
              row["bundle"]["risk_checks"] if "check" in check}
    assert checks["per_theme"]["values"]["themeA"] == \
        round(10.1 * 1000 * 0.5, 6)
    assert checks["unknown_group"]["value"] == 0.0
    giveback = row["bundle"]["audit"]["giveback"]
    assert giveback["required"] == 550.0, "回吐压力按保守有效值"
    _assert_frozen_replay(variant, pack)
