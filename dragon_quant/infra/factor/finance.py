"""财务数据同步适配层（P0.5，**唯一触网点**，方案 §4.9 / §6.4）。

分层约束（§3.4 修订）：``factors/*`` 只允许本模块 import providers；
``engine.py`` / ``builtin/*`` 永不触网、不 import provider。

三条 PIT 铁律：
  1. **法定截止日 23:59:59**：截止日当天 16:40 不得使用当晚才可能披露的期次
     （比较全程 aware datetime，禁止字符串比较）。
  2. **修订 vintage 优先**：非首版一律 ``revised``，可用时刻下界 = 首次下载时刻，
     绝不借用观测时刻；``observed`` 只能抬高、不能拉低可用时刻。
  3. **观测只做值级证明**：只有观测值与 vintage 语义字段哈希相等且观测时刻
     ≤ 首版写入时刻，才把 basis 升级为 ``observed``；否则计 ``observed_unmatched``。
"""

from __future__ import annotations

import gzip
import hashlib
import json
import math
import re
from datetime import datetime, timedelta
from typing import Iterable, Optional, Sequence
from zoneinfo import ZoneInfo

from .engine import format_ts, parse_local, phase_ts
from dragon_quant.infra import clock
from . import finance_store as fin_store

SHANGHAI = ZoneInfo("Asia/Shanghai")

# 命名快照中本方案使用的语义字段（``get_finance_info`` 字段名）
FIN_FIELDS = (
    "zong_guben", "meigujing_zichan", "jing_zichan", "zhuying_shouru",
    "jing_lirun", "yingye_lirun", "zhuying_lirun", "shuihou_lirun", "lirun_zonghe",
)
# 观测→账本值级匹配的最小公共字段集（两侧都必须可算同一哈希）
HASH_FIELDS = ("meigujing_zichan", "jing_zichan", "zhuying_shouru", "jing_lirun")
# 校准必须拿到独立证据的四个核心字段（方案 §4.9.2）
REQUIRED_CALIBRATION_FIELDS = (
    "meigujing_zichan", "jing_zichan", "zhuying_shouru", "jing_lirun")
CALIBRATION_FIELDS = REQUIRED_CALIBRATION_FIELDS + ("yingye_lirun", "zhuying_lirun")
# 比率/同比因子需要的映射字段（由 builtin/financial.py 消费）
MAPPED_FIELDS = CALIBRATION_FIELDS + ("zong_guben", "shuihou_lirun")
SCALES = (1.0, 1e4, 1e-4, 1e8)
_SCALE_NAMES = {1.0: "1", 1e4: "1e4", 1e-4: "1e-4", 1e8: "1e8"}
NUMERIC_TOLERANCE = 0.02          # 数值证据：中位相对误差 ≤ 2%
HIERARCHY_TOLERANCE = 1.05        # 层级不等式允许 5% 口径差
MIN_SAMPLES = 3


# ─── 时间 / 报告期 ─────────────────────────────────────────────────────────


def report_period_deadline(period: str) -> str:
    """报告期 → 法定披露截止日 **23:59:59**（``%Y-%m-%dT%H:%M:%S``）。

    A 股截止日盘后披露是常态：取零点会把「当晚才披露」当成「当天开盘前已知」。
    """
    day = datetime.strptime(period, "%Y-%m-%d")
    if (day.month, day.day) == (3, 31):
        deadline = datetime(day.year, 4, 30, 23, 59, 59)
    elif (day.month, day.day) == (6, 30):
        deadline = datetime(day.year, 8, 31, 23, 59, 59)
    elif (day.month, day.day) == (9, 30):
        deadline = datetime(day.year, 10, 31, 23, 59, 59)
    elif (day.month, day.day) == (12, 31):
        deadline = datetime(day.year + 1, 4, 30, 23, 59, 59)
    else:
        raise ValueError(f"非法报告期: {period!r}（只支持 0331/0630/0930/1231）")
    return deadline.replace(tzinfo=SHANGHAI).strftime("%Y-%m-%dT%H:%M:%S")


def next_report_period(period: str) -> str:
    day = datetime.strptime(period, "%Y-%m-%d")
    if (day.month, day.day) == (3, 31):
        return f"{day.year}-06-30"
    if (day.month, day.day) == (6, 30):
        return f"{day.year}-09-30"
    if (day.month, day.day) == (9, 30):
        return f"{day.year}-12-31"
    if (day.month, day.day) == (12, 31):
        return f"{day.year + 1}-03-31"
    raise ValueError(f"非法报告期: {period!r}")


def prior_year_period(period: str) -> str:
    day = datetime.strptime(period, "%Y-%m-%d")
    return f"{day.year - 1}-{day.month:02d}-{day.day:02d}"


def resolve_record_available_at(*, period: str, observed_first_at: Optional[str],
                                vintage_first_seen: str,
                                is_first_vintage: bool) -> tuple[str, str]:
    """账本记录 → ``(available_at, pit_basis)``（方案 §6.4.2，vintage 优先）。"""
    deadline = parse_local(report_period_deadline(period))
    seen = parse_local(vintage_first_seen)
    if not is_first_vintage:
        base = deadline
        if observed_first_at:
            base = min(deadline, parse_local(observed_first_at))
        return format_ts(max(base, seen)), "revised"
    if observed_first_at:
        return format_ts(min(deadline, parse_local(observed_first_at))), "observed"
    return format_ts(deadline), "statutory_backfilled"


