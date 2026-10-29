"""因子评估用的纯 Python 统计工具（零 numpy / scipy / pandas）。

全部函数返回 ``None`` 表示「样本不足或退化」，**绝不返回 0 冒充有效结论**。
这些函数只做数学，不含任何 I/O 或业务口径，便于单测与复用。

口径：
  - ``rankdata`` 为 1-based 平均名次（并列取平均）；
  - ``spearman`` = 对名次做 Pearson；
  - ``newey_west_t`` 用 Bartlett 核，lag 由调用方按 ``horizon`` 派生；
  - ``block_bootstrap_t`` 按 block 标签整块重采样（固定种子，可复现）。
"""

from __future__ import annotations

import math
import random
from typing import Iterable, Optional, Sequence

DEFAULT_BOOTSTRAP_SEED = 20261022


def _finite(values: Iterable) -> list[float]:
    out: list[float] = []
    for value in values:
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(numeric):
            out.append(numeric)
    return out


def mean(values: Iterable) -> Optional[float]:
    vals = _finite(values)
    if not vals:
        return None
    return sum(vals) / len(vals)


def stdev(values: Iterable, *, ddof: int = 1) -> Optional[float]:
    vals = _finite(values)
    if len(vals) - ddof <= 0:
        return None
    centre = sum(vals) / len(vals)
    variance = sum((v - centre) ** 2 for v in vals) / (len(vals) - ddof)
    if variance < 0:
        return None
    return math.sqrt(variance) if variance > 0 else 0.0


def median(values: Iterable) -> Optional[float]:
    vals = sorted(_finite(values))
    if not vals:
        return None
    mid = len(vals) // 2
    if len(vals) % 2:
        return vals[mid]
    return (vals[mid - 1] + vals[mid]) / 2.0


def quantile(sorted_values: Sequence[float], q: float) -> Optional[float]:
    """线性插值分位（与 numpy 默认 ``linear`` 一致）；输入须升序。"""
    values = list(sorted_values)
    if not values:
        return None
    if q <= 0:
        return values[0]
    if q >= 1:
        return values[-1]
    position = (len(values) - 1) * q
    low = int(math.floor(position))
    high = int(math.ceil(position))
    if low == high:
        return values[low]
    weight = position - low
    return values[low] * (1 - weight) + values[high] * weight


def rankdata(values: Sequence[float]) -> list[float]:
    """1-based 平均名次（并列取平均）；O(n log n)。"""
    indexed = sorted(enumerate(values), key=lambda item: item[1])
    ranks = [0.0] * len(values)
    position = 0
    while position < len(indexed):
        end = position + 1
        while end < len(indexed) and indexed[end][1] == indexed[position][1]:
            end += 1
        # 1-based 名次区间 [position+1, end] 的平均
        average = (position + 1 + end) / 2.0
        for offset in range(position, end):
            ranks[indexed[offset][0]] = average
        position = end
    return ranks


def _paired(x: Sequence, y: Sequence) -> tuple[list[float], list[float]]:
    left: list[float] = []
    right: list[float] = []
    for a, b in zip(x, y):
        try:
            fa, fb = float(a), float(b)
        except (TypeError, ValueError):
            continue
        if math.isfinite(fa) and math.isfinite(fb):
            left.append(fa)
            right.append(fb)
    return left, right


def pearson(x: Sequence, y: Sequence) -> Optional[float]:
    left, right = _paired(x, y)
    n = len(left)
    if n < 2:
        return None
    mean_x = sum(left) / n
    mean_y = sum(right) / n
    cov = sum((a - mean_x) * (b - mean_y) for a, b in zip(left, right))
    var_x = sum((a - mean_x) ** 2 for a in left)
    var_y = sum((b - mean_y) ** 2 for b in right)
    denominator = math.sqrt(var_x * var_y)
    if denominator == 0:
        return None
    value = cov / denominator
    return max(-1.0, min(1.0, value))


def spearman(x: Sequence, y: Sequence) -> Optional[float]:
    left, right = _paired(x, y)
    if len(left) < 2:
        return None
    return pearson(rankdata(left), rankdata(right))


