"""因子矩阵导出（方案 §4.7）：长表 → ``date × code``，CSV / JSON + sidecar。

派生视图，不是主存储：只读 ``factor_values`` + ``factor_registry``，不写因子表。
清洗（``CleanConfig``）**只在当日截面内**做——绝不使用全样本均值/标准差，
否则会引入前视偏差（调研 §4.3）。

清洗口径：
  - ``winsorize="mad"``：``median ± 3 × 1.4826 × MAD``；``MAD == 0`` 时
    **跳过缩尾**并记 ``winsorize_skipped="mad_zero"``（绝不除零、绝不回退全样本分位）；
  - ``winsorize="quantile"``：按 ``limits`` 当日截面分位缩尾；
  - ``normalize="rank"``：``(avg_rank / n - 0.5) × 3.46``，并列取平均名次；
  - ``normalize="zscore"``：当日截面均值/标准差（总体方差）；标准差为 0 时跳过并记
    ``normalize_skipped="zero_std"``；
  - ``missing="drop"``：丢掉任一请求因子缺失的 ``(date, code)`` 行（complete-case）。
"""

from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Optional
from zoneinfo import ZoneInfo

from . import stats as fstats
from . import storage as fstore
from .registry import get_spec
from dragon_quant.runtime import clock

SHANGHAI = ZoneInfo("Asia/Shanghai")
PIPELINE_VERSION = "factor-matrix/1"
MAD_SCALE = 1.4826
MAD_K = 3.0
RANK_SCALE = 3.46

MISSING_MODES = ("nan", "drop")
WINSORIZE_MODES = ("none", "mad", "quantile")
NORMALIZE_MODES = ("none", "rank", "zscore")
FORMATS = ("csv", "json")


@dataclass(frozen=True)
class CleanConfig:
    """清洗配置；默认值对应「标准研究视图」（MAD 缩尾 + rank 归一）。"""

    missing: str = "nan"
    winsorize: str = "mad"
    limits: tuple = (0.05, 0.95)
    normalize: str = "rank"
    max_stale: int = 0


def identity_clean() -> CleanConfig:
    """原始值视图（``factor export`` 默认，只有 ``--clean`` 才清洗）。"""
    return CleanConfig(missing="nan", winsorize="none", normalize="none")


def validate_clean(clean: CleanConfig) -> None:
    if clean.missing not in MISSING_MODES:
        raise ValueError(f"missing 非法: {clean.missing!r}（合法值 {MISSING_MODES}）")
    if clean.winsorize not in WINSORIZE_MODES:
        raise ValueError(f"winsorize 非法: {clean.winsorize!r}（合法值 {WINSORIZE_MODES}）")
    if clean.normalize not in NORMALIZE_MODES:
        raise ValueError(f"normalize 非法: {clean.normalize!r}（合法值 {NORMALIZE_MODES}）")
    low, high = clean.limits
    if clean.winsorize == "quantile" and not (0.0 <= low < high <= 1.0):
        raise ValueError(f"limits 非法: {clean.limits!r}（应满足 0 ≤ low < high ≤ 1）")
    if not isinstance(clean.max_stale, int) or clean.max_stale < 0:
        raise ValueError("max_stale 必须是非负整数")


def _normalize_date(raw: str) -> str:
    from .service import normalize_date
    return normalize_date(raw)


