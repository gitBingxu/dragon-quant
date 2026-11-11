"""Strategy SDK 纯数据类型与协议（用户 workspace 的唯一稳定导入面）。

本模块只依赖标准库与 ``infra.factor.types`` 的兼容导出，**不 import** Web、
commands、workspace 装载器或 ``review_account`` 实现，保证用户策略在只装
PyPI 包的环境里也能把它当作稳定 ABI 使用。

只读约定（公开开发约定，非技术沙箱）：``StrategyContext`` 的数据在构造时
已经深冻结为不可变结构；策略不得写库、调用 provider、修改引擎状态。系统只
承诺对返回值做有限校验，不对任意 Python 代码做强制沙箱。
"""

from __future__ import annotations

import dataclasses
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, Optional, Protocol, runtime_checkable

SDK_VERSION = 1

#: Intent 允许的动作
INTENT_ACTIONS = ("buy", "sell")
#: 卖出比例（当前半仓模型只支持减半或整仓）
EXIT_FRACTIONS = (0.5, 1.0)

#: FactorValue 状态
FACTOR_STATUS_OK = "ok"
FACTOR_STATUS_MISSING = "missing"        # 该时点无值（缺失）
FACTOR_STATUS_UNAVAILABLE = "unavailable"  # 尚未可知（未来数据）


class StrategyError(ValueError):
    """用户策略定义/返回值非法。运行期必须显式失败，绝不静默退内建策略。"""