def _finite(value) -> Optional[float]:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if math.isfinite(numeric) else None


def _round(value) -> float:
    return round(float(value), 4)


def values_hash(values: dict) -> str:
    """语义字段规范化哈希（用于观测 ↔ vintage 值级证明）。"""
    payload = {
        field: _round(values[field])
        for field in HASH_FIELDS
        if field in values and _finite(values.get(field)) is not None
    }
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _named_values(info: dict) -> dict:
    out: dict = {}
    for field in FIN_FIELDS:
        numeric = _finite(info.get(field))
        if numeric is not None:
            out[field] = _round(numeric)
    return out


def _map_fields(fields: Sequence[float], mapping: dict) -> dict:
    out: dict = {}
    for field in MAPPED_FIELDS:
        spec = mapping.get(field)
        if not isinstance(spec, dict):
            continue
        index = spec.get("index")
        if not isinstance(index, int) or index < 0 or index >= len(fields):
            continue
        numeric = _finite(fields[index])
        if numeric is None:
            continue
        out[field] = _round(numeric * float(spec.get("scale", 1.0)))
    return out


def _same_values(left: dict, right: dict) -> bool:
    keys = set(left) | set(right)
    return all(_finite(left.get(k)) == _finite(right.get(k)) for k in keys)


def _period_from_updated(updated_date) -> Optional[str]:
    """``updated_date`` 恰为季末（YYYYMMDD）时可作为期次线索；否则 None。"""
    try:
        text = str(int(updated_date))
    except (TypeError, ValueError):
        return None
    if len(text) != 8 or text[4:] not in ("0331", "0630", "0930", "1231"):
        return None
    return f"{text[:4]}-{text[4:6]}-{text[6:8]}"


def _period_from_filename(filename: str) -> Optional[str]:
    match = re.search(r"(\d{8})", filename or "")
    if not match:
        return None
    text = match.group(1)
    if text[4:] not in ("0331", "0630", "0930", "1231"):
        return None
    return f"{text[:4]}-{text[4:6]}-{text[6:8]}"


def _match_period(code: str, values: dict, *, db_path=None) -> Optional[str]:
    """观测语义值在账本中的值级匹配（用于推导观测期次）。"""
    target = values_hash(values)
    for row in fin_store.list_records_for_code(code, db_path=db_path):
        if values_hash(row["values"]) == target:
            return row["report_period"]
    return None


# ─── 同步：日度观测 ─────────────────────────────────────────────────────────


def _provider_from_services(services):
    """可选 M2 装配注入：取出 ``bundle.tdx``；无注入返回 None。

    None 表示沿用既有 ``TdxProvider()`` 构造路径，显式 ``tdx=`` 参数与
    现有 patch 目标保持不变。
    """
    if services is None:
        return None
    bundle = getattr(services, "providers", None)
    if bundle is None:
        return None
    return getattr(bundle, "tdx", None)


def _default_tdx(services=None):
    provider = _provider_from_services(services)
    if provider is not None:
        return provider
    from dragon_quant.infra.provider.tdx import TdxProvider
    return TdxProvider()


def _today() -> str:
    return clock.now(SHANGHAI).date().isoformat()


def load_trade_calendar(days: int = 180, *,
                        services=None) -> tuple[list[str], Optional[str]]:
    """真实交易日历（TDX 上证指数日K，经 ``utils/trading.tdx_trade_dates``）。

    只供 stale 逾期判定使用；失败返回 ``([], "stale_calendar_missing: ...")``，
    **不阻断 build**（调用方把警告写入报告，stale 判定退化为工作日近似）。
    ``services`` 为可选 M2 装配注入：其 ``providers.tdx`` 非空时用它取日历。
    """
    try:
        from dragon_quant.infra.market_calendar import tdx_trade_dates
        return list(tdx_trade_dates(_default_tdx(services), days=days)), None
    except Exception as exc:
        return [], f"stale_calendar_missing: {type(exc).__name__}: {exc}"