def _now() -> str:
    return clock.now(SHANGHAI).strftime("%Y-%m-%dT%H:%M:%S")


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _clean_cross_section(values: list[tuple[str, float]], config: CleanConfig,
                         skipped: dict) -> list[tuple[str, float]]:
    """对单日单因子的截面值做缩尾 + 归一（保持输入顺序）。"""
    if not values:
        return values
    working = list(values)

    if config.winsorize == "mad":
        sample = [value for _, value in working]
        centre = fstats.median(sample)
        mad = fstats.median([abs(value - centre) for value in sample])
        if centre is None:
            pass
        elif not mad:
            skipped["winsorize_skipped"] = "mad_zero"
        else:
            spread = MAD_K * MAD_SCALE * mad
            working = [(code, _clamp(value, centre - spread, centre + spread))
                       for code, value in working]
    elif config.winsorize == "quantile":
        ordered = sorted(value for _, value in working)
        low = fstats.quantile(ordered, config.limits[0])
        high = fstats.quantile(ordered, config.limits[1])
        if low is not None and high is not None:
            working = [(code, _clamp(value, low, high)) for code, value in working]

    if config.normalize == "rank":
        size = len(working)
        if size < 2:
            # 单只截面不含截面信息，rank 归一没有意义（避免输出 ±1.73 极值）。
            skipped["normalize_skipped"] = "insufficient_cross_section"
            return working
        ranks = fstats.rankdata([value for _, value in working])
        working = [
            (code, (rank / size - 0.5) * RANK_SCALE)
            for (code, _), rank in zip(working, ranks)
        ]
    elif config.normalize == "zscore":
        sample = [value for _, value in working]
        centre = fstats.mean(sample)
        spread = fstats.stdev(sample, ddof=0)
        if centre is None or spread is None:
            pass
        elif spread == 0:
            skipped["normalize_skipped"] = "zero_std"
        else:
            working = [(code, (value - centre) / spread) for code, value in working]
    return working


def _write_csv(path: Path, dates: list[str], codes: list[str], columns: list[str],
               matrix: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["date", "code", *columns])
        for trade_date in dates:
            for code in codes:
                row = matrix.get(trade_date, {}).get(code)
                if row is None:
                    continue
                writer.writerow([
                    trade_date, code,
                    *("" if row.get(name) is None else format(row[name], ".12g")
                      for name in columns),
                ])


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                               allow_nan=False, indent=2), encoding="utf-8")