def kendall_tau_b(x: Sequence, y: Sequence) -> Optional[float]:
    """Kendall tau-b（处理并列）；O(n²)，截面规模（数十到数百）足够。"""
    left, right = _paired(x, y)
    n = len(left)
    if n < 2:
        return None
    concordant = discordant = ties_x = ties_y = 0
    for i in range(n - 1):
        for j in range(i + 1, n):
            dx = left[i] - left[j]
            dy = right[i] - right[j]
            if dx == 0 and dy == 0:
                ties_x += 1
                ties_y += 1
            elif dx == 0:
                ties_x += 1
            elif dy == 0:
                ties_y += 1
            elif (dx > 0) == (dy > 0):
                concordant += 1
            else:
                discordant += 1
    denominator = math.sqrt(
        (concordant + discordant + ties_x) * (concordant + discordant + ties_y))
    if denominator == 0:
        return None
    return (concordant - discordant) / denominator


def newey_west_t(series: Sequence[float], *, lag: int) -> Optional[float]:
    """Newey-West 修正 t（Bartlett 核）。lag=0 时退化为朴素 t（总体方差）。"""
    values = _finite(series)
    n = len(values)
    if n < 2:
        return None
    centre = sum(values) / n
    residuals = [value - centre for value in values]
    gamma0 = sum(r * r for r in residuals) / n
    variance = gamma0
    for offset in range(1, max(0, int(lag)) + 1):
        if offset >= n:
            break
        gamma = sum(residuals[t] * residuals[t - offset] for t in range(offset, n)) / n
        weight = 1.0 - offset / (lag + 1.0)
        variance += 2.0 * weight * gamma
    if variance <= 0:
        return None
    return centre / math.sqrt(variance / n)


def autocorr1(series: Sequence[float]) -> Optional[float]:
    values = _finite(series)
    if len(values) < 3:
        return None
    return pearson(values[:-1], values[1:])


def block_bootstrap_t(values: Sequence[float], blocks: Sequence,
                      *, iterations: int = 500,
                      seed: int = DEFAULT_BOOTSTRAP_SEED) -> Optional[float]:
    """按 block 整块重采样，返回 ``mean / std(bootstrap means)``。

    典型用途：1q 因子把「报告期」当 block，避免日频同值造成的伪独立。
    少于 2 个 block 或 std 为 0 → ``None``。
    """
    pairs = [(value, block) for value, block in zip(values, blocks)
             if block is not None]
    grouped: dict = {}
    for value, block in pairs:
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(numeric):
            continue
        grouped.setdefault(block, []).append(numeric)
    blocks_list = list(grouped.values())
    if len(blocks_list) < 2:
        return None
    observed = mean([v for group in blocks_list for v in group])
    if observed is None:
        return None
    rng = random.Random(seed)
    samples: list[float] = []
    for _ in range(max(2, int(iterations))):
        draw: list[float] = []
        for _ in range(len(blocks_list)):
            draw.extend(blocks_list[rng.randrange(len(blocks_list))])
        draw_mean = mean(draw)
        if draw_mean is not None:
            samples.append(draw_mean)
    error = stdev(samples, ddof=1)
    if not error:
        return None
    return observed / error


def wilson_interval(hits: int, n: int, *, z: float = 1.96) -> Optional[list[float]]:
    """Wilson 95% 描述性区间（``hits/n``）；``n<=0`` → ``None``。

    区间只用于描述命中率的抽样不确定性，不用于「是否达标」的决策；
    同日多只、相邻 horizon 重叠都会让区间偏窄（调用方须标注 caveat）。
    """
    n = int(n)
    if n <= 0:
        return None
    p = min(1.0, max(0.0, float(hits) / n))
    z_squared = z * z
    denominator = 1.0 + z_squared / n
    center = (p + z_squared / (2.0 * n)) / denominator
    half = (z * math.sqrt(p * (1.0 - p) / n + z_squared / (4.0 * n * n))
            / denominator)
    return [max(0.0, center - half), min(1.0, center + half)]


def percentile_rank(values: Sequence[float], value: float) -> Optional[float]:
    """``value`` 在 ``values`` 中的百分位（0-100，mid-rank 口径）。

    并列值按 0.5 个名次计，因此最大值在有限样本下略低于 100
    （n 个样本时为 ``100×(n-0.5)/n``）；这是展示口径，不用于排序。
    """
    vals = _finite(values)
    if not vals:
        return None
    rank = 0.0
    for item in vals:
        if item < value:
            rank += 1.0
        elif item == value:
            rank += 0.5
    return 100.0 * rank / len(vals)
