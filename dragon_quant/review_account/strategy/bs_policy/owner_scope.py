"""按冻结归属/批次/计划生成保护义务（纯值，无 I/O）。

本模块是 **W2 纯核与 W3 账户适配器之间唯一的保护事实契约**：外部解析器（可读取
快照/运行时的 Adapter）把账本批次规范化为 :class:`ProtectionBatch`，这里只按
``config`` 的失效/灾难线逐批生成 :class:`ProtectionObligation`。它不 import 账本、
存储、provider 或运行时，也不调用用户代码。

关键语义：

- 同 ``code`` 的不同归属/计划是**不同业务身份**：逐批用自身成本与失效线评估，
  绝不先把全账户按 code 加权平均再触发（R6/L4）。
- 批次数据缺失（成本/日期/可卖量/持有天数）显式保留原因，不补造事实；
  未知可卖量按受限处理（不当作全部可卖）。
- 义务数量用**剩余量**：已完成/部分成交后按剩余目标继续，不会重复减半。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Optional

from .config import BSPolicyConfig
from .plans import PolicyCondition, ProtectionObligation
from .sizing import _disaster_price, _stop_price


@dataclass(frozen=True)
class ProtectionBatch:
    """用于保护评估的**只读批次事实**（由适配器从真实账本规范化）。

    ``sellable`` 是该交易日的真实可卖量（未知可卖日必须为 0）；``held_days`` 未知
    时保留 ``None``（不推断），对应时间失效不会被伪造成已触发。

    A1/A2：``peak_price`` 是**先前已知**的持久化峰值（``None`` = 无历史，不伪造），
    ``took_profit_half`` 只由真实成交推进；两者都参与旧冻结退出规则。
    """

    batch_id: str
    code: str
    remaining: int
    sellable: int
    cost_price: float
    entry_date: str = ""
    plan_id: str = ""
    held_days: Optional[int] = None
    strategy_ref: Mapping = field(default_factory=dict)
    source: str = ""
    reasons: tuple[str, ...] = ()
    peak_price: Optional[float] = None
    peak_source_ts: Optional[int] = None
    peak_available_at: Optional[int] = None
    took_profit_half: bool = False
    first_day_seen: bool = False

    @property
    def peak_known(self) -> bool:
        return self.peak_price is not None


def batch_facts(batch, *, trade_date: str, held_days: Optional[int] = None,
                facts=None, reasons: tuple[str, ...] = ()) -> ProtectionBatch:
    """把账本/GUI 批次（duck-typed）规范化为纯保护事实。

    只读取既有字段；可卖量走批次自身的 ``sellable(trade_date)``（未知可卖 = 0），
    绝不回退成"全部可卖"。``facts`` 是该批次已持久化的历史峰值/半仓事实；缺失时
    保留 unknown（不伪造）。
    """
    remaining = int(getattr(batch, "remaining", 0) or 0)
    sellable = (batch.sellable(trade_date) if callable(
        getattr(batch, "sellable", None)) else 0)
    return ProtectionBatch(
        batch_id=str(batch.batch_id), code=str(batch.code), remaining=remaining,
        sellable=int(sellable), cost_price=float(getattr(batch, "cost_price", 0.0)),
        entry_date=str(getattr(batch, "entry_date", "") or ""),
        plan_id=str(getattr(batch, "plan_id", "") or ""),
        held_days=held_days,
        strategy_ref=dict(getattr(batch, "strategy_ref", {}) or {}),
        source=str(getattr(batch, "source", "") or ""),
        reasons=tuple(reasons),
        peak_price=getattr(facts, "peak_price", None),
        peak_source_ts=getattr(facts, "peak_source_ts", None),
        peak_available_at=getattr(facts, "peak_available_at", None),
        took_profit_half=bool(getattr(facts, "took_profit_half", False)),
        first_day_seen=bool(getattr(facts, "first_day_seen", False)))


def _mark_price(marks: Mapping, code: str) -> Optional[float]:
    value = (marks or {}).get(code)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if value > 0 else None


def batch_invalidation(batch: ProtectionBatch, config: BSPolicyConfig,
                       marks: Mapping, *, sequence: int = 0
                       ) -> Optional[ProtectionObligation]:
    """单个批次的价/灾难/时间失效义务；未触发返回 ``None``。"""
    if batch.remaining <= 0:
        return None
    mark = _mark_price(marks, batch.code)
    reasons: list[str] = list(batch.reasons)
    executable = True
    if mark is None:
        reasons.append("valuation_missing")
        executable = False
    if batch.sellable <= 0:
        reasons.append("no_sellable_quantity")
        executable = False
    if batch.cost_price <= 0:
        reasons.append("cost_basis_missing")
        executable = False
        stop = disaster = None
    else:
        stop = _stop_price(batch.cost_price, config)
        disaster = _disaster_price(batch.cost_price, config)

    kind: Optional[str] = None
    condition: Optional[PolicyCondition] = None
    if disaster is not None and mark is not None and mark <= disaster:
        kind = "disaster"
        condition = PolicyCondition(kind="price_below", code=batch.code,
                                    value=disaster, unit="cny_share")
    elif stop is not None and mark is not None and mark <= stop:
        kind = "price_invalidation"
        condition = PolicyCondition(kind="price_below", code=batch.code,
                                    value=stop, unit="cny_share")
    elif batch.held_days is not None \
            and batch.held_days >= config.invalidation.time_limit_days:
        kind = "time_invalidation"
        condition = PolicyCondition(
            kind="time_limit", code=batch.code,
            value=float(config.invalidation.time_limit_days),
            unit="trading_day")
    if kind is None:
        return None
    return ProtectionObligation(
        obligation_id=f"{kind}:{batch.code}:{batch.batch_id}",
        code=batch.code, kind=kind, action="exit", qty=batch.remaining,
        executable=executable, reasons=tuple(dict.fromkeys(reasons)),
        condition=condition, sequence=sequence,
        owner=dict(batch.strategy_ref), batch_ids=(batch.batch_id,),
        plan_id=batch.plan_id)


def batch_obligations(batches, config: BSPolicyConfig, marks: Mapping, *,
                      sequence_start: int = 0
                      ) -> list[ProtectionObligation]:
    """逐批生成失效义务（按 batch_id 稳定排序，保证事件序列可复现）。"""
    obligations: list[ProtectionObligation] = []
    sequence = sequence_start
    for batch in sorted(batches, key=lambda item: item.batch_id):
        obligation = batch_invalidation(batch, config, marks, sequence=sequence)
        if obligation is None:
            continue
        obligations.append(obligation)
        sequence += 1
    return obligations


def owner_group(batches) -> dict[tuple, list[ProtectionBatch]]:
    """按 ``(code, 完整归属身份, plan_id)`` 分组（不跨 owner/plan 归并）。"""
    from dragon_quant.review_account.execution_core.identity import ref_identity

    groups: dict[tuple, list[ProtectionBatch]] = {}
    for batch in batches:
        key = (batch.code, ref_identity(batch.strategy_ref), batch.plan_id)
        groups.setdefault(key, []).append(batch)
    return groups
