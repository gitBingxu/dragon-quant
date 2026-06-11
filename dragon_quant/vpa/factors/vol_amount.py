"""
因子①：成交量 vs 成交额 灵敏度 / 新高背离

文案依据（量价关系一）：
- 量能分成「成交量」和「成交金额」，是硬币的两面。
- 价格低时成交量更灵敏；价格高时成交额更灵敏。
- 「新高必须有新量（成交量）」本质是错的——价格大涨后，
  应看成交额是否同步新高，而非成交量。

实现：当价格处于相对高位（区间涨幅大）时，以成交额新高作为健康标准；
若价格新高而成交额未新高 → 量价背离偏空；成交额同步新高 → 偏多。
"""

from __future__ import annotations

from dragon_quant.models.types import KBar
from dragon_quant.vpa.types import (
    FactorResult, SIGNAL_BULLISH, SIGNAL_BEARISH, SIGNAL_NEUTRAL,
)
from dragon_quant.vpa.factors.base import is_new_high, clamp

NAME = "vol_amount"
TITLE = "量额灵敏度"


def factor(klines: list[KBar], ctx: dict) -> FactorResult:
    lookback = ctx.get("new_high_lookback", 20)
    high_run_pct = ctx.get("high_run_pct", 30.0)  # 区间累计涨幅阈值，超过视为相对高位

    closes = [k.close for k in klines]
    volumes = [k.volume for k in klines]
    amounts = [k.amount for k in klines]

    window = klines[-lookback:]
    win_low = min(k.low for k in window)
    run_pct = (closes[-1] - win_low) / win_low * 100 if win_low > 0 else 0.0

    price_new_high = is_new_high(closes, lookback)
    vol_new_high = is_new_high(volumes, lookback)
    amount_new_high = is_new_high(amounts, lookback)

    # 高位时以成交额为准，否则成交量与成交额都可
    in_high_zone = run_pct >= high_run_pct
    key_metric = "成交额" if in_high_zone else "成交量/额"
    key_new_high = amount_new_high if in_high_zone else (vol_new_high or amount_new_high)

    details = {
        "run_pct": round(run_pct, 2),
        "in_high_zone": in_high_zone,
        "price_new_high": price_new_high,
        "vol_new_high": vol_new_high,
        "amount_new_high": amount_new_high,
        "key_metric": key_metric,
    }

    zone_label = "相对高位" if in_high_zone else "非高位"
    evidence = [
        f"近{lookback}日区间累计涨幅 {run_pct:.1f}%（阈值 {high_run_pct:.0f}%）→ 判定为{zone_label}",
        f"高位看成交额、低位看成交量；当前以「{key_metric}」为验证标准",
        f"价格新高: {'是' if price_new_high else '否'}｜成交量新高: {'是' if vol_new_high else '否'}｜成交额新高: {'是' if amount_new_high else '否'}",
    ]

    if not price_new_high:
        return FactorResult(
            NAME, TITLE, SIGNAL_NEUTRAL, 50.0,
            f"价格未创{lookback}日新高，量额验证暂不适用", evidence, details,
        )

    if key_new_high:
        score = clamp(70.0 + (10.0 if amount_new_high and vol_new_high else 0.0))
        evidence.append(f"价格新高且{key_metric}同步新高 → 量价确认（健康）")
        return FactorResult(
            NAME, TITLE, SIGNAL_BULLISH, score,
            f"价格新高且{key_metric}同步新高，量价确认健康", evidence, details,
        )

    # 价格新高但关键量能未新高 → 背离
    score = clamp(30.0 if in_high_zone else 40.0)
    evidence.append(f"价格新高但{key_metric}未同步 → 量价背离（高位尤其需警惕放量不足）")
    return FactorResult(
        NAME, TITLE, SIGNAL_BEARISH, score,
        f"价格新高但{key_metric}未同步（高位看额），量价背离需警惕", evidence, details,
    )
