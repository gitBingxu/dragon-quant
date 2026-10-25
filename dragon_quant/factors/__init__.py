"""因子层公共门面（M7：收窄到 13 个批准出口 + 一轮 lazy 旧名兼容）。

分层约束不变：``factors/*`` 不 import ``scorers/*`` / ``review_account/*`` /
``auction/*``（``factors/finance.py`` 的 providers 例外仅限该模块）；
``storage/*`` 不 import ``factors/*``。

公共出口（``__all__``，顺序即用户批准的 B3 清单）：
    build / list_factors / query / status / export_matrix / evaluate_factor /
    fin_sync / fin_calibrate / fin_status / parse_only /
    FactorModel / annotate_pool / CleanConfig

``export_matrix`` / ``evaluate_factor`` 保持收窄前的原始实现身份
（``factors.matrix`` / ``factors.evaluation``）；CLI 的用例包装
（日期归一 + ``ok`` 退出码语义）位于文档化公共子模块 ``factors.service``，
命令层经该子模块调用，不改变包根语义。

兼容策略（保留一轮，M11 删除旧名）：
  - 旧包根名仍可 ``from dragon_quant.factors import canonical_scan``，但不再出现在
    ``__all__``，只经 ``_LEGACY_EXPORTS`` 懒解析；
  - 解析永远运行期进行且**不写回模块 ``__dict__``**：这样
    ``patch("dragon_quant.factors.service.build")`` 对 ``factors.build`` 仍然生效，
    命令层无需深引内部模块；
  - 包根自身零 eager import（PEP 562），``import dragon_quant.factors`` 不会加载任何
    ``dragon_quant.factors.*`` 子模块。
"""

from __future__ import annotations

import importlib

__all__ = [
    "build",
    "list_factors",
    "query",
    "status",
    "export_matrix",
    "evaluate_factor",
    "fin_sync",
    "fin_calibrate",
    "fin_status",
    "parse_only",
    "FactorModel",
    "annotate_pool",
    "CleanConfig",
]

# public name -> owner submodule（相对 factors 包）。
# export_matrix / evaluate_factor 保持 M7 之前的原始实现身份（matrix / evaluation），
# CLI 用的用例包装属于 ``factors.service``，命令层经该文档化公共子模块调用。
_PUBLIC_EXPORTS = {
    "build": "service",
    "list_factors": "service",
    "query": "service",
    "status": "service",
    "export_matrix": "matrix",
    "evaluate_factor": "evaluation",
    "fin_sync": "service",
    "fin_calibrate": "service",
    "fin_status": "service",
    "parse_only": "service",
    "FactorModel": "decision",
    "annotate_pool": "decision",
    "CleanConfig": "matrix",
}

# 旧包根名（保留一轮，不进 __all__；M11 删除）。
_LEGACY_EXPORTS = {
    "FACTOR_SPECS": "registry",
    "AsOfViolation": "types",
    "BackfilledSource": "types",
    "EqualWeightRankModel": "decision",
    "FactorContext": "types",
    "FactorFrame": "types",
    "FactorRegistryError": "registry",
    "FactorSpec": "types",
    "FactorValue": "types",
    "PoolInput": "decision",
    "PoolItem": "decision",
    "build_completion_rate": "decision",
    "build_date": "engine",
    "build_factor": "engine",
    "canonical_scan": "engine",
    "candidate_factor_date": "decision",
    "compute_version": "registry",
    "compute_version_info": "registry",
    "default_snapshot": "decision",
    "get_spec": "registry",
    "phase_ts": "engine",
    "resolve_available_at": "engine",
    "resolve_specs": "registry",
    "save_default_model_snapshot": "decision",
    "spec_names": "registry",
}

_LAZY_EXPORTS = {**_PUBLIC_EXPORTS, **_LEGACY_EXPORTS}


def __getattr__(name: str):
    """PEP 562 lazy 解析：公共名 + 旧名共用一张映射，永不缓存。"""
    owner = _LAZY_EXPORTS.get(name)
    if owner is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = importlib.import_module(f"{__name__}.{owner}")
    return getattr(module, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
