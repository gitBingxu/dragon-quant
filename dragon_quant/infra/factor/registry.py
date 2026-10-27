"""因子注册表：``FACTOR_SPECS`` + 校验 + 内容哈希版本（方案 §4.1 / §4.2）。

版本 = 语义字段 + 源码指纹的内容哈希（12 位）。源码指纹用标准库 ``tokenize``
做 token 级规范化（丢弃 COMMENT / NL / 换行 / 缩进），**不得用正则剥 ``#``**：
否则 ``"a#b"`` 这类字符串字面量会被破坏，语义未变却产生新版本。
``getsource`` 失败（冻结包 / zip 导入 / 交互式定义）→ 回退 ``impl_key``，
调用方在 build 报告中记 ``version_source="impl_key"`` 警告。
"""

from __future__ import annotations

import hashlib
import inspect
import io
import json
import math
import tokenize
from typing import Any, Iterable, Mapping, Optional

from dragon_quant.infra.factor.builtin import all_specs
from dragon_quant.infra.factor.types import FactorSpec

FREQUENCIES = ("1d", "5m", "event", "1q")
SOURCES = ("scan", "vpa", "prep", "auction", "computed", "fin")
DIRECTIONS = ("higher_better", "lower_better", "neutral")
UNITS = ("score", "pct", "ratio", "count", "flag", "cny", "cny_per_share")
AVAILABLE_PHASES = ("auction", "open", "close", "prep")
PIT_RULES = ("recorded", "derived", "asof")
SCOPES = ("candidate", "dragon")

_IGNORED_TOKENS = frozenset({
    tokenize.COMMENT,
    tokenize.NL,
    tokenize.NEWLINE,
    tokenize.INDENT,
    tokenize.DEDENT,
    tokenize.ENCODING,
})


class FactorRegistryError(ValueError):
    """注册表非法（重名 / 非法枚举值 / 悬空 depends_on）。"""


