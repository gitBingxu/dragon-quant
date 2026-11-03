"""``assets backfill-stock-daily``：近 N 日计划候选的本地日 K 回填。

模板方法四段式（检测 → 取数 → 合并 → 写入），单 code 失败隔离在取数段：

1. **检测**：``list_recent_plan_candidate_codes`` 取近 N 个快照日的候选并集，
   与本地 ``stock_daily`` 的 last bar 日期比对，得到 missing / stale；
2. **取数**：``tdx.batch_get_daily_klines``（``--dry-run`` 跳过，不构造 provider）；
3. **合并**：保留目标行既有 rows，新采 code 覆盖，未涉及 code 原样保留；
4. **写入**：只写「执行日」或「本地最新 bar 日」的资产行，绝不回写更早历史日。

新建行标 ``partial=True`` 并由 ``asset_health`` 识别（下次 prep 重新采集），既有行的
``requested_codes`` / ``coverage`` / ``review_health`` 一律不动，只追加 backfill 审计块。

依赖方向：``infra/assets`` 内部叶子（db + stock_daily + clock），不 import
``review_account`` / ``infra.factor``，零 pandas。
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

from dragon_quant.infra import clock
from dragon_quant.infra.assets import db as assets_db
from dragon_quant.infra.assets.stock_daily import (
    PLAN_CANDIDATE_MAX_BARS,
    POOL_MAX_BARS,
    STOCK_DAILY_DAYS,
    convert_daily_bars,
    merge_daily_rows,
)

COMMAND = "assets backfill-stock-daily"
SHANGHAI = ZoneInfo("Asia/Shanghai")

#: 检测窗口上限：``stock_daily_last_bar_dates`` 只扫最近 60 个资产日。
MAX_LOOKBACK_DAYS = 60
#: 逐 code 状态体检的扫描上限（资产日）。
MAX_ASSET_DAYS = 60

_PREFIXES = ("SH", "SZ", "BJ")


def normalize_codes(raw) -> list[str]:
    """``--codes`` 规范化：去空白 / EXCHANGE 前缀 / 6 位数字校验 / 去重保序。"""

    if raw is None:
        return []
    tokens = raw.split(",") if isinstance(raw, str) else list(raw)
    codes: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        if token is None:
            continue
        text = str(token).strip().upper()
        for prefix in _PREFIXES:
            if text.startswith(prefix):
                text = text[len(prefix):]
                break
        if not text:
            continue
        if not (len(text) == 6 and text.isdigit()):
            raise ValueError(f"--codes 含非法代码: {token!r}（需 6 位数字）")
        if text not in seen:
            seen.add(text)
            codes.append(text)
    if not codes:
        raise ValueError("--codes 未给出任何有效代码")
    return codes


def _failure(reason: str, kind: str, *, dry_run: bool = False) -> dict:
    return {
        "ok": False,
        "command": COMMAND,
        "dry_run": dry_run,
        "target_date": None,
        "latest_bar_date": None,
        "lookback_days": None,
        "detected": [],
        "missing": [],
        "stale": [],
        "fetched": [],
        "updated": [],
        "skipped": [],
        "failed": [{"code": None, "reason": reason, "kind": kind}],
        "warnings": [],
        "window": {"trade_dates": [], "codes": []},
    }


def _read_existing(trade_date: str) -> tuple[Optional[dict], Optional[str]]:
    """读取目标行；损坏 JSON 按不存在处理并返回错误摘要（不落盘原文）。"""

    try:
        payload = assets_db.get_prep_asset(trade_date, "stock_daily")
    except (ValueError, TypeError) as exc:
        return None, f"{type(exc).__name__}: {exc}"
    if payload is not None and not isinstance(payload, dict):
        return None, f"payload 非 dict: {type(payload).__name__}"
    return payload, None


def _latest_bar_date(asset_date: Optional[str]) -> Optional[str]:
    """最新资产行 payload 内的最大 bar 日期（单份 payload，O(1) 口径）。"""

    if not asset_date:
        return None
    payload, _error = _read_existing(asset_date)
    rows = payload.get("rows") if isinstance(payload, dict) else None
    if not isinstance(rows, dict):
        return None
    dates = [
        str(bar.get("date") or "")
        for bars in rows.values() if isinstance(bars, list)
        for bar in bars if isinstance(bar, dict)
    ]
    dates = [day for day in dates if day]
    return max(dates) if dates else None


def _target_date(today: str, latest_asset_date: Optional[str],
                 latest_bar_date: Optional[str]) -> str:
    """执行日已有行 → 执行日；工作日 → 执行日；周末 → 本地最新 bar 日。"""

    if latest_asset_date == today:
        return today
    if datetime.strptime(today, "%Y-%m-%d").weekday() < 5:
        return today
    return latest_bar_date or today


def _payload(
    *,
    ok: bool,
    dry_run: bool,
    target_date: Optional[str],
    latest_bar_date: Optional[str],
    lookback_days: int,
    detected: list[str],
    missing: list[str],
    stale: list[str],
    fetched: list[str],
    updated: list[str],
    skipped: list[str],
    failed: list[dict],
    warnings: list[str],
    window: dict,
) -> dict:
    """固定键序的 JSON 契约（§6.4）。"""

    return {
        "ok": ok,
        "command": COMMAND,
        "dry_run": dry_run,
        "target_date": target_date,
        "latest_bar_date": latest_bar_date,
        "lookback_days": lookback_days,
        "detected": detected,
        "missing": missing,
        "stale": stale,
        "fetched": fetched,
        "updated": updated,
        "skipped": skipped,
        "failed": failed,
        "warnings": warnings,
        "window": window,
    }


def backfill_stock_daily(args, services) -> dict:
    """执行一次回填；返回值即输出的 JSON 载荷（退出码由 cli_service 映射）。"""

    dry_run = bool(getattr(args, "dry_run", False))
    days = getattr(args, "days", None)
    days = assets_db.PLAN_CANDIDATE_LOOKBACK_DAYS if days is None else int(days)
    if days < 1 or days > MAX_LOOKBACK_DAYS:
        return _failure(f"--days 必须在 1..{MAX_LOOKBACK_DAYS} 之间（收到 {days}）",
                        "args", dry_run=dry_run)

    conn = assets_db._connect(create=False)
    if conn is None:
        return _failure("assets.db 不存在；先运行 prep 生成资产或检查 DQ_DATA_DIR",
                        "env", dry_run=dry_run)
    conn.close()

    raw_codes = getattr(args, "codes", None)
    try:
        explicit = normalize_codes(raw_codes) if raw_codes else None
    except ValueError as exc:
        return _failure(str(exc), "args", dry_run=dry_run)

    today = clock.now(SHANGHAI).strftime("%Y-%m-%d")
    window = assets_db.list_recent_plan_candidate_codes(today, days=days)
    window_codes = list(window.get("codes") or [])

    latest_asset_date = assets_db.latest_stock_daily_asset_date()
    latest_bar_date = _latest_bar_date(latest_asset_date)
    codes = explicit if explicit is not None else window_codes
    last_by_code = (assets_db.stock_daily_last_bar_dates(
        codes, max_asset_days=MAX_ASSET_DAYS) if codes else {})
    missing = [code for code in codes if code not in last_by_code]
    stale = [code for code in codes
             if code in last_by_code and latest_bar_date
             and last_by_code[code] < latest_bar_date]
    if explicit is not None:
        detected = list(codes)
    else:
        selected = set(missing) | set(stale)
        detected = [code for code in codes if code in selected]

    target_date = _target_date(today, latest_asset_date, latest_bar_date)
    window_view = {"trade_dates": list(window.get("trade_dates") or []),
                   "codes": window_codes}

    if dry_run:
        return _payload(
            ok=True, dry_run=True, target_date=target_date,
            latest_bar_date=latest_bar_date, lookback_days=days,
            detected=detected, missing=missing, stale=stale,
            fetched=[], updated=[], skipped=[], failed=[], warnings=[],
            window=window_view)

    providers = getattr(services, "providers", None)
    tdx = getattr(providers, "tdx", None)
    if tdx is None:
        return _failure("无可用 tdx provider；请检查依赖与数据源配置", "env")

    if not detected:
        return _payload(
            ok=True, dry_run=False, target_date=target_date,
            latest_bar_date=latest_bar_date, lookback_days=days,
            detected=[], missing=missing, stale=stale,
            fetched=[], updated=[], skipped=[], failed=[], warnings=[],
            window=window_view)

    try:
        raw_fetched = tdx.batch_get_daily_klines(
            detected, days=STOCK_DAILY_DAYS, end_date=target_date)
    except Exception as exc:  # transport 整体失败：不写库，exit 1
        return _payload(
            ok=False, dry_run=False, target_date=target_date,
            latest_bar_date=latest_bar_date, lookback_days=days,
            detected=detected, missing=missing, stale=stale,
            fetched=[], updated=[], skipped=[], failed=[
                {"code": None, "reason": f"{type(exc).__name__}: {exc}",
                 "kind": "transport"}],
            warnings=[], window=window_view)

    raw_fetched = raw_fetched if isinstance(raw_fetched, dict) else {}
    converted = {
        code: convert_daily_bars(raw_fetched.get(code), target_date)
        for code in detected
    }
    converted = {code: bars for code, bars in converted.items() if bars}
    fetched = [code for code in detected if code in converted]
    skipped = [code for code in detected if code not in converted]

    existing, corrupt_error = _read_existing(target_date)
    warnings: list[str] = []
    if corrupt_error is not None:
        warnings.append("existing_payload_corrupt")
    rows = dict(existing.get("rows") or {}) if isinstance(existing, dict) else {}
    # partial 行没有池清单（requested_codes 是补采 codes），一律按扩池 20 根。
    pool_scope: set[str] = set()
    if isinstance(existing, dict) and existing.get("partial") is not True:
        pool_scope = set(existing.get("requested_codes") or [])
    limits = {code: (POOL_MAX_BARS if code in pool_scope
                     else PLAN_CANDIDATE_MAX_BARS)
              for code in converted}
    to_merge = {code: raw_fetched.get(code) for code in converted}
    merged_rows = merge_daily_rows(rows, to_merge, target_date, limits=limits)

    updated: list[str] = []
    failed: list[dict] = []
    ok = True
    if fetched:
        audit = {
            "at": clock.now(SHANGHAI).isoformat(timespec="seconds"),
            "lookback_days": days,
            "target_date": target_date,
            "requested": list(detected),
            "returned": fetched,
            "missing": skipped,
        }
        if corrupt_error is not None:
            audit["previous_corrupt"] = corrupt_error
        if isinstance(existing, dict):
            payload = dict(existing)
            payload["rows"] = merged_rows
            payload["backfill"] = audit
        else:
            payload = {
                "as_of": today,
                "trade_date": target_date,
                "requested_codes": list(detected),
                "returned_codes": fetched,
                "missing_codes": skipped,
                "rows": merged_rows,
                "coverage": {
                    "requested": len(detected),
                    "returned": len(fetched),
                    "missing": skipped,
                    "ratio": round(len(fetched) / len(detected), 4)
                    if detected else None,
                },
                "truncated": False,
                "review_codes": [],
                "review_health": {"status": "ok", "note": None, "missing": []},
                "partial": True,
                "backfill": audit,
            }
        try:
            assets_db.save_prep_assets(target_date, {"stock_daily": payload})
            updated = fetched
        except Exception as exc:  # 写库失败：整体回滚（save_prep_assets 单事务）
            ok = False
            failed.append({"code": None,
                           "reason": f"{type(exc).__name__}: {exc}",
                           "kind": "write"})

    return _payload(
        ok=ok, dry_run=False, target_date=target_date,
        latest_bar_date=latest_bar_date, lookback_days=days,
        detected=detected, missing=missing, stale=stale,
        fetched=fetched, updated=updated, skipped=skipped,
        failed=failed, warnings=warnings, window=window_view)


__all__ = ["COMMAND", "MAX_LOOKBACK_DAYS", "normalize_codes",
           "backfill_stock_daily"]
