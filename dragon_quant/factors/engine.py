"""因子层 build 骨架：模板方法 + PIT 时点解析 + canonical scan（方案 §4.3 / §6.3）。

固定骨架（每个因子只提供纯 compute 函数）：
    解析 spec → 取源数据 → lookback 校验 → 计算 → 时点解析 → 原子写库 → 报告

三条铁律：
  1. **不写哨兵值**：compute 未产出的 code 不写行；当日无值 → 删除该日该因子旧行。
  2. **源故障 ≠ 当日无值**：取数异常 / 无扫描 / 跨日回填 → 跳过该因子，**不删旧行**。
  3. **PIT 唯一实现**：``resolve_available_at`` 按 ``pit_rule`` 分派
     ``recorded`` / ``derived`` / ``asof``，全程 aware datetime 比较（禁止字符串比较）。
"""

from __future__ import annotations

import json
import math
import time
from datetime import datetime
from typing import Any, Iterable, Optional
from zoneinfo import ZoneInfo

from dragon_quant.factors import storage as fstore
from dragon_quant.factors.registry import compute_version_info, resolve_specs
from dragon_quant.factors.types import (
    AsOfViolation,
    BackfilledSource,
    FactorContext,
    FactorSpec,
    FactorValue,
)
from dragon_quant.storage import db as _db

SHANGHAI = ZoneInfo("Asia/Shanghai")
TS_FORMAT = "%Y-%m-%dT%H:%M:%S"
SOURCE_SCHEMA_VERSION = "1"

# available_phase → 当日基准时点（方案 §6.2）
PHASE_TIMES = {
    "auction": (9, 25),
    "open": (9, 30),
    "close": (15, 5),
    "prep": (16, 40),
}

# 允许写库的因子状态（其余 = 跳过且不删旧行）
WRITE_STATUSES = ("ok", "empty")

_SECTOR_CATEGORIES = ("hy", "gn", "dq")


# ─── PIT 时点 ──────────────────────────────────────────────────────────────


def parse_local(ts: str) -> datetime:
    """解析 SQLite ``YYYY-MM-DD HH:MM:SS`` 或 ISO ``YYYY-MM-DDTHH:MM:SS``。

    **绝对不能按字符串比较**：``'T'(0x54) > ' '(0x20)``，字符串比较会恒取
    phase、静默吞掉「晚到源」。无时区信息一律视为 Asia/Shanghai。
    """
    if not ts or not isinstance(ts, str):
        raise ValueError(f"无法解析时间戳: {ts!r}")
    moment = datetime.fromisoformat(ts.strip().replace(" ", "T"))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=SHANGHAI)
    return moment


def format_ts(moment: datetime) -> str:
    return moment.astimezone(SHANGHAI).strftime(TS_FORMAT)


def phase_ts(trade_date: str, phase: str) -> datetime:
    """``trade_date`` 当日 ``phase`` 的 aware 时点。"""
    try:
        hour, minute = PHASE_TIMES[phase]
    except KeyError as exc:
        raise ValueError(f"非法 available_phase: {phase!r}，合法值 {tuple(PHASE_TIMES)}") from exc
    day = datetime.strptime(trade_date, "%Y-%m-%d")
    return day.replace(hour=hour, minute=minute, second=0, tzinfo=SHANGHAI)


