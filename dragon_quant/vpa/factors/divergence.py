"""
因子④：上涨途中量价背离（缩量新高 = 动能衰竭）

文案依据（量价分析 案例4）：
- 上涨途中持续缩量、价格创新高而量能萎缩 = 量价背离。
- 一旦跌破上升趋势，高位资金做多动能衰竭，背离预警。

实现：在上升趋势中，比较「近段量能均值」与「前段量能均值」，
若价格走高但量能阶梯式萎缩 → bearish 背离预警。
"""

from __future__ import annotations

from dragon_quant.models.types import KBar
from dragon_quant.vpa.types import (
    FactorResult, SIGNAL_BULLISH, SIGNAL_BEARISH, SIGNAL_NEUTRAL,
)
from dragon_quant.vpa.factors.base import avg, clamp

NAME = "divergence"
TITLE = "量价背离"


def factor(klines: list[KBar], ctx: dict) -> FactorResult:
    seg = ctx.get("divergence_seg", 5)  # 前后段各取 seg 根

    if len(klines) < seg * 2:
        return FactorResult(
            NAME, TITLE, SIGNAL_NEUTRAL, 50.0, "数据不足以判断背离", [], {},
        )

    prev_seg = klines[-seg * 2:-seg]
    recent_seg = klines[-seg:]

    prev_price = avg([b.close for b in prev_seg])
    recent_price = avg([b.close for b in recent_seg])
    prev_vol = avg([b.volume for b in prev_seg])
    recent_vol = avg([b.volume for b in recent_seg])

    price_up = recent_price > prev_price
    vol_change = (recent_vol - prev_vol) / prev_vol if prev_vol > 0 else 0.0

    details = {
        "prev_price": round(prev_price, 3),
        "recent_price": round(recent_price, 3),
        "price_up": price_up,
        "vol_change_pct": round(vol_change * 100, 2),
    }

    evidence = [
        f"前{seg}日均价 {prev_price:.2f} → 近{seg}日均价 {recent_price:.2f}（价{'升' if price_up else '稳/降'}）",
        f"前{seg}日均量 {prev_vol/1e4:.0f}万 → 近{seg}日均量 {recent_vol/1e4:.0f}万（量{vol_change*100:+.0f}%）",
    ]

    # 价升量缩 → 背离
    if price_up and vol_change <= -0.2:
        score = clamp(40.0 + vol_change * 30.0)  # 缩量越多分越低
        evidence.append("价升量缩 → 上涨缺乏量能跟进，做多动能衰竭（背离预警）")
        return FactorResult(
            NAME, TITLE, SIGNAL_BEARISH, score,
            f"价升量缩（量能{vol_change*100:+.0f}%），做多动能衰竭背离预警", evidence, details,
        )
    if price_up and vol_change >= 0.0:
        evidence.append("价升量增 → 上涨有量能配合，无背离")
        return FactorResult(
            NAME, TITLE, SIGNAL_BULLISH, clamp(65.0),
            f"价升量增（量能{vol_change*100:+.0f}%），无背离", evidence, details,
        )
    return FactorResult(
        NAME, TITLE, SIGNAL_NEUTRAL, 50.0,
        f"无明显量价背离（价{'升' if price_up else '稳/降'}，量{vol_change*100:+.0f}%）", evidence, details,
    )
