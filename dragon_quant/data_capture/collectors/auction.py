"""竞价过程与竞价终态采集器（TDX）。"""

from __future__ import annotations

from .support import (
    Collector,
    _finite,
    _market_of,
    _source_event_time,
    _status,
)
from ..types import (
    QUALITY_UNVERIFIED,
    CaptureRequest,
    CaptureResult,
    ObservationRecord,
)

class AuctionPathCollector(Collector):
    """当日累计竞价序列（TDX），只在收到之后可用；末笔不是 09:25 终态。"""

    topic = "auction_path"

    def collect(self, request: CaptureRequest, *, provider,
                plan: dict) -> CaptureResult:
        observations: list[ObservationRecord] = []
        observed: list[str] = []
        errors: list[dict] = []
        gaps: list[dict] = []
        for code in request.scope:
            try:
                items = provider.get_auction(code)
            except Exception as exc:  # noqa: BLE001 - transport 失败要分流
                errors.append({"code": code, "reason": "source_unavailable",
                               "error": f"{type(exc).__name__}: {exc}"})
                continue
            if not items:
                gaps.append({"code": code, "reason": "no_data"})
                continue
            normalized = []
            times = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                moment = str(item.get("time") or "")
                normalized.append({
                    "time": moment,
                    "price": _finite(item.get("price")),
                    "matched": _finite(item.get("matched")),
                    "unmatched": _finite(item.get("unmatched")),
                })
                if moment:
                    times.append(moment)
            if not normalized:
                gaps.append({"code": code, "reason": "no_usable_rows"})
                continue
            source_time = (f"{request.trade_date}T{max(times)}"
                           if times else None)
            observations.append(ObservationRecord(
                market=_market_of(code),
                code=code,
                obs_type=self.topic,
                payload={"items": normalized},
                units={"price": "yuan", "matched": "unverified",
                       "unmatched": "unverified"},
                quality=QUALITY_UNVERIFIED,
                source_event_time=source_time,
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
            notes=("last tick is not the 09:25 final trade",
                   "matched/unmatched units unverified"),
        )


# ─── 竞价终态 ────────────────────────────────────────────────────────────────


class AuctionFinalCollector(Collector):
    """09:25 竞价终态的源报告值；与末笔虚拟匹配分开保存。"""

    topic = "auction_final"

    def collect(self, request: CaptureRequest, *, provider,
                plan: dict) -> CaptureResult:
        observations: list[ObservationRecord] = []
        observed: list[str] = []
        errors: list[dict] = []
        gaps: list[dict] = []
        for code in request.scope:
            try:
                snapshot = provider.get_auction_snapshot(code)
            except Exception as exc:  # noqa: BLE001
                errors.append({"code": code, "reason": "source_unavailable",
                               "error": f"{type(exc).__name__}: {exc}"})
                continue
            if not snapshot:
                gaps.append({"code": code, "reason": "no_data"})
                continue
            payload = {
                "price": _finite(snapshot.get("price")),
                "amount": _finite(snapshot.get("amount")),
                "matched": _finite(snapshot.get("matched")),
                "unmatched": _finite(snapshot.get("unmatched")),
                # 真实来源时点与终态来源：不在这里构造 09:25，也不把末笔虚拟
                # 匹配包装成交易所终态；证据层据 terminal_source 判定可用性。
                "source_time": (str(snapshot.get("time") or "") or None),
                "terminal_source": str(
                    snapshot.get("terminal_source") or "unverified"),
            }
            if all(payload[key] is None
                   for key in ("price", "amount", "matched", "unmatched")):
                gaps.append({"code": code, "reason": "no_usable_fields"})
                continue
            observations.append(ObservationRecord(
                market=_market_of(code),
                code=code,
                obs_type=self.topic,
                payload=payload,
                units={"price": "yuan", "amount": "yuan",
                       "matched": "unverified", "unmatched": "unverified"},
                quality=QUALITY_UNVERIFIED,
                source_event_time=_source_event_time(
                    request.trade_date, payload["source_time"]),
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
            notes=("source-reported final values; not independently verified",
                   "amount unit yuan; matched/unmatched units unverified"),
        )


# ─── 分钟 K ──────────────────────────────────────────────────────────────────