def resolve_available_at(spec: FactorSpec, trade_date: str,
                         source_created_at: Optional[str] = None, *,
                         ledger_available_at: Optional[str] = None) -> str:
    """按 ``pit_rule`` 解析因子行的 ``available_at``（唯一实现）。

    - ``derived``：``phase_ts``（源是日期键纯函数，重算不改「当时可知」）。
    - ``asof``：``phase_ts`` + 硬门禁（被选中账本记录 ``available_at`` 必须
      ≤ phase；P0 不产出 asof 因子，P0.5 财务链路复用）。
    - ``recorded``：同日 ``max(phase, source_created_at)``；跨日 source → 抛
      ``BackfilledSource``（调用方 skip 并计 ``skipped_backfilled``）。
    """
    phase = phase_ts(trade_date, spec.available_phase)
    if spec.pit_rule == "derived":
        return format_ts(phase)
    if spec.pit_rule == "asof":
        if ledger_available_at is not None:
            ledger = parse_local(ledger_available_at)
            if ledger > phase:
                raise AsOfViolation(
                    f"{spec.name}: 账本可用时刻 {format_ts(ledger)} 晚于 "
                    f"{trade_date} {spec.available_phase} phase {format_ts(phase)}")
        return format_ts(phase)
    if spec.pit_rule != "recorded":
        raise ValueError(f"{spec.name}: 非法 pit_rule={spec.pit_rule!r}")
    if not source_created_at:
        raise ValueError(f"{spec.name}: recorded 因子缺 source_created_at，无法判定可知时点")
    created = parse_local(source_created_at)
    if created.date().isoformat() != str(trade_date):
        raise BackfilledSource(
            f"{spec.name}: 源行 created_at={format_ts(created)} 与 trade_date={trade_date} 不同日"
            "（跨日回填可能含未来信息，拒绝写入）")
    return format_ts(max(phase, created))


def timezone_warning() -> Optional[str]:
    """本机时区不是 +08:00 时返回警告文本（不阻断 build）。"""
    offset = getattr(time.localtime(), "tm_gmtoff", None)
    if offset is None:
        return None
    if offset != 8 * 3600:
        return f"timezone_warning: 本机 UTC 偏移 {offset // 3600:+d}h，项目要求 Asia/Shanghai (+8h)"
    return None


# ─── canonical scan（同日唯一化，方案 §1.3-D2）────────────────────────────


def canonical_scan(trade_date: str, *, source: str = "v2") -> Optional[dict]:
    """同一天多次扫描的确定性归并：``created_at DESC, top_n DESC, id DESC``。

    三个排序键都必要：``scan_id`` 形如 ``v2_YYYYMMDD_topN``（top_n 未补零），
    纯字典序下 ``v2_20260519_5 > v2_20260519_25``，同秒重扫时必须先比 ``top_n``。
    """
    rows = _db.get_scans_by_date(trade_date, source=source) or []
    if not rows:
        return None
    chosen = max(
        rows,
        key=lambda row: (
            row.get("created_at") or "",
            int(row.get("top_n") or 0),
            str(row.get("id") or ""),
        ),
    )
    # get_scans_by_date 不返回 raw_output（体积大），按选中的 id 再取整行。
    return _db.get_scan(chosen["id"], source=source) or chosen


# ─── 源数据装载（源故障 → skip，不删旧行）────────────────────────────────


def _same_day(ts, trade_date: str) -> bool:
    try:
        return parse_local(ts).date().isoformat() == str(trade_date)
    except (TypeError, ValueError):
        return False


def _load_scan_source(trade_date: str, cache: dict) -> dict:
    key = ("scan", trade_date)
    if key in cache:
        return cache[key]
    try:
        scan = canonical_scan(trade_date)
        if not scan:
            result = {
                "status": "missing_source",
                "reason": f"{trade_date} 无 v2 扫描记录",
                "rows": [], "rows_by_code": {}, "codes": [],
                "scan_id": None, "top_n": None, "created_at": None,
                "raw_output": None, "as_of": None,
            }
        else:
            rows = _db.get_scan_stocks(scan["id"]) or []
            rows_by_code = {
                row["code"]: row for row in rows if row.get("code")
            }
            result = {
                "status": "ok",
                "rows": rows, "rows_by_code": rows_by_code,
                "codes": tuple(sorted(rows_by_code)),
                "scan_id": scan.get("id"), "top_n": scan.get("top_n"),
                "created_at": scan.get("created_at"),
                "raw_output": scan.get("raw_output"),
                "as_of": scan.get("created_at"),
            }
            if not scan.get("created_at"):
                result = {
                    "status": "missing_source",
                    "reason": f"canonical scan {scan.get('id')!r} 缺 created_at，无法判定可知时点",
                    "rows": [], "rows_by_code": {}, "codes": [],
                    "scan_id": scan.get("id"), "top_n": scan.get("top_n"),
                    "created_at": None, "raw_output": scan.get("raw_output"),
                    "as_of": None,
                }
    except Exception as exc:      # 源故障 ≠ 当日无值
        result = {
            "status": "missing_source",
            "reason": f"读取扫描源失败: {type(exc).__name__}: {exc}",
            "rows": [], "rows_by_code": {}, "codes": [],
            "scan_id": None, "top_n": None, "created_at": None,
            "raw_output": None, "as_of": None,
        }
    cache[key] = result
    return result


