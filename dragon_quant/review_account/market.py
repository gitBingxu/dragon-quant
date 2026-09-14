from __future__ import annotations

from datetime import datetime, timedelta
import math
from zoneinfo import ZoneInfo

from dragon_quant.models.types import KBar
from dragon_quant.review_account.indicators import enrich_daily_klines

SHANGHAI = ZoneInfo("Asia/Shanghai")


class DataCoverageError(ValueError):
    pass


def at(day: str, clock: str) -> int:
    return int(datetime.fromisoformat(f"{day}T{clock}").replace(tzinfo=SHANGHAI).timestamp() * 1000)


def event_date(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp / 1000, SHANGHAI).strftime("%Y-%m-%d")


def bar_times(day: str) -> list[int]:
    return [at(day, start) + i * 300_000 for start, count in (("09:35", 24), ("13:05", 24))
            for i in range(count)]


def candidate_dates(calendar: list[str], day: str, count: int) -> list[str]:
    return sorted(d for d in calendar if d < day)[-count:]


def collect_candidates(calendar: list[str], day: str, cfg, loader) -> list[dict]:
    """前 candidate_lookback_days 个交易日的**全部真龙**并集去重。

    取每日 dragons 全量（真龙物化表），按 code 去重保留综合分更高者，并在共享层
    统一做真龙标记与综合分门槛过滤，确保 review-account 与 buy/sell 候选池一致。
    不再截断为前 N 只——买点择优交由 engine 按买点得分 + 五维分排序。
    """
    merged = {}
    for date in candidate_dates(calendar, day, cfg.candidate_lookback_days):
        for cand in loader(date, top_n=None, source=cfg.source):
            code = cand.get("code")
            if not code:
                continue
            if cand.get("is_true_dragon") is False or (cand.get("composite_score") or 0) < cfg.min_score:
                continue
            if code not in merged or candidate_key(cand) < candidate_key(merged[code]):
                merged[code] = cand
    return sorted(merged.values(), key=candidate_key)


def candidate_key(candidate: dict) -> tuple:
    """去重/展示序：综合分越高越优先，同分按 rank、代码稳定排序。"""
    return (-(candidate.get("composite_score") or 0), candidate.get("rank") or 999999,
            candidate.get("code", ""))


def validate_bars(bars: list[KBar], day: str, until: int | None = None) -> list[KBar]:
    expected = [ts for ts in bar_times(day) if until is None or ts <= until]
    selected = sorted((b for b in bars if event_date(b.timestamp) == day
                       and (until is None or b.timestamp <= until)), key=lambda b: b.timestamp)
    if [b.timestamp for b in selected] != expected:
        actual = {b.timestamp for b in selected}
        missing = [datetime.fromtimestamp(t / 1000, SHANGHAI).strftime("%H:%M") for t in expected if t not in actual]
        raise DataCoverageError(f"{day} 5分钟K不完整或时间戳不匹配: 需要{len(expected)}根，得到{len(selected)}根；缺失{','.join(missing)}")
    for b in selected:
        if not (all(math.isfinite(v) for v in (b.open, b.high, b.low, b.close, b.volume, b.amount))
                and 0 < b.low <= min(b.open, b.close) <= max(b.open, b.close) <= b.high
                and b.volume >= 0 and b.amount >= 0):
            raise DataCoverageError(f"{day} 5分钟K包含无效OHLC或量额")
    return selected


def _warmup_rows(history: list[KBar], day: str) -> list[dict]:
    """截至 day 之前的日K指标行；预热不足或含无效OHLC则报错。"""
    prior = [b for b in history if event_date(b.timestamp) < day]
    if any(not (all(math.isfinite(v) for v in (b.open, b.high, b.low, b.close, b.volume, b.amount))
                    and 0 < b.low <= min(b.open, b.close) <= max(b.open, b.close) <= b.high
                    and b.volume >= 0 and b.amount >= 0) for b in prior[-20:]):
        raise DataCoverageError(f"{day} 预热日K包含无效OHLC或量额")
    rows = enrich_daily_klines(prior)
    if len(rows) < 20:
        raise DataCoverageError(f"{day} 缺少20个交易日预热日K")
    return rows


