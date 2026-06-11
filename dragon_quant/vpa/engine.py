"""
量价分析引擎。

拉取个股日K线 → 依次运行所有已注册因子 → 汇总综合量价健康度与信号。

定位：量价分析是「验证器」，输出量价健康度与偏多/中性/偏空观察，
不给出硬性买卖指令（贴合「量能是验证不是决策」的原则）。
"""

from __future__ import annotations

from dragon_quant.vpa.types import (
    VPAReport, FactorResult,
    SIGNAL_BULLISH, SIGNAL_BEARISH, SIGNAL_NEUTRAL,
)
from dragon_quant.vpa.factors import FACTORS
from dragon_quant.vpa.factors.base import ensure_asc, clamp

# 数据最少根数
MIN_BARS = 20

# 引擎传给每个因子的公共参数（阈值集中管理，便于调参）
DEFAULT_CTX = {
    "new_high_lookback": 20,
    "high_run_pct": 30.0,
    "trend_window": 20,
    "vol_ma_window": 5,
    "up_vol_ratio": 1.2,
    "box_window": 20,
    "breakout_vol_mult": 1.5,
    "divergence_seg": 5,
}


def analyze(code: str, source: str = "xueqiu", days: int = 60,
            ctx: dict | None = None) -> VPAReport:
    """对单只个股做量价分析。

    Args:
        code:   股票代码（如 "600519"）
        source: 数据源 xueqiu / tencent
        days:   拉取日K线根数
        ctx:    覆盖 DEFAULT_CTX 的参数

    Returns:
        VPAReport
    """
    from dragon_quant.data import get_kline

    run_ctx = dict(DEFAULT_CTX)
    if ctx:
        run_ctx.update(ctx)

    try:
        klines = get_kline(code, source=source, days=days)
    except Exception as e:
        return VPAReport(
            code=code, source=source, health_score=50.0, signal=SIGNAL_NEUTRAL,
            summary=f"K线拉取失败: {e}", fallback=True, reason=str(e),
        )

    if not klines or len(klines) < MIN_BARS:
        return VPAReport(
            code=code, source=source, health_score=50.0, signal=SIGNAL_NEUTRAL,
            summary=f"K线数据不足（{len(klines) if klines else 0} < {MIN_BARS}）",
            fallback=True, reason="insufficient_data",
        )

    klines = ensure_asc(klines)

    results: list[FactorResult] = []
    for fn in FACTORS:
        try:
            results.append(fn(klines, run_ctx))
        except Exception as e:
            results.append(FactorResult(
                name=getattr(fn, "__module__", "unknown").split(".")[-1],
                title="未知因子", signal=SIGNAL_NEUTRAL, score=50.0,
                note=f"因子执行异常: {e}", evidence=[f"异常: {e}"],
                details={"error": str(e)},
            ))

    health, signal, summary = _aggregate(results)
    return VPAReport(
        code=code, source=source, health_score=health, signal=signal,
        summary=summary, factors=results,
    )


def _aggregate(results: list[FactorResult]) -> tuple[float, str, str]:
    """汇总各因子 → 综合健康度 + 综合信号 + 一句话结论。"""
    if not results:
        return 50.0, SIGNAL_NEUTRAL, "无可用因子"

    health = clamp(sum(r.score for r in results) / len(results))

    bull = sum(1 for r in results if r.signal == SIGNAL_BULLISH)
    bear = sum(1 for r in results if r.signal == SIGNAL_BEARISH)

    if bull > bear:
        signal = SIGNAL_BULLISH
        label = "偏多观察"
    elif bear > bull:
        signal = SIGNAL_BEARISH
        label = "偏空观察"
    else:
        signal = SIGNAL_NEUTRAL
        label = "中性/观望"

    summary = f"{label}（健康度{health:.0f}，多{bull}/空{bear}）"
    return health, signal, summary
