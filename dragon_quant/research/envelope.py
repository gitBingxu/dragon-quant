"""research 公共 envelope、参数校验与游标（``research_v1``）。

约定（方案 §4）：

* 成功与可解释降级都返回同一个完整对象，``ok`` 区分；
* 非法参数 / 未知 dataset/field/filter / 无效 cursor → exit 2；
* 损坏、I/O、只读不可用 → 结构化错误 exit 1；
* 空集 exit 0，并给 ``empty_reason``；``meta`` 永远包含来源、质量、版本、
  返回数、截断与下一页游标。
"""

from __future__ import annotations

import base64
import binascii
import json
from datetime import datetime
from typing import Any, Iterable, Optional

SCHEMA_VERSION = "research_v1"


class ResearchError(Exception):
    code = "research_error"
    exit_code = 1

    def __init__(self, message: str, *, details: Any = None):
        super().__init__(message)
        self.message = message
        self.details = details


class InvalidRequest(ResearchError):
    code = "invalid_request"
    exit_code = 2


class InvalidCursor(ResearchError):
    code = "invalid_cursor"
    exit_code = 2


class ReadonlyUnavailable(ResearchError):
    code = "readonly_unavailable"
    exit_code = 1


def normalize_date(raw: str | None, field: str) -> str | None:
    """Accepted forms: YYYYMMDD / YYYY-MM-DD / YYYY/MM/DD; validated as a real date."""
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    candidates = (text,)
    if len(text) == 8 and text.isdigit():
        candidates = (f"{text[:4]}-{text[4:6]}-{text[6:8]}",)
    elif len(text) == 10 and text[4] == "/" and text[7] == "/":
        candidates = (f"{text[:4]}-{text[5:7]}-{text[8:10]}",)
    try:
        parsed = datetime.strptime(candidates[0], "%Y-%m-%d")
    except ValueError as exc:
        raise InvalidRequest(f"{field} 不是有效日期：{raw!r}") from exc
    return parsed.strftime("%Y-%m-%d")


def normalize_code(raw: str | None) -> str | None:
    if raw is None or str(raw).strip() == "":
        return None
    text = str(raw).strip()
    if not (len(text) == 6 and text.isdigit()):
        raise InvalidRequest(f"--code 必须是 6 位数字：{raw!r}")
    return text


def normalize_int(raw: Any, field: str) -> int | None:
    if raw is None:
        return None
    if isinstance(raw, bool):
        raise InvalidRequest(f"{field} 必须是整数")
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise InvalidRequest(f"{field} 必须是整数：{raw!r}") from exc
    if value < 0:
        raise InvalidRequest(f"{field} 不能为负数：{value}")
    return value


def validate_range(date_from: str | None, date_to: str | None) -> None:
    if date_from and date_to and date_from > date_to:
        raise InvalidRequest("--from 不能晚于 --to")


def _b64encode(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


def _b64decode(text: str) -> str:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding).decode("utf-8")


def encode_cursor(*, dataset: str, filters: dict, fields: list[str] | None,
                  offset: int) -> str:
    payload = {
        "v": 1,
        "dataset": dataset,
        "filters": filters,
        "fields": fields,
        "offset": int(offset),
    }
    return _b64encode(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def decode_cursor(raw: str | None, *, dataset: str, filters: dict,
                  fields: list[str] | None) -> int:
    if raw is None or str(raw) == "":
        return 0
    try:
        payload = json.loads(_b64decode(str(raw)))
    except (binascii.Error, ValueError, UnicodeDecodeError) as exc:
        raise InvalidCursor("cursor 无效（不是本命令生成的游标）") from exc
    if not isinstance(payload, dict) or payload.get("v") != 1:
        raise InvalidCursor("cursor 版本不受支持")
    if payload.get("dataset") != dataset:
        raise InvalidCursor("cursor 属于其他 dataset")
    if payload.get("filters") != filters:
        raise InvalidCursor("cursor 与当前过滤器不匹配")
    if payload.get("fields") != fields:
        raise InvalidCursor("cursor 与当前字段选择不匹配")
    offset = payload.get("offset")
    if not isinstance(offset, int) or offset < 0:
        raise InvalidCursor("cursor 偏移无效")
    return offset


def versions_meta() -> dict:
    """可得的运行时版本标识；不可得显式 null 并给原因，不伪造。"""
    from dragon_quant._version import __version__

    meta = {"cli": __version__, "score": None, "strategy": None, "snapshot": None}
    try:
        from dragon_quant.scan.scores import registry as _registry

        # 只暴露版本标识；具体权重/门槛由 `research strategy` 显式给出。
        meta["score"] = "five_dim_v2" if hasattr(_registry, "DIM_WEIGHTS") else None
    except Exception:  # noqa: BLE001 - 版本信息不可得不是查询失败
        meta["score_unavailable_reason"] = "score_registry_unavailable"
    try:
        from dragon_quant.review_account.models import StrategyConfig

        meta["strategy"] = StrategyConfig().strategy_name
    except Exception:  # noqa: BLE001
        meta["strategy_unavailable_reason"] = "strategy_module_unavailable"
    return meta


def envelope(*, command: str, request: dict, data: Any,
             warnings: Iterable[str] = (),
             meta: Optional[dict] = None, ok: bool = True,
             error: Optional[dict] = None) -> dict:
    base_meta = {
        "source": "v2",
        "data_quality": "ok",
        "score_version": None,
        "strategy_version": None,
        "snapshot_version": None,
        "returned": None,
        "truncated": False,
        "next_cursor": None,
        "non_atomic_read": True,
    }
    base_meta.update(meta or {})
    versions = versions_meta()
    if base_meta.get("score_version") is None:
        base_meta["score_version"] = versions.get("score")
    if base_meta.get("strategy_version") is None:
        base_meta["strategy_version"] = versions.get("strategy")
    if base_meta.get("snapshot_version") is None:
        base_meta["snapshot_version"] = versions.get("snapshot")
    base_meta["versions_available"] = {
        "score": base_meta["score_version"] is not None,
        "strategy": base_meta["strategy_version"] is not None,
        "snapshot": base_meta["snapshot_version"] is not None,
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "ok": bool(ok),
        "command": command,
        "request": request,
        "data": data,
        "meta": base_meta,
        "warnings": list(warnings),
        "error": error,
    }


def error_envelope(*, command: str, request: dict, exc: ResearchError) -> dict:
    payload = envelope(
        command=command, request=request, data=None, ok=False,
        meta={"data_quality": "error"},
        error={"code": exc.code, "message": exc.message, "details": exc.details},
    )
    return payload
