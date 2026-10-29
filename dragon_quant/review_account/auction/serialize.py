"""auction.serialize — 竞价原始资产与 v2 结果序列化（纯函数）。

职责边界：
  - 只做 Quote ↔ dict、AuctionVerdict → dict，不触碰存储层；
  - 输出仅含 JSON 原生类型（dict/list/str/int/float/bool/None）；
  - 非有限浮点一律清洗为 ``None``（``strength`` / ``level`` / ``dims.*.score``
    允许 null，不再用 0 / 50 冒充）；
  - ``version`` 固定字符串 ``"2"``；旧键顺序保留，新增键只追加。
"""

from __future__ import annotations

import math
from dataclasses import asdict, fields
from typing import Any, Optional

from dragon_quant.infra.models import Quote
from dragon_quant.review_account.auction.types import AuctionVerdict

from .features import PIT_QUOTE_FIELDS


class QuotePayloadError(ValueError):
    """quote_json 存在但无法还原为 Quote（损坏 / 关键字段缺失）。"""


_REQUIRED_QUOTE_FIELDS = ("code", "prev_close", "limit_down", "float_market_cap")
_STR_FIELDS = {"code", "name"}
_INT_FIELDS = {"timestamp"}
_OPTIONAL_FIELDS = {"main_net_amount"}


def sanitize_json(value: Any) -> Any:
    """递归清洗为 JSON 原生类型；非有限 float → None。"""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(k): sanitize_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize_json(v) for v in value]
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if hasattr(value, "item"):
        return sanitize_json(value.item())
    raise TypeError(f"不支持 JSON 序列化的类型: {type(value).__name__}")


def _nullable(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _as_float(value: Any, strict_name: Optional[str] = None) -> float:
    if value is None:
        return 0.0
    try:
        number = float(value)
    except (TypeError, ValueError):
        if strict_name:
            raise QuotePayloadError(f"{strict_name} 不是数值: {value!r}")
        return 0.0
    if not math.isfinite(number):
        if strict_name:
            raise QuotePayloadError(f"{strict_name} 非有限值: {value!r}")
        return 0.0
    return number


def _as_int(value: Any) -> int:
    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def quote_pit_fields() -> tuple:
    """允许进入竞价评分的行情字段（PIT 白名单，方案 §6.1）。"""
    return PIT_QUOTE_FIELDS


def quote_to_payload(quote: Optional[Quote]) -> Optional[dict]:
    if quote is None:
        return None
    return sanitize_json(asdict(quote))


def quote_from_payload(payload: Optional[dict]) -> Optional[Quote]:
    if payload is None:
        return None
    if not isinstance(payload, dict):
        raise QuotePayloadError(f"quote payload 必须是 dict，实际 {type(payload).__name__}")
    missing = [name for name in _REQUIRED_QUOTE_FIELDS if name not in payload]
    if missing:
        raise QuotePayloadError(f"quote payload 缺必填字段 {missing}")

    code = payload.get("code")
    if not isinstance(code, str) or not code.strip():
        raise QuotePayloadError("quote payload 的 code 非法")

    kwargs: dict[str, Any] = {}
    strict_numeric = {"prev_close", "limit_down", "float_market_cap"}
    for field in fields(Quote):
        name = field.name
        value = payload.get(name)
        if name in _STR_FIELDS:
            kwargs[name] = str(value) if value is not None else ""
        elif name in _INT_FIELDS:
            kwargs[name] = _as_int(value)
        elif name in _OPTIONAL_FIELDS:
            kwargs[name] = _as_float(value) if value is not None else None
        else:
            kwargs[name] = _as_float(value, name if name in strict_numeric else None)
    return Quote(**kwargs)


def _dim_payload(item) -> dict:
    return {
        "dim": item.dim,
        "label": item.label,
        "score": _nullable(item.score),
        "weight": _nullable(item.weight),
        "effective_weight": _nullable(item.effective_weight),
        "status": item.status,
        "details": sanitize_json(item.details),
    }


def verdict_to_payload(verdict: AuctionVerdict) -> dict:
    """AuctionVerdict → ``analysis_json`` v2 契约 dict（旧键顺序保留）。"""
    dims = {name: _dim_payload(item) for name, item in verdict.dims.items()}
    quality = verdict.data_quality
    return {
        "version": "2",
        "code": verdict.code,
        "is_weak": bool(verdict.is_weak),
        "strength": _nullable(verdict.strength),
        "direction": verdict.direction,
        "level": verdict.level,
        "dims": dims,
        "reject_reason": verdict.reject_reason,
        "unverified": bool(verdict.unverified),
        "rank": None if verdict.rank is None else int(verdict.rank),
        "rank_reason": verdict.rank_reason,
        "pre_veto_strength": _nullable(verdict.pre_veto_strength),
        "validation_status": verdict.validation_status,
        "config_version": verdict.config_version,
        "config_hash": verdict.config_hash,
        "semantics": sanitize_json(dict(verdict.semantics)),
        "data_quality": {
            "status": quality.status,
            "completeness": _nullable(quality.completeness),
            "reason_codes": list(quality.reason_codes),
            "reasons": sanitize_json([dict(r) for r in quality.reasons]),
        },
        "risk_events": [event.as_dict() for event in verdict.risk_events],
        "raw_factors": sanitize_json(dict(verdict.raw_factors)),
        "evidence": sanitize_json(dict(verdict.evidence)),
        "reference_prices": sanitize_json(dict(verdict.reference_prices)),
        "windows": sanitize_json(dict(verdict.windows)),
        "actions": sanitize_json([dict(a) for a in verdict.actions]),
    }
