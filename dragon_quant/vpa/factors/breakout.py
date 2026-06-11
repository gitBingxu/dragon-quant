"""
因子③：突破关键位是否放量

文案依据（量价关系一）：
- 突破验证：突破震荡区上轨/前高时，看多者买入、看空者卖出，
  分歧导致放量，放量突破验证有效；缩量突破有效性存疑。

实现：取倒数第二根之前的区间高点（前高），若最新收盘突破该前高，
检查突破当日量能是否 >= 均量 * 放量倍数。
"""

from __future__ import annotations

from dragon_quant.models.types import KBar
from dragon_quant.vpa.types import (
    FactorResult, SIGNAL_BULLISH, SIGNAL_BEARISH, SIGNAL_NEUTRAL,
)
from dragon_quant.vpa.factors.base import avg, clamp

NAME = "breakout"
TITLE = "突破放量验证"


def factor(klines: list[KBar], ctx: dict) -> FactorResult:
    box_window = ctx.get("box_window", 20)
    vol_window = ctx.get("vol_ma_window", 5)
    vol_mult = ctx.get("breakout_vol_mult", 1.5)

    last = klines[-1]
    # 前高：最新一根之前的区间最高收盘
    prior = klines[-(box_window + 1):-1] if len(klines) > box_window else klines[:-1]
    if not prior:
        return FactorResult(
            NAME, TITLE, SIGNAL_NEUTRAL, 50.0, "数据不足以判断突破", [], {},
        )

    prior_high = max(b.close for b in prior)
    ma_vol = avg([b.volume for b in klines[-(vol_window + 1):-1]])
    vol_ratio = last.volume / ma_vol if ma_vol > 0 else 1.0
    breakout = last.close > prior_high

    details = {
        "prior_high": round(prior_high, 3),
        "last_close": round(last.close, 3),
        "breakout": breakout,
        "vol_ratio": round(vol_ratio, 2),
        "vol_mult_th": vol_mult,
    }

    evidence = [
        f"近{box_window}日前高(收盘) {prior_high:.2f}，最新收盘 {last.close:.2f} → {'已突破' if breakout else '未突破'}",
        f"突破日量能 vs {vol_window}日均量 = {vol_ratio:.2f}x（放量阈值 {vol_mult}x）",
    ]

    if not breakout:
        return FactorResult(
            NAME, TITLE, SIGNAL_NEUTRAL, 50.0,
            "未突破前高，突破验证暂不适用", evidence, details,
        )

    if vol_ratio >= vol_mult:
        score = clamp(70.0 + (vol_ratio - vol_mult) * 15.0)
        evidence.append("放量突破 → 看空者离场、看多者进场，分歧放大，突破有效")
        return FactorResult(
            NAME, TITLE, SIGNAL_BULLISH, score,
            f"放量突破前高（量比{vol_ratio:.2f}x），突破有效性强", evidence, details,
        )
    evidence.append("缩量突破 → 缺乏新增资金承接，突破有效性存疑")
    return FactorResult(
        NAME, TITLE, SIGNAL_BEARISH, clamp(40.0),
        f"突破前高但缩量（量比{vol_ratio:.2f}x），突破有效性存疑", evidence, details,
    )
