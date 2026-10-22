"""因子层 CLI 编排（build / list / query / status）。

只做参数归一、逐日调用 engine、组装稳定 JSON 契约；
错误以 ``ValueError``（参数）或异常（运行时）上抛，由 CLI 层决定输出通道与退出码。
"""

from __future__ import annotations

from typing import Iterable, Optional

from dragon_quant.factors import engine, storage as fstore
from dragon_quant.factors.registry import (
    FACTOR_SPECS,
    compute_version_info,
    resolve_specs,
    spec_names,
)
from dragon_quant.storage import db as _db


def normalize_date(raw: str) -> str:
    """YYYYMMDD / YYYY-MM-DD / YYYY/MM/DD → YYYY-MM-DD；非法格式抛 ValueError。"""
    text = str(raw or "").strip()
    if len(text) == 8 and text.isdigit():
        text = f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    elif len(text) == 10 and text[4] == "/" and text[7] == "/":
        text = f"{text[:4]}-{text[5:7]}-{text[8:10]}"
    from datetime import datetime

    try:
        return datetime.strptime(text, "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError as exc:
        raise ValueError(
            f"无效日期: {raw!r}（支持 YYYYMMDD / YYYY-MM-DD / YYYY/MM/DD）") from exc


def parse_only(raw: Optional[Iterable[str] | str]) -> Optional[list[str]]:
    """``--only`` / ``--name`` 解析：逗号分隔字符串 / 可迭代 → 去空去重名字列表。"""
    if raw is None:
        return None
    if isinstance(raw, str):
        items = raw.split(",")
    else:
        items = []
        for item in raw:
            items.extend(str(item).split(","))
    names = [item.strip() for item in items if item and item.strip()]
    return names or None


def _resolve_dates(date: Optional[str], date_from: Optional[str],
                   date_to: Optional[str]) -> tuple[list[str], str]:
    if date:
        return [normalize_date(date)], "date"
    if not date_from or not date_to:
        raise ValueError("build 需要 --date，或同时指定 --from 与 --to")
    start = normalize_date(date_from)
    end = normalize_date(date_to)
    if start > end:
        raise ValueError(f"--from({start}) 不能晚于 --to({end})")
    dates = _db.list_scan_dates(date_from=start, date_to=end)
    return dates, "range"


def build(date: Optional[str] = None, date_from: Optional[str] = None,
          date_to: Optional[str] = None, only=None, *, db_path=None) -> dict:
    """单日 / 区间 build。

    区间按「区间内有扫描记录的交易日」逐日展开（无扫描的日期无法产出因子，
    不伪造空跑记录）；每日独立事务、独立审计报告，单日失败不影响其他日。

    财务因子需要真实交易日历做 stale 逾期判定：只有本次确实构建 ``fin`` 因子时
    才经 ``finance.load_trade_calendar`` 取 TDX 指数日K（获取失败 → 空日历 +
    ``stale_calendar_missing`` 警告，绝不阻断 build）。
    """
    names = parse_only(only)
    dates, mode = _resolve_dates(date, date_from, date_to)
    calendar: tuple[str, ...] = ()
    warnings: list[str] = []
    specs = resolve_specs(names, enabled_only=names is None)
    if any(spec.source == "fin" for spec in specs):
        from datetime import datetime
        from zoneinfo import ZoneInfo

        from dragon_quant.factors import finance
        # 日历必须覆盖到最早构建日（+ 缓冲），否则 stale 逾期日数被截断。
        today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
        earliest = dates[0] if dates else today.isoformat()
        span = (today - datetime.strptime(earliest, "%Y-%m-%d").date()).days
        loaded, error = finance.load_trade_calendar(days=max(180, span + 60))
        calendar = tuple(loaded)
        if error:
            warnings.append(error)
    reports = [
        engine.build_date(trade_date, names=names, db_path=db_path,
                          calendar=calendar)
        for trade_date in dates
    ]
    if mode == "range" and not dates:
        warnings.append("no_scans_in_range: 区间内没有扫描记录")
    ok = all(report.get("ok") for report in reports) if reports else mode == "range"
    return {
        "ok": ok,
        "mode": mode,
        "dates": dates,
        "build_ids": [report.get("build_id") for report in reports],
        "reports": reports,
        "warnings": warnings,
    }


def list_factors() -> list[dict]:
    """当前代码注册表（代码是唯一真值；不读 DB）。"""
    result = []
    for spec in sorted(FACTOR_SPECS, key=lambda s: s.name):
        version, version_source = compute_version_info(spec)
        result.append({
            "name": spec.name,
            "label": spec.label,
            "version": version,
            "version_source": version_source,
            "source": spec.source,
            "frequency": spec.frequency,
            "direction": spec.direction,
            "unit": spec.unit,
            "available_phase": spec.available_phase,
            "pit_rule": spec.pit_rule,
            "lookback": spec.lookback,
            "default_enabled": spec.default_enabled,
            "depends_on": list(spec.depends_on),
            "valid_from": spec.valid_from,
            "deprecated_at": spec.deprecated_at,
            "description": spec.description,
        })
    return result


def query(*, date: Optional[str] = None, date_from: Optional[str] = None,
          date_to: Optional[str] = None, name: Optional[str] = None,
          code: Optional[str] = None, version: Optional[str] = None,
          db_path=None) -> dict:
    """点查（``--date``）或单因子区间时序（``--from/--to + --name``）。"""
    if name:
        # 提前校验因子名，避免返回空集被误读为「该因子无值」
        if name not in spec_names():
            raise ValueError(f"未知因子: {name!r}（合法值 {spec_names()}）")

    if date:
        trade_date = normalize_date(date)
        codes = [code] if code else None
        names = [name] if name else None
        payload = fstore.get_day_values(
            trade_date, names=names, codes=codes, version=version, db_path=db_path)
        return {
            "ok": True,
            "mode": "day",
            "trade_date": trade_date,
            **payload,
        }

    if not date_from or not date_to:
        raise ValueError("query 需要 --date，或同时指定 --from 与 --to")
    if not name:
        raise ValueError("区间 query 必须指定 --name（单因子时序）")
    start = normalize_date(date_from)
    end = normalize_date(date_to)
    if start > end:
        raise ValueError(f"--from({start}) 不能晚于 --to({end})")
    codes = [code] if code else None
    payload = fstore.get_series(
        name, codes, start, end, version=version, db_path=db_path)
    return {"ok": True, "mode": "series", **payload}


def status(*, db_path=None) -> dict:
    """存储状态 + 代码注册表现状 + 已注册版本映射。"""
    store = fstore.status(db_path=db_path)
    registered = fstore.resolve_versions(spec_names(), db_path=db_path)
    factors = []
    for spec in sorted(FACTOR_SPECS, key=lambda s: s.name):
        version, version_source = compute_version_info(spec)
        factors.append({
            "name": spec.name,
            "version": version,
            "version_source": version_source,
            "default_enabled": spec.default_enabled,
            "registered_version": registered.get(spec.name),
        })
    return {
        "ok": True,
        **store,
        "registered_versions": registered,
        "factors": factors,
    }


# ─── 财务链路（P0.5，唯一触网点在 factors/finance.py）─────────────────────


def _today() -> str:
    from datetime import datetime
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()


def _candidate_codes(trade_date: str) -> list[str]:
    scan = engine.canonical_scan(trade_date)
    if not scan:
        raise ValueError(f"{trade_date} 无 v2 扫描记录，无法确定财务观测候选池")
    rows = _db.get_scan_stocks(scan["id"]) or []
    codes = sorted({row["code"] for row in rows if row.get("code")})
    if not codes:
        raise ValueError(f"{trade_date} 扫描 {scan['id']} 无候选标的")
    return codes


def fin_sync(*, snapshot: bool = False, backfill: bool = False,
             date: Optional[str] = None, from_period: Optional[str] = None,
             to_period: Optional[str] = None, force: bool = False,
             host: Optional[str] = None, db_path=None) -> dict:
    """``factor fin sync``：日度观测（当日）或按报告期回填（可历史）。"""
    if snapshot == backfill:
        raise ValueError("fin sync 需要且只能指定 --snapshot 或 --backfill")
    from dragon_quant.factors import finance

    if snapshot:
        trade_date = normalize_date(date) if date else _today()
        if trade_date != _today():
            raise ValueError(
                f"财务观测仅当日可采（same_day_only）：{trade_date} != 今天 {_today()}")
        codes = _candidate_codes(trade_date)
        payload = finance.sync_snapshots(trade_date, codes, db_path=db_path)
        return {"ok": payload.get("status") != "error", "mode": "snapshot",
                **payload}

    if from_period and to_period and normalize_date(from_period) > normalize_date(to_period):
        raise ValueError(f"--from-period({from_period}) 不能晚于 --to-period({to_period})")
    payload = finance.sync_backfill(
        periods=None,
        from_period=normalize_date(from_period) if from_period else None,
        to_period=normalize_date(to_period) if to_period else None,
        force=force, host=host, db_path=db_path)
    ok = not payload.get("failed") or payload.get("inserted", 0) > 0
    return {"ok": ok, "mode": "backfill", **payload}


def fin_calibrate(*, sample: int = 20, period: Optional[str] = None,
                  host: Optional[str] = None, db_path=None) -> dict:
    from dragon_quant.factors import finance

    if int(sample) < 1:
        raise ValueError("--sample 必须 ≥ 1")
    payload = finance.calibrate_field_map(
        sample=int(sample), period=normalize_date(period) if period else None,
        host=host, db_path=db_path)
    return {"ok": payload.get("status") == "verified", "mode": "calibrate", **payload}


def fin_status(*, db_path=None) -> dict:
    from dragon_quant.factors import finance
    return finance.status(db_path=db_path)


# ─── 矩阵导出 / 因子评估（P1）──────────────────────────────────────────────


def export_matrix(date_from: str, date_to: str, *, names, version=None,
                  codes=None, clean=None, fmt: str = "csv",
                  out_path=None, db_path=None) -> dict:
    """``factor export``：长表 → ``date × code`` 矩阵 + sidecar。"""
    from dragon_quant.factors.matrix import export_matrix as _export
    return _export(
        date_from, date_to, names=names, version=version, codes=codes,
        clean=clean, fmt=fmt, out_path=out_path, db_path=db_path)


def evaluate_factor(name: str, date_from: str, date_to: str, *, data,
                    horizon: int = 1, groups: int = 2, version=None,
                    min_days: int = 20, min_cross_section: int = 10,
                    pit_policy: str = "strict", db_path=None) -> dict:
    """``factor eval``：RankIC / ICIR / NW-t / 分组 / 覆盖率 / PIT 口径。"""
    from dragon_quant.factors.evaluation import evaluate_factor as _evaluate
    payload = _evaluate(
        name, date_from, date_to, horizon=horizon, groups=groups,
        version=version, data=data, min_days=min_days,
        min_cross_section=min_cross_section, pit_policy=pit_policy,
        db_path=db_path)
    # CLI 以 ok 决定退出码；insufficient_data 是有效结论（exit 0），不是失败。
    payload.setdefault("ok", True)
    return payload
