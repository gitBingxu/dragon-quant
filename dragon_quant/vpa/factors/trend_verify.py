"""
因子②：趋势验证（涨放量 / 调缩量 / 新高量同步）

文案依据（量价关系一）：
- 健康的上升趋势：上涨时量能逐步放大，调整时量能随之缩小，
  价格新高时量能同步创新高。
- 量能是趋势的「验证」，量价齐升说明趋势健康。

实现：统计回看窗口内涨日均量 vs 跌日均量比值（涨放量 > 1 偏多），
并检查最近一根价格新高日的量能是否 >= 均量。
"""

from __future__ import annotations

from dragon_quant.models.types import KBar
from dragon_quant.vpa.types import (
    FactorResult, SIGNAL_BULLISH, SIGNAL_BEARISH, SIGNAL_NEUTRAL,
)
from dragon_quant.vpa.factors.base import avg, clamp

NAME = "trend_verify"
TITLE = "趋势量价验证"


def factor(klines: list[KBar], ctx: dict) -> FactorResult:
    window = ctx.get("trend_window", 20)
    vol_window = ctx.get("vol_ma_window", 5)
    up_ratio_th = ctx.get("up_vol_ratio", 1.2)  # 涨日均量/跌日均量阈值

    bars = klines[-window:]
    up_vols = [b.volume for b in bars if b.pct > 0]
    down_vols = [b.volume for b in bars if b.pct < 0]

    up_avg = avg(up_vols)
    down_avg = avg(down_vols)
    ratio = up_avg / down_avg if down_avg > 0 else (2.0 if up_avg > 0 else 1.0)

    # 趋势方向：窗口内累计涨幅
    trend_up = bars[-1].close > bars[0].close

    # 最近量能 vs 均量
    recent_vol = klines[-1].volume
    ma_vol = avg([b.volume for b in klines[-vol_window:]])
    recent_vs_ma = recent_vol / ma_vol if ma_vol > 0 else 1.0

    details = {
        "trend_up": trend_up,
        "up_down_vol_ratio": round(ratio, 2),
        "recent_vol_vs_ma": round(recent_vs_ma, 2),
        "up_days": len(up_vols),
        "down_days": len(down_vols),
    }

    evidence = [
        f"近{window}日趋势方向: {'上升' if trend_up else '下降/横盘'}（首{bars[0].close:.2f} → 末{bars[-1].close:.2f}）",
        f"涨日 {len(up_vols)} 天均量 {up_avg/1e4:.0f}万 vs 跌日 {len(down_vols)} 天均量 {down_avg/1e4:.0f}万 → 涨跌量比 {ratio:.2f}（阈值 {up_ratio_th}）",
        f"最新量能 vs {vol_window}日均量 = {recent_vs_ma:.2f}x",
    ]

    # 涨日放量、跌日缩量 → 健康
    healthy = ratio >= up_ratio_th
    score = clamp(50.0 + (ratio - 1.0) * 40.0)

    if trend_up and healthy:
        evidence.append("上升趋势 + 涨放量/调缩量 → 量价齐升，趋势健康")
        return FactorResult(
            NAME, TITLE, SIGNAL_BULLISH, score,
            f"上升趋势中涨放量/调缩量（量比{ratio:.2f}），趋势健康", evidence, details,
        )
    if trend_up and not healthy:
        evidence.append("上涨但涨日未明显放量 → 缺乏量能配合，趋势存疑")
        return FactorResult(
            NAME, TITLE, SIGNAL_BEARISH, clamp(40.0),
            f"上涨但涨跌量比仅{ratio:.2f}，缺乏量能配合，趋势存疑", evidence, details,
        )
    if (not trend_up) and ratio < 1.0:
        evidence.append("下跌且跌日放量 → 下跌趋势有量验证（偏空）")
        return FactorResult(
            NAME, TITLE, SIGNAL_BEARISH, clamp(score),
            f"下跌且跌日放量（量比{ratio:.2f}），下跌趋势有量验证", evidence, details,
        )
    return FactorResult(
        NAME, TITLE, SIGNAL_NEUTRAL, clamp(score),
        f"趋势/量能信号不明确（量比{ratio:.2f}）", evidence, details,
    )