def build_row(history: list[KBar], day: str, opening: float, bars: list[KBar],
              timestamp: int, *, execution_price: float | None = None,
              limit_up: float | None = None, limit_down: float | None = None) -> dict:
    if not math.isfinite(opening) or opening <= 0:
        raise DataCoverageError(f"{day} 无有效开盘价")
    prior = [b for b in history if event_date(b.timestamp) < day]
    rows = _warmup_rows(history, day)
    prev = rows[-1]
    known = [b for b in bars if b.timestamp <= timestamp]
    current = known[-1] if known else None
    close = current.close if current else opening
    daily = KBar(at(day, "15:00"), sum(b.volume for b in known), opening,
                 max([opening] + [b.high for b in known]), min([opening] + [b.low for b in known]),
                 close, close - prev["close"], (close / prev["close"] - 1) * 100, 0,
                 sum(b.amount for b in known))
    row = enrich_daily_klines(prior + [daily])[-1]
    row.update({
        "prev_row": prev, "hist_rows": rows, "intraday_bars": known,
        "limit_up": limit_up or round(prev["close"] * 1.1, 2),
        "limit_down": limit_down or round(prev["close"] * .9, 2),
        "execution_price": execution_price, "execution_timestamp": timestamp, "bar_high": current.high if current else opening,
        "bar_low": current.low if current else opening,
        "bar_close": close, "bar_timestamp": current.timestamp if current else at(day, "09:30"),
        "previous_ma5": rows[-2].get("ma5"), "observed_at": timestamp,
    })
    tr = [max(r["high"] - r["low"], abs(r["high"] - (r.get("prev_close") or r["close"])),
              abs(r["low"] - (r.get("prev_close") or r["close"]))) for r in rows[-14:]]
    row["atr"] = sum(tr) / len(tr)
    return row


def calendar_start(day: str, lookback: int = 3) -> str:
    return (datetime.fromisoformat(day) - timedelta(days=max(40, lookback * 4))).strftime("%Y-%m-%d")


def build_daily_row(history: list[KBar], day: str, phase: str, timestamp: int,
                    *, execution_price: float | None = None) -> dict:
    """无完整5分钟K时，用当日日K兜底构造 row（成交价保守近似）。

    仅两个可观测态：`open` 只暴露当日开盘价（bar_* 均为开盘价），`late`/`close`
    暴露当日全 OHLC。决策事件传 execution_price=None（不成交），紧随的 fill 事件
    传成交价与更晚的 timestamp，与盘中 signal→fill 分离一致。intraday_bars 恒为空，
    依赖分时的规则（分歧买龙、高开未封板窗口、同根K保本排序）在 evaluate_* 内自然
    跳过；买卖决策仍复用同一 evaluate_buy/evaluate_sell。
    """
    today = next((b for b in history if event_date(b.timestamp) == day), None)
    if today is None:
        raise DataCoverageError(f"{day} 缺少当日日K，无法兜底回测")
    if not (all(math.isfinite(v) for v in (today.open, today.high, today.low, today.close,
                                           today.volume, today.amount))
            and 0 < today.low <= min(today.open, today.close)
            and max(today.open, today.close) <= today.high):
        raise DataCoverageError(f"{day} 当日日K包含无效OHLC")
    rows = _warmup_rows(history, day)
    prev = rows[-1]
    row = enrich_daily_klines([b for b in history if event_date(b.timestamp) <= day])[-1]
    only_open = phase == "open"
    row.update({
        "phase": phase, "prev_row": prev, "hist_rows": rows, "intraday_bars": [],
        "limit_up": round(prev["close"] * 1.1, 2), "limit_down": round(prev["close"] * .9, 2),
        "execution_price": execution_price, "execution_timestamp": timestamp,
        "bar_high": today.open if only_open else today.high,
        "bar_low": today.open if only_open else today.low,
        "bar_close": today.open if only_open else today.close,
        "bar_timestamp": timestamp, "previous_ma5": rows[-2].get("ma5"),
        "observed_at": timestamp, "data_quality": "daily_fallback",
    })
    tr = [max(r["high"] - r["low"], abs(r["high"] - (r.get("prev_close") or r["close"])),
              abs(r["low"] - (r.get("prev_close") or r["close"]))) for r in rows[-14:]]
    row["atr"] = sum(tr) / len(tr)
    return row
