"""分钟/日 K 采集器（不复权、未结束 bar 不写）。"""

from __future__ import annotations

from datetime import timedelta

from .support import (
    Collector,
    _capture_semantics,
    _in_session,
    _iso,
    _market_of,
    _moment_from_ms,
    _non_negative,
    _parse_cutoff,
    _parse_moment,
    _status,
    _valid_ohlc,
)
from dragon_quant.infra.assets.observations import content_hash

from ..types import (
    QUALITY_UNVERIFIED,
    BarRecord,
    CaptureRequest,
    CaptureResult,
    ObservationRecord,
)

class MinuteBarCollector(Collector):
    """不复权 1 分 K；未结束 bar 不写，bar 右端语义未证明时显式标注。"""

    topic = "minute_bars"

    def collect(self, request: CaptureRequest, *, provider,
                plan: dict) -> CaptureResult:
        bars: list[BarRecord] = []
        observations: list[ObservationRecord] = []
        observed: list[str] = []
        errors: list[dict] = []
        gaps: list[dict] = []
        cutoff = _parse_cutoff(request.cutoff)
        if cutoff is None:
            return CaptureResult(
                topic=self.topic, expected_scope=request.scope,
                status="failed",
                errors=({"reason": "invalid_cutoff",
                         "message": f"cutoff 非法: {request.cutoff!r}"},),
            )
        for code in request.scope:
            try:
                raw = provider.get_minute_kline(code)
            except Exception as exc:  # noqa: BLE001
                errors.append({"code": code, "reason": "intraday_missing",
                               "error": f"{type(exc).__name__}: {exc}"})
                continue
            if not raw:
                gaps.append({"code": code, "reason": "no_data"})
                continue
            semantics = _capture_semantics(provider, "1min")
            converted: list[BarRecord] = []
            skipped_unfinished = 0
            invalid = 0
            outside = 0
            for bar in raw:
                end = _moment_from_ms(getattr(bar, "timestamp", None))
                if end is None:
                    invalid += 1
                    continue
                if end > cutoff:
                    skipped_unfinished += 1
                    continue
                start = end - timedelta(minutes=1)
                if not _in_session(end) or not _in_session(start):
                    outside += 1
                    continue
                values = _valid_ohlc(
                    getattr(bar, "open", None), getattr(bar, "high", None),
                    getattr(bar, "low", None), getattr(bar, "close", None))
                if values is None:
                    invalid += 1
                    continue
                open_px, high_px, low_px, close_px = values
                converted.append(BarRecord(
                    market=_market_of(code),
                    code=code,
                    period="1min",
                    interval_start=_iso(start),
                    interval_end=_iso(end),
                    open=open_px,
                    high=high_px,
                    low=low_px,
                    close=close_px,
                    volume=_non_negative(getattr(bar, "volume", None)),
                    amount=_non_negative(getattr(bar, "amount", None)),
                    completeness="final",
                    time_semantics=semantics["time_semantics"],
                    unit_semantics=semantics["unit_semantics"],
                    calibration_id=semantics.get("calibration_id", ""),
                ))
            if invalid:
                gaps.append({"code": code, "reason": "invalid_ohlc",
                             "count": invalid})
            if outside:
                gaps.append({"code": code, "reason": "outside_session",
                             "count": outside})
            if not converted:
                gaps.append({"code": code, "reason": "no_finished_bars",
                             "skipped_unfinished": skipped_unfinished})
                continue
            bars.extend(converted)
            digest = content_hash([[bar.interval_end, bar.close, bar.volume]
                                   for bar in converted])
            observations.append(ObservationRecord(
                market=_market_of(code),
                code=code,
                obs_type="minute_bars_series",
                payload={
                    "bar_count": len(converted),
                    "first_interval_end": converted[0].interval_end,
                    "last_interval_end": converted[-1].interval_end,
                    "series_hash": digest,
                    "bar_time_semantics": semantics["time_semantics"],
                    "bar_time_basis": "right_edge",
                    "adjustment": "none",
                    "calibration_id": semantics.get("calibration_id", ""),
                },
                units={"price": "yuan",
                       "volume": ("share" if semantics["unit_semantics"]
                                  == "verified" else "unverified"),
                       "amount": ("yuan" if semantics["unit_semantics"]
                                  == "verified" else "unverified")},
                quality=QUALITY_UNVERIFIED,
                valid_until=None,
            ))
            observed.append(code)
        return CaptureResult(
            topic=self.topic,
            expected_scope=request.scope,
            observed_scope=tuple(observed),
            observations=tuple(observations),
            bars=tuple(bars),
            errors=tuple(errors),
            gaps=tuple(gaps),
            quality=QUALITY_UNVERIFIED,
            status=_status(len(request.scope), len(observed), len(errors)),
            notes=("unadjusted 1min bars; bar right-edge semantics unverified",
                   "unfinished bars (interval_end > cutoff) are not stored"),
        )