def _load_vpa_source(trade_date: str, cache: dict) -> dict:
    key = ("vpa", trade_date)
    if key in cache:
        return cache[key]
    try:
        rows = _db.list_vpa_analysis(trade_date) or []
    except Exception as exc:
        result = {
            "status": "missing_source",
            "reason": f"读取 vpa_analysis 失败: {type(exc).__name__}: {exc}",
            "rows": [], "codes": [], "created_at": None,
            "skipped_backfilled": 0, "skipped_backfilled_samples": [],
        }
        cache[key] = result
        return result

    kept: list[dict] = []
    skipped: list[str] = []
    for row in rows:
        code = row.get("code") or ""
        if _same_day(row.get("created_at"), trade_date):
            kept.append(row)
        else:
            skipped.append(code)
    if not rows:
        result = {
            "status": "missing_source",
            "reason": f"{trade_date} 无 vpa_analysis 记录",
            "rows": [], "codes": [], "created_at": None,
            "skipped_backfilled": 0, "skipped_backfilled_samples": [],
        }
    elif not kept:
        # 全部为跨日回填（review.py 用运行当天 K 线写历史 trade_date），整行 skip。
        result = {
            "status": "skipped_backfilled",
            "reason": f"{len(skipped)} 条 vpa 行 created_at 与 trade_date 不同日，全部拒绝写入",
            "rows": [], "codes": [], "created_at": None,
            "skipped_backfilled": len(skipped),
            "skipped_backfilled_samples": sorted(skipped)[:10],
        }
    else:
        latest = max(kept, key=lambda row: parse_local(row["created_at"]))
        result = {
            "status": "ok",
            "rows": kept,
            "codes": tuple(sorted({row["code"] for row in kept if row.get("code")})),
            "created_at": latest.get("created_at"),
            "as_of": latest.get("created_at"),
            "skipped_backfilled": len(skipped),
            "skipped_backfilled_samples": sorted(skipped)[:10],
        }
    cache[key] = result
    return result


def _load_fin_source(trade_date: str, cache: dict, *, calendar: Iterable[str] = (),
                     db_path=None) -> dict:
    """财务源：候选池取自 canonical scan，账本 as-of 解析交给 ``factors/finance.py``。

    ``finance`` 是分层规则允许的唯一 provider 触点（方案 §3.4 修订）；engine 只
    组装缓存与降级状态，不直接 import providers。
    """
    key = ("fin", trade_date)
    if key in cache:
        return cache[key]
    scan = _load_scan_source(trade_date, cache)
    if scan.get("status") != "ok":
        result = {
            "status": scan.get("status") or "missing_source",
            "reason": f"财务因子依赖候选池，但扫描源不可用: {scan.get('reason')}",
            "current": {}, "prior": {}, "cohort": {},
            "observed": {}, "codes": (), "as_of": None, "available_at": None,
        }
        cache[key] = result
        return result
    pool_profile = {
        code: {
            "composite": row.get("composite_score"),
            "market_cap": row.get("market_cap"),
            "is_true_dragon": (None if row.get("is_true_dragon") is None
                               else bool(row.get("is_true_dragon"))),
        }
        for code, row in (scan.get("rows_by_code") or {}).items()
    }
    try:
        from dragon_quant.factors import finance
        result = finance.load_fin_source(
            trade_date, scan.get("codes") or (), calendar=tuple(calendar),
            pool_profile=pool_profile, db_path=db_path)
    except Exception as exc:      # 源故障 ≠ 当日无值
        result = {
            "status": "missing_source",
            "reason": f"财务账本读取失败: {type(exc).__name__}: {exc}",
            "current": {}, "prior": {}, "cohort": {},
            "observed": {}, "codes": (), "as_of": None, "available_at": None,
        }
    cache[key] = result
    return result


