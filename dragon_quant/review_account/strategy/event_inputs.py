"""事件输入适配（纯叶子）：行情行 → 报价/标记事实，实际输入 → 稳定来源身份。

从 :mod:`account_event` 抽出的独立变化原因：数据源字段/新鲜度/身份口径变化只改
这里，事件编排（决策/撤销/撮合/恢复）不变。本模块不读 DB/时钟，不 import
ledger/store 或策略运行时。
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Mapping, Optional

from dragon_quant.review_account.execution_core.matching import ExecutionQuote
from dragon_quant.review_account.execution_core.models import LedgerError
from dragon_quant.review_account.execution_core.runtime_facts import (
    MARK_QUOTE, mark_prices, reduce_mark_facts,
)

_CST = timezone(timedelta(hours=8))

#: 来源哈希语义版本：纳入全部实际决定输入（F5）。
SOURCE_HASH_VERSION = "c3b3/1"


def iso(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=_CST).isoformat()


def hash_payload(payload) -> str:
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      allow_nan=False)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def canonical_input(value, *, depth: int = 0):
    """把实际消费的决定输入规范化成稳定 JSON 值（不偷偷忽略不支持的类型）。"""
    if depth > 32:
        raise LedgerError("决定输入嵌套过深，拒绝生成不稳定来源哈希")
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value
    if isinstance(value, (list, tuple)):
        return [canonical_input(item, depth=depth + 1) for item in value]
    if isinstance(value, Mapping):
        return {str(key): canonical_input(item, depth=depth + 1)
                for key, item in value.items()}
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return canonical_input(to_dict(), depth=depth + 1)
    raise LedgerError(
        f"不支持的决策输入类型（无法稳定序列化）: {type(value).__name__}")


def runtime_fingerprint(runtime, cost_model, *, max_quote_age_ms: Optional[int],
                        order_ttl_ms: Optional[int], calendar,
                        allow_unknown_risk: bool) -> dict:
    """真实执行授权配置/完整 ref/费用/新鲜度等控制进入身份（F5）。"""
    return {
        "ref": canonical_input(dict(getattr(runtime, "strategy_ref", {}) or {})),
        "config_hash": str(getattr(getattr(runtime, "config", None),
                                   "content_hash", "") or ""),
        "cost_model": {
            "commission_rate": cost_model.commission_rate,
            "min_commission": cost_model.min_commission,
            "stamp_tax_rate": cost_model.stamp_tax_rate,
            "buy_slippage": cost_model.buy_slippage,
            "sell_slippage": cost_model.sell_slippage,
            "lot_size": cost_model.lot_size,
        },
        "max_quote_age_ms": max_quote_age_ms,
        "order_ttl_ms": order_ttl_ms,
        "calendar": list(calendar or ()),
        "allow_unknown_risk": bool(allow_unknown_risk),
    }


def source_hash(event, *, decide: bool, decision_kwargs,
                capacity_override, candidates, fingerprint) -> str:
    """F5：所有实际消费的行情事实与决定输入进入稳定来源身份。"""
    rows = {str(code): canonical_input(row)
            for code, row in (getattr(event, "rows", {}) or {}).items()}
    kwargs = decision_kwargs or {}
    return hash_payload({
        "source_hash_version": SOURCE_HASH_VERSION,
        "timestamp": int(event.timestamp),
        "event_time": iso(int(event.timestamp)),
        "trade_date": getattr(event, "trade_date", ""),
        "phase": getattr(event, "phase", ""),
        "quotes": rows,
        "unavailable": canonical_input(
            dict(getattr(event, "unavailable", {}) or {})),
        "last_closes": canonical_input(
            dict(getattr(event, "last_closes", {}) or {})),
        "evidence_available": canonical_input(sorted(
            str(item) for item in
            (getattr(event, "evidence_available", ()) or ()))),
        "decide": bool(decide),
        "candidates": canonical_input(list(candidates or ())),
        "decision_inputs": canonical_input(dict(kwargs)),
        "capacity_override": canonical_input(dict(capacity_override or {})),
        "runtime": fingerprint,
    })


def update_marks(state, event, timestamp: int, *,
                 max_quote_age_ms: Optional[int]) -> None:
    """标记事实（A1）：源时点/可得时点不被事件时钟覆盖，旧收盘不冒充可执行价。"""
    facts, _reasons = reduce_mark_facts(
        getattr(state, "mark_facts", None) or {},
        rows=getattr(event, "rows", {}) or {},
        last_closes=getattr(event, "last_closes", {}) or {},
        event_ts=int(timestamp), max_age_ms=max_quote_age_ms)
    state.mark_facts = facts
    state.marks = mark_prices(facts)


def current_equity(state, event_ts: int, *,
                   max_age_ms: Optional[int] = None) -> Optional[float]:
    """本事件**已可得可执行报价**下的账户权益；任一持仓缺新鲜报价返回 None。

    ``last_close`` 旧估值、stale 报价与超出显式新鲜度规格的旧 quote 都不证明
    当前风险（main M1），不参与本事件回撤/新增风险授权。未声明年龄规格时只
    接受本次事件观测（``available_at == event_ts``）的报价。
    """
    prices: dict[str, float] = {}
    for code, fact in (getattr(state, "mark_facts", {}) or {}).items():
        if fact.kind != MARK_QUOTE or fact.stale:
            continue
        available = int(fact.available_at)
        if available > int(event_ts):
            continue
        if max_age_ms is None:
            if available != int(event_ts):
                continue
        elif int(event_ts) - available > int(max_age_ms):
            continue
        prices[str(code)] = float(fact.price)
    total = float(state.cash)
    for batch in state.batches:
        if batch.remaining <= 0:
            continue
        price = prices.get(batch.code)
        if price is None:
            return None
        total += price * batch.remaining
    return total


def quote(event, code: str, day: str, *,
          max_quote_age_ms: Optional[int]) -> ExecutionQuote:
    unavailable = getattr(event, "unavailable", {}) or {}
    row = (getattr(event, "rows", {}) or {}).get(code)
    event_ts = int(getattr(event, "timestamp", 0))
    exec_ts = available_ms = price = None
    detail = ""
    if code in unavailable:
        detail = unavailable[code]
    if row is not None:
        exec_ts = row.get("execution_timestamp")
        if exec_ts is None:
            exec_ts = row.get("observed_at")
        available_ms = row.get("available_at")
        if available_ms is None:
            available_ms = row.get("observed_at") or exec_ts
        price = row.get("execution_price")
    used_ts = int(exec_ts) if isinstance(exec_ts, int) else None
    available_at = iso(int(available_ms)) if isinstance(
        available_ms, int) else ""
    tradable, reason = True, detail
    if row is None:
        tradable, reason = False, detail or "no_quote"
    elif not isinstance(price, (int, float)) or isinstance(price, bool) \
            or price <= 0:
        tradable, reason = False, reason or "price_missing"
    elif used_ts is None:
        # P1：缺源执行时点不能补当前事件时钟冒充可执行报价。
        tradable, reason = False, reason or "quote_time_missing"
    elif used_ts > event_ts or (isinstance(available_ms, int)
                                and available_ms > event_ts):
        tradable, reason = False, reason or "quote_from_future"
    elif isinstance(available_ms, int) and available_ms < used_ts:
        tradable, reason = False, reason or "quote_available_before_source"
    elif max_quote_age_ms is not None \
            and event_ts - used_ts > int(max_quote_age_ms):
        tradable, reason = False, reason or "quote_too_old"
    else:
        if row.get("stale"):
            tradable, reason = False, "quote_stale"
        elif row.get("tradable") is False:
            tradable, reason = False, "not_tradable"
        elif row.get("limit_up") is not None \
                and price >= float(row["limit_up"]) - 1e-6:
            tradable, reason = False, "limit_up_locked"
        elif row.get("limit_down") is not None \
                and price <= float(row["limit_down"]) + 1e-6:
            tradable, reason = False, "limit_down_locked"
    capacity = row.get("available_qty") if row is not None else None
    if capacity is not None and (isinstance(capacity, bool)
                                 or not isinstance(capacity, int)):
        capacity = None
    quote_ts = used_ts if used_ts is not None else event_ts
    return ExecutionQuote(
        code=code, event_id=f"evt:{event_ts}", event_ts=quote_ts,
        event_time=iso(quote_ts), trade_date=day, price=price,
        available_at=available_at, tradable=tradable,
        capacity_qty=capacity, reason=reason,
        stale=bool(row.get("stale")) if row else False)


__all__ = [
    "SOURCE_HASH_VERSION", "canonical_input", "current_equity", "hash_payload",
    "iso", "quote",
    "runtime_fingerprint", "source_hash", "update_marks",
]
