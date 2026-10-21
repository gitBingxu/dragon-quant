from __future__ import annotations

from datetime import datetime, timedelta
import math
from typing import Optional
from zoneinfo import ZoneInfo

from dragon_quant.models.types import KBar
from dragon_quant.review_account.indicators import enrich_daily_klines
from dragon_quant.review_account.priority import (
    PREP_RECOMMENDED, auction_state, prep_state, ranking_mode,
)

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


_FALLBACK_REASONS = {
    "missing": "no_snapshot",
    "read_error": "snapshot_read_error",
    "calendar": "calendar",
}


def prep_snapshot_for(day: str, source: str,
                      calendar: list[str] | None) -> tuple[Optional[dict], Optional[str], Optional[str]]:
    """读取 day 的严格前一交易日 T-1 的 prep 快照。

    返回 ``(snapshot, snapshot_date, unavailable_reason)``：
    - ``snapshot_date`` 是 **返回值**（T-1），入参 ``day`` 是消费交易日 T；
    - 日历缺失/不足 → ``(None, None, "calendar")``；
    - 无行 → ``(None, None, "missing")``；读取异常 → ``(None, None, "read_error")``。

    ``collect_candidates_ex``、``load_market_sentiment``、``auction`` 池共用本函数，
    禁止调用方先算 dates[-1] 再传入（会读成 T-2）。
    """
    dates = candidate_dates(calendar or [], day, 1)
    if not dates:
        return None, None, "calendar"
    snapshot_date = dates[-1]
    try:
        from dragon_quant.storage import assets_db
        snap = assets_db.get_prep_snapshot(snapshot_date, source=source)
    except Exception:
        return None, None, "read_error"
    if not isinstance(snap, dict):
        return None, None, "missing"
    return snap, snapshot_date, None


def _prep_block(candidate: Optional[dict], source_date: Optional[str],
                state: Optional[bool]) -> dict:
    candidate = candidate if isinstance(candidate, dict) else {}
    return {
        "state": state,
        "source_date": source_date,
        "verdict": candidate.get("verdict"),
        "verdict_label": candidate.get("verdict_label"),
        "ignore_reason": candidate.get("ignore_reason"),
    }


def _auction_block(record: Optional[dict], source_date: str) -> dict:
    record = record if isinstance(record, dict) else {}
    return {
        "state": auction_state(record) if record else None,
        "loaded": bool(record),
        "source_date": source_date,
        "strength": record.get("strength"),
        "level": record.get("level"),
        "is_weak": record.get("is_weak"),
        "unverified": record.get("unverified"),
        "reject_reason": record.get("reject_reason"),
        "error": record.get("error"),
    }


def _annotate(candidate: dict, prep_block: dict, auction_block: dict) -> None:
    candidate["entry_priority"] = {
        "t": {"state": candidate.get("is_true_dragon")},
        "prep": prep_block,
        "auction": auction_block,
    }


def _default_auction_loader(day: str, source: str = "v2") -> list[dict]:
    from dragon_quant.storage import assets_db
    return assets_db.list_auction_snapshots(day, source=source)


