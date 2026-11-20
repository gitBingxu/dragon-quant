"""非现金风险预算（L2）：显式上限、全账户累计、主题/未知组与同事件并发核算。

契约（主验收 L2 / decision-02 切片1）：

- ``None`` = **未知**（未配置）→ 不能新增风险；``0.0`` = **零额度**（同样不能新增）。
  只有显式正数才是可用额度；不把未配置自动放宽为无限。
- 已成交批次 ``initial_risk`` 与未成交买单 ``reserved_risk`` 都计入占用；资金预留
  （``reserved_amount``）不是风险金额，两者不混用。
- 风险按**全账户**累计；主题用显式有限元组（可多主题），未知组的风险保守地
  叠加到每个已知主题上（不因主题缺失而漏算）。
- 同一事件内多笔命令必须按「基准占用 + 本事件已接受风险」逐笔核算，不能都按
  旧预算各自通过（共享核心的并发超额修复）。

本模块是纯叶子：不读时钟/env/数据库。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Optional

from .models import LedgerError, LedgerState

_EPS = 1e-9


def _valid_amount(value, *, field_name: str) -> Optional[float]:
    """风险金额校验：None 透传；拒绝布尔/非数值/NaN/Inf/负数（fail closed）。"""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LedgerError(f"{field_name} 必须是有限数值，得到 {type(value).__name__}")
    number = float(value)
    if not math.isfinite(number):
        raise LedgerError(f"{field_name} 不允许 NaN/Infinity")
    if number < 0:
        raise LedgerError(f"{field_name} 不能为负")
    return number


@dataclass(frozen=True)
class RiskCaps:
    """非现金风险上限（元）。``None`` 表示未知/未配置 → 限制新增风险。"""

    per_trade: Optional[float] = None
    account: Optional[float] = None
    unknown_group: Optional[float] = None
    per_theme: Mapping[str, float] = field(default_factory=dict)
    theme_default: Optional[float] = None

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        """非法额度（NaN/inf/布尔/负数）必须显式失败，不能退化成“不限”。"""
        for name in ("per_trade", "account", "unknown_group", "theme_default"):
            _valid_amount(getattr(self, name), field_name=f"RiskCaps.{name}")
        for theme, cap in dict(self.per_theme or {}).items():
            if not isinstance(theme, str) or not theme:
                raise LedgerError("RiskCaps.per_theme 主题必须是非空字符串")
            _valid_amount(cap, field_name=f"RiskCaps.per_theme[{theme}]")
            if cap is None:
                raise LedgerError(
                    f"RiskCaps.per_theme[{theme}] 不允许 None（未知用缺省表）")

    @classmethod
    def teaching_fixture(cls) -> "RiskCaps":
        """显式教学/测试限额（大而有限，非生产默认，不表示“无限”）。"""
        return cls(per_trade=1_000_000_000.0, account=1_000_000_000_000.0,
                   unknown_group=1_000_000_000.0,
                   theme_default=1_000_000_000.0)


@dataclass
class RiskBook:
    """风险占用：账户总额 + 未知组 + 逐主题（同一笔风险可计入多个主题）。"""

    total: float = 0.0
    unknown: float = 0.0
    themes: dict[str, float] = field(default_factory=dict)
    #: 有持仓但**风险未知**（未声明风险金额）的批次/挂单数量；>0 时新增风险受限。
    unknown_holdings: int = 0

    @property
    def has_unknown_holding(self) -> bool:
        return self.unknown_holdings > 0

    @classmethod
    def from_state(cls, state: LedgerState) -> "RiskBook":
        book = cls()
        for batch in state.batches:
            if batch.remaining <= 0:
                continue
            if batch.initial_risk > 0:
                book.add(float(batch.initial_risk), batch.risk_themes)
            else:
                # 未声明风险 ≠ 已知零风险：显式记未知占用，限制新增风险。
                book.unknown_holdings += 1
        for order in state.orders:
            if order.side != "buy" or order.is_terminal:
                continue
            if order.risk_amount > 0:
                book.add(float(order.reserved_risk), order.risk_themes)
            else:
                book.unknown_holdings += 1
        return book

    def add(self, risk: float, themes: Iterable[str] = ()) -> None:
        value = _valid_amount(risk, field_name="RiskBook.risk")
        if value is None or value <= 0:
            return
        self.total += value
        values = tuple(str(item) for item in themes if str(item))
        if not values:
            self.unknown += value
            return
        for theme in values:
            self.themes[theme] = self.themes.get(theme, 0.0) + value

    def used_for_theme(self, theme: str) -> float:
        """该主题的占用：本主题已用 + 未知组（未知风险保守叠加到已知主题）。"""
        return self.themes.get(theme, 0.0) + self.unknown


def check_new_risk(caps: Optional[RiskCaps], risk: float,
                   themes: Iterable[str] = (), *,
                   book: Optional[RiskBook] = None,
                   allow_unknown_risk: bool = False) -> Optional[str]:
    """新增风险前检查；返回拒绝原因或 ``None``。

    直接撮合/场景教学单元可用 ``allow_unknown_risk=True`` 显式跳过风险金额未知
    的拒绝（fixture 假设），但 ``None`` 上限仍不会自动变成“无限”。
    """
    values = tuple(str(item) for item in themes if str(item))
    risk = _valid_amount(risk, field_name="new risk")
    book = book or RiskBook()
    if not risk or risk <= 0:
        return None if allow_unknown_risk else "risk_amount_unknown"
    if caps is None:
        return None if allow_unknown_risk else "risk_limit_unknown"
    caps.validate()
    if book.has_unknown_holding and not allow_unknown_risk:
        # 缺风险估值/压力的存量持仓：新增风险受限（保护/减仓不受此限）。
        return "risk_unknown_holding"
    if caps.per_trade is None:
        return "per_trade_risk_unknown"
    if risk > caps.per_trade + _EPS:
        return "per_trade_risk_exceeded"
    if caps.account is None:
        return "account_risk_unknown"
    if book.total + risk > caps.account + _EPS:
        return "account_risk_exceeded"
    if values:
        for theme in values:
            cap = (caps.per_theme.get(theme)
                   if theme in caps.per_theme else caps.theme_default)
            if cap is None:
                return "theme_risk_unknown"
            if book.used_for_theme(theme) + risk > cap + _EPS:
                return "theme_risk_exceeded"
        return None
    if caps.unknown_group is None:
        return "unknown_group_risk_unknown"
    if book.unknown + risk > caps.unknown_group + _EPS:
        return "unknown_group_risk_exceeded"
    # 未知主题的新增风险同样不得突破已知主题预算（保守叠加）：
    # 检查「book 已占用主题 ∪ 显式额度主题」的并集，各自按显式/默认额度计。
    for theme in sorted(set(book.themes) | set(caps.per_theme)):
        cap = (caps.per_theme.get(theme)
               if theme in caps.per_theme else caps.theme_default)
        if cap is None:
            return "theme_risk_unknown"
        if book.used_for_theme(theme) + risk > cap + _EPS:
            return "theme_risk_exceeded"
    return None