def sync_snapshots(trade_date: str, codes: Iterable[str], *, tdx=None,
                   db_path=None, now: Optional[str] = None,
                   services=None) -> dict:
    """日度观测：逐 code ``get_finance_info`` → UPSERT ``fin_observed_snapshots``。

    ``same_day_only``：``trade_date != today`` 直接拒绝（未观测到的日子永久缺失，
    不能用当前最新财务回填历史——那是前视）。
    ``services`` 为可选 M2 装配注入：其 ``providers.tdx`` 非空时使用它；显式
    ``tdx=`` 优先于 bundle。
    """
    codes = sorted({str(c) for c in (codes or []) if c})
    if not codes:
        raise ValueError("sync_snapshots 需要至少一个 code")
    if trade_date != _today():
        raise ValueError(
            f"财务观测仅当日可采（same_day_only）：trade_date={trade_date} 不是今天 {_today()}")
    tdx = tdx or _default_tdx(services)
    captured_at = now or format_ts(clock.now(SHANGHAI))
    rows: list[dict] = []
    errors: list[dict] = []
    updated_dates: dict[str, int] = {}
    for code in codes:
        try:
            info = tdx.get_finance_info(code)
            values = _named_values(info)
            if not any(field in values for field in HASH_FIELDS):
                raise RuntimeError("命名财务字段缺失（无法做值级 PIT 证明）")
            period = _match_period(code, values, db_path=db_path) \
                or _period_from_updated(info.get("updated_date"))
            updated = info.get("updated_date")
            rows.append({
                "code": code,
                "updated_date": int(updated) if updated is not None else None,
                "period": period,
                "values": values,
                "values_hash": values_hash(values),
                "captured_at": captured_at,
            })
            key = str(updated)
            updated_dates[key] = updated_dates.get(key, 0) + 1
        except Exception as exc:
            errors.append({"code": code, "error": f"{type(exc).__name__}: {exc}"})
    processed = fin_store.upsert_observed_snapshots(
        trade_date, rows, db_path=db_path) if rows else 0
    if not errors:
        status = "ok"
    elif processed:
        status = "partial"
    else:
        status = "error"
    return {
        "status": status,
        "trade_date": trade_date,
        "codes": len(codes),
        "ok": processed,
        "errors": errors,
        "updated_dates": updated_dates,
        "captured_at": captured_at,
    }


# ─── 同步：按报告期回填 ─────────────────────────────────────────────────────


def _period_in_range(period: str, *, periods, from_period, to_period) -> bool:
    if periods is not None and period not in set(periods):
        return False
    if from_period and period < from_period:
        return False
    if to_period and period > to_period:
        return False
    return True


