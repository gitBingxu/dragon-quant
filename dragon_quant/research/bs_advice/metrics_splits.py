"""固定参数时间分窗、label_end purge 与成熟度（纯函数）。

成熟度口径（main C2 验收）：

- 所有时点用**带时区 datetime** 比较；naive/非法 cutoff 不能当 None 放宽。
- 真实已实现闭合交易在「真实退出（closed_event）且信息收到」时成熟，**不再**
  叠加 scenario 未来期限；前向窗口标签必须显式冻结 ``label_end`` 与接收事实。
- date-only cutoff 明确转换为该研究日末（CST 23:59:59.999999）。
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

_CST = timezone(timedelta(hours=8))


def time_splits(trade_dates, *, train=0.6, valid=0.2, purge_days=None,
                horizon_days=None):
    """固定参数分窗；purge 按真实日历上的 label_end 是否落在窗内判定。"""
    days = sorted(set(str(day) for day in trade_dates if day))
    horizon = None if horizon_days is None else int(horizon_days)
    purge = (horizon if purge_days is None and horizon is not None
             else max(0, int(purge_days or 0)))
    if not days:
        return {"mode": "fixed_parameter_split", "windows": {},
                "purged_days": {}, "purge_days": purge,
                "purge_basis": ("label_end_calendar" if horizon is not None
                                else "explicit_days"),
                "training_performed": False}
    n = len(days)
    train_end = min(n, int(n * train))
    valid_end = min(n, train_end + int(n * valid))
    windows = {}
    ranges = {
        "train": (0, max(0, train_end - 1)) if train_end else None,
        "valid": ((train_end, valid_end - 1)
                  if valid_end > train_end else None),
        "test": ((valid_end, n - 1) if valid_end < n else None),
    }
    purged = {}
    for name, window in ranges.items():
        if window is None:
            windows[name] = []
            purged[name] = []
            continue
        low, high = window
        windows[name] = [days[low], days[high]]
        purged[name] = [days[index] for index in range(low, high + 1)
                        if index + purge <= high]
    return {"mode": "fixed_parameter_split", "windows": windows,
            "purged_days": purged, "purge_days": purge,
            "purge_basis": ("label_end_calendar" if horizon is not None
                            else "explicit_days"),
            "training_performed": False,
            "note": "fixed parameters; no training selection"}


def _moment(value):
    """把毫秒/ISO/datetime 转成带时区 datetime；naive/非法返回 None。"""
    if value is None or value == "" or isinstance(value, bool):
        return None
    if not isinstance(value, (int, float, str)) and hasattr(value, "tzinfo"):
        return value if value.tzinfo is not None else None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value) / 1000, tz=_CST)
        except (OverflowError, OSError, ValueError):
            return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _cutoff(value):
    """返回 (cutoff_datetime, error)。date-only 明确转到该日结束时刻。"""
    if value is None or value == "":
        return None, "cutoff_missing"
    if not isinstance(value, (int, float, str)) and hasattr(value, "tzinfo"):
        if value.tzinfo is None:
            return None, "cutoff_naive"
        return value, None
    text = str(value)
    try:
        if len(text) == 10:
            day = date.fromisoformat(text)
            return datetime.combine(day, time(23, 59, 59, 999999),
                                    tzinfo=_CST), None
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None, "cutoff_invalid"
    if parsed.tzinfo is None:
        return None, "cutoff_naive"
    return parsed, None


def _sample_id(item, index):
    return str(item.get("sample_id") or item.get("closed_trade_id")
               or f"sample:{index}")


def maturity_report(closed_trades, calendar, *, evaluation_cutoff=None,
                    horizon_days: int = 0, research_start=None,
                    execution_support=None):
    """按真实冻结时点判定每类标签成熟；返回稳定 sample_id 关联原数据。

    ``horizon_days`` 仅用于前向标签 ``label_end`` 的显式推导前提说明，不对已
    实现闭合交易追加模拟期限（``kind=realized``）。
    """
    del horizon_days  # 保留参数以兼容既有调用；realized 不叠加未来期限
    cutoff, cutoff_error = _cutoff(evaluation_cutoff)
    start = None
    if research_start:
        try:
            start = date.fromisoformat(str(research_start)[:10])
        except ValueError:
            start = None
    support = dict(execution_support or {})
    blockers: list[str] = []
    if cutoff_error:
        blockers.append(cutoff_error)
    if support and support.get("status") not in (None, "usable"):
        blockers.append(str(support.get("status")))
    matured = unmatured = preexisting = 0
    exclusions: dict[str, int] = {}
    details: list[dict] = []

    def reject(item, index, reason, **extra):
        nonlocal unmatured
        unmatured += 1
        exclusions[reason] = exclusions.get(reason, 0) + 1
        details.append({"sample_id": _sample_id(item, index),
                        "status": "unmatured", "reason": reason, **extra})

    for index, item in enumerate(closed_trades or ()):
        kind = str(item.get("kind") or "realized")
        closed_at = _moment(item.get("closed_event"))
        label_end = _moment(item.get("label_end"))
        received = _moment(item.get("received_at") or item.get("received"))
        available = _moment(item.get("available_at") or item.get("available"))
        if kind == "realized":
            if closed_at is None:
                reject(item, index, "closed_event_missing")
                continue
            if start is not None and closed_at.date() < start:
                preexisting += 1
                details.append({"sample_id": _sample_id(item, index),
                                "status": "preexisting",
                                "reason": "closed_before_research_window",
                                "closed_at": closed_at.isoformat()})
                continue
            end = max(value for value in
                      (closed_at, received, available) if value is not None)
        else:
            if label_end is None:
                reject(item, index, "label_end_missing")
                continue
            if received is None and available is None:
                reject(item, index, "receive_time_missing")
                continue
            end = max(value for value in
                      (label_end, received, available) if value is not None)
        if cutoff is None or cutoff_error:
            reject(item, index, cutoff_error or "cutoff_missing",
                   label_end=end.isoformat())
            continue
        if end > cutoff:
            reject(item, index, "after_cutoff", label_end=end.isoformat())
            continue
        matured += 1
        details.append({"sample_id": _sample_id(item, index),
                        "status": "matured", "kind": kind,
                        "label_end": end.isoformat(),
                        "closed_at": (closed_at.isoformat()
                                      if closed_at else None),
                        "received_at": (received.isoformat()
                                        if received else None),
                        "available_at": (available.isoformat()
                                         if available else None)})
    qualified = bool(not blockers and matured and not unmatured)
    return {
        "matured": matured, "unmatured": unmatured, "preexisting": preexisting,
        "sample": matured + unmatured + preexisting,
        "effect_sample": matured + unmatured,
        "matured_ids": [item["sample_id"] for item in details
                        if item["status"] == "matured"],
        "evaluation_cutoff": cutoff.isoformat() if cutoff else None,
        "cutoff_error": cutoff_error,
        "execution_support": support or None,
        "blockers": blockers,
        "exclusions": exclusions,
        "details": details,
        "status": "evaluated" if qualified else "insufficient_data",
    }
