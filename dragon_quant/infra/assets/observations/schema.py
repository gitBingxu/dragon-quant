"""采集仓储的版本/表清单/错误（无 I/O）。"""

from __future__ import annotations

from typing import Optional

SCHEMA_VERSION = "1"

CAPTURE_TABLES = (
    "capture_plans",
    "capture_runs",
    "market_observations",
    "market_bar_versions",
)

READY = "ready"
UNINITIALIZED = "uninitialized"
MISSING_DATABASE = "missing_database"


class ObservationStoreError(RuntimeError):
    """仓储层拒绝写入或读取失败（形状/租约/并发/损坏）。"""

    reason = "store_error"

    def __init__(self, message: str, reason: Optional[str] = None) -> None:
        super().__init__(message)
        if reason:
            self.reason = reason


class StoreUninitialized(ObservationStoreError):
    """采集表尚不存在（合法首跑）；不是数据为空。"""

    reason = "store_uninitialized"


class CorruptEvidence(ObservationStoreError):
    """已存 JSON / schema 损坏：必须显式失败，不能当空结构。"""

    reason = "corrupt_evidence"


class LeaseLostError(ObservationStoreError):
    """fencing token / owner / 租约有效期不再有效：旧 worker 不得提交。"""

    reason = "lease_lost"


# ─── 规范化与哈希 ────────────────────────────────────────────────────────────