def freeze(value: Any) -> Any:
    """深度只读：dict/list/dataclass → MappingProxyType/tuple/只读映射，标量原样返回。

    dataclass（如行情 ``KBar``）会先转成纯 dict 再冻结，避免用户通过嵌套对象
    修改引擎持有的可变实例。
    """
    from types import MappingProxyType

    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        params = getattr(value, "__dataclass_params__", None)
        if params is not None and getattr(params, "frozen", False):
            # 已不可变的数据类（如 FactorValue）原样保留，用户仍可 .value 访问。
            return value
        return MappingProxyType({str(k): freeze(v) for k, v in asdict(value).items()})
    if isinstance(value, Mapping):
        return MappingProxyType({str(k): freeze(v) for k, v in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(freeze(v) for v in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(freeze(v) for v in value)
    return value


@dataclass(frozen=True)
class FactorValue:
    """策略读取到的一个因子值（含时点与状态，缺失不补零）。"""

    name: str
    value: Optional[float] = None
    version: str = ""
    available_at: Optional[str] = None
    status: str = FACTOR_STATUS_OK
    reason: str = ""

    @property
    def missing(self) -> bool:
        return self.value is None or self.status != FACTOR_STATUS_OK


@dataclass(frozen=True)
class StrategyContext:
    """单事件只读上下文：每个事件重新构造，晚于当前时点的数据不会出现。"""

    timestamp: int
    trade_date: str
    phase: str
    candidate: Mapping
    row: Optional[Mapping]
    position: Optional[Mapping]
    account: Mapping
    parameters: Mapping
    factors: Mapping

    @classmethod
    def build(cls, *, timestamp: int, trade_date: str, phase: str,
              candidate: Mapping, row: Optional[Mapping],
              position: Optional[Mapping], account: Mapping,
              parameters: Mapping, factors: Mapping) -> "StrategyContext":
        return cls(
            timestamp=int(timestamp),
            trade_date=str(trade_date),
            phase=str(phase),
            candidate=freeze(candidate or {}),
            row=freeze(row) if row is not None else None,
            position=freeze(position) if position is not None else None,
            account=freeze(account or {}),
            parameters=freeze(parameters or {}),
            factors=freeze(factors or {}),
        )


@dataclass(frozen=True)
class StrategyIntent:
    """策略决策意图（结构化，不直接撮合/记账）。"""

    action: str
    reason_code: str
    reason_text: str = ""
    priority: float = 0.0
    position_pct: Optional[float] = None   # buy：建议上限 (0, 100]
    exit_fraction: Optional[float] = None  # sell：0.5 或 1
    evidence: Mapping = field(default_factory=dict)


@runtime_checkable
class Strategy(Protocol):
    """用户策略协议：工厂 ``create_strategy(params)`` 返回本对象。"""

    descriptor: Mapping

    def evaluate_entry(self, ctx: StrategyContext) -> Optional[StrategyIntent]:
        ...

    def evaluate_exit(self, ctx: StrategyContext) -> Optional[StrategyIntent]:
        ...

    def explain(self, ctx: StrategyContext) -> list[Mapping]:
        ...


def _finite(value: Any, *, field_name: str) -> float:
    import math
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise StrategyError(f"{field_name} 必须是有限数值，得到 {type(value).__name__}")
    number = float(value)
    if not math.isfinite(number):
        raise StrategyError(f"{field_name} 不允许 NaN/Infinity")
    return number


def _json_serializable(value: Any, *, where: str) -> None:
    import json
    try:
        json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise StrategyError(f"{where} 必须是 JSON 可序列化结构：{exc}") from exc


def validate_intent(intent: Any, *, expected_action: str) -> StrategyIntent:
    """把用户返回值规范化为 ``StrategyIntent`` 并做有限校验。

    非法输出必须显式失败（方向错误 / NaN / 越界 / 未知结构），不能被当作
    「无信号」吞掉——否则策略错误会静默变成不交易。
    """
    if intent is None:
        raise StrategyError("内部调用错误：None 应在调用前短路")
    if not isinstance(intent, StrategyIntent):
        raise StrategyError(
            f"策略必须返回 StrategyIntent 或 None，得到 {type(intent).__name__}")
    if intent.action not in INTENT_ACTIONS:
        raise StrategyError(f"未知动作 {intent.action!r}，合法值 {INTENT_ACTIONS}")
    if intent.action != expected_action:
        raise StrategyError(
            f"动作不匹配：{expected_action} 评估返回了 {intent.action}")
    if not isinstance(intent.reason_code, str) or not intent.reason_code.strip():
        raise StrategyError("reason_code 必须是非空字符串")
    if not isinstance(intent.reason_text, str):
        raise StrategyError("reason_text 必须是字符串")
    priority = _finite(intent.priority, field_name="priority")
    if expected_action == "buy":
        if intent.exit_fraction is not None:
            raise StrategyError("买入意图不得设置 exit_fraction")
        if intent.position_pct is not None:
            pct = _finite(intent.position_pct, field_name="position_pct")
            if not (0 < pct <= 100):
                raise StrategyError(f"position_pct 必须在 (0, 100]，得到 {pct}")
    else:
        if intent.position_pct is not None:
            raise StrategyError("卖出意图不得设置 position_pct")
        if intent.exit_fraction is None:
            raise StrategyError("卖出意图必须设置 exit_fraction（0.5 或 1）")
        fraction = _finite(intent.exit_fraction, field_name="exit_fraction")
        if not any(abs(fraction - allowed) < 1e-9 for allowed in EXIT_FRACTIONS):
            raise StrategyError(
                f"exit_fraction 只支持 {EXIT_FRACTIONS}，第一版不支持任意比例分批")
    if not isinstance(intent.evidence, Mapping):
        raise StrategyError("evidence 必须是 Mapping")
    _json_serializable(dict(intent.evidence), where="evidence")
    return StrategyIntent(
        action=intent.action,
        reason_code=intent.reason_code.strip(),
        reason_text=intent.reason_text,
        priority=priority,
        position_pct=(_finite(intent.position_pct, field_name="position_pct")
                      if intent.position_pct is not None else None),
        exit_fraction=(_finite(intent.exit_fraction, field_name="exit_fraction")
                       if intent.exit_fraction is not None else None),
        evidence=dict(intent.evidence),
    )


def _plain(value: Any) -> Any:
    """只读结构 → JSON 可序列化纯数据（worker 协议用）。"""
    if isinstance(value, Mapping):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, (set, frozenset)):
        return sorted(_plain(v) for v in value)
    return value


def context_to_payload(ctx: "StrategyContext") -> dict:
    return {
        "timestamp": ctx.timestamp,
        "trade_date": ctx.trade_date,
        "phase": ctx.phase,
        "candidate": _plain(ctx.candidate),
        "row": _plain(ctx.row) if ctx.row is not None else None,
        "position": _plain(ctx.position) if ctx.position is not None else None,
        "account": _plain(ctx.account),
        "parameters": _plain(ctx.parameters),
        "factors": {
            name: {
                "name": value.name, "value": value.value, "version": value.version,
                "available_at": value.available_at, "status": value.status,
                "reason": value.reason,
            }
            for name, value in (ctx.factors or {}).items()
        },
    }


def context_from_payload(payload: Mapping) -> "StrategyContext":
    factors = {
        name: FactorValue(**dict(item))
        for name, item in (payload.get("factors") or {}).items()
    }
    return StrategyContext.build(
        timestamp=payload["timestamp"], trade_date=payload["trade_date"],
        phase=payload["phase"], candidate=payload.get("candidate") or {},
        row=payload.get("row"), position=payload.get("position"),
        account=payload.get("account") or {},
        parameters=payload.get("parameters") or {}, factors=factors)


def intent_to_payload(intent: Optional["StrategyIntent"]) -> Optional[dict]:
    if intent is None:
        return None
    return {
        "action": intent.action, "reason_code": intent.reason_code,
        "reason_text": intent.reason_text, "priority": intent.priority,
        "position_pct": intent.position_pct, "exit_fraction": intent.exit_fraction,
        "evidence": _plain(intent.evidence),
    }
