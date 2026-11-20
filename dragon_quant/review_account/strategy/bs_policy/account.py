"""账户/持仓/批次/预留与市场风险 view（只读值对象，无账本写入）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Optional

from dragon_quant.review_account.evidence import freeze, thaw

from .base import PolicyInputError, _finite, _finite_or_none, _valid_date


def legacy_single_batch(code: str, *, qty: int, sellable_qty: int,
                        cost_price: float = 0.0, entry_date: str = "",
                        strategy_ref: Optional[Mapping] = None,
                        plan_id: str = "",
                        owner: str = "") -> "BatchView":
    """把**有明确数量/可卖量**的旧持仓行映射为稳定单批（R2）。

    旧 run 没有批次，但数量、成本、入场与可卖量已知时可无损映射为一个批次：
    批次 ID 由语义键确定（同输入永远同 ID，不在每次聚合时漂移），并保留原
    归属与来源。信息未知（无入场日/可卖量）时**不推断**日期或可卖量：仍生成
    条目但保持该行的可卖量原值，交由上层按受限处理。
    """
    ref = dict(strategy_ref or {})
    entry = entry_date or ""
    stable = f"legacy:{code}:{entry}:{qty}:{round(float(cost_price), 6)}"
    return BatchView(batch_id=stable, qty=qty, sellable_qty=sellable_qty,
                     entry_date=entry, cost_price=float(cost_price),
                     sellable_date=None, strategy_ref=ref,
                     plan_id=plan_id, owner=owner, original_qty=qty)


@dataclass(frozen=True)
class BatchView:
    """真实批次真值：qty / sellable_qty / 可卖日 / 成本 / 归属。

    批次一旦提供，聚合可卖量必须与批次之和一致；W2 不用聚合字段覆盖批次真值。
    ``strategy_ref`` / ``plan_id`` / ``owner`` 保留冻结策略与计划归属（R6），
    使同 code 的多批次可以按原归属分别保护/增配，而不是只取第一个 ref。
    """

    batch_id: str
    qty: int
    sellable_qty: int
    entry_date: str = ""
    cost_price: float = 0.0
    sellable_date: Optional[str] = None
    strategy_ref: Mapping = field(default_factory=dict)
    plan_id: str = ""
    owner: str = ""
    #: 原始买入量（remaining ``qty`` 之外）：用于按「剩余目标」而非每次重新
    #: 减半；缺失（旧行）时按当前 qty 处理。
    original_qty: Optional[int] = None
    #: 交易家族（G3 probe + 其 add / G7 换仓买卖腿归并同一闭合组）；空 = 旧口径。
    family_id: str = ""
    #: 该批次买入时冻结的计划风险（元）；0 = 未知（新增风险受限，不当作零风险）。
    initial_risk: float = 0.0
    #: 投影来源标记：来自账本状态时保持原 batch_id/可卖模式（场景模拟与账本
    #: 同一 scope）；账户文件解析默认 False（旧口径按可卖/锁定拆分）。不参与
    #: 序列化（账户文件 schema 不变）。
    ledger_origin: bool = False
    #: 账本可卖模式（``on``/``after``/``unknown``，仅投影携带，不序列化）。
    sellable_mode: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.batch_id, str) or not self.batch_id:
            raise PolicyInputError("batch_id 必须是非空字符串")
        object.__setattr__(self, "strategy_ref",
                           freeze(dict(self.strategy_ref or {})))
        if self.original_qty is None:
            object.__setattr__(self, "original_qty", self.qty)
        elif isinstance(self.original_qty, bool) \
                or not isinstance(self.original_qty, int) \
                or self.original_qty < self.qty:
            raise PolicyInputError("batch original_qty 必须是 >= qty 的整数")
        if isinstance(self.qty, bool) or not isinstance(self.qty, int) or self.qty < 0:
            raise PolicyInputError("batch qty 必须是非负整数")
        if isinstance(self.sellable_qty, bool) or not isinstance(
                self.sellable_qty, int) or not 0 <= self.sellable_qty <= self.qty:
            raise PolicyInputError("batch sellable_qty 必须在 [0, qty] 内")
        cost = _finite_or_none(self.cost_price)
        if cost is None or cost < 0:
            raise PolicyInputError("batch cost_price 必须是非负有限数值")
        risk = _finite_or_none(self.initial_risk)
        if risk is None or risk < 0:
            raise PolicyInputError("batch initial_risk 必须是非负有限数值")
        if self.entry_date and not _valid_date(self.entry_date):
            raise PolicyInputError(f"非法 batch entry_date: {self.entry_date!r}")
        if self.sellable_date and not _valid_date(self.sellable_date):
            raise PolicyInputError(
                f"非法 batch sellable_date: {self.sellable_date!r}")

    def to_dict(self) -> dict:
        return {
            "batch_id": self.batch_id,
            "qty": self.qty,
            "sellable_qty": self.sellable_qty,
            "entry_date": self.entry_date,
            "cost_price": self.cost_price,
            "sellable_date": self.sellable_date,
            "strategy_ref": thaw(self.strategy_ref),
            "plan_id": self.plan_id,
            "owner": self.owner,
            "original_qty": self.original_qty,
            "family_id": self.family_id,
            "initial_risk": self.initial_risk,
        }


@dataclass(frozen=True)
class PositionView:
    """只读持仓：qty/sellable_qty/成本/盯市/主题归属/压力估计。"""

    code: str
    name: str = ""
    qty: int = 0
    sellable_qty: int = 0
    cost_price: float = 0.0
    entry_date: str = ""
    mark: Optional[float] = None
    theme_ids: tuple[str, ...] = ()
    pressure_pct: Optional[float] = None
    trend: str = "unknown"           # up / flat / down / unknown
    held_days: Optional[int] = None
    strategy_ref: Mapping = field(default_factory=dict)
    batches: tuple[BatchView, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "theme_ids", tuple(self.theme_ids or ()))
        object.__setattr__(self, "batches", tuple(self.batches or ()))
        if not isinstance(self.code, str) or not self.code:
            raise PolicyInputError("持仓 code 必须是非空字符串")
        for name in ("qty", "sellable_qty"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise PolicyInputError(f"position {name} 必须是非负整数")
        if self.sellable_qty > self.qty:
            raise PolicyInputError("position sellable_qty 不能大于 qty")
        cost = _finite_or_none(self.cost_price)
        if cost is None or cost < 0:
            raise PolicyInputError("position cost_price 必须是非负有限数值")
        if self.mark is not None and _finite_or_none(self.mark) is None:
            raise PolicyInputError("position mark 必须是正有限数值或 None")
        if self.pressure_pct is not None:
            pct = _finite_or_none(self.pressure_pct)
            if pct is None or not 0 <= pct <= 1:
                raise PolicyInputError("position pressure_pct 必须在 [0,1] 内")
        if self.batches:
            if sum(batch.qty for batch in self.batches) != self.qty:
                raise PolicyInputError(
                    "批次数量之和必须等于聚合 qty（不能用聚合字段覆盖批次真值）")
            total_sellable = sum(batch.sellable_qty for batch in self.batches)
            if self.sellable_qty not in (0, total_sellable):
                raise PolicyInputError(
                    "批次可卖量之和与聚合 sellable_qty 冲突")
            # 批次真值优先：聚合字段 0 时由批次推导，不允许反向覆盖批次。
            object.__setattr__(self, "sellable_qty", total_sellable)

    def sellable(self) -> int:
        qty = max(0, int(self.qty))
        if self.batches:
            # 批次存在时只信批次；绝不回退到可能过期的聚合 sellable_qty。
            return min(qty, sum(batch.sellable_qty for batch in self.batches))
        return min(qty, max(0, int(self.sellable_qty)))

    def market_value(self) -> Optional[float]:
        price = _finite(self.mark)
        return price * self.qty if price is not None else None

    def unrealized_pnl(self) -> Optional[float]:
        value = self.market_value()
        if value is None:
            return None
        return value - self.cost_price * self.qty

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "name": self.name,
            "qty": self.qty,
            "sellable_qty": self.sellable(),
            "cost_price": self.cost_price,
            "entry_date": self.entry_date,
            "mark": self.mark,
            "theme_ids": list(self.theme_ids),
            "pressure_pct": self.pressure_pct,
            "trend": self.trend,
            "held_days": self.held_days,
            "strategy_ref": thaw(self.strategy_ref),
            "batches": [batch.to_dict() for batch in self.batches],
        }


@dataclass(frozen=True)
class Reservation:
    """订单预留（未成交买单/卖单）：同样计入共同压力与预算占用。"""

    code: str
    side: str
    qty: int
    price: Optional[float] = None
    theme_ids: tuple[str, ...] = ()
    pressure_pct: Optional[float] = None
    purpose: str = ""

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "side": self.side,
            "qty": self.qty,
            "price": self.price,
            "theme_ids": list(self.theme_ids),
            "pressure_pct": self.pressure_pct,
            "purpose": self.purpose,
        }


@dataclass(frozen=True)
class AccountView:
    cash: Optional[float]
    positions: tuple[PositionView, ...] = ()
    reservations: tuple[Reservation, ...] = ()
    peak_equity: Optional[float] = None
    drawdown_pct: Optional[float] = None
    consecutive_losses: int = 0
    pending_orders: tuple[Mapping, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "positions", tuple(self.positions or ()))
        object.__setattr__(self, "reservations", tuple(self.reservations or ()))
        if self.cash is not None:
            cash = _finite_or_none(self.cash)
            if cash is None or cash < 0:
                raise PolicyInputError("账户 cash 必须是非负有限数值或 None")
        for name in ("peak_equity",):
            value = getattr(self, name)
            if value is not None and (_finite_or_none(value) is None
                                      or float(value) < 0):
                raise PolicyInputError(f"{name} 必须是非负有限数值或 None")
        if self.drawdown_pct is not None:
            value = _finite_or_none(self.drawdown_pct)
            if value is None or value < 0:
                raise PolicyInputError("drawdown_pct 必须是非负有限数值或 None")

    def positions_of(self, code: str) -> tuple[PositionView, ...]:
        return tuple(item for item in self.positions if item.code == code)

    def total_qty(self, code: str) -> int:
        return sum(item.qty for item in self.positions_of(code))

    def total_sellable(self, code: str) -> int:
        return sum(item.sellable() for item in self.positions_of(code))

    def batches_of(self, code: str) -> tuple[BatchView, ...]:
        batches: list[BatchView] = []
        for position in self.positions_of(code):
            batches.extend(position.batches)
        return tuple(batches)

    def weighted_cost(self, code: str) -> float:
        positions = self.positions_of(code)
        qty = sum(item.qty for item in positions)
        if qty <= 0:
            return 0.0
        return sum(item.cost_price * item.qty for item in positions) / qty

    def aggregated_positions(self) -> tuple[PositionView, ...]:
        """同 code 多行（不同归属/批次）合并成交易视图，但保留全部批次。

        R2：某 code 同时存在**有批次行**与**无批次旧行**时，为旧行映射稳定单批，
        保证批次之和 == 聚合 qty，不会因一行缺批次让整轮评估抛错、吞掉其它
        可验证保护；批次 ID 由语义键确定，不会每次聚合漂移。
        """
        codes = sorted({item.code for item in self.positions if item.qty > 0})
        aggregated: list[PositionView] = []
        for code in codes:
            rows = [item for item in self.positions_of(code) if item.qty > 0]
            qty = sum(item.qty for item in rows)
            batches: list[BatchView] = []
            for item in rows:
                if item.batches:
                    batches.extend(item.batches)
                else:
                    batches.append(legacy_single_batch(
                        item.code, qty=item.qty, sellable_qty=item.sellable(),
                        cost_price=item.cost_price, entry_date=item.entry_date,
                        strategy_ref=item.strategy_ref))
            themes: list[str] = []
            for item in rows:
                for theme in item.theme_ids:
                    if theme not in themes:
                        themes.append(theme)
            trends = {item.trend for item in rows}
            trend = ("down" if "down" in trends else
                     "flat" if "flat" in trends else
                     "up" if trends == {"up"} else "unknown")
            pressures = [item.pressure_pct for item in rows
                         if item.pressure_pct is not None]
            pressure = max(pressures) if pressures else None
            marks = [item.mark for item in rows if item.mark is not None]
            held = [item.held_days for item in rows if item.held_days is not None]
            aggregated.append(PositionView(
                code=code,
                name=next((item.name for item in rows if item.name), ""),
                qty=qty,
                sellable_qty=sum(item.sellable() for item in rows),
                cost_price=self.weighted_cost(code),
                entry_date=min((item.entry_date for item in rows
                                if item.entry_date), default=""),
                mark=marks[0] if marks else None,
                theme_ids=tuple(themes),
                pressure_pct=pressure,
                trend=trend,
                held_days=max(held) if held else None,
                strategy_ref=next((item.strategy_ref for item in rows
                                   if item.strategy_ref), {}),
                batches=tuple(batches),
            ))
        return tuple(aggregated)

    def marked_equity(self) -> Optional[float]:
        if self.cash is None:
            return None
        total = float(self.cash)
        for position in self.positions:
            value = position.market_value()
            if value is None:
                return None
            total += value
        return total

    def position(self, code: str) -> Optional[PositionView]:
        for item in self.positions:
            if item.code == code:
                return item
        return None

    def to_dict(self) -> dict:
        return {
            "cash": self.cash,
            "positions": [position.to_dict() for position in self.positions],
            "reservations": [item.to_dict() for item in self.reservations],
            "peak_equity": self.peak_equity,
            "drawdown_pct": self.drawdown_pct,
            "consecutive_losses": self.consecutive_losses,
            "marked_equity": self.marked_equity(),
        }



@dataclass(frozen=True)
class MarketView:
    regime: str = "unknown"
    new_risk_allowed: Optional[bool] = None
    breadth: Optional[float] = None
    reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "regime": self.regime,
            "new_risk_allowed": self.new_risk_allowed,
            "breadth": self.breadth,
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class RiskEnvelope:
    equity: Optional[float] = None
    available_risk: Optional[float] = None
    available_cash: Optional[float] = None
    halted: bool = False
    halt_reasons: tuple[str, ...] = ()
    drawdown_pct: Optional[float] = None
    giveback_used: float = 0.0

    def to_dict(self) -> dict:
        return {
            "equity": self.equity,
            "available_risk": self.available_risk,
            "available_cash": self.available_cash,
            "halted": self.halted,
            "halt_reasons": list(self.halt_reasons),
            "drawdown_pct": self.drawdown_pct,
            "giveback_used": self.giveback_used,
        }
