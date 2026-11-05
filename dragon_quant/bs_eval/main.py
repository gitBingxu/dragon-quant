"""``bs-eval`` 用例：选行、run 记录、增量/force/limit 与计数器。"""

from __future__ import annotations

import hashlib
import json
from typing import Optional

from dragon_quant.bs_eval import constants, sources, storage
from dragon_quant.bs_eval.evaluator import BsEvaluator
from dragon_quant.infra import clock
from dragon_quant.review_account.models import StrategyConfig

SHANGHAI = "Asia/Shanghai"


def _normalize_date(value: Optional[str], field: str) -> Optional[str]:
    if value is None or value == "":
        return None
    text = str(value).strip()
    if len(text) == 8 and text.isdigit():
        text = f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    try:
        import datetime as dt

        return dt.datetime.strptime(text, "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError as exc:
        raise ValueError(f"{field} 日期格式非法: {value}") from exc


def _engine_config() -> StrategyConfig:
    """默认策略配置的副本；仅覆盖引擎记账名义资金（不模拟资金/仓位）。"""

    base = StrategyConfig(source="v2")
    values = dict(base.to_json_dict())
    values["initial_cash"] = constants.BS_EVAL_NOTIONAL_CASH
    return StrategyConfig.from_dict(values)


def _strategy_hash(cfg: StrategyConfig) -> str:
    blob = json.dumps(
        {"config": cfg.to_json_dict(), "evaluator_version": constants.EVALUATOR_VERSION},
        ensure_ascii=False, sort_keys=True, default=str,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _market_data(services):
    from dragon_quant.review_account.data import MarketData

    provider = None
    if services is not None and getattr(services, "providers", None) is not None:
        provider = services.providers.xueqiu
    return MarketData(provider=provider)


def _select(rows: list[dict], existing: dict, *, force: bool,
            limit: Optional[int]) -> tuple[list[dict], int]:
    pending: list[tuple[str, str, str, dict]] = []
    skipped = 0
    for row in rows:
        key = (row["trade_date"], row["code"])
        current = existing.get(key)
        if not force and current is not None and int(current.get("is_terminal") or 0):
            skipped += 1
            continue
        pending.append((
            (current or {}).get("last_attempt_at") or "",
            row["trade_date"], row["code"], row,
        ))
    pending.sort(key=lambda item: (item[0], item[1], item[2]))
    selected = [item[3] for item in pending]
    if limit is not None:
        selected = selected[:limit]
    return selected, skipped


def run_bs_eval(*, date_from: Optional[str] = None,
                date_to: Optional[str] = None, limit: Optional[int] = None,
                force: bool = False, source: str = "v2",
                services=None, data=None, verbose: bool = True) -> dict:
    """批量回放全部 ``dragons_v2`` 行并落库；返回批次汇总。"""

    if (source or "").lower() != "v2":
        raise ValueError("bs-eval 只支持 --source v2")
    if limit is not None and int(limit) <= 0:
        raise ValueError("--limit 必须大于 0")
    date_from = _normalize_date(date_from, "--from")
    date_to = _normalize_date(date_to, "--to")
    if date_from and date_to and date_from > date_to:
        raise ValueError("--from 不能晚于 --to")

    started = clock.monotonic()
    engine_cfg = _engine_config()
    strategy_hash = _strategy_hash(engine_cfg)

    rows = sources.list_dragons(date_from, date_to)
    latest = storage.latest_run()
    auto_force = bool(
        latest and (latest.get("strategy_hash") != strategy_hash
                    or latest.get("evaluator_version") != constants.EVALUATOR_VERSION)
    )
    effective_force = bool(force or auto_force)
    note = (constants.NOTE_STRATEGY_OR_EVALUATOR_CHANGED if auto_force else None)

    counters = {key: 0 for key in storage.COUNTER_KEYS}
    counters["rows_scanned"] = len(rows)

    replay_end = None
    calendar: list[str] = []
    evaluator = None
    if rows:
        data = data or _market_data(services)
        start = date_from or rows[0]["trade_date"]
        today = clock.now(_shanghai()).strftime("%Y-%m-%d")
        calendar = data.calendar(start, today)
        if not calendar:
            raise ValueError("交易日历为空，无法回放")
        replay_end = calendar[-1]
        evaluator = BsEvaluator(engine_cfg, data, calendar, replay_end)

    run_id = storage.start_run(
        range_from=date_from, range_to=date_to, limit_n=limit,
        force=effective_force, evaluator_version=constants.EVALUATOR_VERSION,
        strategy_name=engine_cfg.strategy_name,
        strategy_params=engine_cfg.to_json_dict(), strategy_hash=strategy_hash,
    )

    existing = storage.list_existing(date_from, date_to)
    selected, skipped = _select(
        rows, existing, force=effective_force, limit=limit)
    counters["rows_skipped_terminal"] = skipped

    for row in selected:
        try:
            payload = evaluator.evaluate(row)
        except Exception as exc:  # noqa: BLE001 - 单行隔离，继续其它行
            note_text = f"{type(exc).__name__}: {exc}"[:200]
            payload = evaluator.cannot_evaluate(row, note_text, note_text)
        payload.update({
            "trade_date": row["trade_date"],
            "code": row["code"],
            "name": row.get("name") or "",
            "source": "v2",
            "run_id": run_id,
            "strategy_hash": strategy_hash,
            "evaluator_version": constants.EVALUATOR_VERSION,
        })
        storage.upsert_signal(payload, force=effective_force)
        counters["rows_evaluated"] += 1
        status = payload.get("eval_status")
        if status == constants.STATUS_NO_SIGNAL:
            counters["rows_no_signal"] += 1
        if status in (constants.STATUS_OPEN, constants.STATUS_CLOSED):
            counters["rows_bought"] += 1
        if status == constants.STATUS_CLOSED:
            counters["rows_sold"] += 1
        if status == constants.STATUS_CANNOT_EVALUATE:
            counters["rows_error"] += 1
        if int(payload.get("buy_window_partial") or 0) \
                or int(payload.get("sell_window_partial") or 0):
            counters["rows_partial"] += 1

    storage.finish_run(run_id, counters, note=note)
    elapsed = round(clock.monotonic() - started, 2)

    return {
        "ok": True,
        "run_id": run_id,
        "range_from": date_from,
        "range_to": date_to,
        "replay_end": replay_end,
        "strategy_hash": strategy_hash,
        "evaluator_version": constants.EVALUATOR_VERSION,
        "force": effective_force,
        "auto_force": auto_force,
        "note": note,
        "elapsed_s": elapsed,
        "counters": counters,
    }


def _shanghai():
    from zoneinfo import ZoneInfo

    return ZoneInfo(SHANGHAI)


__all__ = ["run_bs_eval"]
