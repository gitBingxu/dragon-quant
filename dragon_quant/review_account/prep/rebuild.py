"""prep dragons 窗口资产：镜像读取 + 窗口 payload 构造 + ``--recompute`` 本地重建。

- :func:`build_dragons_payload`：逐日窗口 + 血缘构造（普通采集与 recompute 重建共用）；
- :func:`_read_dragon_day` / :func:`_stale_dates`：读 ``assets.db.dragons`` 镜像并逐日
  对账（对账是**单向下限**：只能发现「源库有行而镜像缺行」，孤儿行靠
  ``assets backfill-dragons --dry-run``）；
- :func:`rebuild_dragons_for_recompute`：用本地镜像重建 5 日窗口，零采集零网络、
  不构造 provider（``--recompute`` 不触网是硬约束）。

旧 patch 目标 ``dragon_quant.review_account.prep.<name>`` 经 :func:`legacy` 在调用期
从包门面解析，因此包拆分后 monkeypatch 仍真实改变运行时行为。
"""

from __future__ import annotations

import sys
from typing import Any, Optional

from dragon_quant.runtime import clock
from dragon_quant.storage.dragons_mirror import BACKFILL_HINT

from .compute import SHANGHAI, _list_of_dicts

# 复盘窗口：含 T 在内的最近 N 个交易日（T-4 / … / T）
WINDOW_TRADING_DAYS = 5

# 目标日镜像缺行的短重试窗口（scan 主写已提交、镜像 fan-out 尚未写完的毫秒级并发）
MIRROR_RETRY_DELAY = 0.2
# 逐日对账错误前缀：区分「镜像丢数据」与「该日真的没有真龙」
MIRROR_STALE_PREFIX = "mirror_stale_day"

_MISSING = object()


def legacy(name: str) -> Any:
    """按名从 prep 包门面解析旧 patch 目标（调用期解析，不做静态别名）。

    包拆分前 ``dragon_quant.review_account.prep.<name>`` 就是模块属性，运维脚本与
    测试仍在 monkeypatch 它（provider 类、``run_prep`` 内部入口、``ASSET_COLLECTORS``、
    镜像读取函数等）。门面继续持有这些名字，子模块在调用期经本函数反查，因此 patch
    依旧改变运行时行为。门面缺失该名字时**抛错**：静默回退到静态全局会让 patch
    悄悄失效，那是拆分后最危险的退化。
    """
    package = sys.modules.get(__package__)
    if package is not None:
        value = getattr(package, name, _MISSING)
        if value is not _MISSING:
            return value
    raise AttributeError(
        f"{__package__}.{name} 缺失：旧 patch 目标必须由 prep 包门面导出")


def _stale_dates(dates: list) -> dict[str, int]:
    """逐日镜像对账（异常不外抛给载荷构造；由调用方决定严格度）。"""
    return legacy("dragons_mirror").stale_dates(dates)


def _read_dragon_day(day: str) -> list[dict]:
    """读镜像单日真龙（类型收敛）；读取异常由调用方逐日隔离。"""
    raw_rows = legacy("dragons_mirror").get_dragons(day)
    if not isinstance(raw_rows, list):
        raise TypeError(f"get_dragons 返回 {type(raw_rows).__name__}，预期 list")
    return _list_of_dicts(raw_rows)


def _is_mirror_stale_day(day: dict) -> bool:
    """该日是否因镜像缺行被标 error（而非真实无真龙）。"""
    error = day.get("error")
    return isinstance(error, str) and error.startswith(MIRROR_STALE_PREFIX)


def build_dragons_payload(window: list, window_days: int, *,
                          trade_date: str,
                          window_source: str = "calendar",
                          day_rows: Optional[dict] = None,
                          day_errors: Optional[dict] = None,
                          stale: Optional[dict] = None,
                          extra: Optional[dict] = None) -> dict:
    """逐日窗口 + 血缘构造 dragons 资产 payload（普通采集与 recompute 重建共用）。

    - ``day_rows`` / ``day_errors``：按日预读结果（缺省按空日处理）；
    - ``stale``：``{date: 源库行数}`` 的逐日对账结果，命中日强制改写为 ``error``
      （**不得**保留 ``empty``），使「数据丢失」与「真实空日」在快照/CLI 可区分；
    - ``window_source``：``calendar``（TDX 日历解析）或 ``local``（recompute 本地推断）；
    - ``extra``：只增审计键（如 recompute 的 ``rebuilt_at``）。
    """
    window = list(window or [])
    day_rows = day_rows if isinstance(day_rows, dict) else {}
    day_errors = day_errors if isinstance(day_errors, dict) else {}
    stale = stale if isinstance(stale, dict) else {}
    rows_all: list[dict] = []
    days: list[dict] = []
    for day in window:
        rows = _list_of_dicts(day_rows.get(day))
        error = day_errors.get(day)
        if day in stale:
            rows = []
            error = (f"{MIRROR_STALE_PREFIX}: 源库有行而镜像缺行"
                     f"（源库 {stale[day]} 行）；运行 {BACKFILL_HINT} 修复")
        scan_ids = sorted({r.get("scan_id") for r in rows if r.get("scan_id")})
        versions = sorted({r.get("version") for r in rows if r.get("version")})
        entry = {
            "trade_date": day,
            # 同日多次 scan（不同 topN）无法唯一归属 → null，不输出列表
            "scan_id": scan_ids[0] if len(scan_ids) == 1 else None,
            "version": versions[0] if len(versions) == 1 else None,
            "count": len(rows),
            "status": "error" if error else ("ok" if rows else "empty"),
        }
        if error:
            entry["error"] = error
        days.append(entry)
        for row in rows:
            item = dict(row)
            item["trade_date"] = day       # 保留真实入选日（供 compute 去重/血缘）
            rows_all.append(item)

    all_scan_ids = sorted({d["scan_id"] for d in days if d.get("scan_id")})
    all_versions = sorted({d["version"] for d in days if d.get("version")})
    single_day = len(window) == 1
    payload = {
        "trade_date": trade_date,
        # 顶层兼容键仅对单日窗口有值；多日窗口血缘一律看 days[]
        "scan_id": all_scan_ids[0] if single_day and len(all_scan_ids) == 1 else None,
        "version": all_versions[0] if single_day and len(all_versions) == 1 else None,
        "count": len(rows_all),
        "rows": rows_all,
        "window_days": window_days,
        "window_trade_dates": window,
        "window_source": window_source,
        "days": days,
    }
    if extra:
        payload.update(extra)
    return payload