_PREP_DRAGONS_DEGRADED_TOKENS = frozenset(
    {"dragons", "dragons_partial", "dragons_window_short"})


def _prep_empty_is_degraded(snapshot: dict) -> bool:
    """``candidates == []`` 是否来自 dragons 降级（R32）。

    快照在 dragons 资产不可用（``window_days=0``）或窗口降级时同样会写出空
    列表；把它当「真实空」会 DELETE 掉不可再生的历史因子行。无法确认健康时
    一律按降级处理（fail-closed）。
    """
    dragon = snapshot.get("dragon_source")
    if not isinstance(dragon, dict):
        return True
    window_days = dragon.get("window_days")
    if isinstance(window_days, bool) or not isinstance(window_days, int) or window_days < 1:
        return True
    dates = dragon.get("dates")
    if not isinstance(dates, list) or not dates:
        return True
    if any(not isinstance(day, dict) or day.get("status") != "ok" for day in dates):
        return True
    sentiment = snapshot.get("sentiment")
    if not isinstance(sentiment, dict):
        return True
    raw = sentiment.get("degraded")
    tokens = {str(item) for item in raw} if isinstance(raw, (list, tuple, set)) else set()
    return bool(tokens & _PREP_DRAGONS_DEGRADED_TOKENS)


def _load_prep_source(trade_date: str, cache: dict) -> dict:
    """prep 快照源：只读 ``assets.db``（``create=False``），损坏整族 skip 不删旧行。"""
    key = ("prep", trade_date)
    if key in cache:
        return cache[key]

    def _missing(reason: str) -> dict:
        return {
            "status": "missing_source", "reason": reason,
            "snapshot_date": trade_date, "snapshot_version": None,
            "snapshot_created_at": None, "candidates": [],
            "candidates_by_code": {}, "codes": (), "as_of": None,
        }

    try:
        from dragon_quant.storage import assets_db
        snapshot, meta = assets_db.get_prep_snapshot(
            trade_date, source="v2", with_meta=True)
    except Exception as exc:      # 单列 JSON 无逐行隔离：整族 skip（R30）
        result = _missing(f"读取 prep_snapshots 失败: {type(exc).__name__}: {exc}")
        cache[key] = result
        return result
    if not isinstance(snapshot, dict):
        result = _missing(f"{trade_date} 无 prep_snapshots 记录")
        cache[key] = result
        return result

    candidates = snapshot.get("candidates")
    if not isinstance(candidates, list):
        result = _missing("prep_snapshots.candidates 不是数组（malformed）")
        cache[key] = result
        return result
    if not candidates and _prep_empty_is_degraded(snapshot):
        result = _missing(
            "empty_degraded_source: candidates 为空且 dragons 血缘不可用/降级，"
            "不能当作当日无值")
        cache[key] = result
        return result

    by_code: dict[str, dict] = {}
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        code = candidate.get("code")
        if code:
            by_code[str(code)] = candidate
    result = {
        "status": "ok",
        "snapshot_date": trade_date,
        "snapshot_version": snapshot.get("version"),
        "snapshot_created_at": (meta or {}).get("created_at"),
        "candidates": candidates,
        "candidates_by_code": by_code,
        "codes": tuple(sorted(by_code)),
        "as_of": None,
    }
    cache[key] = result
    return result


