"""因子层存储门面（领域语义 → ``storage.factors`` 的 SQL 层）。

分层（方案 §3.4）：
  - 本模块属 ``factors/*``，可 import ``storage/*``；反向禁止；
  - 只做 dataclass ↔ 行 dict 转换与返回结构组装，不写 SQL；
  - 所有对外 API 都显式返回 ``resolved_versions``（版本=None 时绝不静默）。
"""

from __future__ import annotations

import json
from typing import Iterable, Optional

from dragon_quant.factors.types import FactorValue
from dragon_quant.storage import factors as _sql


def replace_factor_day(trade_date: str, factor_name: str, factor_version: str,
                       values: Iterable[FactorValue], *, db_path=None) -> int:
    """整日整因子原子替换（空结果也 DELETE）。"""
    rows = [
        {
            "code": value.code,
            "value": value.value,
            "available_at": value.available_at,
            "source": value.source,
            "source_scan_id": value.source_scan_id,
            "source_meta_json": value.source_meta_json,
        }
        for value in values
    ]
    return _sql.replace_factor_day(
        trade_date, factor_name, factor_version, rows, db_path=db_path)


def replace_sector_day(trade_date: str, rows: Iterable[dict], *, db_path=None) -> int:
    return _sql.replace_sector_day(trade_date, rows, db_path=db_path)


def get_sector_map(trade_date: str, *, db_path=None) -> dict[str, dict]:
    return _sql.get_sector_map(trade_date, db_path=db_path)


def resolve_versions(names: Iterable[str], *, db_path=None) -> dict[str, str]:
    return _sql.resolve_versions(names, db_path=db_path)


def save_registry_snapshots(specs: Iterable, versions: dict[str, str], *,
                            valid_from_overrides: Optional[dict] = None,
                            db_path=None) -> int:
    """按版本映射写注册表快照；已存在版本 DO NOTHING（默认版本不漂移）。"""
    from dragon_quant.factors.registry import spec_to_registry_row

    overrides = valid_from_overrides or {}
    rows = [
        spec_to_registry_row(spec, versions[spec.name],
                             valid_from=overrides.get(spec.name))
        for spec in specs
        if spec.name in versions
    ]
    return _sql.save_registry_snapshots(rows, db_path=db_path)


def record_build_run(trade_date: str, report: dict, *, started_at: Optional[str] = None,
                     finished_at: Optional[str] = None, db_path=None) -> int:
    return _sql.record_build_run(
        trade_date, report, started_at=started_at, finished_at=finished_at,
        db_path=db_path)


def list_build_runs(*, trade_date: Optional[str] = None, limit: int = 20,
                    db_path=None) -> list[dict]:
    return _sql.list_build_runs(trade_date=trade_date, limit=limit, db_path=db_path)


def list_registry(*, names: Optional[Iterable[str]] = None, db_path=None) -> list[dict]:
    return _sql.list_registry(names=names, db_path=db_path)


def status(*, db_path=None) -> dict:
    return _sql.factor_status(db_path=db_path)