def sync_backfill(*, periods: Optional[Sequence[str]] = None,
                  from_period: Optional[str] = None,
                  to_period: Optional[str] = None, tdx=None, force: bool = False,
                  host: Optional[str] = None, db_path=None,
                  now: Optional[str] = None, services=None) -> dict:
    """按报告期回填 gpcw 账本。

    幂等：同 ``(period, md5)`` 且状态 ok → 零下载（``force=True`` 强制重下）。
    md5 变化 = **追加新 vintage**（不覆盖旧行）；值未变的 code 不产生新行。
    ``services`` 为可选 M2 装配注入：其 ``providers.tdx`` 非空时使用它；显式
    ``tdx=`` 优先于 bundle。
    """
    tdx = tdx or _default_tdx(services)
    now_iso = now or format_ts(clock.now(SHANGHAI))
    files = tdx.get_financial_files(host=host)
    parsed = [(period, entry) for entry in files
              if (period := _period_from_filename(entry.get("filename") or ""))]
    parsed.sort(key=lambda item: item[0])
    window = sorted({period for period, _ in parsed})
    if (periods is None and from_period is None and to_period is None
            and len(window) > 12):
        # 方案 §1.2：默认只处理最近 12 个报告期，显式 --from-period 才扩大
        keep = set(window[-12:])
        parsed = [item for item in parsed if item[0] in keep]
        window = sorted(keep)
    downloaded = skipped = inserted = retracted = unchanged = 0
    observed_unmatched = 0
    failed: list[dict] = []
    handled: list[str] = []
    layouts: dict[str, str] = {}

    for period, entry in parsed:
        if not _period_in_range(
                period, periods=periods, from_period=from_period, to_period=to_period):
            continue
        md5 = entry["md5"]
        existing = [row for row in fin_store.list_report_files(
            report_period=period, db_path=db_path) if row["md5"] == md5]
        if existing and existing[0]["status"] in ("ok", "layout_unverified") \
                and not force:
            skipped += 1
            continue
        try:
            records = tdx.get_financial_records(entry["filename"], host=host)
        except Exception as exc:
            failed.append({"period": period, "md5": md5,
                           "error": f"{type(exc).__name__}: {exc}"})
            continue
        downloaded += 1
        handled.append(period)
        if not records:
            fin_store.upsert_report_file(
                period, filename=entry["filename"], md5=md5,
                filesize=entry.get("size"), first_seen_at=now_iso,
                status="parse_error", error="records 为空（不删除任何旧行）",
                db_path=db_path)
            failed.append({"period": period, "md5": md5, "error": "empty records"})
            continue

        num_fields = len(records[0].get("fields") or [])
        layout_key = f"gpcw_{num_fields}"
        calibration = fin_store.get_field_calibration(layout_key, db_path=db_path)
        if not calibration or calibration.get("status") != "verified":
            blob = gzip.compress(json.dumps(
                [[str(r.get("code")), list(r.get("fields") or [])] for r in records],
                ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
            fin_store.upsert_report_file(
                period, filename=entry["filename"], md5=md5,
                filesize=entry.get("size"), first_seen_at=now_iso,
                status="layout_unverified", records_count=len(records),
                raw_blob=blob,
                error=f"布局 {layout_key} 未校准（fail-closed，原始 float 已 gzip 落库）",
                db_path=db_path)
            layouts[layout_key] = "unverified"
            continue

        mapping = calibration.get("mapping") or {}
        layouts[layout_key] = "verified"
        latest = fin_store.latest_vintage_values(period, db_path=db_path)
        is_first = not fin_store.has_prior_records(period, md5, db_path=db_path)
        vintage_rows: list[dict] = []
        present_codes: set[str] = set()
        for record in records:
            code = str(record.get("code") or "")
            if code:
                present_codes.add(code)
            values = _map_fields(record.get("fields") or [], mapping)
            if not code or not values:
                continue
            previous = latest.get(code)
            if previous and _same_values(previous["values"], values):
                unchanged += 1
                continue
            digest = values_hash(values)
            observations = fin_store.list_observed_for_code(code, db_path=db_path)
            match = next((obs for obs in observations
                          if obs["values_hash"] == digest
                          and parse_local(obs["captured_at"]) <= parse_local(now_iso)),
                         None)
            if match is None and any(
                    (obs.get("period") or _period_from_updated(obs.get("updated_date"))) == period
                    for obs in observations):
                observed_unmatched += 1
            available_at, basis = resolve_record_available_at(
                period=period,
                observed_first_at=match["captured_at"] if match else None,
                vintage_first_seen=now_iso,
                is_first_vintage=is_first)
            vintage_rows.append({
                "code": code, "values": values,
                "available_at": available_at, "pit_basis": basis,
                "first_seen_at": now_iso,
            })

        result = fin_store.insert_report_vintage(
            period, vintage_rows, file_md5=md5, layout_key=layout_key,
            present_codes=present_codes, retracted_at=now_iso, db_path=db_path)
        inserted += result["inserted"]
        retracted += result["retracted"]
        fin_store.upsert_report_file(
            period, filename=entry["filename"], md5=md5,
            filesize=entry.get("size"), first_seen_at=now_iso, status="ok",
            records_count=len(records), db_path=db_path)

    return {
        "periods": sorted(set(handled)),
        "window": {
            "from_period": min(window) if window else None,
            "to_period": max(window) if window else None,
            "candidates": len(window),
        },
        "downloaded": downloaded,
        "skipped": skipped,
        "inserted": inserted,
        "unchanged": unchanged,
        "retracted": retracted,
        "observed_unmatched": observed_unmatched,
        "layouts": layouts,
        "failed": failed,
    }


# ─── 字段校准 ───────────────────────────────────────────────────────────────


def _median(values: list[float]) -> float:
    if not values:
        return float("inf")
    ordered = sorted(values)
    mid = len(ordered) // 2
    return (ordered[mid] if len(ordered) % 2
            else (ordered[mid - 1] + ordered[mid]) / 2.0)


def _index_errors(field: str, samples: list[tuple[list[float], dict]]) -> list[dict]:
    """每个索引在最优 scale 下的中位相对误差（按误差升序）。"""
    num_fields = len(samples[0][0])
    out: list[dict] = []
    for index in range(num_fields):
        best_scale = None
        best_err = float("inf")
        for scale in SCALES:
            errors = []
            for fields, named in samples:
                target = _finite(named.get(field))
                if target is None or target == 0:
                    continue
                value = _finite(fields[index]) if index < len(fields) else None
                if value is None:
                    continue
                errors.append(abs(value * scale - target) / abs(target))
            if len(errors) < MIN_SAMPLES:
                continue
            err = _median(errors)
            if err < best_err:
                best_err, best_scale = err, scale
        if best_scale is not None:
            out.append({"index": index, "scale": best_scale, "err": best_err})
    out.sort(key=lambda item: (item["err"], item["index"]))
    return out


def _solve_calibration(samples: list[tuple[list[float], dict]]) -> dict:
    """恒等式 + 层级不等式锚定字段映射（方案 §4.9.2 / §10-20）。"""
    num_fields = len(samples[0][0]) if samples else 0
    mapping: dict[str, dict] = {}
    confidence: dict[str, dict] = {}
    for field in MAPPED_FIELDS:
        candidates = _index_errors(field, samples)
        best = next((c for c in candidates
                     if c["err"] <= NUMERIC_TOLERANCE), None)
        if best is None:
            continue
        second = next((c for c in candidates
                       if c["index"] != best["index"]), None)
        lens = ((second["err"] - best["err"]) / max(second["err"], 1e-12)
                if second and math.isfinite(second["err"]) else 1.0)
        mapping[field] = {"index": best["index"], "scale": best["scale"]}
        confidence[field] = {
            "best_err": round(best["err"], 6),
            "second_err": (round(second["err"], 6)
                           if second and math.isfinite(second["err"]) else None),
            "lens": round(lens, 6),
            "sample_n": len(samples),
            "scale": _SCALE_NAMES.get(best["scale"], str(best["scale"])),
            "evidence": "numeric_match",
        }

    # 恒等式锚定：bps × 总股本(万股→股) ≈ 净资产
    identity_ok = False
    if {"meigujing_zichan", "jing_zichan", "zong_guben"} <= set(mapping):
        checks = 0
        for fields, _named in samples:
            values = _map_fields(fields, mapping)
            bps = values.get("meigujing_zichan")
            shares = values.get("zong_guben")
            assets = values.get("jing_zichan")
            if bps is None or shares is None or not assets:
                continue
            expected = bps * shares * 1e4
            if abs(expected - assets) / abs(assets) <= NUMERIC_TOLERANCE:
                checks += 1
        identity_ok = checks >= MIN_SAMPLES
        if identity_ok:
            for field in ("meigujing_zichan", "jing_zichan", "zong_guben"):
                confidence[field]["evidence"] = "identity_bps_times_shares"
                confidence[field]["identity_checks"] = checks

    # 层级不等式：净利润 ≤ 营业利润 ≤ 主营利润
    hierarchy_ok = False
    hierarchy_fields = ("jing_lirun", "yingye_lirun", "zhuying_lirun")
    if set(hierarchy_fields) <= set(mapping):
        checks = 0
        for fields, _named in samples:
            values = _map_fields(fields, mapping)
            net = values.get("jing_lirun")
            operating = values.get("yingye_lirun")
            main = values.get("zhuying_lirun")
            if net is None or operating is None or main is None:
                continue
            if (net <= operating * HIERARCHY_TOLERANCE
                    and operating <= main * HIERARCHY_TOLERANCE):
                checks += 1
        hierarchy_ok = checks >= MIN_SAMPLES
        if hierarchy_ok:
            for field in hierarchy_fields:
                confidence[field]["evidence"] = "hierarchy_inequality"
                confidence[field]["hierarchy_checks"] = checks
        # 净利润与税后利润近等值时按显式优先级择一（不靠 lens）
        if "jing_lirun" in confidence and "shuihou_lirun" in confidence:
            confidence["jing_lirun"].setdefault("note", "priority_net_profit_over_tax")

    has_evidence = all(
        field in mapping and field in confidence
        and confidence[field].get("evidence") in (
            "numeric_match", "identity_bps_times_shares", "hierarchy_inequality")
        for field in REQUIRED_CALIBRATION_FIELDS)
    status = "verified" if (has_evidence and (identity_ok or hierarchy_ok)) else "unverified"
    return {
        "layout_key": f"gpcw_{num_fields}",
        "num_fields": num_fields,
        "mapping": mapping,
        "confidence": confidence,
        "status": status,
        "sample_n": len(samples),
        "anchors": {"identity": identity_ok, "hierarchy": hierarchy_ok},
    }


def calibrate_field_map(*, sample: int = 20, tdx=None, db_path=None,
                        host: Optional[str] = None,
                        period: Optional[str] = None, services=None) -> dict:
    """用 ``get_finance_info`` 的命名值反推 gpcw 字段索引与单位并落表。

    未拿到独立证据 → ``status="unverified"``（财务因子 fail-closed 禁用）。
    ``services`` 为可选 M2 装配注入：其 ``providers.tdx`` 非空时使用它；显式
    ``tdx=`` 优先于 bundle。
    """
    tdx = tdx or _default_tdx(services)
    files = tdx.get_financial_files(host=host)
    candidates = [(p, item) for item in files
                  if (p := _period_from_filename(item.get("filename") or ""))]
    if period:
        candidates = [c for c in candidates if c[0] == period]
    if not candidates:
        raise ValueError(f"gpcw 文件清单为空或不含报告期 {period!r}")
    target_period, entry = max(candidates, key=lambda item: item[0])
    records = tdx.get_financial_records(entry["filename"], host=host)
    records = [r for r in records if r.get("fields")]
    if not records:
        raise ValueError(f"{entry['filename']} 无可用记录，无法校准")
    by_layout: dict[int, list[dict]] = {}
    for record in records:
        by_layout.setdefault(len(record["fields"]), []).append(record)
    layout_size = max(by_layout, key=lambda size: len(by_layout[size]))
    chosen = by_layout[layout_size][:max(1, int(sample))]

    samples: list[tuple[list[float], dict]] = []
    errors: list[dict] = []
    for record in chosen:
        code = str(record.get("code") or "")
        try:
            info = tdx.get_finance_info(code)
        except Exception as exc:
            errors.append({"code": code, "error": f"{type(exc).__name__}: {exc}"})
            continue
        named = _named_values(info)
        if named:
            samples.append((list(record["fields"]), named))
    if len(samples) < MIN_SAMPLES:
        raise ValueError(
            f"校准样本不足（可用 {len(samples)} < {MIN_SAMPLES}），无法锚定字段映射")

    payload = _solve_calibration(samples)
    payload["report_period"] = target_period
    payload["filename"] = entry["filename"]
    payload["errors"] = errors
    fin_store.upsert_field_calibration(
        payload["layout_key"], payload, db_path=db_path)
    return payload


# ─── as-of 展开（build 读取入口）────────────────────────────────────────────


def _trade_days_after(deadline: datetime, trade_date: str,
                      calendar: Iterable[str]) -> int:
    days = [d for d in (calendar or ())
            if d > deadline.date().isoformat() and d <= trade_date]
    if days:
        return len(days)
    # 无日历时退化为工作日近似（不引入额外日历实现）
    count = 0
    cursor = deadline.date() + timedelta(days=1)
    end = datetime.strptime(trade_date, "%Y-%m-%d").date()
    while cursor <= end:
        if cursor.weekday() < 5:
            count += 1
        cursor += timedelta(days=1)
    return count


def is_stale_period(period: str, trade_date: str,
                    calendar: Iterable[str] = ()) -> tuple[bool, int]:
    """``T > 下一期法定截止日 + 5 交易日`` → 逾期（旧期不得冒充最新）。"""
    next_deadline = parse_local(report_period_deadline(next_report_period(period)))
    if phase_ts(trade_date, "prep") <= next_deadline:
        return False, 0
    elapsed = _trade_days_after(next_deadline, trade_date, calendar)
    return elapsed > 5, elapsed


def _load_observed_fallback(trade_date: str, codes: Iterable[str], as_of: str,
                            db_path=None) -> dict[str, dict]:
    """账本无可用期时的观测回退：**跨观测日**取 captured_at ≤ T@prep 的最新一条。

    ``trade_date`` 只用于诊断；实现按 as_of 选取，因此「17:02 观测」不进当日
    16:40 的行，但次日 16:40 起可用（方案 §7 / §8.1）。
    """
    observed = fin_store.latest_observed_before(codes, as_of, db_path=db_path)
    out: dict[str, dict] = {}
    for code, row in observed.items():
        captured = row.get("captured_at")
        if not captured or parse_local(captured) > parse_local(as_of):
            continue
        values = row.get("values") or {}
        if not values:
            continue
        out[code] = {
            "period": row.get("period") or _period_from_updated(row.get("updated_date")),
            "values": values,
            "available_at": captured,
            "pit_basis": "observed",
            "layout_key": None,
            "file_md5": None,
            "captured_at": captured,
            "observed_trade_date": row.get("trade_date"),
        }
    return out


def _earliest_available(*, db_path=None) -> Optional[str]:
    """首个「当期 + P-4Q 且布局已校准、两者都已可用」的报告期可用日。

    运行期派生（方案 §4.1）：不写死日期，避免把未验证的历史深度当承诺。
    """
    periods = fin_store.get_report_periods(db_path=db_path)
    future = "9999-12-31T23:59:59"
    calibrated = {row["layout_key"] for row in
                  fin_store.list_field_calibrations(db_path=db_path)
                  if row.get("status") == "verified"}
    for period in periods:
        prior = prior_year_period(period)
        if prior not in periods:
            continue
        current = fin_store.get_period_asof(
            _period_codes(period, db_path=db_path), period, future, db_path=db_path)
        if not current:
            continue
        prior_rows = fin_store.get_period_asof(
            _period_codes(prior, db_path=db_path), prior, future, db_path=db_path)
        if not prior_rows:
            continue
        usable = [
            code for code, row in current.items()
            if code in prior_rows and (row.get("layout_key") in calibrated)
        ]
        if not usable:
            continue
        moments = [
            max(parse_local(current[code]["available_at"]),
                parse_local(prior_rows[code]["available_at"]))
            for code in usable
        ]
        return format_ts(min(moments))
    return None


def _period_codes(period: str, *, db_path=None) -> list[str]:
    return list(fin_store.latest_vintage_values(period, db_path=db_path))


def load_fin_source(trade_date: str, codes: Iterable[str], *, calendar: Iterable[str] = (),
                    pool_profile: Optional[dict] = None, db_path=None,
                    services=None) -> dict:
    """``source_rows`` 组装：as-of 展开 + cohort + 观测回退 + 逾期/校准门禁。

    返回结构（``builtin/financial.py`` 的输入契约）::

        {"status": "ok"|"no_report_yet"|"stale_period_skipped"|"field_map_unverified",
         "current": {code: {period, values, available_at, pit_basis, layout_key, file_md5}},
         "prior":   {code: {...}},
         "cohort":  {modal_period, n_total, n_cohort, n_mismatch, by_period,
                     excluded_profile},
         "observed": {code: {...}},
         "codes": (...), "as_of": phase_ts, "available_at": max(current),
         "earliest_available": date, "warnings": [...], "degraded": [...]}
    """
    codes = tuple(sorted({str(c) for c in (codes or []) if c}))
    calendar = tuple(calendar or ())
    as_of = format_ts(phase_ts(trade_date, "prep"))
    warnings: list[str] = []
    degraded: list[str] = []
    if not calendar:
        # stale 逾期判定必须有真实交易日历；缺失时退化为工作日近似并显式告警。
        warnings.append("stale_calendar_missing")
    elif min(calendar) > trade_date or max(calendar) < trade_date:
        # 日历非空但不覆盖当日（历史 build 超出日历窗口）→ 同样视为缺失。
        warnings.append(
            f"stale_calendar_missing: coverage({min(calendar)}~{max(calendar)} "
            f"不含 {trade_date})")

    # 当日未同步观测 → 先采集（失败不阻断 build，仅观测回退不可用）
    if trade_date == _today() and codes and not fin_store.has_observed_snapshots(
            trade_date, db_path=db_path):
        try:
            sync_snapshots(trade_date, codes, db_path=db_path,
                           services=services)
        except Exception as exc:
            degraded.append(f"snapshot_sync_failed: {type(exc).__name__}: {exc}")

    ledger = fin_store.get_report_asof(codes, as_of, db_path=db_path)
    unverified_layouts: set[str] = set()
    unverified_codes = 0
    current: dict[str, dict] = {}
    for code, row in ledger.items():
        layout_key = row.get("layout_key")
        calibration = (fin_store.get_field_calibration(layout_key, db_path=db_path)
                       if layout_key else None)
        if not calibration or calibration.get("status") != "verified":
            unverified_layouts.add(str(layout_key))
            unverified_codes += 1
            continue
        current[code] = {**row, "period": row["report_period"]}
    retracted_count = fin_store.count_retracted(codes, as_of, db_path=db_path)
    if unverified_codes:
        warnings.append(f"layout_unverified: {unverified_codes} 只布局未校准已丢弃")

    observed = _load_observed_fallback(trade_date, codes, as_of, db_path=db_path)
    for code in list(observed):
        if code in current:
            observed.pop(code)

    if not current and not observed:
        if unverified_layouts:
            status = "field_map_unverified"
        else:
            status = "no_report_yet"
        return {
            "status": status,
            "reason": (f"布局未校准: {sorted(unverified_layouts)}（财务因子 fail-closed）"
                       if status == "field_map_unverified"
                       else f"{trade_date} 16:40 前无可用报告期"),
            "current": {}, "prior": {}, "observed": {},
            "cohort": {}, "codes": (), "as_of": as_of, "available_at": None,
            "earliest_available": _earliest_available(db_path=db_path),
            "warnings": warnings, "degraded": degraded,
            "layout_unverified": unverified_codes,
            "retracted_count": retracted_count,
        }

    # 严格同报告期 cohort：只保留众数报告期；观测回退仅在账本无期时使用
    by_period: dict[str, list[str]] = {}
    for code, row in current.items():
        if row.get("period"):
            by_period.setdefault(row["period"], []).append(code)
    for code, row in observed.items():
        if row.get("period"):
            by_period.setdefault(row["period"], []).append(code)
    if not by_period:
        return {
            "status": "no_report_yet",
            "reason": "账本/观测均无法确定报告期",
            "current": {}, "prior": {}, "observed": {}, "cohort": {},
            "codes": (), "as_of": as_of, "available_at": None,
            "earliest_available": _earliest_available(db_path=db_path),
            "warnings": warnings, "degraded": degraded,
            "layout_unverified": unverified_codes,
            "retracted_count": retracted_count,
        }
    modal_period = max(by_period, key=lambda p: (len(by_period[p]), p))
    n_total = sum(len(v) for v in by_period.values())
    n_cohort = len(by_period[modal_period])
    excluded = [code for period, members in by_period.items()
                if period != modal_period for code in members]
    excluded_profile = _cohort_excluded_profile(
        excluded, pool_profile or {}, by_period, modal_period,
        included=by_period[modal_period])
    if excluded:
        warnings.append("cohort_selection_bias")

    stale, stale_days = is_stale_period(modal_period, trade_date, calendar)
    if stale:
        return {
            "status": "stale_period_skipped",
            "reason": (f"报告期 {modal_period} 逾期 {stale_days} 个交易日"
                       "（超过下一期截止日 + 5 日），不冒充最新"),
            "current": {}, "prior": {}, "observed": {}, "cohort": {},
            "codes": (), "as_of": as_of, "available_at": None,
            "earliest_available": _earliest_available(db_path=db_path),
            "warnings": warnings, "degraded": degraded,
            "layout_unverified": unverified_codes,
            "retracted_count": retracted_count,
        }

    cohort_codes = [c for c in by_period[modal_period]]
    current_cohort = {c: current[c] for c in cohort_codes if c in current}
    observed_cohort = {c: observed[c] for c in cohort_codes if c in observed}
    prior = {
        code: {**row, "period": row["report_period"]}
        for code, row in fin_store.get_period_asof(
            cohort_codes, prior_year_period(modal_period), as_of,
            db_path=db_path).items()
    }

    stale_days_by_code = {c: stale_days for c in cohort_codes}
    resolved = {**current_cohort, **observed_cohort}
    available = max(parse_local(row["available_at"]) for row in resolved.values())
    return {
        "status": "ok",
        "current": current_cohort,
        "prior": prior,
        "observed": observed_cohort,
        "cohort": {
            "modal_period": modal_period,
            "n_total": n_total,
            "n_cohort": n_cohort,
            "n_mismatch": len(excluded),
            "by_period": {p: len(v) for p, v in sorted(by_period.items())},
            "cohort_policy": "modal",
            "excluded_profile": excluded_profile,
            "stale_days": stale_days_by_code,
        },
        "codes": tuple(cohort_codes),
        "as_of": as_of,
        "available_at": format_ts(available),
        "earliest_available": _earliest_available(db_path=db_path),
        "finance_data_versions": _finance_versions(current_cohort, prior),
        "layout_unverified": unverified_codes,
        "retracted_count": retracted_count,
        "warnings": warnings,
        "degraded": degraded,
    }


def _finance_versions(current: dict[str, dict], prior: dict[str, dict]) -> list[dict]:
    """本次 as-of 实际读到的 ``(period, file_md5, first_seen_at, available_at)``。

    同一期文件被多个 code 引用时取最早 available_at / first_seen_at，并把观测
    回退（无 md5）排除——它没有「期文件版本」语义。
    """
    merged: dict[tuple, dict] = {}
    for record in [*current.values(), *prior.values()]:
        period = record.get("period") or record.get("report_period")
        md5 = record.get("file_md5")
        if not period or not md5:
            continue
        key = (period, md5)
        entry = merged.setdefault(key, {
            "period": period, "file_md5": md5,
            "first_seen_at": record.get("first_seen_at"),
            "available_at": record.get("available_at"),
        })
        for field in ("first_seen_at", "available_at"):
            value = record.get(field)
            if value and (not entry.get(field) or value < entry[field]):
                entry[field] = value
    return sorted(merged.values(), key=lambda item: (item["period"], item["file_md5"]))


def _cohort_excluded_profile(excluded: list[str], pool_profile: dict,
                             by_period: dict[str, list[str]],
                             modal_period: str,
                             included: Optional[list[str]] = None) -> dict:
    """被 cohort 剔除者的画像（方案 §4.3.1 / R25：不是纯覆盖率代价）。

    额外量化（P1 补齐）：
      - ``composite_percentile``：被剔除者的综合分在**整个当日池**中的分位
        （0-100，并列取平均名次）；
      - ``size_proxy``：被剔除者与 cohort 内的 ``market_cap`` 中位数对比
        （规模代理；market_cap 缺失时该块为 ``None``）。
    """
    from .stats import percentile_rank

    included = list(included or by_period.get(modal_period, []))
    composites = []
    excluded_sizes = []
    included_sizes = []
    limit_up = 0
    for code in excluded:
        profile = pool_profile.get(code) or {}
        composite = _finite(profile.get("composite"))
        if composite is not None:
            composites.append(composite)
        size = _finite(profile.get("market_cap"))
        if size is not None:
            excluded_sizes.append(size)
        if profile.get("is_true_dragon"):
            limit_up += 1
    for code in included:
        size = _finite((pool_profile.get(code) or {}).get("market_cap"))
        if size is not None:
            included_sizes.append(size)

    pool_composites = [
        value for value in (_finite((pool_profile.get(code) or {}).get("composite"))
                            for code in pool_profile)
        if value is not None
    ]
    percentiles = [percentile_rank(pool_composites, value) for value in composites]
    percentiles = [value for value in percentiles if value is not None]
    return {
        "count": len(excluded),
        "codes": sorted(excluded)[:20],
        "by_period": {p: len(v) for p, v in sorted(by_period.items()) if p != modal_period},
        "composite": ({
            "n": len(composites),
            "min": round(min(composites), 4),
            "median": round(_median(composites), 4),
            "max": round(max(composites), 4),
        } if composites else None),
        "composite_percentile": ({
            "n": len(percentiles),
            "median": round(_median(percentiles), 4),
            "min": round(min(percentiles), 4),
            "max": round(max(percentiles), 4),
        } if percentiles else None),
        "size_proxy": ({
            "field": "market_cap",
            "n": len(excluded_sizes),
            "median": round(_median(excluded_sizes), 4),
            "included_median": (round(_median(included_sizes), 4)
                                if included_sizes else None),
        } if excluded_sizes else None),
        "true_dragon_ratio": (round(limit_up / len(excluded), 4) if excluded else None),
        "note": ("composite 取自当日 scan 池；披露时点与质地相关，剔除组非随机"),
    }


# ─── 状态 ──────────────────────────────────────────────────────────────────


def status(*, db_path=None) -> dict:
    """``factor fin status``：账本 / 校准 / 观测 / PIT 口径 / pandas 依赖来源。"""
    store = fin_store.finance_status(db_path=db_path)
    periods = store.get("periods") or []
    calibrated = fin_store.list_field_calibrations(db_path=db_path)
    latest_period = periods[-1] if periods else None
    next_deadline = (report_period_deadline(next_report_period(latest_period))
                     if latest_period else None)
    basis = store["report_records"]["by_basis"]
    total_basis = sum(basis.values())
    observed_ratio = (round(basis.get("observed", 0) / total_basis, 4)
                      if total_basis else None)
    return {
        "ok": True,
        **store,
        "latest_period": latest_period,
        "next_deadline": next_deadline,
        "overdue": (parse_local(next_deadline) < clock.now(SHANGHAI)
                    if next_deadline else None),
        "calibrations": calibrated,
        "verified_layouts": [c["layout_key"] for c in calibrated
                             if c.get("status") == "verified"],
        "pit_basis_mix": {
            "counts": basis,
            "observed_ratio": observed_ratio,
            "restatement_unverifiable": basis.get("statutory_backfilled", 0),
        },
        "dependencies": {
            "pandas": "transitive(tmdx)",
            "note": "项目代码零 import pandas；DataFrame 仅经 df.to_dict('records') 适配",
        },
        "schedule": "每日收盘后 factor fin sync --snapshot（或 factor build 内置补齐）",
    }