def _load_auction_source(trade_date: str, cache: dict) -> dict:
    """竞价快照源：``list_auction_snapshots`` 已逐行隔离 JSON 损坏。"""
    key = ("auction", trade_date)
    if key in cache:
        return cache[key]

    def _missing(reason: str) -> dict:
        return {
            "status": "missing_source", "reason": reason,
            "snapshot_date": trade_date, "snapshot_version": None,
            "snapshot_created_at": None, "rows_by_code": {}, "codes": (),
            "corrupt_rows": [], "as_of": None,
        }

    try:
        from dragon_quant.storage import assets_db
        rows = assets_db.list_auction_snapshots(
            trade_date, source="v2", with_meta=True)
    except Exception as exc:
        result = _missing(f"读取 auction_snapshots 失败: {type(exc).__name__}: {exc}")
        cache[key] = result
        return result
    rows = [row for row in (rows or []) if isinstance(row, dict)]
    if not rows:
        # 空池与「命令未运行」在库中不可区分（R33）：按源未产出 skip、不删旧行。
        result = _missing(f"{trade_date} 无 auction_snapshots 记录")
        cache[key] = result
        return result

    corrupt = [
        str(row.get("code")) for row in rows
        if row.get("code") and "error" in row
    ]
    usable = [row for row in rows if row.get("code") and "error" not in row]
    if not usable:
        result = _missing("all_corrupt: auction_snapshots 全部行 JSON 损坏")
        cache[key] = result
        return result

    rows_by_code = {str(row["code"]): row for row in usable}
    created = [str(row["created_at"]) for row in usable if row.get("created_at")]
    versions = [str(row["version"]) for row in usable if row.get("version")]
    result = {
        "status": "ok",
        "snapshot_date": trade_date,
        "snapshot_version": versions[0] if versions else "1",
        "snapshot_created_at": max(created) if created else None,
        "rows_by_code": rows_by_code,
        "codes": tuple(sorted(rows_by_code)),
        "corrupt_rows": corrupt,
        "as_of": None,
    }
    cache[key] = result
    return result


def _load_source(spec: FactorSpec, trade_date: str, cache: dict, *,
                 calendar: Iterable[str] = (), db_path=None) -> dict:
    if spec.source == "scan":
        return _load_scan_source(trade_date, cache)
    if spec.source == "vpa":
        return _load_vpa_source(trade_date, cache)
    if spec.source == "fin":
        return _load_fin_source(trade_date, cache, calendar=calendar, db_path=db_path)
    if spec.source == "prep":
        return _load_prep_source(trade_date, cache)
    if spec.source == "auction":
        return _load_auction_source(trade_date, cache)
    return {
        "status": "missing_source",
        "reason": f"未接入 source={spec.source!r}",
        "rows": [], "codes": [],
    }


# ─── 模板方法：单因子 build ───────────────────────────────────────────────


def _elapsed_ms(started: float) -> float:
    return round((time.monotonic() - started) * 1000, 1)


def _absence_expected(meta: dict, codes: int) -> bool:
    """空结果是否可由 counters 完全解释（R26）：全部未校准 / 全部预期缺席。"""
    meta = meta or {}
    if meta.get("expected_empty") is True:
        return True
    counters = meta.get("counters") or {}
    unverified = counters.get("unverified_rows")
    return (isinstance(unverified, int) and not isinstance(unverified, bool)
            and codes > 0 and unverified == codes)


def _absence_warning(meta: dict, codes: int) -> str:
    counters = (meta or {}).get("counters") or {}
    unverified = counters.get("unverified_rows")
    if isinstance(unverified, int) and not isinstance(unverified, bool) and unverified == codes:
        return f"unverified_rows={unverified}"
    return "expected_empty（缺失可由 counters 解释）"


