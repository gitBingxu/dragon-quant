"""``dragons_v2`` 行 → 闸门中立的评测候选。

A 口径「一视同仁」：不套 prep 资格闸门 / 情绪闸门 / 组合风控。0.8 兼容视图
里唯一可买标记是 ``review_pool=True`` 且 ``review_eligible=True``；置位后
``evaluate_buy`` / ``_divergence_preconditions`` 的 ``review_not_eligible``、
旧身份（``is_true_dragon=False``）与 ``min_score`` 三道前置闸门全部不触发，
且**不伪造**任何分数。
"""

from __future__ import annotations

from typing import Mapping


def eval_candidate(row: Mapping) -> dict:
    """把 dragons_v2 行投影成 evaluate_buy 可接受的候选。"""

    return {
        "code": row["code"],
        "name": row.get("name") or "",
        "trade_date": row["trade_date"],
        "rank": row.get("rank"),
        "composite_score": row.get("composite_score"),
        "is_true_dragon": True,
        "review_pool": True,
        "review_eligible": True,
    }


__all__ = ["eval_candidate"]
