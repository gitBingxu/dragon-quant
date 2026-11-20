"""证据快照与能力检查（W1）稳定窄入口。

分层（改动原因隔离）：

- :mod:`schema` 版本/能力常量/错误；:mod:`frozen` 递归只读值；
- :mod:`semantics` 来源校准与按能力字段要求；:mod:`facts` 单条事实；
- :mod:`snapshots` 冻结请求/快照/指纹；:mod:`store_adapter` 只读仓储适配；
- :mod:`need` / :mod:`checks` 证据需求与能力判定/覆盖。

外部调用继续使用 ``from dragon_quant.review_account import evidence``；
本层不执行交易、不构造 provider、不读真实账户。
"""

from __future__ import annotations

from .checks import (
    check_need,
    check_needs,
    coverage_summary,
    source_status,
    summarize_checks,
)
from .facts import EvidenceFact
from .frozen import FrozenMapping, freeze, thaw
from .need import CapabilityCheck, EvidenceNeed
from .schema import (
    BAR_CAPABILITIES,
    CALIBRATION_VERSION,
    CAPABILITIES,
    DEFAULT_BAR_LIMIT,
    DEFAULT_FACT_LIMIT,
    EVIDENCE_SCHEMA_VERSION,
    INPUT_ACCOUNT_STATE,
    INPUT_EXTERNAL_SOURCES,
    INPUT_RISK_BUDGET,
    INPUT_SELLABLE_QTY,
    OBS_CAPABILITIES,
    PROTECT_EXISTING,
    PROTECT_NEW_RISK,
    QUALITY_UNVERIFIED,
    QUALITY_VERIFIED,
    SUPPORTED_SCHEMA_VERSIONS,
    TERMINAL_SOURCE_EXCHANGE_FINAL,
    UNVERIFIED,
    VERIFIED,
    EvidenceError,
)
from .semantics import (
    CAPABILITY_REQUIREMENTS,
    DEFAULT_SEMANTICS,
    CapabilityRequirement,
    SourceSemantics,
    derive_usable,
    semantics_for,
    bar_payload_valid,
)
from .snapshots import EvidenceRequest, EvidenceSnapshot, slice_snapshot

__all__ = [
    "EVIDENCE_SCHEMA_VERSION",
    "SUPPORTED_SCHEMA_VERSIONS",
    "CALIBRATION_VERSION",
    "DEFAULT_FACT_LIMIT",
    "DEFAULT_BAR_LIMIT",
    "CAPABILITIES",
    "CAPABILITY_REQUIREMENTS",
    "OBS_CAPABILITIES",
    "BAR_CAPABILITIES",
    "INPUT_ACCOUNT_STATE",
    "INPUT_RISK_BUDGET",
    "INPUT_SELLABLE_QTY",
    "INPUT_EXTERNAL_SOURCES",
    "PROTECT_NEW_RISK",
    "PROTECT_EXISTING",
    "TERMINAL_SOURCE_EXCHANGE_FINAL",
    "QUALITY_VERIFIED",
    "QUALITY_UNVERIFIED",
    "VERIFIED",
    "UNVERIFIED",
    "EvidenceError",
    "FrozenMapping",
    "freeze",
    "thaw",
    "SourceSemantics",
    "semantics_for",
    "DEFAULT_SEMANTICS",
    "CapabilityRequirement",
    "CapabilityCheck",
    "EvidenceFact",
    "EvidenceRequest",
    "EvidenceSnapshot",
    "EvidenceNeed",
    "build_snapshot",
    "snapshot_from_dict",
    "slice_snapshot",
    "check_need",
    "check_needs",
    "summarize_checks",
    "source_status",
    "coverage_summary",
    "derive_usable",
    "bar_payload_valid",
    "store",
]


# 惰性 I/O 出口（R3）：``import review_account.evidence`` / ``import bs_policy``
# 只加载纯模型/语义/取值（frozen/facts/snapshots/need/checks），**不**触发
# ``infra.assets.db`` / ``observations.write`` / ``store_adapter``。只有显式访问
# ``build_snapshot`` / ``snapshot_from_dict`` / ``store`` 时才加载只读仓储适配。
_LAZY_STORE_ADAPTER = frozenset({"build_snapshot", "snapshot_from_dict"})


def __getattr__(name: str):
    if name in _LAZY_STORE_ADAPTER:
        from . import store_adapter
        return getattr(store_adapter, name)
    if name == "store":
        from dragon_quant.infra.assets import observations as observations_store
        return observations_store
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