def build_factor(spec: FactorSpec, trade_date: str, *, source_cache: Optional[dict] = None,
                 calendar: Iterable[str] = (), db_path=None) -> dict:
    """单因子单日 build；异常逐因子隔离，永不向调用方抛（除编程错误外）。"""
    started = time.monotonic()
    version, version_source = compute_version_info(spec)
    entry: dict[str, Any] = {
        "name": spec.name,
        "label": spec.label,
        "version": version,
        "version_source": version_source,
        "status": "ok",
        "rows": 0,
        "source": spec.source,
        "available_at": None,
        "source_scan_id": None,
        "codes": 0,
        "coverage": None,
        "invalid_value": 0,
        "skipped_backfilled": 0,
        "warnings": [],
        "reason": None,
        "meta": {},
    }
    if version_source == "impl_key":
        entry["warnings"].append(
            "version_source=impl_key（源码不可得，已回退 impl_key）")

    cache = source_cache if source_cache is not None else {}
    source = _load_source(spec, trade_date, cache, calendar=calendar, db_path=db_path)
    entry["skipped_backfilled"] = int(source.get("skipped_backfilled") or 0)
    if source.get("status") != "ok":
        entry["status"] = source.get("status") or "missing_source"
        entry["reason"] = source.get("reason")
        entry["elapsed_ms"] = _elapsed_ms(started)
        return entry

    codes = tuple(source.get("codes") or ())
    entry["codes"] = len(codes)
    calendar = tuple(calendar)
    if spec.lookback and len(calendar) < spec.lookback:
        entry["status"] = "insufficient_data"
        entry["reason"] = f"lookback={spec.lookback} 超出可用交易日 {len(calendar)}"
        entry["elapsed_ms"] = _elapsed_ms(started)
        return entry

    ctx = FactorContext(
        trade_date=trade_date,
        codes=codes,
        source_rows=source,
        as_of=source.get("as_of") or source.get("created_at") or "",
        calendar=calendar,
        params=spec.params,
    )
    try:
        frame = spec.compute(ctx)
    except BackfilledSource as exc:
        entry["status"] = "skipped_backfilled"
        entry["reason"] = str(exc)
        entry["elapsed_ms"] = _elapsed_ms(started)
        return entry
    except Exception as exc:
        entry["status"] = "error"
        entry["reason"] = f"计算失败: {type(exc).__name__}: {exc}"
        entry["elapsed_ms"] = _elapsed_ms(started)
        return entry
    if frame is None:
        entry["status"] = "error"
        entry["reason"] = "compute 返回 None"
        entry["elapsed_ms"] = _elapsed_ms(started)
        return entry

    values: dict[str, float] = {}
    for code, value in (frame.values or {}).items():
        if not code:
            continue
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            entry["invalid_value"] += 1
            continue
        if not math.isfinite(numeric):
            entry["invalid_value"] += 1
            continue
        values[str(code)] = numeric

    try:
        available_at = resolve_available_at(
            spec, trade_date, frame.available_at or source.get("created_at"),
            ledger_available_at=(
                (frame.available_at or source.get("available_at"))
                if spec.pit_rule == "asof" else None))
    except BackfilledSource as exc:
        entry["status"] = "skipped_backfilled"
        entry["reason"] = str(exc)
        entry["elapsed_ms"] = _elapsed_ms(started)
        return entry
    except AsOfViolation as exc:
        entry["status"] = "asof_violation"
        entry["reason"] = str(exc)
        entry["elapsed_ms"] = _elapsed_ms(started)
        return entry
    except Exception as exc:
        entry["status"] = "error"
        entry["reason"] = f"时点解析失败: {type(exc).__name__}: {exc}"
        entry["elapsed_ms"] = _elapsed_ms(started)
        return entry

    source_scan_id = frame.source_scan_id or source.get("scan_id")
    # 序列化失败只影响本因子：标 error 且**不写库**（不删旧行、不打断整日 build）。
    try:
        source_meta_json = fstore.dump_source_meta(frame.meta)
    except Exception as exc:
        entry["status"] = "error"
        entry["reason"] = f"source_meta 序列化失败: {type(exc).__name__}: {exc}"
        entry["elapsed_ms"] = _elapsed_ms(started)
        return entry
    rows = [
        FactorValue(
            trade_date=trade_date,
            code=code,
            factor_name=spec.name,
            factor_version=version,
            value=values[code],
            available_at=available_at,
            source=spec.source,
            source_scan_id=source_scan_id,
            source_meta_json=source_meta_json,
        )
        for code in sorted(values)
    ]
    try:
        written = fstore.replace_factor_day(
            trade_date, spec.name, version, rows, db_path=db_path)
    except Exception as exc:
        entry["status"] = "error"
        entry["reason"] = f"写入失败: {type(exc).__name__}: {exc}"
        entry["elapsed_ms"] = _elapsed_ms(started)
        return entry

    coverage = (len(values) / len(codes)) if codes else None
    entry.update({
        "status": "ok" if rows else "empty",
        "rows": written,
        "available_at": available_at,
        "source_scan_id": source_scan_id,
        "coverage": round(coverage, 4) if coverage is not None else None,
        "meta": frame.meta or {},
    })
    # 源侧警告（stale_calendar_missing / cohort_selection_bias / layout_unverified）
    # 进 build 报告，运维无需翻 source_meta_json 才能发现降级。
    for warning in (frame.meta or {}).get("warnings") or []:
        text = str(warning)
        if text not in entry["warnings"]:
            entry["warnings"].append(text)
    if not rows:
        if _absence_expected(frame.meta, len(codes)):
            entry["warnings"].append(_absence_warning(frame.meta, len(codes)))
        else:
            entry["warnings"].append("empty_unexpected")
    if entry["invalid_value"]:
        entry["warnings"].append(f"invalid_value={entry['invalid_value']}")
    entry["elapsed_ms"] = _elapsed_ms(started)
    return entry