# ─── recompute：从镜像本地重建窗口（零网络）──────────────────────────────────

def _local_window_dates(trade_date: str, stored_dragons) -> list[str]:
    """recompute 的本地窗口：存量窗口日期 ∪ 镜像中 ``<= T`` 的日期 → 最近 N 个。"""
    candidates: set[str] = set()
    if isinstance(stored_dragons, dict):
        raw = stored_dragons.get("window_trade_dates")
        for day in raw if isinstance(raw, list) else []:
            if isinstance(day, str) and day and day <= trade_date:
                candidates.add(day)
    try:
        mirror_dates = legacy("dragons_mirror").list_dragon_trade_dates()
    except Exception:
        mirror_dates = []
    for day in mirror_dates:
        if isinstance(day, str) and day and day <= trade_date:
            candidates.add(day)
    return sorted(candidates)[-WINDOW_TRADING_DAYS:]


def _stored_rows_by_day(stored: dict) -> dict[str, list]:
    """存量 dragons 资产 → ``{trade_date: rows}``（旧资产无行内日期时归到 payload 日期）。"""
    payload_date = stored.get("trade_date")
    out: dict[str, list] = {}
    for row in _list_of_dicts(stored.get("rows")):
        day = row.get("trade_date") or payload_date
        if isinstance(day, str) and day:
            out.setdefault(day, []).append(row)
    return out


def rebuild_dragons_for_recompute(trade_date: str, assets: dict) -> Optional[dict]:
    """recompute 用本地镜像重建 5 日窗口的 dragons 资产（零采集零网络）。

    仅当存量 dragons 资产带 ``days[]``（多日口径）时重建；旧单日资产返回
    ``None``，保持 R1 既有「按单日口径重放」语义。``window_days=1`` 的旧单日
    资产在普通 prep 下由 ``dragons_window_stale`` 触发本地重读自动升级。

    **不降级原则**：镜像缺某日但存量资产仍冻结着该日 rows 时，重放保留存量行
    （审计键 ``rebuilt_fallback_days``），绝不把可用资产改成缺行资产；当日
    既无镜像也无存量行时保留 stale 标记（error），不静默写成 ``empty``。
    """
    stored = (assets or {}).get("dragons")
    if not isinstance(stored, dict) or not _list_of_dicts(stored.get("days")):
        return None
    window = _local_window_dates(trade_date, stored)
    if not window:
        return None
    read_day = legacy("_read_dragon_day")
    stale_dates = legacy("_stale_dates")
    stored_by_day = _stored_rows_by_day(stored)
    stale = stale_dates(window)
    day_rows: dict[str, list] = {}
    day_errors: dict[str, str] = {}
    fallback_days: list[str] = []
    for day in window:
        try:
            rows = read_day(day)
        except Exception as e:  # 单日异常隔离：该日标 error，其余继续
            rows = []
            day_errors[day] = f"{type(e).__name__}: {e}"
        # 仅在「源库仍有行、镜像缺行」时用存量行兜底；源库确认为空的日期
        # 按真实 empty 输出，避免把合法清空误报成有数据。
        if not rows and day in stale:
            stored_rows = stored_by_day.get(day) or []
            if stored_rows:
                day_rows[day] = stored_rows
                day_errors.pop(day, None)
                fallback_days.append(day)
                stale.pop(day, None)     # 存量行兜底后不再算缺行
                continue
        day_rows[day] = rows

    extra = {
        "rebuilt_at": clock.now(SHANGHAI).strftime("%Y-%m-%d %H:%M:%S"),
        "rebuilt_from_window_days": stored.get("window_days"),
    }
    if fallback_days:
        extra["rebuilt_fallback_days"] = fallback_days
    return build_dragons_payload(
        window, WINDOW_TRADING_DAYS, trade_date=trade_date,
        window_source="local", day_rows=day_rows, day_errors=day_errors,
        stale=stale, extra=extra)


def _same_dragons_payload(old, new) -> bool:
    """重建内容是否与存量一致（``rebuilt_at`` 是审计时戳，不参与比较）。"""
    if not isinstance(old, dict) or not isinstance(new, dict):
        return False
    return ({k: v for k, v in old.items() if k != "rebuilt_at"}
            == {k: v for k, v in new.items() if k != "rebuilt_at"})
