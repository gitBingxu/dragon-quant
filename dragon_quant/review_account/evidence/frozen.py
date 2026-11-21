"""递归只读结构与严格反序列化辅助（纯值语义）。"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any, Iterable, Optional

from .schema import EvidenceError

class FrozenMapping(Mapping):
    """递归只读映射：构造时冻结所有嵌套 Mapping/list/tuple。

    ``to_dict`` 输出独立普通结构，修改导出 dict（含嵌套）不会回写对象。
    """

    __slots__ = ("_data",)

    def __init__(self, data: Mapping) -> None:
        if not isinstance(data, Mapping):
            raise EvidenceError(f"只读映射需要 Mapping，实际 {type(data).__name__}")
        object.__setattr__(
            self, "_data", {str(key): freeze(value) for key, value in data.items()})

    def __getitem__(self, key: Any) -> Any:
        return self._data[key]

    def __iter__(self):
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def __repr__(self) -> str:  # pragma: no cover - 诊断输出
        return f"FrozenMapping({self._data!r})"

    def __eq__(self, other: object) -> bool:
        if isinstance(other, FrozenMapping):
            return thaw(self) == thaw(other)
        if isinstance(other, Mapping):
            return thaw(self) == thaw(dict(other))
        return NotImplemented

    def __ne__(self, other: object) -> bool:
        result = self.__eq__(other)
        return NotImplemented if result is NotImplemented else not result



def freeze(value: Any) -> Any:
    """递归冻结普通值：Mapping → FrozenMapping，序列 → tuple，集合 → frozenset。"""
    if isinstance(value, FrozenMapping):
        return value
    if isinstance(value, Mapping):
        return FrozenMapping(value)
    if isinstance(value, (list, tuple)):
        return tuple(freeze(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(freeze(item) for item in value)
    return value


def thaw(value: Any) -> Any:
    """把冻结结构还原成独立普通 JSON 结构（dict / list / 标量）。"""
    if isinstance(value, Mapping):
        return {str(key): thaw(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [thaw(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted((thaw(item) for item in value), key=repr)
    return copy.deepcopy(value)


def _strict_mapping(payload: Any, *, what: str) -> Mapping:
    if not isinstance(payload, Mapping):
        raise EvidenceError(f"{what} 必须是对象，实际 {type(payload).__name__}")
    return payload


def _optional_mapping(payload: Mapping, key: str, *, what: str) -> Mapping:
    value = payload.get(key)
    if value is None:
        return {}
    return _strict_mapping(value, what=what)


def _strict_keys(payload: Mapping, allowed: Iterable[str], *, what: str) -> None:
    unknown = sorted(set(map(str, payload)) - set(allowed))
    if unknown:
        raise EvidenceError(f"{what} 含未知字段: {unknown}", reason="schema_mismatch")


def _string(payload: Mapping, key: str, *, default: Optional[str] = None,
            required: bool = False) -> str:
    value = payload.get(key, default)
    if value is None:
        if required:
            raise EvidenceError(f"缺少必填字段 {key}", reason="schema_mismatch")
        return ""
    if not isinstance(value, str):
        raise EvidenceError(f"{key} 必须是字符串", reason="schema_mismatch")
    return value


def _integer(payload: Mapping, key: str, *, default: int = 0) -> int:
    value = payload.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise EvidenceError(f"{key} 必须是整数", reason="schema_mismatch")
    if value < 0:
        raise EvidenceError(f"{key} 不能为负", reason="schema_mismatch")
    return value


# ─── 来源语义校准 ────────────────────────────────────────────────────────────