# ─── 主板块快照（从 scans.raw_output 派生，不猜）──────────────────────────


def write_sector_snapshot(trade_date: str, *, source_cache: Optional[dict] = None,
                          db_path=None) -> dict:
    """解析 canonical scan 的 ``raw_output.ranking`` 写 ``factor_sector_snapshots``。

    ``category`` 如实记录 hy/gn/dq（只有 hy 是真实行业）；缺失/非法 → 不写行。
    ``raw_output`` 为空或解析失败 → 不删旧快照（源不可用 ≠ 当日无归属）。
    """
    cache = source_cache if source_cache is not None else {}
    source = _load_scan_source(trade_date, cache)
    if source.get("status") != "ok":
        return {"status": source.get("status") or "missing_source",
                "rows": 0, "reason": source.get("reason")}

    raw = source.get("raw_output")
    if not raw:
        return {"status": "missing_raw_output", "rows": 0,
                "reason": "canonical scan 的 raw_output 为空，不猜测主板块"}
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        return {"status": "missing_raw_output", "rows": 0,
                "reason": f"raw_output 解析失败: {type(exc).__name__}: {exc}"}
    ranking = payload.get("ranking") if isinstance(payload, dict) else None
    if not isinstance(ranking, list):
        return {"status": "missing_raw_output", "rows": 0,
                "reason": "raw_output.ranking 不是数组"}

    rows: list[dict] = []
    seen: set[tuple[str, str]] = set()
    skipped = 0
    for item in ranking:
        if not isinstance(item, dict):
            skipped += 1
            continue
        code = item.get("code")
        sector = item.get("primary_sector")
        category = item.get("primary_sector_category")
        if not code or not sector or category not in _SECTOR_CATEGORIES:
            skipped += 1
            continue
        key = (str(code), category)
        if key in seen:
            continue
        seen.add(key)
        rows.append({
            "code": str(code),
            "industry_code": sector,
            "industry_name": item.get("primary_sector_name"),
            "category": category,
            "source": item.get("primary_sector_source"),
            "scan_id": source.get("scan_id"),
        })

    try:
        written = fstore.replace_sector_day(trade_date, rows, db_path=db_path)
    except Exception as exc:
        return {"status": "error", "rows": 0,
                "reason": f"写入失败: {type(exc).__name__}: {exc}"}

    by_category: dict[str, int] = {}
    for row in rows:
        by_category[row["category"]] = by_category.get(row["category"], 0) + 1
    result = {
        "status": "ok",
        "rows": written,
        "skipped": skipped,
        "by_category": by_category,
        "scan_id": source.get("scan_id"),
    }
    if not rows:
        result["reason"] = "raw_output.ranking 无有效主板块字段"
    return result