def export_matrix(date_from: str, date_to: str, *, names: Iterable[str],
                  version: Optional[str] = None,
                  codes: Optional[Iterable[str]] = None,
                  clean: Optional[CleanConfig] = None,
                  fmt: str = "csv", out_path: Optional[str] = None,
                  db_path=None) -> dict:
    """导出因子矩阵；``clean=None`` 等同「原始值」（方案 §4.7 默认）。

    需要清洗时显式传 ``CleanConfig()``（MAD + rank）或自定义配置。
    """
    start = _normalize_date(date_from)
    end = _normalize_date(date_to)
    if start > end:
        raise ValueError(f"--from({start}) 不能晚于 --to({end})")
    if fmt not in FORMATS:
        raise ValueError(f"fmt 非法: {fmt!r}（合法值 {FORMATS}）")
    config = clean if clean is not None else identity_clean()
    validate_clean(config)

    requested = [name.strip() for name in (names or []) if name and name.strip()]
    if not requested:
        raise ValueError("export 至少需要一个 --name")
    for name in requested:
        get_spec(name)          # 提前校验：未知因子立即报错，不返回空矩阵

    resolved: dict[str, str] = {}
    missing_versions: list[str] = []
    for name in requested:
        if version:
            resolved[name] = version
        else:
            found = fstore.resolve_versions([name], db_path=db_path)
            if name in found:
                resolved[name] = found[name]
            else:
                missing_versions.append(name)
    pairs = [(name, resolved[name]) for name in requested if name in resolved]
    rows = fstore.get_matrix_rows(
        start, end, names=requested, version_pairs=pairs, codes=codes,
        db_path=db_path) if pairs else []
    if version:
        # 显式版本既可能与 --code 过滤后的 rows 不相交；先看注册表是否存在
        # (name, version)，避免把「已注册但本次过滤无行」误报为 missing_versions。
        registered = {
            (row["factor_name"], row["factor_version"])
            for row in fstore.list_registry(names=requested, db_path=db_path)
        }
        present_names = {row["factor_name"] for row in rows}
        for name in requested:
            if name in present_names or (name, version) in registered:
                continue
            if name not in missing_versions:
                missing_versions.append(name)

    dates = sorted({row["trade_date"] for row in rows})
    # 用户显式给了 codes 时保留其全集（缺失当天就是空），否则只取出现过的 code
    if codes:
        code_list = sorted({str(code) for code in codes if code})
    else:
        code_list = sorted({row["code"] for row in rows})

    matrix: dict[str, dict[str, dict[str, Optional[float]]]] = {}
    present: dict[str, set[str]] = {name: set() for name in requested}
    for row in rows:
        trade_date = row["trade_date"]
        code = row["code"]
        matrix.setdefault(trade_date, {}).setdefault(code, {})[row["factor_name"]] = \
            float(row["value"])
        present.setdefault(row["factor_name"], set()).add(f"{trade_date}|{code}")

    coverage_by_date: dict[str, dict[str, float]] = {}
    for trade_date in dates:
        day_codes = set(matrix.get(trade_date, {}))
        denominator = len(day_codes) or 1
        coverage_by_date[trade_date] = {
            name: round(sum(1 for code in day_codes
                            if matrix[trade_date].get(code, {}).get(name) is not None)
                        / denominator, 6)
            for name in requested
        }

    total_cells = len(dates) * len(code_list)
    missing_rate_by_factor = {
        name: (round(1.0 - len(present.get(name, ())) / total_cells, 6)
               if total_cells else 0.0)
        for name in requested
    }

    skipped: dict[str, object] = {
        "winsorize_skipped": None,
        "normalize_skipped": None,
        "winsorize_skipped_dates": [],
        "normalize_skipped_dates": [],
        "dropped_rows": 0,
        "dropped_codes": [],
    }
    cleaned = config.winsorize != "none" or config.normalize != "none" \
        or config.missing == "drop"
    if cleaned:
        for trade_date in dates:
            for name in requested:
                pairs_in_day = [
                    (code, matrix[trade_date][code][name])
                    for code in sorted(matrix.get(trade_date, {}))
                    if matrix[trade_date][code].get(name) is not None
                ]
                day_skipped: dict[str, object] = {}
                result = _clean_cross_section(pairs_in_day, config, day_skipped)
                for code, value in result:
                    matrix[trade_date][code][name] = value
                for key in ("winsorize_skipped", "normalize_skipped"):
                    if day_skipped.get(key):
                        skipped[key] = day_skipped[key]
                        skipped[f"{key}_dates"].append(trade_date)
        if config.missing == "drop":
            dropped: list[str] = []
            for trade_date in dates:
                for code in list(matrix.get(trade_date, {})):
                    row = matrix[trade_date][code]
                    if any(row.get(name) is None for name in requested):
                        matrix[trade_date].pop(code)
                        dropped.append(f"{trade_date}:{code}")
            skipped["dropped_rows"] = len(dropped)
            skipped["dropped_codes"] = dropped[:20]

    generated_at = _now()
    build_ids = fstore.list_build_ids(start, end, db_path=db_path)
    meta = {
        "dates": dates,
        "codes": code_list,
        "columns": requested,
        "resolved_versions": resolved,
        "missing_versions": missing_versions,
        "clean": asdict(config),
        "missing_rate_by_factor": missing_rate_by_factor,
        "coverage_by_date": coverage_by_date,
        "build_ids": build_ids,
        "generated_at": generated_at,
        "pipeline_version": PIPELINE_VERSION,
        "winsorize_skipped": skipped["winsorize_skipped"],
        "normalize_skipped": skipped["normalize_skipped"],
        "winsorize_skipped_dates": sorted(set(skipped["winsorize_skipped_dates"])),
        "normalize_skipped_dates": sorted(set(skipped["normalize_skipped_dates"])),
        "dropped_rows": skipped["dropped_rows"],
        "dropped_codes": skipped["dropped_codes"],
    }

    warnings: list[str] = []
    if not dates:
        warnings.append("no_factor_values: 区间内没有可导出的因子值")
    if missing_versions:
        warnings.append(f"missing_versions: {missing_versions}（未注册，未导出）")

    out_file = meta_file = None
    if out_path:
        target = Path(out_path).expanduser()
        if fmt == "csv":
            _write_csv(target, dates, code_list, requested, matrix)
        else:
            _write_json(target, {"dates": dates, "codes": code_list,
                                 "columns": requested, "values": matrix})
        meta_target = Path(str(target) + ".meta.json")
        _write_json(meta_target, meta)
        out_file, meta_file = str(target), str(meta_target)

    return {
        "ok": True,
        "mode": "export",
        "date_from": start,
        "date_to": end,
        **meta,
        "matrix": matrix,
        "out_path": out_file,
        "meta_path": meta_file,
        "warnings": warnings,
    }