def collect_candidates_ex(calendar: list[str], day: str, cfg, loader,
                          auction_loader=None) -> tuple[list[dict], dict]:
    """候选池 + P/A 三值注解 + 上下文（排序模式/回退原因/降级）。

    池语义（与 auction 采集池共用）：
    - 优先 T-1 prep 快照（strict T-1）；
    - 快照缺失/读取异常/无候选/无资格（min_score/真龙）→ legacy 3 日真龙；
    - 快照有候选但推荐集（strong/light）为空 → **D7 回退 legacy**，回退池不再应用
      ignore 出池；P 仍按快照 verdict 标注（ignore→False、无该 code→None）；
    - 旧快照完全不含 verdict → 使用快照候选、P 全 Unknown（C7）。
    """
    source = getattr(cfg, "source", "v2")
    min_score = getattr(cfg, "min_score", 50.0)
    ranking_on = bool(getattr(cfg, "auction_ranking_enabled", False))
    veto_on = bool(getattr(cfg, "auction_veto_enabled", False))
    ctx = {
        "ranking_mode": "legacy",
        "pool_source": "legacy",
        "fallback_reason": None,
        "pool_empty": False,
        "prep_snapshot_date": None,
        "prep_unavailable_reason": None,
        "auction_ranking_enabled": ranking_on,
        "auction_veto_enabled": veto_on,
        "auction_rows": 0,
        "auction_verified_rows": 0,
        "would_veto_count": 0,
        "filtered_ignore": 0,
        "recommended_count": 0,
        "ignore_reasons": [],
        "prep_degraded": False,
        "degraded": [],
    }

    snap = snapshot_date = reason = None
    if source == "v2":
        snap, snapshot_date, reason = prep_snapshot_for(day, source, calendar)
    ctx["prep_snapshot_date"] = snapshot_date
    ctx["prep_unavailable_reason"] = reason

    verdict_map: dict[str, dict] = {}
    pool: Optional[list[dict]] = None
    if isinstance(snap, dict):
        # 只要快照存在就记录降级状态（含 empty_snapshot 窄路径）。
        sentiment = snap.get("sentiment")
        if isinstance(sentiment, dict):
            ctx["prep_degraded"] = bool(
                sentiment.get("degraded") or sentiment.get("factor_errors"))
    if isinstance(snap, dict) and isinstance(snap.get("candidates"), list) and snap["candidates"]:
        raw = [c for c in snap["candidates"] if isinstance(c, dict) and c.get("code")]
        verdict_map = {c["code"]: c for c in raw}
        # 诊断字段先于 eligible 判定计算：no_eligible 的空池输出也要有原因样本。
        ctx["ignore_reasons"] = [
            c.get("ignore_reason") for c in raw if c.get("verdict") == "ignore"
        ]
        eligible = [c for c in raw
                    if c.get("is_true_dragon") is not False
                    and (c.get("composite_score") or 0) >= min_score]
        if not eligible:
            ctx["fallback_reason"] = "no_eligible"
        else:
            has_verdict = any("verdict" in c for c in eligible)
            recommended = [c for c in eligible if c.get("verdict") in PREP_RECOMMENDED]
            ctx["recommended_count"] = len(recommended)
            if not has_verdict:
                pool = eligible                       # 旧快照：P=Unknown，不过滤
            elif not getattr(cfg, "prep_verdict_filter_enabled", True):
                pool = eligible                       # 消融：复现旧候选池
            elif not recommended:
                ctx["fallback_reason"] = "all_ignored"  # D7：回退 legacy
            else:
                pool = [c for c in eligible if c.get("verdict") != "ignore"]
                ctx["filtered_ignore"] = len(eligible) - len(pool)
    elif isinstance(snap, dict):
        ctx["fallback_reason"] = "empty_snapshot"
    elif source == "v2":
        ctx["fallback_reason"] = _FALLBACK_REASONS.get(reason or "missing", "no_snapshot")

    if pool is None:
        pool = _legacy_candidates(calendar, day, cfg, loader)
        ctx["pool_source"] = "legacy"
        for cand in pool:
            snap_cand = verdict_map.get(cand.get("code"))
            state = prep_state(snap_cand) if snap_cand is not None else None
            _annotate(cand, _prep_block(snap_cand, snapshot_date, state),
                      _auction_block(None, day))
    else:
        ctx["pool_source"] = "prep"
        for cand in pool:
            _annotate(cand, _prep_block(cand, snapshot_date, prep_state(cand)),
                      _auction_block(None, day))

    # A 注解：任一开关开启才读取；两者皆关 → 零读取（AC8'）
    if source == "v2" and (ranking_on or veto_on):
        rows: list[dict] = []
        try:
            rows = (auction_loader or _default_auction_loader)(day, source="v2")
        except Exception as exc:
            ctx["degraded"].append(
                f"竞价快照读取失败（A 全中性）：{type(exc).__name__}: {exc}")
        row_map = {r.get("code"): r for r in (rows or [])
                   if isinstance(r, dict) and r.get("code")}
        ctx["auction_rows"] = len(row_map)
        ctx["auction_verified_rows"] = sum(
            1 for r in row_map.values() if auction_state(r) is not None)
        for cand in pool:
            block = _auction_block(row_map.get(cand.get("code")), day)
            cand.setdefault("entry_priority", {})["auction"] = block
            if block["state"] is False:
                ctx["would_veto_count"] += 1

    pool = sorted(pool, key=candidate_key)
    ctx["pool_empty"] = not pool
    ctx["ranking_mode"] = ranking_mode(
        prep_snapshot_date=snapshot_date,
        auction_rows=ctx["auction_rows"],
        auction_verified_rows=ctx["auction_verified_rows"],
    )
    return pool, ctx