# ─── 单日 / 区间 build ────────────────────────────────────────────────────


def _now_str() -> str:
    return datetime.now(SHANGHAI).strftime(TS_FORMAT)


def build_date(trade_date: str, *, names: Optional[Iterable[str]] = None,
               db_path=None, calendar: Iterable[str] = ()) -> dict:
    """构建某交易日全部（或指定）因子，写审计报告，返回报告 dict。

    - 逐因子隔离：单因子失败不中断，状态进 ``factors``；
    - 区间调用方（service）逐日独立调用本函数，逐日独立事务。
    """
    started_at = _now_str()
    cache: dict = {}
    # names=None = 只构建 default_enabled 因子（P0.5 财务因子默认关，不得被静默计算）；
    # 显式 --only 不受影响（用户点名的因子即使默认关也构建）。
    specs = resolve_specs(names, enabled_only=names is None)
    versions: dict[str, str] = {}
    reports: dict[str, dict] = {}
    fin_valid_from: dict[str, str] = {}
    for spec in specs:
        version, _ = compute_version_info(spec)
        versions[spec.name] = version
        reports[spec.name] = build_factor(
            spec, trade_date, source_cache=cache, calendar=calendar, db_path=db_path)
        derived = (reports[spec.name].get("meta") or {}).get("earliest_available")
        if spec.source == "fin" and derived and reports[spec.name].get("status") == "ok":
            fin_valid_from[spec.name] = derived

    sector = write_sector_snapshot(trade_date, source_cache=cache, db_path=db_path)

    warnings: list[str] = []
    registry_inserted = 0
    try:
        registry_inserted = fstore.save_registry_snapshots(
            specs, versions, valid_from_overrides=fin_valid_from, db_path=db_path)
    except Exception as exc:
        warnings.append(f"注册表写入失败: {type(exc).__name__}: {exc}")

    tz_warning = timezone_warning()
    if tz_warning:
        warnings.append(tz_warning)

    scan_source = cache.get(("scan", trade_date)) or {}
    scan_available = scan_source.get("status") == "ok"
    if not scan_available:
        warnings.append("no_scan_for_date: 五维因子与主板块快照本次未产出")
    for name, entry in reports.items():
        if entry.get("status") == "error":
            warnings.append(f"{name}: {entry.get('reason')}")
        if entry.get("status") == "empty":
            meta = entry.get("meta") or {}
            codes = int(entry.get("codes") or 0)
            if _absence_expected(meta, codes):
                # 全量未校准 / 预期缺席：只报计数器，避免每天刷 empty 告警（R26）。
                warnings.append(f"{name}: {_absence_warning(meta, codes)}")
            else:
                warnings.append(f"{name}: empty（源可用但当日无值）")

    finished_at = _now_str()
    has_error = (
        any(entry.get("status") == "error" for entry in reports.values())
        or sector.get("status") == "error"
    )
    needs_scan = any(spec.source == "scan" for spec in specs)
    ok = (scan_available or not needs_scan) and not has_error
    report = {
        "schema_version": SOURCE_SCHEMA_VERSION,
        "trade_date": trade_date,
        "scan": ({
            "scan_id": scan_source.get("scan_id"),
            "top_n": scan_source.get("top_n"),
            "created_at": scan_source.get("created_at"),
            "candidates": len(scan_source.get("rows") or []),
        } if scan_available else None),
        "factors": reports,
        "sector_snapshot": sector,
        "registry_inserted": registry_inserted,
        "warnings": warnings,
        "timezone_warning": tz_warning,
        "started_at": started_at,
        "finished_at": finished_at,
        "ok": ok,
    }
    try:
        report["build_id"] = fstore.record_build_run(
            trade_date, report, started_at=started_at, finished_at=finished_at,
            db_path=db_path)
    except Exception as exc:
        warnings.append(f"审计报告写入失败: {type(exc).__name__}: {exc}")
        report["build_id"] = None
        report["ok"] = False
    return report
