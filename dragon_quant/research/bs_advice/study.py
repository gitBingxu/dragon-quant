"""显式研究规范（有限命名 variant / 统计阈值 / 评价截止），严格 schema。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from dragon_quant.review_account.strategy.bs_policy import BSPolicyConfig
from dragon_quant.review_account.strategy.event_inputs import hash_payload

STUDY_SCHEMA_VERSION = 1
MAX_VARIANTS = 20
KINDS = ("baseline_no_new_risk", "policy")


class StudyError(ValueError):
    def __init__(self, message: str, *, code: str = "study_invalid") -> None:
        super().__init__(message)
        self.code = code


def _keys(raw: Mapping, allowed, what: str) -> None:
    unknown = sorted(set(map(str, raw)) - set(allowed))
    if unknown:
        raise StudyError(f"{what} 含未知字段: {unknown}")


@dataclass(frozen=True)
class VariantSpec:
    name: str
    kind: str
    config: BSPolicyConfig = None  # type: ignore[assignment]

    def to_dict(self) -> dict:
        return {
            "name": self.name, "kind": self.kind,
            "config": self.config.to_dict() if self.config else None,
            "config_hash": self.config.content_hash if self.config else None,
            "fixture": bool(self.config.fixture) if self.config else False,
        }


@dataclass(frozen=True)
class StudySpec:
    study_id: str
    variants: tuple[VariantSpec, ...]
    evaluation_cutoff: str = ""
    tail_threshold_pct: float = None  # type: ignore[assignment]
    splits: Mapping = None  # type: ignore[assignment]
    version: int = STUDY_SCHEMA_VERSION

    @property
    def digest(self) -> str:
        return hash_payload(self.to_dict())

    def to_dict(self) -> dict:
        return {
            "schema_version": self.version, "study_id": self.study_id,
            "variants": [variant.to_dict() for variant in self.variants],
            "evaluation_cutoff": self.evaluation_cutoff,
            "tail_threshold_pct": self.tail_threshold_pct,
            "splits": dict(self.splits or {}),
        }


def parse_study(payload: Mapping) -> StudySpec:
    if not isinstance(payload, Mapping):
        raise StudyError("研究规范必须是 JSON 对象")
    _keys(payload, {"schema_version", "study_id", "variants",
                    "evaluation_cutoff", "tail_threshold_pct", "splits"},
          "study")
    if payload.get("schema_version") != STUDY_SCHEMA_VERSION:
        raise StudyError(
            f"不支持的研究规范 schema_version: {payload.get('schema_version')!r}")
    study_id = str(payload.get("study_id") or "").strip()
    if not study_id:
        raise StudyError("study_id 不能为空")
    raw_variants = payload.get("variants")
    if not isinstance(raw_variants, (list, tuple)) or not raw_variants:
        raise StudyError("variants 必须是非空数组")
    if len(raw_variants) > MAX_VARIANTS:
        raise StudyError(f"variants 最多 {MAX_VARIANTS} 个")
    variants, names = [], set()
    for raw in raw_variants:
        if not isinstance(raw, Mapping):
            raise StudyError("每个 variant 必须是 JSON 对象")
        item = dict(raw)
        _keys(item, {"name", "kind", "config"}, "variant")
        name = str(item.get("name") or "").strip()
        if not name or name in names:
            raise StudyError(f"variant name 为空或重复: {name!r}")
        names.add(name)
        kind = str(item.get("kind") or "")
        if kind not in KINDS:
            raise StudyError(f"未知 variant kind: {kind!r}")
        config = None
        if kind == "policy":
            if not isinstance(item.get("config"), Mapping):
                raise StudyError(f"variant {name} 缺 config")
            try:
                config = BSPolicyConfig.from_dict(dict(item["config"]))
            except Exception as exc:  # noqa: BLE001
                raise StudyError(
                    f"variant {name} 配置非法：{exc}") from exc
        elif item.get("config") is not None:
            raise StudyError(f"baseline variant {name} 不应带 config")
        variants.append(VariantSpec(name=name, kind=kind, config=config))
    if not any(variant.kind == "policy" for variant in variants):
        raise StudyError("至少需要一个 policy variant")
    cutoff = payload.get("evaluation_cutoff")
    if cutoff is not None and not isinstance(cutoff, str):
        raise StudyError("evaluation_cutoff 必须是字符串")
    threshold = payload.get("tail_threshold_pct")
    if threshold is not None:
        if isinstance(threshold, bool) or not isinstance(threshold, (int, float)):
            raise StudyError("tail_threshold_pct 必须是数值")
        threshold = float(threshold)
        if not 0 <= threshold <= 100:
            raise StudyError("tail_threshold_pct 必须在 0..100")
    splits = payload.get("splits")
    if splits is not None and not isinstance(splits, Mapping):
        raise StudyError("splits 必须是对象")
    split_data = dict(splits or {})
    _keys(split_data, {"train", "valid", "purge_days", "horizon_days"},
          "study.splits")
    for key, low, high in (("train", 0.0, 1.0), ("valid", 0.0, 1.0)):
        if key in split_data:
            value = split_data[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise StudyError(f"study.splits.{key} 必须是数值")
            if not low <= float(value) <= high:
                raise StudyError(f"study.splits.{key} 必须在 {low}..{high}")
    for key in ("purge_days", "horizon_days"):
        if key in split_data:
            value = split_data[key]
            if isinstance(value, bool) or not isinstance(value, int) \
                    or value < 0:
                raise StudyError(f"study.splits.{key} 必须是非负整数")
    return StudySpec(study_id=study_id, variants=tuple(variants),
                     evaluation_cutoff=cutoff or "", tail_threshold_pct=threshold,
                     splits=split_data)


__all__ = [
    "STUDY_SCHEMA_VERSION", "MAX_VARIANTS", "KINDS", "StudyError",
    "VariantSpec", "StudySpec", "parse_study",
]
