"""因子层门面（方案 §3.4）。

对外只暴露少量入口；CLI 与未来的 ``review_account`` 接入不关心内部模块。
分层约束：``factors/*`` 不 import ``scorers/*``；``storage/*`` 不 import ``factors/*``。
"""

from __future__ import annotations

from dragon_quant.factors.decision import (
    EqualWeightRankModel,
    FactorModel,
    PoolInput,
    PoolItem,
    annotate_pool,
    build_completion_rate,
    candidate_factor_date,
    default_snapshot,
    save_default_model_snapshot,
)
from dragon_quant.factors.engine import (
    build_date,
    build_factor,
    canonical_scan,
    phase_ts,
    resolve_available_at,
)
from dragon_quant.factors.evaluation import evaluate_factor
from dragon_quant.factors.matrix import CleanConfig, export_matrix
from dragon_quant.factors.registry import (
    FACTOR_SPECS,
    FactorRegistryError,
    compute_version,
    compute_version_info,
    get_spec,
    resolve_specs,
    spec_names,
)
from dragon_quant.factors.service import (
    build,
    list_factors,
    query,
    status,
)
from dragon_quant.factors.types import (
    AsOfViolation,
    BackfilledSource,
    FactorContext,
    FactorFrame,
    FactorSpec,
    FactorValue,
)

__all__ = [
    "FACTOR_SPECS",
    "AsOfViolation",
    "BackfilledSource",
    "CleanConfig",
    "EqualWeightRankModel",
    "FactorContext",
    "FactorFrame",
    "FactorModel",
    "FactorRegistryError",
    "FactorSpec",
    "FactorValue",
    "PoolInput",
    "PoolItem",
    "annotate_pool",
    "build",
    "build_completion_rate",
    "build_date",
    "build_factor",
    "canonical_scan",
    "candidate_factor_date",
    "compute_version",
    "compute_version_info",
    "default_snapshot",
    "evaluate_factor",
    "export_matrix",
    "get_spec",
    "list_factors",
    "phase_ts",
    "query",
    "resolve_available_at",
    "resolve_specs",
    "save_default_model_snapshot",
    "spec_names",
    "status",
]
