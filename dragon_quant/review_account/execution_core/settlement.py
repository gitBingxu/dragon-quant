"""闭合交易结算（纯叶子）：共享执行核心的连亏/已实现净额事实。

修复原 ``transition._apply`` 的两个已知错误：

- 买入成交会把 ``consecutive_losses`` 重置为 0（买单不是闭合交易）；
- 每笔部分卖出都单独计一次连亏（同一闭合交易被多计）。

口径：闭合身份 = 完整 owner + 明确交易计划/入场组（无 plan 时用稳定
``obligation:<id>`` 或 ``batch:<id>`` 兼容标识）。**只有整组持仓清零**时才记录
一次闭合净额并更新 ``consecutive_losses``；净额含对应买/卖费用，用实际闭合
净额而非未实现 PnL。后续 G3 加仓的 family/plan 关联由 C3b 消费/扩展，本模块
只保留可靠原始闭合事实。
"""

from __future__ import annotations

from typing import Iterable, Mapping

from .identity import ref_identity

_EPS = 1e-9


def trade_group_key(batch) -> str:
    """闭合交易身份：owner + 家族/明确计划（无 plan 用稳定兼容标识）。

    完整 owner + family/entry 组：``probe`` 与其后续 ``add`` 用同一 ``family_id``
    归并，不会因不同计划 ID 被拆成多笔独立亏损；不同 owner/code/独立家族不合并。
    """
    owner = repr(ref_identity(dict(getattr(batch, "strategy_ref", {}) or {})))
    code = str(getattr(batch, "code", "") or "")
    plan = (str(getattr(batch, "family_id", "") or "")
            or str(getattr(batch, "plan_id", "") or ""))
    if not plan:
        obligation = str(getattr(batch, "obligation_id", "") or "")
        plan = (f"obligation:{obligation}" if obligation
                else f"batch:{getattr(batch, 'batch_id', '')}")
    # 显式包含 code：不同股票即使碰巧共享外部 family 字符串/计划，也各自独立
    # 闭合，不把另一只的持仓/亏损混入本组（影响闭合时点/连亏/PnL 归因）。
    return f"{owner}|{code}|{plan}"


def _new_group(key: str, batch) -> dict:
    return {
        "trade_group_id": key,
        "code": str(getattr(batch, "code", "") or ""),
        "plan_id": str(getattr(batch, "plan_id", "") or ""),
        "realized_net": 0.0,
        "sold_qty": 0,
        "open_qty": 0,
        "closed": False,
        "cycles": 0,
        "last_event": None,
    }


def _ensure_open_groups(stats: dict[str, dict], batches,
                        event_ts: int) -> set[str]:
    """按当前未平批次登记/重开交易组；已闭合组再次建仓即开始新周期。"""
    touched: set[str] = set()
    open_qty: dict[str, int] = {}
    first_batch: dict[str, object] = {}
    for batch in batches or ():
        if int(getattr(batch, "remaining", 0) or 0) <= 0:
            continue
        key = trade_group_key(batch)
        touched.add(key)
        first_batch.setdefault(key, batch)
        open_qty[key] = open_qty.get(key, 0) + int(batch.remaining)
    for key, qty in open_qty.items():
        group = stats.get(key)
        if group is None:
            group = _new_group(key, first_batch[key])
            stats[key] = group
        elif group.get("closed"):
            # 同一计划再次建仓 → 新周期，不复用上一笔闭合净额。
            group.update(realized_net=0.0, sold_qty=0, closed=False,
                         cycles=int(group.get("cycles") or 0) + 1)
        group["open_qty"] = qty
    return touched


def settle_fills(state, *, fills: Iterable, batches_before: Mapping[str, object],
                 event_ts: int) -> list[dict]:
    """按真实成交更新闭合交易事实；返回本事件新闭合的交易记录。

    ``state`` 的 ``batches`` 必须**已应用**本事件批次更新；``batches_before`` 是
    应用前的批次快照，用于取成本/原始买入费用。买入成交不改连亏。
    """
    sells = [fill for fill in (fills or ())
             if getattr(fill, "side", "") == "sell"]
    if not sells:
        return []
    stats: dict[str, dict] = dict(getattr(state, "trade_stats", {}) or {})
    _ensure_open_groups(stats, getattr(state, "batches", ()) or (), event_ts)
    affected: set[str] = set()
    for fill in sells:
        batch = (batches_before or {}).get(str(getattr(fill, "batch_id", "")))
        if batch is None:
            continue
        key = trade_group_key(batch)
        group = stats.get(key) or _new_group(key, batch)
        stats[key] = group
        buy_fee_share = 0.0
        original = int(getattr(batch, "qty", 0) or 0)
        if original > 0:
            buy_fee_share = float(getattr(batch, "fee", 0.0) or 0.0) \
                * int(fill.qty) / original
        proceeds = float(fill.price) * int(fill.qty) - float(fill.fee)
        cost = float(batch.cost_price) * int(fill.qty) + buy_fee_share
        group["realized_net"] = round(
            float(group.get("realized_net") or 0.0) + proceeds - cost, 6)
        group["sold_qty"] = int(group.get("sold_qty") or 0) + int(fill.qty)
        affected.add(key)
    closed: list[dict] = []
    for key in sorted(affected):
        group = stats[key]
        open_qty = sum(int(getattr(batch, "remaining", 0) or 0)
                       for batch in getattr(state, "batches", ()) or ()
                       if trade_group_key(batch) == key)
        group["open_qty"] = open_qty
        if open_qty > _EPS or group.get("closed"):
            continue
        group["closed"] = True
        group["last_event"] = int(event_ts)
        net = float(group.get("realized_net") or 0.0)
        state.consecutive_losses = (int(getattr(state, "consecutive_losses", 0))
                                    + 1 if net < 0 else 0)
        record = {
            "closed_trade_id": f"{key}|cycle:{int(group.get('cycles') or 0)}",
            "trade_group_id": key,
            "code": group.get("code", ""),
            "plan_id": group.get("plan_id", ""),
            "realized_net": net,
            "loss": net < 0,
            "closed_event": int(event_ts),
        }
        closed_trades = list(getattr(state, "closed_trades", ()) or ())
        closed_trades.append(record)
        state.closed_trades = closed_trades
        closed.append(record)
    state.trade_stats = stats
    return closed


__all__ = ["trade_group_key", "settle_fills"]
