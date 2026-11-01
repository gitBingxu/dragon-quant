"""竞价成效：本地日 K + 复用 ``factor decision-review`` 纯核心（方案 §3.5 / §5.4）。

边界：
  - 行情**只读** ``prep_assets.stock_daily``（经 ``load_bars`` 注入），不 import
    任何 provider、不触网；打开页面不会补行情；
  - 快照经 ``store`` 注入（``AuctionSnapshotStore`` 只读适配）；
  - 分组 / 方向 / 成本口径全部来自 ``run_decision_review``，本模块只做窗口裁剪
    与输出投影，不复制七步流水线，也不改任何阈值。
"""

from __future__ import annotations

from typing import Any, Callable, Mapping, Sequence

from dragon_quant.infra.factor.decision_review import (
    DEFAULT_COST_BPS,
    DEFAULT_HORIZONS,
    DEFAULT_MIN_DAYS,
    DEFAULT_MIN_GROUP_SAMPLES,
    build_adapter,
    run_decision_review,
)

from .local_market import LOCAL_CAVEATS, LocalStockDailyMarket, shift_day

WINDOW_DEFAULT = 20
WINDOW_MAX = 60
# 单日上限 60 只不适用于窗口并集；硬上限 300，超限按最近出现优先截断。
MAX_WINDOW_CODES = 300
BAR_LOOKBACK_DAYS = 120
BAR_LOOKAHEAD_DAYS = 30
SNAPSHOT_LOOKBACK_DAYS = 180
ALLOWED_HORIZONS = (1, 3, 5)


class _CachingWindowStore:
    """窗口内快照缓存：``run_decision_review`` 只加载窗口日期，避免重复解析。"""

    def __init__(self, store, dates: Sequence[str]) -> None:
        self._store = store
        self._dates = list(dates)
        self._cache: dict[str, Any] = {}

    def snapshot_dates(self, date_from: str, date_to: str) -> list[str]:
        return [day for day in self._dates if date_from <= day <= date_to]

    def load(self, day: str):
        if day not in self._cache:
            self._cache[day] = self._store.load(day)
        return self._cache[day]


def _horizon_groups(payload: Mapping[str, Any], horizon: int) -> list[dict]:
    groups: list[dict] = []
    for group in payload.get("groups") or []:
        summary = ((group.get("horizons") or {}).get(str(horizon)) or {})
        groups.append({
            "group": group.get("group"),
            "label": group.get("label"),
            "expected_direction": group.get("expected_direction"),
            "observations": int(summary.get("observations") or 0),
            "valid": int(summary.get("valid") or 0),
            "excluded": int(summary.get("excluded") or 0),
            "coverage": summary.get("coverage"),
            "direction_evaluable": bool(summary.get("direction_evaluable")),
            "direction_hit_rate": summary.get("direction_hit_rate"),
            "mean_net_return_pct": summary.get("mean_net_return_pct"),
            "median_net_return_pct": summary.get("median_net_return_pct"),
            "quality": summary.get("quality") or "not_evaluable",
            "above_reference": summary.get("above_reference"),
        })
    return groups


def _spread(payload: Mapping[str, Any], horizon: int):
    spread = ((payload.get("spreads") or {}).get(str(horizon)) or {})
    return spread.get("value")


def _selected_day(samples: Sequence[Mapping[str, Any]], trade_date: str) -> list[dict]:
    rows = [
        {
            "code": sample.get("code"),
            "horizon": sample.get("horizon"),
            "entry_date": sample.get("entry_date"),
            "exit_date": sample.get("exit_date"),
            "net_return_pct": sample.get("net_return_pct"),
            "outcome": sample.get("outcome"),
            "exclusion_reason": sample.get("exclusion_reason"),
        }
        for sample in samples
        if sample.get("snapshot_date") == trade_date
    ]
    rows.sort(key=lambda row: (str(row.get("code") or ""),
                               int(row.get("horizon") or 0)))
    return rows


def _empty_effect(*, trade_date: str, date_from: str, horizons: list[int],
                  reason: str, coverage: Mapping[str, Any],
                  warnings: list[str] | None = None) -> dict:
    return {
        "status": "insufficient_data",
        "reason": reason,
        "date_from": date_from,
        "date_to": trade_date,
        "data_source": "local_stock_daily",
        "calendar_basis": "bars_union",
        "cost_bps": float(DEFAULT_COST_BPS),
        "horizons": [
            {"horizon": horizon, "groups": [], "spread": None}
            for horizon in horizons
        ],
        "selected_day": [],
        "local_coverage": dict(coverage),
        "warnings": list(warnings or []),
        "caveats": list(LOCAL_CAVEATS),
    }


def _coverage(*, snapshot_dates: int, window_codes: int, loaded_codes: int,
              selected_codes: int, codes_with_bars: int, bar_dates: int,
              truncated: bool) -> dict:
    return {
        "snapshot_dates": snapshot_dates,
        "window_codes": window_codes,
        "loaded_codes": loaded_codes,
        "selected_codes": selected_codes,
        "codes_with_bars": codes_with_bars,
        "bar_dates": bar_dates,
        "truncated": truncated,
    }


