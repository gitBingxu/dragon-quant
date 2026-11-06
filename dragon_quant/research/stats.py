"""研究统计纯核（无 I/O）：有限值、均值、日期级成组 bootstrap 区间。

Web ``presenters.accuracy`` 与 ``research.scan_quality`` 共用本模块，避免两套
统计口径漂移。
"""

from __future__ import annotations

import math
import random
from typing import Any, Sequence

BOOTSTRAP_SEED = 20261105
BOOTSTRAP_RESAMPLES = 1000
BOOTSTRAP_MIN_SAMPLES = 5


def finite_values(values: Sequence[Any]) -> list[float]:
    """只保留有限数值：``None`` / 非数值 / inf / nan 不是有效收益。"""
    result: list[float] = []
    for value in values:
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(number):
            result.append(number)
    return result


def mean(values: Sequence[float]) -> float | None:
    numbers = finite_values(values)
    return round(sum(numbers) / len(numbers), 4) if numbers else None


def bootstrap_interval(
        left: Sequence[tuple[str, float]],
        right: Sequence[tuple[str, float]], *,
        seed: int = BOOTSTRAP_SEED,
        resamples: int = BOOTSTRAP_RESAMPLES) -> dict:
    """**日期级成组**重抽样的组间均值差区间（固定 seed，可复现）。

    输入是 ``(日期, 数值)``：每次有放回抽取**日期**，再把该日全部样本一起纳入，
    保留同日样本相关性；不是对股票收益值做独立重抽样。样本/独立日不足不出区间。
    ``None`` / 非有限值不是有效收益，进入重采样前剔除（不当作 0）。
    """
    left = [(day, values[0]) for day, value in left
            if (values := finite_values([value]))]
    right = [(day, values[0]) for day, value in right
             if (values := finite_values([value]))]
    left_days = sorted({day for day, _ in left})
    right_days = sorted({day for day, _ in right})
    if (len(left) < BOOTSTRAP_MIN_SAMPLES
            or len(right) < BOOTSTRAP_MIN_SAMPLES
            or len(left_days) < 2 or len(right_days) < 2):
        return {"status": "insufficient_samples", "seed": seed,
                "resamples": resamples, "low": None, "high": None,
                "left_days": len(left_days), "right_days": len(right_days),
                "caveat": "独立日样本不足，不提供区间"}

    def by_day(items: Sequence[tuple[str, float]]) -> dict[str, list[float]]:
        grouped: dict[str, list[float]] = {}
        for day, value in items:
            grouped.setdefault(day, []).append(value)
        return grouped

    left_map, right_map = by_day(left), by_day(right)
    rng = random.Random(seed)
    diffs = []
    for _ in range(resamples):
        sampled_left = [rng.choice(left_days) for _ in left_days]
        sampled_right = [rng.choice(right_days) for _ in right_days]
        l_values = [v for day in sampled_left for v in left_map[day]]
        r_values = [v for day in sampled_right for v in right_map[day]]
        diffs.append(sum(l_values) / len(l_values)
                     - sum(r_values) / len(r_values))
    diffs.sort()
    low = diffs[int(0.025 * (len(diffs) - 1))]
    high = diffs[int(0.975 * (len(diffs) - 1))]
    return {"status": "ok", "seed": seed, "resamples": resamples,
            "low": round(low, 4), "high": round(high, 4),
            "left_days": len(left_days), "right_days": len(right_days),
            "method": "date_block_bootstrap",
            "caveat": "日期级重抽样的描述性区间，非独立样本显著性"}


__all__ = ["BOOTSTRAP_MIN_SAMPLES", "BOOTSTRAP_RESAMPLES", "BOOTSTRAP_SEED",
           "bootstrap_interval", "finite_values", "mean"]
