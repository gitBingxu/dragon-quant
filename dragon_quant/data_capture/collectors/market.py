"""行情快照与板块成员采集器（离散观测，不是连续盘口）。"""

from __future__ import annotations

from .support import Collector, _finite, _market_of, _status
from ..types import (
    QUALITY_UNVERIFIED,
    CaptureRequest,
    CaptureResult,
    ObservationRecord,
)

class QuoteCollector(Collector):
    """关键时点的离散行情快照（不是连续盘口，也不是本单队列）。"""

    topic = "quote"

    def collect(self, request: CaptureRequest, *, provider,
                plan: dict) -> CaptureResult:
        observations: list[ObservationRecord] = []
        errors: list[dict] = []
        gaps: list[dict] = []
        try:
            quotes = provider.batch_get_quotes(list(request.scope))
        except Exception as exc:  # noqa: BLE001
            return CaptureResult(
                topic=self.topic, expected_scope=request.scope,
                status="failed",
                errors=({"reason": "source_unavailable",
                         "error": f"{type(exc).__name__}: {exc}"},),
            )
        by_code = {}
        for quote in quotes or ():
            code = getattr(quote, "code", None)
            if code:
                by_code[str(code)] = quote
        observed = []
        for code in request.scope:
            quote = by_code.get(code)
            if quote is None:
                gaps.append({"code": code, "reason": "no_data"})
                continue
            payload = {
                "price": _finite(getattr(quote, "price", None)),
                "prev_close": _finite(getattr(quote, "prev_close", None)),
                "open": _finite(getattr(quote, "open_px", None)),
                "high": _finite(getattr(quote, "high", None)),
                "low": _finite(getattr(quote, "low", None)),
                "volume": _finite(getattr(quote, "volume", None)),
                "amount": _finite(getattr(quote, "amount", None)),
                "pct": _finite(getattr(quote, "pct", None)),
            }
            observations.append(ObservationRecord(
                market=_market_of(code),
                code=code,
                obs_type=self.topic,
                payload=payload,
                units={"price": "yuan", "volume": "unverified",
                       "amount": "unverified", "pct": "percent"},
                quality=QUALITY_UNVERIFIED,
            ))
            observed.append(code)
        return CaptureResult(
            topic=self.topic,
            expected_scope=request.scope,
            observed_scope=tuple(observed),
            observations=tuple(observations),
            errors=tuple(errors),
            gaps=tuple(gaps),
            quality=QUALITY_UNVERIFIED,
            status=_status(len(request.scope), len(observed), len(errors)),
            notes=("discrete snapshots, not a continuous order book",),
        )


# ─── 板块/主题归属 ──────────────────────────────────────────────────────────


class MembershipCollector(Collector):
    """板块当前成员名单快照；变化时间只能定位到两次观测区间。"""

    topic = "sector_membership"

    def collect(self, request: CaptureRequest, *, provider,
                plan: dict) -> CaptureResult:
        observations: list[ObservationRecord] = []
        observed: list[str] = []
        errors: list[dict] = []
        gaps: list[dict] = []
        for sector_code in request.scope:
            try:
                members = provider.get_sector_components(
                    sector_code, page=1, all_pages=True)
            except Exception as exc:  # noqa: BLE001
                errors.append({"code": sector_code,
                               "reason": "theme_membership_missing",
                               "error": f"{type(exc).__name__}: {exc}"})
                continue
            codes = sorted({
                str(getattr(item, "code", "") or "")
                for item in members or ()
                if getattr(item, "code", None)
            })
            if not codes:
                gaps.append({"code": sector_code, "reason": "no_data"})
                continue
            observations.append(ObservationRecord(
                market="sector",
                code=sector_code,
                obs_type=self.topic,
                payload={"codes": codes,
                         "page_completeness": "unverified"},
                units={"membership": "code_list"},
                quality=QUALITY_UNVERIFIED,
            ))
            observed.append(sector_code)
        return CaptureResult(
            topic=self.topic,
            expected_scope=request.scope,
            observed_scope=tuple(observed),
            observations=tuple(observations),
            errors=tuple(errors),
            gaps=tuple(gaps),
            quality=QUALITY_UNVERIFIED,
            status=_status(len(request.scope), len(observed), len(errors)),
            notes=("current membership snapshots only; never backfilled to earlier dates",
                   "page completeness unverified"),
        )
