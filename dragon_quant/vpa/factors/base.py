"""
量价因子共享工具与约定。

因子签名约定：

    def factor(klines: list[KBar], ctx: dict) -> FactorResult

- klines: 已按 timestamp 升序（旧→新）的日K线，长度由引擎保证 >= MIN_BARS
- ctx:    引擎传入的公共参数（均量窗口、回看窗口等），见 engine.DEFAULT_CTX
- 返回:    FactorResult，单因子异常由引擎捕获降级，无需在因子内吞异常
"""

from __future__ import annotations

from datetime import datetime

from dragon_quant.models.types import KBar


def ensure_asc(klines: list[KBar]) -> list[KBar]:
    """确保按 timestamp 升序（旧→新）。参照 scorers/anti_drop.py 的约定。"""
    if len(klines) <= 1:
        return klines
    prev_ts = klines[0].timestamp
    for b in klines[1:]:
        if b.timestamp < prev_ts:
            return sorted(klines, key=lambda x: x.timestamp)
        prev_ts = b.timestamp
    return klines


def to_date_str(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000).strftime("%Y-%m-%d")


def avg(values: list[float]) -> float:
    """安全均值，空列表返回 0。"""
    vals = [v for v in values if v is not None]
    return sum(vals) / len(vals) if vals else 0.0


def clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


def is_new_high(values: list[float], lookback: int) -> bool:
    """最后一个值是否为近 lookback 个值（含自身）内的最高。"""
    if not values:
        return False
    window = values[-lookback:] if lookback > 0 else values
    return values[-1] >= max(window)
