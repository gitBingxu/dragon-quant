"""逐批运行事实与标记新鲜度（纯叶子，W2 场景 / W3 账本共用）。

A1/A3：持久化**决策真正需要的历史事实**，而不是每次用当前 mark 重建：

- 已知峰值及其源时间/可用时点。只有**先前已知**的可执行行情可抬升峰值；
  买入当根（``source_ts <= 首次跟踪事件``）不算买后历史，同根高点不能反向
  激活本根保护。
- 首日状态（是否已在其首个可卖日被评估）与已完成半仓腿标记。半仓标记只由
  **真实成交**推进，生成意图/拒单/零容量不消耗次数。
- 源时点/可得时点不被当前事件时钟覆盖；未来可得、非有限、非正价分别保留
  原因。``last_close`` 只是有说明的旧估值，不成为可执行价，也不覆盖真实行情。

本模块零 I/O、零时钟/env，不 import ledger/store/策略运行时；适配器
（``AccountEngine``、``ScenarioRun``）各自把事件行转成 :class:`MarkFact` 后
调用同一 reducer，场景与账本因此共享事实语义（A6）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Mapping, Optional

MARK_QUOTE = "quote"
MARK_LAST_CLOSE = "last_close"

#: 显式**教学/测试**年龄假设（1 个交易日）。仅用于 fixture/helper 显式声明；
#: 不是生产默认，不流入正式入口（正式入口缺规格=unknown→新增风险受限）。
TEACHING_QUOTE_MAX_AGE_MS = 86_400_000


def validate_max_age_ms(value) -> Optional[int]:
    """显式年龄规格校验：``None``=未知；否则必须是非负有限数值（拒绝布尔）。"""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RuntimeFactError("max_age_ms 必须是数值或 None")
    if not math.isfinite(float(value)):
        raise RuntimeFactError("max_age_ms 不允许 NaN/Infinity")
    if float(value) < 0:
        raise RuntimeFactError("max_age_ms 不能为负")
    return int(value)


class RuntimeFactError(ValueError):
    """运行事实/标记的非法或损坏输入。"""


def _finite(value, *, positive: bool = False) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    if positive and number <= 0:
        return None
    return number


def _ms(value) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return int(value)


def held_days(entry_date: str, trade_date: str,
              calendar) -> Optional[int]:
    """按显式交易日历计持有天数；缺日历/日期未知返回 ``None``（不推断）。"""
    if not entry_date or not trade_date or not calendar:
        return None
    return len([day for day in calendar if entry_date < day <= trade_date])


def _date(value) -> str:
    return value if isinstance(value, str) else ""


@dataclass(frozen=True)
class MarkFact:
    """一条已知标记：价格 + 源时点/可得时点 + 语义与新鲜度。

    ``kind == last_close`` 是**旧估值**（可参与权益估值，但 ``executable=False``，
    不能作为成交价或被当作当前可执行授权）。

    P1/P2：源时点/可得时点必须由数据源显式给出（缺失不再补当前事件时钟）；
    ``bar_high``/``bar_complete`` 记录**已完成 bar** 的真实高点，只有**完整买后区间**
    （``interval_start_ts > entry_source_ts`` 且区间可得）才并入后续峰值；缺区间时
    保留 quote 价作为已知下界，不用 bar_high 冒充完整买后高点。
    """

    code: str
    price: float
    source_ts: int
    available_at: int
    event_time: str = ""
    kind: str = MARK_QUOTE
    stale: bool = False
    bar_high: Optional[float] = None
    bar_complete: bool = False
    interval_start_ts: Optional[int] = None
    interval_end_ts: Optional[int] = None

    def __post_init__(self) -> None:
        if not isinstance(self.code, str) or not self.code:
            raise RuntimeFactError("mark.code 必须是非空字符串")
        if _finite(self.price, positive=True) is None:
            raise RuntimeFactError("mark.price 必须是正有限数值")
        if _ms(self.source_ts) is None or _ms(self.available_at) is None:
            raise RuntimeFactError("mark 源时点/可得时点必须是整数毫秒")
        if int(self.available_at) < int(self.source_ts):
            raise RuntimeFactError("mark 可得时点不能早于源时点")
        if self.kind not in (MARK_QUOTE, MARK_LAST_CLOSE):
            raise RuntimeFactError(f"非法 mark.kind: {self.kind!r}")
        if self.bar_high is not None \
                and _finite(self.bar_high, positive=True) is None:
            raise RuntimeFactError("mark.bar_high 必须是正有限数值")
        if (self.interval_start_ts is None) != (self.interval_end_ts is None):
            raise RuntimeFactError("mark 区间起点/终点必须同时给出")
        if self.interval_start_ts is not None:
            if _ms(self.interval_start_ts) is None \
                    or _ms(self.interval_end_ts) is None:
                raise RuntimeFactError("mark 区间起止必须是整数毫秒")
            if int(self.interval_start_ts) >= int(self.interval_end_ts):
                raise RuntimeFactError("mark 区间起点必须早于终点")

    @property
    def executable(self) -> bool:
        return self.kind == MARK_QUOTE and not self.stale

    @property
    def interval_fully_after(self) -> Optional[int]:
        """区间起点（完全买后判断用）；缺区间返回 ``None``（不能推断）。"""
        return (None if self.interval_start_ts is None
                else int(self.interval_start_ts))

    def to_row(self) -> dict:
        return {"code": self.code, "price": self.price,
                "source_ts": self.source_ts,
                "available_at": self.available_at,
                "event_time": self.event_time, "kind": self.kind,
                "stale": bool(self.stale), "bar_high": self.bar_high,
                "bar_complete": bool(self.bar_complete),
                "interval_start_ts": self.interval_start_ts,
                "interval_end_ts": self.interval_end_ts}

    @classmethod
    def from_row(cls, value: Mapping) -> "MarkFact":
        try:
            return cls(code=str(value["code"]), price=value["price"],
                       source_ts=int(value["source_ts"]),
                       available_at=int(value["available_at"]),
                       event_time=_date(value.get("event_time")),
                       kind=str(value.get("kind") or MARK_QUOTE),
                       stale=bool(value.get("stale")),
                       bar_high=(None if value.get("bar_high") is None
                                 else value["bar_high"]),
                       bar_complete=bool(value.get("bar_complete")),
                       interval_start_ts=(
                           None if value.get("interval_start_ts") is None
                           else int(value["interval_start_ts"])),
                       interval_end_ts=(
                           None if value.get("interval_end_ts") is None
                           else int(value["interval_end_ts"])))
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeFactError(f"损坏的 mark fact: {exc}") from exc


def observe_row(code: str, row, *, event_ts: int,
                max_age_ms: Optional[int] = None
                ) -> tuple[Optional[MarkFact], str]:
    """事件行情行 → 可持久化标记；非法/未来/缺源时点返回 ``(None, 原因)``。

    P1：源时点/可得时点缺失时**不补当前事件时钟**（返回 ``source_time_missing``），
    源时点/可得时点晚于事件时刻返回 ``mark_from_future``。``max_age_ms`` 是调用
    方**显式声明**的容许年龄模型；超龄标记仍保留为估值，但 ``stale=True``（不能
    抬升当前可执行峰值/授权新增）。
    """
    if not isinstance(row, Mapping):
        return None, "row_invalid"
    price = row.get("execution_price")
    if price is None:
        price = row.get("bar_close")
    price = _finite(price, positive=True)
    if price is None:
        return None, "price_missing"
    source_ts = _ms(row.get("execution_timestamp"))
    if source_ts is None:
        source_ts = _ms(row.get("observed_at"))
    if source_ts is None:
        return None, "source_time_missing"
    available_at = _ms(row.get("available_at"))
    if available_at is None:
        available_at = source_ts
    if source_ts > int(event_ts) or available_at > int(event_ts):
        return None, "mark_from_future"
    if available_at < source_ts:
        return None, "available_before_source"
    stale = bool(row.get("stale"))
    max_age = validate_max_age_ms(max_age_ms)
    if max_age is not None and int(event_ts) - source_ts > max_age:
        stale = True
    bar_complete = bool(row.get("bar_complete"))
    interval_start = _ms(row.get("interval_start"))
    interval_end = _ms(row.get("interval_end"))
    # 区间缺失/不完整/非法/未完成：保留 quote 价作为已知下界，但不把该 high 当作
    # 完整买后峰值证据（不猜周期、不宽松默认 bar_complete）。
    interval_ok = (interval_start is not None and interval_end is not None
                   and interval_start < interval_end
                   and interval_end <= int(event_ts))
    if not interval_ok:
        bar_complete = False
        interval_start = interval_end = None
    bar_high = None
    if bar_complete and interval_start is not None:
        bar_high = _finite(row.get("bar_high", row.get("bar_high_price")),
                           positive=True)
        if bar_high is None:
            bar_high = _finite(row.get("high"), positive=True)
    return MarkFact(
        code=code, price=price, source_ts=source_ts,
        available_at=available_at, event_time=_date(row.get("event_time")),
        kind=MARK_QUOTE, stale=stale, bar_high=bar_high,
        bar_complete=bar_complete, interval_start_ts=interval_start,
        interval_end_ts=interval_end), ""


def observe_last_close(code: str, price, *, event_ts: int
                       ) -> Optional[MarkFact]:
    """收盘旧值 → 非可执行估值标记（不当作当前可执行价）。"""
    value = _finite(price, positive=True)
    if value is None:
        return None
    return MarkFact(code=code, price=value, source_ts=int(event_ts),
                    available_at=int(event_ts), kind=MARK_LAST_CLOSE,
                    stale=True)


def _newer_source(candidate: MarkFact, current: MarkFact) -> bool:
    """仅当候选来源不旧于当前投影时才替换（旧修订不得覆盖更新 source）。"""
    return (candidate.source_ts, candidate.available_at) >= (
        current.source_ts, current.available_at)


def reduce_mark_facts(facts: Mapping[str, MarkFact], *, rows,
                      last_closes, event_ts: int,
                      max_age_ms: Optional[int] = None
                      ) -> tuple[dict[str, MarkFact], dict[str, str]]:
    """把本事件新行情并入已知标记；**没有新行情不刷新旧标记的新鲜度**。

    P1：未来/缺源时点不落库（保留原因）；旧 source 修订不覆盖更新 source 的
    当前投影；``max_age_ms`` 为显式声明的容许年龄模型，超龄标记改为 stale 估值。
    """
    updated = dict(facts or {})
    reasons: dict[str, str] = {}
    for code, row in (rows or {}).items():
        fact, reason = observe_row(str(code), row, event_ts=int(event_ts),
                                   max_age_ms=max_age_ms)
        if fact is not None:
            current = updated.get(fact.code)
            if current is not None and not _newer_source(fact, current):
                reasons.setdefault(str(code), "stale_source_revision")
                continue
            updated[fact.code] = fact
        elif reason:
            reasons[str(code)] = reason
    for code, price in (last_closes or {}).items():
        current = updated.get(str(code))
        if current is not None and current.kind == MARK_QUOTE:
            continue                      # 真实行情优先，旧收盘不得覆盖
        fact = observe_last_close(str(code), price, event_ts=int(event_ts))
        if fact is not None:
            if current is not None and not _newer_source(fact, current):
                reasons.setdefault(str(code), "stale_source_revision")
                continue
            updated[fact.code] = fact
    return updated, reasons


def mark_prices(facts: Mapping[str, MarkFact]) -> dict[str, float]:
    """标记事实 → 旧 ``state.marks`` 价格视图（含 last_close 估值）。"""
    return {code: fact.price for code, fact in (facts or {}).items()}


@dataclass(frozen=True)
class BatchFacts:
    """一个真实批次的运行事实（可变状态，**不写入**批次不可变身份哈希）。"""

    batch_id: str
    entry_source_ts: Optional[int] = None
    peak_price: Optional[float] = None
    peak_source_ts: Optional[int] = None
    peak_available_at: Optional[int] = None
    took_profit_half: bool = False
    first_day_seen: bool = False
    last_fill_event: Optional[int] = None
    version: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.batch_id, str) or not self.batch_id:
            raise RuntimeFactError("batch facts.batch_id 必须是非空字符串")
        for name in ("peak_price",):
            value = getattr(self, name)
            if value is not None and _finite(value, positive=True) is None:
                raise RuntimeFactError(f"batch facts.{name} 必须是正有限数值")
        for name in ("entry_source_ts", "peak_source_ts",
                     "peak_available_at", "last_fill_event"):
            value = getattr(self, name)
            if value is not None and _ms(value) is None:
                raise RuntimeFactError(f"batch facts.{name} 必须是整数毫秒")

    @property
    def peak_known(self) -> bool:
        return self.peak_price is not None

    def to_row(self) -> dict:
        return {"batch_id": self.batch_id,
                "entry_source_ts": self.entry_source_ts,
                "peak_price": self.peak_price,
                "peak_source_ts": self.peak_source_ts,
                "peak_available_at": self.peak_available_at,
                "took_profit_half": bool(self.took_profit_half),
                "first_day_seen": bool(self.first_day_seen),
                "last_fill_event": self.last_fill_event,
                "version": int(self.version)}

    @classmethod
    def from_row(cls, value: Mapping) -> "BatchFacts":
        try:
            return cls(
                batch_id=str(value["batch_id"]),
                entry_source_ts=(None if value.get("entry_source_ts") is None
                                 else int(value["entry_source_ts"])),
                peak_price=(None if value.get("peak_price") is None
                            else value["peak_price"]),
                peak_source_ts=(None if value.get("peak_source_ts") is None
                                else int(value["peak_source_ts"])),
                peak_available_at=(None if value.get("peak_available_at") is None
                                   else int(value["peak_available_at"])),
                took_profit_half=bool(value.get("took_profit_half")),
                first_day_seen=bool(value.get("first_day_seen")),
                last_fill_event=(None if value.get("last_fill_event") is None
                                 else int(value["last_fill_event"])),
                version=int(value.get("version") or 1))
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeFactError(f"损坏的 batch facts: {exc}") from exc


def reduce_batch_facts(facts: Mapping[str, BatchFacts], *, batches,
                       marks: Mapping[str, MarkFact], event_ts: int,
                       trade_date: str, calendar=()
                       ) -> dict[str, BatchFacts]:
    """逐批更新峰值/首日事实：只用先前已知的可执行标记抬升峰值。

    P2：已有**已完成且带明确区间** bar 的真实 high 只有在**区间完全落在买后**
    （``interval_start_ts > entry_source_ts``）且可得时才能并入后续峰值；缺区间或
    跨入场（入场落在 bar 区间内）一律不用 bar_high 冒充完整买后高点，只保留 quote
    价作为已知下界。买入当根（``source_ts <= entry_source_ts``）不算买后历史。保护
    评估在本 reducer 之前完成，故同根新高不反向触发同根退出。
    """
    updated = dict(facts or {})
    calendar = tuple(calendar or ())
    for batch in batches or ():
        if int(getattr(batch, "remaining", 0) or 0) <= 0:
            continue
        batch_id = str(batch.batch_id)
        fact = updated.get(batch_id) or BatchFacts(
            batch_id=batch_id, entry_source_ts=int(event_ts))
        mark = (marks or {}).get(str(batch.code))
        if mark is not None and fact.entry_source_ts is not None \
                and mark.source_ts > fact.entry_source_ts:
            candidates: list[float] = []
            if mark.executable:
                candidates.append(float(mark.price))
            # bar_high 只有在区间起点严格晚于入场来源时点（完全买后）才成立。
            if mark.bar_complete and mark.bar_high is not None \
                    and mark.interval_start_ts is not None \
                    and int(mark.interval_start_ts) > fact.entry_source_ts \
                    and int(mark.interval_end_ts or 0) <= int(event_ts) \
                    and mark.available_at <= int(event_ts):
                candidates.append(float(mark.bar_high))
            for price in candidates:
                if fact.peak_price is None or price > fact.peak_price:
                    fact = replace(fact, peak_price=price,
                                   peak_source_ts=mark.source_ts,
                                   peak_available_at=mark.available_at)
        if not fact.first_day_seen:
            days = held_days(str(getattr(batch, "entry_date", "") or ""),
                             trade_date, calendar)
            if days == 1:
                fact = replace(fact, first_day_seen=True)
        updated[batch_id] = fact
    return updated


def apply_fill_facts(facts: Mapping[str, BatchFacts], *, fills,
                     batches_before: Mapping[str, object], event_ts: int
                     ) -> dict[str, BatchFacts]:
    """用**真实成交**推进半仓/成交审计事实（意图/拒单/零容量不推进）。

    ``took_profit_half`` 只在该批次**累计已卖达到原始量一半**时置位（含半仓腿的
    分笔成交），不是任何部分卖出都算，避免小幅减仓误吞首日/半仓唯一性守卫。
    """
    updated = dict(facts or {})
    for fill in fills or ():
        if getattr(fill, "side", "") != "sell":
            continue
        batch_id = str(getattr(fill, "batch_id", "") or "")
        if not batch_id:
            continue
        fact = updated.get(batch_id) or BatchFacts(
            batch_id=batch_id, entry_source_ts=int(event_ts))
        batch = (batches_before or {}).get(batch_id)
        original = int(getattr(batch, "qty", 0) or 0)
        remaining_before = int(getattr(batch, "remaining", 0) or 0)
        sold_total = original - max(0, remaining_before - int(fill.qty))
        half_done = original > 0 and sold_total * 2 >= original
        updated[batch_id] = replace(
            fact, took_profit_half=fact.took_profit_half or half_done,
            last_fill_event=int(event_ts))
    return updated


__all__ = [
    "MARK_QUOTE", "MARK_LAST_CLOSE", "RuntimeFactError", "MarkFact",
    "BatchFacts", "held_days", "observe_row", "observe_last_close",
    "reduce_mark_facts", "mark_prices", "reduce_batch_facts",
    "apply_fill_facts", "TEACHING_QUOTE_MAX_AGE_MS", "validate_max_age_ms",
]
