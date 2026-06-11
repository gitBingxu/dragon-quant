"""
量价因子注册表。

新增因子 = 实现一个 `factor(klines, ctx) -> FactorResult` 函数，
然后在此处的 FACTORS 列表中注册即可，无需改动引擎/CLI/review。
"""

from __future__ import annotations

from dragon_quant.vpa.factors import (
    vol_amount,
    trend_verify,
    breakout,
    divergence,
)

# 引擎按顺序执行的因子列表
FACTORS = [
    vol_amount.factor,
    trend_verify.factor,
    breakout.factor,
    divergence.factor,
]