def get_day_values(trade_date: str, *, names: Optional[Iterable[str]] = None,
                   codes: Optional[Iterable[str]] = None,
                   version: Optional[str] = None,
                   versions: Optional[dict] = None, db_path=None) -> dict:
    """点查某日因子值，返回按 code / factor 分组的字典。

    ``values[code][factor_name] = value``；缺失因子不出现（无行 → 无键）。

    版本过滤（方案 §5.5）：
    - ``versions``（P2 模型快照）：按 ``{name: version}`` 精确读取，模型快照是唯一
      真值，不再按注册表解析默认版本；
    - ``version``：单版本精确过滤；本次查询实际无行的因子进入 ``missing_versions``
      （``--version --code`` 组合不会把空结果伪装成「版本已解析」）；
    - 两者皆空：先按注册表解析默认版本，再由 SQL 按 ``(factor_name, factor_version)``
      精确过滤。否则同名多版本并存会返回多行，上层按 ``values[code][name]`` 聚合会
      被排序末位静默覆盖。
    """
    requested = [n for n in (names or []) if n]
    code_filter = [c for c in (codes or []) if c]
    if versions:
        resolved = {str(name): str(ver) for name, ver in dict(versions).items()
                    if name and ver}
        rows = _sql.get_day_values(
            trade_date, names=requested or list(resolved) or None,
            codes=code_filter or None,
            version_pairs=list(resolved.items()), db_path=db_path)
        candidate_names = requested or list(resolved)
    elif version:
        rows = _sql.get_day_values(
            trade_date, names=requested or None, codes=code_filter or None,
            version=version, db_path=db_path)
        present = {r["factor_name"] for r in rows}
        candidate_names = requested or sorted(present)
        resolved = {name: version for name in candidate_names if name in present}
    else:
        candidate_names = requested or _sql.list_value_names(
            trade_date, codes=code_filter or None, db_path=db_path)
        resolved = _sql.resolve_versions(candidate_names, db_path=db_path)
        rows = _sql.get_day_values(
            trade_date, names=requested or None, codes=code_filter or None,
            version_pairs=list(resolved.items()), db_path=db_path)
    values: dict[str, dict[str, float]] = {}
    available_at: dict[str, dict[str, str]] = {}
    source_scan_id: dict[str, dict[str, Optional[str]]] = {}
    for row in rows:
        code = row["code"]
        name = row["factor_name"]
        values.setdefault(code, {})[name] = row["value"]
        available_at.setdefault(code, {})[name] = row["available_at"]
        source_scan_id.setdefault(code, {})[name] = row["source_scan_id"]
    return {
        "trade_date": trade_date,
        "rows": len(rows),
        "resolved_versions": resolved,
        "missing_versions": [n for n in candidate_names if n not in resolved],
        "values": values,
        "available_at": available_at,
        "source_scan_id": source_scan_id,
    }


def get_series(name: str, codes: Optional[Iterable[str]], date_from: str, date_to: str, *,
               version: Optional[str] = None, db_path=None) -> dict:
    """单因子区间时序；返回结构与 ``get_day_values`` 同族（带 resolved_versions）。

    ``version=None`` 时解析默认版本并进入 SQL 过滤；未注册 → 空结果并显式标
    ``missing_versions``（绝不把所有并存版本混成一条时序）。
    """
    if version:
        resolved = {name: version}
        rows = _sql.get_series(name, codes, date_from, date_to,
                               version=version, db_path=db_path)
    else:
        resolved = _sql.resolve_versions([name], db_path=db_path)
        rows = (_sql.get_series(name, codes, date_from, date_to,
                                version=resolved[name], db_path=db_path)
                if name in resolved else [])
    return {
        "factor": name,
        "date_from": date_from,
        "date_to": date_to,
        "rows": rows,
        "resolved_versions": resolved,
        "missing_versions": [] if name in resolved else [name],
    }


def dump_source_meta(meta) -> Optional[str]:
    """把 frame.meta 规范化成 ``source_meta_json``（确定性 + 拒绝 NaN）。"""
    if not meta:
        return None
    return json.dumps(meta, ensure_ascii=False, sort_keys=True, allow_nan=False)


def get_matrix_rows(date_from: str, date_to: str, *,
                    names: Optional[Iterable[str]] = None,
                    version_pairs: Optional[Iterable[tuple[str, str]]] = None,
                    codes: Optional[Iterable[str]] = None,
                    db_path=None) -> list[dict]:
    return _sql.get_matrix_rows(
        date_from, date_to, names=names, version_pairs=version_pairs,
        codes=codes, db_path=db_path)


def list_build_ids(date_from: str, date_to: str, *, db_path=None) -> list[int]:
    return _sql.list_build_ids(date_from, date_to, db_path=db_path)


# ─── 模型快照（P2 决策接入）────────────────────────────────────────────────


def save_model_snapshot(model_name: str, model_version: str, *,
                        params: dict, factor_versions: dict, db_path=None) -> bool:
    """写入模型快照；已存在同版本 → False（语义不可变，不覆盖）。"""
    return bool(_sql.save_model_snapshot(
        model_name, model_version, params=params,
        factor_versions=factor_versions, db_path=db_path))


def get_model_snapshot(model_name: str, model_version: Optional[str] = None, *,
                       db_path=None) -> Optional[dict]:
    """读取模型快照；``model_version`` 为空取最新；无快照/损坏 → None。"""
    return _sql.get_model_snapshot(model_name, model_version, db_path=db_path)


def list_value_dates(date_from: str, date_to: str, *, db_path=None) -> set[str]:
    """区间内出现过因子值的交易日集合（build 完成率分母用）。"""
    return _sql.list_value_dates(date_from, date_to, db_path=db_path)