def _canonical(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(k): _canonical(v) for k, v in value.items()}
    if isinstance(value, (set, frozenset)):
        return sorted((_canonical(v) for v in value), key=repr)
    if isinstance(value, (list, tuple)):
        return [_canonical(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("因子参数含非有限浮点（NaN/inf），拒绝参与版本哈希")
    return value


def canonical_json(payload: Any) -> str:
    """确定性 JSON（键序稳定 + 拒绝 NaN），用于版本哈希。"""
    return json.dumps(_canonical(payload), ensure_ascii=False, sort_keys=True,
                      allow_nan=False, separators=(",", ":"))


def _normalize_tokens(source: str) -> Optional[str]:
    """token 级规范化源码：只保留语义 token，丢弃注释 / 空白 / 缩进。"""
    try:
        tokens = tokenize.generate_tokens(io.StringIO(source).readline)
        parts = [
            tok.string for tok in tokens
            if tok.type not in _IGNORED_TOKENS and tok.type != tokenize.ENDMARKER
        ]
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return None
    return "".join(parts)


def source_fingerprint(compute) -> Optional[str]:
    """实现指纹（token 规范化后的源码 sha256）；取不到源码返回 None。"""
    try:
        source = inspect.getsource(compute)
    except (OSError, TypeError):
        return None
    normalized = _normalize_tokens(source)
    if normalized is None:
        return None
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


_UNSET = object()


def spec_payload(spec: FactorSpec, *, fingerprint=_UNSET) -> dict:
    """参与版本哈希的语义字段（label / description / owner 刻意排除）。"""
    if fingerprint is _UNSET:
        fingerprint = source_fingerprint(spec.compute)
    return {
        "name": spec.name,
        "frequency": spec.frequency,
        "source": spec.source,
        "depends_on": sorted(spec.depends_on),
        "lookback": spec.lookback,
        "direction": spec.direction,
        "unit": spec.unit,
        "available_phase": spec.available_phase,
        "pit_rule": spec.pit_rule,
        "scope": sorted(spec.scope),
        "params": _canonical(spec.params),
        "impl": fingerprint or spec.impl_key,
    }


def compute_version_info(spec: FactorSpec) -> tuple[str, str]:
    """返回 ``(version, version_source)``。

    ``version_source`` ∈ ``{"override", "source", "impl_key"}``。
    ``override`` 仅迁移场景使用；``impl_key`` 表示源码不可得（冻结包等），
    调用方应在 build 报告中标记警告。
    """
    if spec.version_override:
        return spec.version_override, "override"
    fingerprint = source_fingerprint(spec.compute)
    payload = spec_payload(spec, fingerprint=fingerprint)
    digest = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()[:12]
    return f"v{digest}", ("source" if fingerprint else "impl_key")


def compute_version(spec: FactorSpec) -> str:
    return compute_version_info(spec)[0]


def spec_hash(spec: FactorSpec) -> str:
    """完整 spec 哈希（落 ``factor_registry.spec_hash``，供审计对账）。"""
    payload = spec_payload(spec)
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def validate_registry(specs: Iterable[FactorSpec]) -> None:
    """校验注册表；任何非法项直接 ``FactorRegistryError``（fail-fast）。"""
    specs = list(specs)
    seen: dict[str, FactorSpec] = {}
    for spec in specs:
        if not isinstance(spec, FactorSpec):
            raise FactorRegistryError(f"注册表元素不是 FactorSpec: {spec!r}")
        if not spec.name or not isinstance(spec.name, str):
            raise FactorRegistryError("因子 name 必须是非空字符串")
        if spec.name in seen:
            raise FactorRegistryError(f"因子重名: {spec.name}")
        seen[spec.name] = spec
        if spec.frequency not in FREQUENCIES:
            raise FactorRegistryError(
                f"{spec.name}: 非法 frequency={spec.frequency!r}，合法值 {FREQUENCIES}")
        if spec.source not in SOURCES:
            raise FactorRegistryError(
                f"{spec.name}: 非法 source={spec.source!r}，合法值 {SOURCES}")
        if spec.direction not in DIRECTIONS:
            raise FactorRegistryError(
                f"{spec.name}: 非法 direction={spec.direction!r}，合法值 {DIRECTIONS}")
        if spec.unit not in UNITS:
            raise FactorRegistryError(
                f"{spec.name}: 非法 unit={spec.unit!r}，合法值 {UNITS}")
        if spec.available_phase not in AVAILABLE_PHASES:
            raise FactorRegistryError(
                f"{spec.name}: 非法 available_phase={spec.available_phase!r}，"
                f"合法值 {AVAILABLE_PHASES}")
        if spec.pit_rule not in PIT_RULES:
            raise FactorRegistryError(
                f"{spec.name}: 非法 pit_rule={spec.pit_rule!r}，合法值 {PIT_RULES}")
        if isinstance(spec.scope, str) or not spec.scope:
            raise FactorRegistryError(f"{spec.name}: scope 必须是非空元组")
        for scope in spec.scope:
            if scope not in SCOPES:
                raise FactorRegistryError(
                    f"{spec.name}: 非法 scope={scope!r}，合法值 {SCOPES}")
        if not isinstance(spec.lookback, int) or spec.lookback < 0:
            raise FactorRegistryError(f"{spec.name}: lookback 必须是非负整数")
        if not callable(spec.compute):
            raise FactorRegistryError(f"{spec.name}: compute 必须可调用")
        if not spec.impl_key:
            raise FactorRegistryError(f"{spec.name}: impl_key 不能为空")
        if not isinstance(spec.params, Mapping):
            raise FactorRegistryError(f"{spec.name}: params 必须是 Mapping")
    for spec in specs:
        for dep in spec.depends_on:
            if dep not in seen:
                raise FactorRegistryError(
                    f"{spec.name}: depends_on 指向不存在的因子 {dep!r}")


FACTOR_SPECS: tuple[FactorSpec, ...] = all_specs()
_BY_NAME: dict[str, FactorSpec] = {spec.name: spec for spec in FACTOR_SPECS}

validate_registry(FACTOR_SPECS)


def spec_names() -> tuple[str, ...]:
    return tuple(sorted(_BY_NAME))


def get_spec(name: str) -> FactorSpec:
    try:
        return _BY_NAME[name]
    except KeyError as exc:
        raise FactorRegistryError(f"未知因子: {name!r}（合法值 {spec_names()}）") from exc


def resolve_specs(names: Optional[Iterable[str]] = None,
                  *, enabled_only: bool = False) -> list[FactorSpec]:
    """按名字解析 spec；``names=None`` → 全部（或仅 ``default_enabled``）。

    返回按 name 升序，保证 build / 报告顺序确定。
    """
    if names is None:
        specs = list(FACTOR_SPECS)
        if enabled_only:
            specs = [s for s in specs if s.default_enabled]
    else:
        ordered: list[FactorSpec] = []
        unknown: list[str] = []
        seen: set[str] = set()
        for name in names:
            if name in seen:
                continue
            seen.add(name)
            spec = _BY_NAME.get(name)
            if spec is None:
                unknown.append(name)
                continue
            ordered.append(spec)
        if unknown:
            raise FactorRegistryError(
                f"未知因子: {sorted(unknown)}（合法值 {spec_names()}）")
        specs = ordered
    return sorted(specs, key=lambda s: s.name)


def spec_to_registry_row(spec: FactorSpec, version: str,
                         *, valid_from: Optional[str] = None) -> dict:
    """构造 ``factor_registry`` 行（存储层只收 plain dict）。

    ``valid_from`` 允许运行期覆盖：财务因子的数据可得起点由 build 解析
    （``fin_earliest_available``）后写入，spec 本身保持 ``None``（方案 §4.1）。
    """
    return {
        "factor_name": spec.name,
        "factor_version": version,
        "label": spec.label,
        "frequency": spec.frequency,
        "source": spec.source,
        "depends_on_json": canonical_json(list(spec.depends_on)),
        "lookback": spec.lookback,
        "direction": spec.direction,
        "unit": spec.unit,
        "available_phase": spec.available_phase,
        "pit_rule": spec.pit_rule,
        "scope_json": canonical_json(list(spec.scope)),
        "params_json": canonical_json(dict(spec.params)),
        "default_enabled": spec.default_enabled,
        "valid_from": valid_from if valid_from is not None else spec.valid_from,
        "deprecated_at": spec.deprecated_at,
        "owner": spec.owner,
        "description": spec.description,
        "spec_hash": spec_hash(spec),
    }