# ─── 日 K ────────────────────────────────────────────────────────────────────


class DailyBarCollector(Collector):
    """不复权日 K；provider 只提供前复权时显式留缺口，绝不冒充执行价证据。"""

    topic = "daily_bars"

    def collect(self, request: CaptureRequest, *, provider,
                plan: dict) -> CaptureResult:
        config = plan.get("task_config") or {}
        days = int(config.get("daily_days") or 20)
        bars: list[BarRecord] = []
        observed: list[str] = []
        errors: list[dict] = []
        gaps: list[dict] = []
        semantics = _capture_semantics(provider, "daily")
        for code in request.scope:
            fetch = getattr(provider, "get_daily_kline_unadjusted", None)
            if fetch is None:
                gaps.append({"code": code,
                             "reason": "unadjusted_daily_unavailable"})
                continue
            try:
                raw = fetch(code, days)
            except NotImplementedError:
                gaps.append({"code": code,
                             "reason": "unadjusted_daily_unavailable"})
                continue
            except Exception as exc:  # noqa: BLE001
                errors.append({"code": code, "reason": "source_unavailable",
                               "error": f"{type(exc).__name__}: {exc}"})
                continue
            if not raw:
                gaps.append({"code": code, "reason": "no_data"})
                continue
            cutoff_moment = _parse_moment(request.cutoff)
            if cutoff_moment is None:
                gaps.append({"code": code, "reason": "invalid_cutoff"})
                continue
            cutoff_minutes = cutoff_moment.hour * 60 + cutoff_moment.minute
            usable = 0
            invalid = 0
            unfinished = 0
            weekend = 0
            for bar in raw:
                moment = _moment_from_ms(getattr(bar, "timestamp", None))
                if moment is None:
                    invalid += 1
                    continue
                day = moment.date()
                if day.weekday() >= 5:
                    weekend += 1
                    continue
                # 当日未收盘的日 K 不是 final；绝不把盘中日 K 当完整证据。
                if day >= cutoff_moment.date():
                    if not (day == cutoff_moment.date()
                            and cutoff_minutes >= 15 * 60):
                        unfinished += 1
                        continue
                values = _valid_ohlc(
                    getattr(bar, "open", None), getattr(bar, "high", None),
                    getattr(bar, "low", None), getattr(bar, "close", None))
                if values is None:
                    invalid += 1
                    continue
                open_px, high_px, low_px, close_px = values
                day_text = day.isoformat()
                bars.append(BarRecord(
                    market=_market_of(code),
                    code=code,
                    period="daily",
                    interval_start=f"{day_text}T09:30:00+08:00",
                    interval_end=f"{day_text}T15:00:00+08:00",
                    open=open_px,
                    high=high_px,
                    low=low_px,
                    close=close_px,
                    volume=_non_negative(getattr(bar, "volume", None)),
                    amount=_non_negative(getattr(bar, "amount", None)),
                    completeness="final",
                    time_semantics="verified",
                    unit_semantics=semantics["unit_semantics"],
                    calibration_id=semantics.get("calibration_id", ""),
                ))
                usable += 1
            if invalid:
                gaps.append({"code": code, "reason": "invalid_ohlc",
                             "count": invalid})
            if unfinished:
                gaps.append({"code": code, "reason": "unfinished_daily_bar",
                             "count": unfinished})
            if weekend:
                gaps.append({"code": code, "reason": "weekend_bar",
                             "count": weekend})
            if not usable:
                gaps.append({"code": code, "reason": "no_finished_bars"})
                continue
            observed.append(code)
        return CaptureResult(
            topic=self.topic,
            expected_scope=request.scope,
            observed_scope=tuple(observed),
            bars=tuple(bars),
            errors=tuple(errors),
            gaps=tuple(gaps),
            quality=QUALITY_UNVERIFIED,
            status=_status(len(request.scope), len(observed), len(errors)),
            notes=("unadjusted daily bars only; QFQ is never used as execution evidence",
                   "volume/amount units unverified"),
        )


# ─── 行情快照 ────────────────────────────────────────────────────────────────
