"""冻结归属身份（纯叶子，不 import 策略运行时）。

完整身份参与 workspace/strategy/snapshot/params_hash/factor_versions：任一变化即
非同一归属（L4）。W2 场景、W3 账本、运行时与投影都复用这一窄公共函数，避免
"core → strategy" 的反向依赖，也避免各自复制一份口径。
"""

from __future__ import annotations

from typing import Mapping, Optional


def stable_order_id(*, event_time: int, code: str, action: str, plan_id: str,
                    obligation_id: str = "", sequence: int = 0,
                    batch_ids=(), owner=None) -> str:
    """纯公开的确定性订单 ID 工厂（W2/W3 共用；G3/G7 依赖解析只用这一份）。

    身份 = 事件时点 + 完整 owner + 计划/义务 + 方向 + 序号 + 批次 scope；同一
    输入永远同 ID，使依赖可先分配再解析，且不在父单同事件成交时提前解锁子单。
    """
    import hashlib

    scope = ",".join(sorted(str(item) for item in batch_ids or ()))
    key = (f"{int(event_time)}|{code}|{action}|{plan_id}|{obligation_id}|"
           f"{int(sequence)}|{scope}|{ref_identity(owner)}")
    return "ord-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]


def ref_identity(ref: Optional[Mapping]) -> tuple:
    """冻结身份（不含展示字段）；相同身份才允许复用活动运行时。"""
    value = dict(ref or {})
    versions = value.get("factor_versions") or {}
    version_key = tuple(sorted((str(k), str(v)) for k, v in versions.items()))
    return (value.get("workspace_id"), value.get("strategy_id"),
            value.get("snapshot_id"), value.get("params_hash"), version_key)