def collect_candidates(calendar: list[str], day: str, cfg, loader) -> list[dict]:
    """候选池（兼容包装）：优先 T-1 盘后复盘快照；必要时回退旧 3 日真龙并集。"""
    return collect_candidates_ex(calendar, day, cfg, loader)[0]


def _legacy_candidates(calendar: list[str], day: str, cfg, loader) -> list[dict]:
    """旧逻辑：前 candidate_lookback_days 个交易日的全部真龙并集去重。"""
    lookback = getattr(cfg, "candidate_lookback_days", 3)
    min_score = getattr(cfg, "min_score", 50.0)
    source = getattr(cfg, "source", "v2")
    merged = {}
    for date in candidate_dates(calendar, day, lookback):
        for cand in loader(date, top_n=None, source=source):
            code = cand.get("code")
            if not code:
                continue
            if cand.get("is_true_dragon") is False or (cand.get("composite_score") or 0) < min_score:
                continue
            if code not in merged or candidate_key(cand) < candidate_key(merged[code]):
                merged[code] = cand
    return sorted(merged.values(), key=candidate_key)


def candidate_key(candidate: dict) -> tuple:
    """去重/展示序：综合分越高越优先，同分按 rank、代码稳定排序。"""
    return (-(candidate.get("composite_score") or 0), candidate.get("rank") or 999999,
            candidate.get("code", ""))


def load_market_sentiment(calendar: list[str], day: str, cfg) -> Optional[float]:
    """读 T-1 盘后复盘快照的情绪建议仓位（position_pct 标量）。

    快照是「T-1 盘后选、T 日买」，故消费 T-1 快照的 sentiment.position_pct。
    缺失/空/回退旧逻辑时返回 None（调用方降级为 sizing 1.0、禁买不生效）。
    """
    if cfg.source != "v2":
        return None
    snap, _, _ = prep_snapshot_for(day, cfg.source, calendar)
    sentiment = (snap or {}).get("sentiment") or {}
    pct = sentiment.get("position_pct")
    if pct is None:
        return None
    try:
        pct = float(pct)
    except (TypeError, ValueError):
        return None
    return max(0.0, min(1.0, pct))  # clamp 到 [0,1]


def validate_bars(bars: list[KBar], day: str, until: int | None = None) -> list[KBar]:
    expected = [ts for ts in bar_times(day) if until is None or ts <= until]
    selected = sorted((b for b in bars if event_date(b.timestamp) == day
                       and (until is None or b.timestamp <= until)), key=lambda b: b.timestamp)
    if [b.timestamp for b in selected] != expected:
        actual = {b.timestamp for b in selected}
        missing = [datetime.fromtimestamp(t / 1000, SHANGHAI).strftime("%H:%M") for t in expected if t not in actual]
        raise DataCoverageError(
            f"{day} 5分钟K不完整或时间戳不匹配: 需要{len(expected)}根，"
            f"得到{len(selected)}根；缺失{','.join(missing)}")
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
        "execution_price": execution_price, "execution_timestamp": timestamp,
        "bar_high": current.high if current else opening,
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
