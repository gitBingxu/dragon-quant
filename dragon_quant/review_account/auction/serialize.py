"""auction.serialize — 竞价原始资产/结果的 JSON 序列化（纯函数）。

职责边界：
  - 只做 Quote ↔ dict、AuctionVerdict → dict，不触碰存储层；
  - 输出仅含 JSON 原生类型（dict/list/str/int/float/bool/None）；
  - 非有限浮点按上下文处理：
      * Quote 快照 / ScoreResult.details：清洗为 ``None``（原始资产允许未知值）；
      * 契约数值字段（strength / score / weight）：抛 ``ValueError``，
        由调用方隔离为该股写入失败，**不得**写 null 污染契约。
"""

from __future__ import annotations

import math
from dataclasses import asdict, fields
from typing import Any, Optional

from dragon_quant.infra.models import Quote
from dragon_quant.review_account.auction.types import AuctionVerdict


class QuotePayloadError(ValueError):
    """quote_json 存在但无法还原为 Quote（损坏 / 关键字段缺失）。"""


# quote 还原的必填字段：评分器实际消费的三个数值 + 代码身份。
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
    if hasattr(value, "item"):          # numpy 标量等：不引入依赖，尽力还原
        return sanitize_json(value.item())
    raise TypeError(f"不支持 JSON 序列化的类型: {type(value).__name__}")


def _as_float(value: Any, strict_name: Optional[str] = None) -> float:
    """None / 非有限 → 0.0；``strict_name`` 用于必填数值字段，非法即抛错。"""
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


def quote_to_payload(quote: Optional[Quote]) -> Optional[dict]:
    """Quote → JSON 原生 dict；非有限浮点清洗为 None。None → None。"""
    if quote is None:
        return None
    return sanitize_json(asdict(quote))


def quote_from_payload(payload: Optional[dict]) -> Optional[Quote]:
    """dict → Quote；None → None；损坏/必填缺失 → QuotePayloadError。

    - 必填：code / prev_close / limit_down / float_market_cap；
    - 可空数值字段按 0.0 还原（与 TDX `_f` 口径一致），main_net_amount 保持 None；
    - 未知键忽略（前向兼容）。
    """
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


def _finite(value: Any, field_name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} 不是数值: {value!r}") from exc
    if not math.isfinite(number):
        raise ValueError(f"{field_name} 非有限值: {value!r}")
    return number


def verdict_to_payload(verdict: AuctionVerdict) -> dict:
    """AuctionVerdict → assets_db.analysis_json 契约 dict（version 固定 "1"）。"""
    dims: dict[str, dict] = {}
    for name, result in verdict.dims.items():
        dims[name] = {
            "dim": result.dim,
            "score": _finite(result.score, f"dims.{name}.score"),
            "weight": _finite(result.weight, f"dims.{name}.weight"),
            "details": sanitize_json(result.details),
        }
    rank = None if verdict.rank is None else int(verdict.rank)
    return {
        "version": "1",
        "code": verdict.code,
        "is_weak": bool(verdict.is_weak),
        "strength": _finite(verdict.strength, "strength"),
        "direction": verdict.direction,
        "level": verdict.level,
        "dims": dims,
        "reject_reason": verdict.reject_reason,
        "unverified": bool(verdict.unverified),
        "rank": rank,
    }