def _window_codes(store: _CachingWindowStore, dates: Sequence[str],
                  ) -> dict[str, tuple[int, int]]:
    """code → (最近出现顺序, 出现次数)；用于 300 上限截断排序。"""

    seen: dict[str, tuple[int, int]] = {}
    for index, day in enumerate(dates):
        for row in store.load(day) or []:
            code = row.get("code") if isinstance(row, Mapping) else None
            if not code:
                continue
            order, count = seen.get(str(code), (index, 0))
            seen[str(code)] = (max(order, index), count + 1)
    return seen


def run_auction_effect(
    *,
    trade_date: str,
    store,
    load_bars: Callable[[list[str], str, str], Mapping[str, Any]],
    window: int = WINDOW_DEFAULT,
    horizons: Sequence[int] | None = None,
    min_days: int = DEFAULT_MIN_DAYS,
    min_group_samples: int = DEFAULT_MIN_GROUP_SAMPLES,
) -> dict:
    """构建 ``AuctionEffect`` 视图（窗口聚合 + 选中日逐股 + 本地覆盖）。"""

    window = max(1, min(WINDOW_MAX, int(window or WINDOW_DEFAULT)))
    requested = sorted({int(h) for h in (horizons or DEFAULT_HORIZONS)})
    lookback_start = shift_day(trade_date, -SNAPSHOT_LOOKBACK_DAYS)
    dates = sorted({str(day) for day in
                    store.snapshot_dates(lookback_start, trade_date)})[-window:]

    empty_coverage = _coverage(
        snapshot_dates=0, window_codes=0, loaded_codes=0,
        selected_codes=0, codes_with_bars=0, bar_dates=0, truncated=False)
    if not dates:
        return _empty_effect(trade_date=trade_date, date_from=lookback_start,
                             horizons=requested, reason="no_snapshot",
                             coverage=empty_coverage)

    window_store = _CachingWindowStore(store, dates)
    usage = _window_codes(window_store, dates)
    ordered_codes = sorted(
        usage, key=lambda code: (-usage[code][0], -usage[code][1], code))
    codes_truncated = len(ordered_codes) > MAX_WINDOW_CODES
    codes = ordered_codes[:MAX_WINDOW_CODES]

    selected_rows = (window_store.load(trade_date)
                     if trade_date in dates else [])
    selected_codes = sorted({str(row.get("code")) for row in selected_rows
                             if isinstance(row, Mapping) and row.get("code")})

    bar_from = shift_day(trade_date, -BAR_LOOKBACK_DAYS)
    bar_to = shift_day(trade_date, BAR_LOOKAHEAD_DAYS)
    loaded = load_bars(codes, bar_from, bar_to) or {}
    bars = dict(loaded.get("rows") or {})
    bars_truncated = bool(loaded.get("truncated"))
    bar_dates = list(loaded.get("bar_dates") or [])

    coverage = _coverage(
        snapshot_dates=len(dates),
        window_codes=len(ordered_codes),
        loaded_codes=len(bars),
        selected_codes=len(selected_codes),
        codes_with_bars=sum(1 for code in codes if bars.get(code)),
        bar_dates=len(bar_dates),
        truncated=codes_truncated or bars_truncated,
    )
    if not bars:
        return _empty_effect(trade_date=trade_date, date_from=dates[0],
                             horizons=requested, reason="local_bars_missing",
                             coverage=coverage)

    adapter = build_adapter("auction", window_store)
    payload = run_decision_review(
        adapter,
        market=LocalStockDailyMarket(bars),
        date_from=dates[0],
        date_to=trade_date,
        horizons=requested,
        include_samples=True,
        min_days=min_days,
        min_group_samples=min_group_samples,
    )

    status = payload.get("status") or "insufficient_data"
    reason = payload.get("reason")
    if status == "insufficient_data" and not reason:
        # 工具已给出有效结论（样本不足/低置信），不是错误。
        reason = "insufficient_samples"

    warnings = [str(item) for item in (payload.get("warnings") or [])]
    if codes_truncated:
        warnings.append(
            f"codes_truncated: 窗口代码并集 {len(ordered_codes)} 超过上限 "
            f"{MAX_WINDOW_CODES}，已按最近出现优先截断")
    if bars_truncated:
        warnings.append("bars_truncated: 本地日 K 超过 max_bars 上限，已整组截断")

    config = payload.get("config") or {}
    caveats = [str(item) for item in (payload.get("caveats") or [])]
    for item in LOCAL_CAVEATS:
        if item not in caveats:
            caveats.append(item)

    return {
        "status": status,
        "reason": reason,
        "date_from": payload.get("date_from") or dates[0],
        "date_to": payload.get("date_to") or trade_date,
        "data_source": "local_stock_daily",
        "calendar_basis": config.get("calendar_basis") or "bars_union",
        "cost_bps": float(config.get("cost_bps") or DEFAULT_COST_BPS),
        "horizons": [
            {
                "horizon": horizon,
                "groups": _horizon_groups(payload, horizon),
                "spread": _spread(payload, horizon),
            }
            for horizon in requested
        ],
        "selected_day": _selected_day(payload.get("samples") or [], trade_date),
        "local_coverage": coverage,
        "warnings": warnings,
        "caveats": caveats,
    }


__all__ = [
    "ALLOWED_HORIZONS",
    "LOCAL_CAVEATS",
    "MAX_WINDOW_CODES",
    "LocalStockDailyMarket",
    "run_auction_effect",
]
