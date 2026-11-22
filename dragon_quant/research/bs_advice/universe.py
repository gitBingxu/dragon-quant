"""事前（ex-ante）候选母集装配（W4a/A1）。

只读复用既有资产：最近 5 个真实交易日 ``dragons_v2`` + 严格 T-1 ``prep`` 的
全部 ``review_candidates``（含 null/0/rejected 供分母）+ 持仓 + capture 计划中
仍跟踪的标的。保留来源引用、纳入/排除原因与缺口；**不按事后赢家重选**。

读取器可注入（测试/其它宿主），默认走公开只读入口；本模块不触网、不写库。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Callable, Iterable, Mapping, Optional

UNIVERSE_VERSION = "bs_universe/1"
DEFAULT_DRAGON_DAYS = 5
DEFAULT_CAP = 5000


@dataclass(frozen=True)
class UniverseItem:
    code: str
    name: str = ""
    rank: Optional[int] = None
    sources: tuple[str, ...] = ()
    source_refs: tuple[Mapping, ...] = ()
    eligible: bool = True
    eligibility_reasons: tuple[str, ...] = ()
    theme_ids: tuple[str, ...] = ()
    tracked: bool = False
    immature: bool = False

    def to_dict(self) -> dict:
        return {
            "code": self.code, "name": self.name, "rank": self.rank,
            "sources": list(self.sources),
            "source_refs": [dict(item) for item in self.source_refs],
            "eligible": bool(self.eligible),
            "eligibility_reasons": list(self.eligibility_reasons),
            "theme_ids": list(self.theme_ids),
            "tracked": bool(self.tracked), "immature": bool(self.immature),
        }


@dataclass(frozen=True)
class ExAnteUniverse:
    as_of: str
    trade_dates: tuple[str, ...] = ()
    items: tuple[UniverseItem, ...] = ()
    coverage: Mapping = field(default_factory=dict)
    gaps: tuple[Mapping, ...] = ()
    market_context: Mapping = field(default_factory=dict)
    market_meta: Mapping = field(default_factory=dict)
    version: str = UNIVERSE_VERSION

    @property
    def codes(self) -> tuple[str, ...]:
        return tuple(item.code for item in self.items)

    def item(self, code: str) -> Optional[UniverseItem]:
        return next((entry for entry in self.items if entry.code == code), None)

    def to_dict(self) -> dict:
        return {
            "schema_version": self.version, "as_of": self.as_of,
            "trade_dates": list(self.trade_dates),
            "items": [item.to_dict() for item in self.items],
            "coverage": dict(self.coverage),
            "gaps": [dict(item) for item in self.gaps],
            "market_context": dict(self.market_context or {}),
            "market_meta": dict(self.market_meta or {}),
        }


# ─── 默认只读读取器（可注入）────────────────────────────────────────────────


def _default_dragons(date_from: str, date_to: str) -> Optional[list[dict]]:
    """分页读取 dragons（不把单页 cap 当完整母集）。"""
    from dragon_quant.scan.storage import research_reads

    rows: list[dict] = []
    offset = 0
    page = 500
    while len(rows) < DEFAULT_CAP:
        batch = research_reads.dragons(date_from=date_from, date_to=date_to,
                                       limit=page, offset=offset)
        if batch is None:
            return None if not rows else rows
        rows.extend(batch)
        if len(batch) < page:
            break
        offset += page
    return rows


def _default_prep(before_date: str):
    from dragon_quant.infra.assets import db as assets_db

    return assets_db.get_previous_prep_snapshot(before_date, readonly=True)


def _default_calendar(as_of: str):
    from dragon_quant.infra.assets import observations

    try:
        groups, total = observations.bar_groups(
            None, as_of, periods=["daily"], limit=DEFAULT_CAP)
    except observations.ObservationStoreError:
        return [], False
    dates = sorted({row["trade_date"] for row in groups if row.get("trade_date")})
    return dates, bool(total > len(groups))


def _default_plan(trade_date: str):
    from dragon_quant.infra.assets import observations

    try:
        return observations.latest_plan(trade_date, strict=False)
    except observations.ObservationStoreError:
        return None


# ─── 组装 ───────────────────────────────────────────────────────────────────


def _prep_entry(item: Mapping, rank: int) -> UniverseItem:
    code = str(item.get("code") or "")
    eligibility = str(item.get("eligibility") or "")
    position = item.get("position_pct")
    position_ok = (isinstance(position, (int, float))
                   and not isinstance(position, bool) and position > 0)
    eligible = eligibility == "eligible" and position_ok
    reasons: list[str] = []
    if not eligible:
        reason = item.get("reason_code")
        if reason:
            reasons.append(str(reason))
        if not position_ok:
            reasons.append("prep_no_position")
        if eligibility != "eligible":
            reasons.append(f"prep_eligibility:{eligibility or 'unknown'}")
    mainline = item.get("mainline") if isinstance(item.get("mainline"), Mapping) else {}
    theme = mainline.get("board_name") if isinstance(mainline, Mapping) else None
    return UniverseItem(
        code=code, name=str(item.get("name") or ""), rank=rank,
        sources=("prep",), source_refs=({"source": "prep",
                                          "trade_date": item.get("trade_date"),
                                          "role": item.get("role")},),
        eligible=bool(eligible), eligibility_reasons=tuple(reasons),
        theme_ids=(str(theme),) if theme else ())


def _dragon_entry(item: Mapping, rank: int) -> UniverseItem:
    return UniverseItem(
        code=str(item.get("code") or ""), name=str(item.get("name") or ""),
        rank=rank, sources=("dragons",),
        source_refs=({"source": "dragons",
                      "trade_date": item.get("trade_date"),
                      "scan_id": item.get("scan_id"),
                      "is_true_dragon": item.get("is_true_dragon")},),
        eligible=True)


def assemble_universe(*, as_of: str, db_path=None,
                      dragons_reader: Optional[Callable] = None,
                      prep_reader: Optional[Callable] = None,
                      calendar_reader: Optional[Callable] = None,
                      plan_reader: Optional[Callable] = None,
                      holdings: Iterable[str] = (),
                      watch_codes: Iterable[str] = (),
                      dragon_days: int = DEFAULT_DRAGON_DAYS,
                      cap: int = DEFAULT_CAP) -> ExAnteUniverse:
    """按 ``as_of`` 组装事前母集；读取失败/缺口只记录，不伪装全覆盖。"""
    as_of_date = str(as_of)[:10]
    dragons_reader = dragons_reader or _default_dragons
    prep_reader = prep_reader or _default_prep
    calendar_reader = calendar_reader or _default_calendar
    plan_reader = plan_reader or _default_plan
    gaps: list[dict] = []
    calendar_result = calendar_reader(as_of_date)
    if isinstance(calendar_result, tuple):
        calendar, calendar_truncated = list(calendar_result[0] or []), bool(
            calendar_result[1])
    else:
        calendar, calendar_truncated = list(calendar_result or []), False
    if calendar_truncated:
        gaps.append({"reason": "calendar_truncated",
                     "detail": "本地日K分组超过读取上限；last5 可能不完整"})
    preloaded_dragons: Optional[list[dict]] = None
    if not calendar:
        # 无观测日 K 时，用 dragons 自身日期恢复真实交易日历（只取日期）。
        try:
            since = (date.fromisoformat(as_of_date)
                     - timedelta(days=30)).isoformat()
            preloaded_dragons = list(dragons_reader(since, as_of_date) or [])
        except Exception as exc:  # noqa: BLE001
            gaps.append({"reason": "dragons_calendar_unavailable",
                         "detail": f"{type(exc).__name__}: {exc}"})
            preloaded_dragons = []
        calendar = sorted({str(row.get("trade_date")) for row in
                           (preloaded_dragons or ())
                           if row.get("trade_date")})
    trade_dates = tuple(sorted(day for day in calendar if day < as_of_date
                               and len(day) == 10))[-dragon_days:]
    calendar_complete = (len(trade_dates) >= dragon_days
                         and not calendar_truncated)
    if not calendar_complete:
        gaps.append({"reason": "calendar_incomplete",
                     "detail": f"只有 {len(trade_dates)}/<{dragon_days} 个真实交易日"})
    if not trade_dates:
        gaps.append({"reason": "calendar_missing",
                     "detail": "本地无早于 as_of 的交易日历；母集可能不完整"})
    merged: dict[str, dict] = {}
    if trade_dates:
        try:
            if preloaded_dragons is not None:
                rows = [row for row in preloaded_dragons
                        if trade_dates[0] <= str(row.get("trade_date") or "")
                        <= trade_dates[-1]]
            else:
                rows = dragons_reader(trade_dates[0], trade_dates[-1]) or []
        except Exception as exc:  # noqa: BLE001 - 源不可用只记缺口
            rows = []
            gaps.append({"reason": "dragons_unavailable",
                         "detail": f"{type(exc).__name__}: {exc}"})
        for index, row in enumerate(rows):
            code = str(row.get("code") or "")
            if not code:
                continue
            entry = _dragon_entry(row, index + 1)
            prior = merged.get(code)
            if prior is None:
                merged[code] = {"item": entry, "refs": list(entry.source_refs)}
            else:
                merged[code]["refs"].extend(entry.source_refs)
    else:
        gaps.append({"reason": "dragons_skipped",
                     "detail": "无交易日历，未读取 dragons"})
    try:
        prep_payload, prep_meta = prep_reader(as_of_date)
    except Exception as exc:  # noqa: BLE001
        prep_payload, prep_meta = None, {"error": f"{type(exc).__name__}: {exc}"}
    prep_candidates = []
    market_context: dict = {}
    expected_prev = trade_dates[-1] if trade_dates else None
    if isinstance(prep_payload, Mapping):
        prep_candidates = list(prep_payload.get("review_candidates") or [])
        market_source = prep_payload.get("market")
        if not isinstance(market_source, Mapping):
            review = prep_payload.get("review")
            market_source = (review.get("market")
                             if isinstance(review, Mapping) else None)
        if isinstance(market_source, Mapping):
            market_context = dict(market_source)
    if prep_meta.get("error"):
        gaps.append({"reason": "prep_corrupt", "detail": str(prep_meta["error"])})
        prep_candidates = []
        market_context = {}
    elif prep_payload is not None and expected_prev \
            and prep_meta.get("trade_date") != expected_prev:
        # T-1 必须是真实前一交易日；更旧快照不能悄当 T-1。
        gaps.append({"reason": "prep_not_previous_trading_day",
                     "detail": f"prep={prep_meta.get('trade_date')} "
                               f"expected={expected_prev}"})
        prep_candidates = []
        market_context = {}
    elif prep_payload is None:
        gaps.append({"reason": "prep_snapshot_missing",
                     "detail": "无严格早于 as_of 的 prep 快照"})
    elif not prep_candidates:
        gaps.append({"reason": "prep_candidates_missing",
                     "detail": "T-1 prep 无 review_candidates"})
    for index, row in enumerate(prep_candidates):
        code = str(row.get("code") or "")
        if not code:
            continue
        entry = _prep_entry(row, index + 1)
        prior = merged.get(code)
        if prior is None:
            merged[code] = {"item": entry, "refs": list(entry.source_refs)}
        else:
            current = prior["item"]
            prior["item"] = UniverseItem(
                code=code, name=current.name or entry.name,
                rank=current.rank or entry.rank,
                sources=tuple(dict.fromkeys(current.sources + entry.sources)),
                source_refs=tuple(current.source_refs + entry.source_refs),
                eligible=current.eligible and entry.eligible,
                eligibility_reasons=tuple(dict.fromkeys(
                    current.eligibility_reasons + entry.eligibility_reasons)),
                theme_ids=tuple(dict.fromkeys(
                    current.theme_ids + entry.theme_ids)))
            prior["refs"].extend(entry.source_refs)
    plan = plan_reader(as_of_date)
    plan_codes: list[str] = []
    if isinstance(plan, Mapping):
        for member in plan.get("members") or []:
            if isinstance(member, Mapping) and member.get("code"):
                plan_codes.append(str(member["code"]))
    for code in list(holdings) + list(watch_codes) + plan_codes:
        code = str(code)
        if not code or code in merged:
            continue
        merged[code] = {"item": UniverseItem(
            code=code, sources=("account" if code in set(holdings) else "tracked",),
            source_refs=({"source": "account_or_tracked"},),
            tracked=True, immature=True), "refs": []}
    items = sorted((entry["item"] for entry in merged.values()),
                   key=lambda item: (item.rank is None, item.rank or 0, item.code))
    if len(items) >= cap:
        gaps.append({"reason": "universe_truncated", "detail": f"cap={cap}"})
        items = items[:cap]
    covered_dragons = sum(1 for item in items if "dragons" in item.sources)
    covered_prep = sum(1 for item in items if "prep" in item.sources)
    return ExAnteUniverse(
        as_of=as_of_date, trade_dates=trade_dates, items=tuple(items),
        coverage={
            "version": UNIVERSE_VERSION,
            "trade_dates": list(trade_dates),
            "calendar_complete": calendar_complete,
            "calendar_truncated": calendar_truncated,
            "dragon_rows": covered_dragons,
            "prep_items": covered_prep,
            "prep_candidates": len(prep_candidates),
            "holdings": len(tuple(holdings)),
            "tracked": len(plan_codes),
            "items": len(items),
            "eligible_items": sum(1 for item in items if item.eligible),
            "truncated": bool(len(merged) > cap),
            "denominator_note": ("全部 prep review_candidates（含 null/0/rejected）"
                                 "与 dragons 进入分母；不按结果重选"),
        },
        gaps=tuple(gaps), market_context=market_context,
        market_meta=dict(prep_meta or {}))


__all__ = [
    "UNIVERSE_VERSION", "UniverseItem", "ExAnteUniverse", "assemble_universe",
]
