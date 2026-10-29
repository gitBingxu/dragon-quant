"""派生数据清理（``assets purge-derived``）：只删可重建的 prep 派生数据。

边界（技术方案 §5）：

* **目标**：退役的 2 个旧评分因子 + 当前注册的 prep 因子（事实 5 + review 18），
  ``prep_snapshots`` 全部行，以及引用了任一目标因子的 ``factor_model_snapshots`` 行；
* **保护**：``prep_assets``（原始证据）/ ``auction_*`` / 非目标因子 /
  ``factor_build_runs`` / 云端对象，一律零改动；
* 默认 dry-run；``apply_purge`` 在**单事务**内重算复核、固定顺序删除，
  计数变化即回滚；
* 本模块只做 DB，不 import 云端同步、不触网、不执行重建。

设计模式：**注册表（Registry）** —— 目标名与重建名单是显式数据，不用 glob 猜名字；
**模板方法（Template Method）** —— dry-run 与 apply 共用同一套计数函数，保证
「展示的」与「删除的」是同一口径。
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from typing import Optional

# 退役后不再重建的旧五维评分因子（唯一权威已迁到 prep_review_*）。
RETIRED_FACTOR_NAMES: tuple[str, ...] = ("prep_composite", "prep_verdict")


def _registered_prep_names() -> tuple[str, ...]:
    """当前注册表中 ``source == "prep"`` 的因子名（单一真源）。"""

    from dragon_quant.infra.factor.registry import FACTOR_SPECS

    return tuple(spec.name for spec in FACTOR_SPECS if spec.source == "prep")


# 清理目标 = 退役因子 ∪ 当前注册的 prep 因子；新增 prep 因子必须自动纳入。
TARGET_FACTOR_NAMES: tuple[str, ...] = tuple(sorted(
    set(RETIRED_FACTOR_NAMES) | set(_registered_prep_names())))
REBUILD_FACTOR_NAMES: tuple[str, ...] = tuple(
    name for name in TARGET_FACTOR_NAMES if name not in RETIRED_FACTOR_NAMES)


@dataclass(frozen=True)
class PurgePlan:
    """dry-run 结果；``apply_purge`` 在同一份口径上复算并删除。"""

    db_path: Optional[str]
    db_exists: bool
    counts: dict
    protected: dict
    models: list[dict] = field(default_factory=list)
    rebuild_steps: list[str] = field(default_factory=list)
    warnings: list[dict] = field(default_factory=list)
    prep_dates: list[str] = field(default_factory=list)


def _resolve_path(db_path: Optional[str]) -> str:
    if db_path is not None:
        return str(db_path)
    from dragon_quant.infra.storage.paths import ASSETS_DB_PATH

    return str(ASSETS_DB_PATH)


def _open_read(path: str) -> Optional[sqlite3.Connection]:
    """纯只读连接（唯一连接站点在 ``infra.assets.db``）；库不存在返回 None。"""

    from dragon_quant.infra.assets import db as assets_db

    return assets_db._connect_readonly(path=path)


def _open_write(path: str) -> sqlite3.Connection:
    from dragon_quant.infra.assets import db as assets_db

    return assets_db._connect(path=path)


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def _scalar(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> int:
    row = conn.execute(sql, params).fetchone()
    return int(row[0] or 0) if row else 0


def _placeholders(count: int) -> str:
    return ",".join("?" for _ in range(count))


def _by_factor(conn: sqlite3.Connection, table: str, column: str) -> dict:
    names = TARGET_FACTOR_NAMES
    if not names or not _table_exists(conn, table):
        return {}
    rows = conn.execute(
        f"SELECT {column}, COUNT(*) FROM {table} "
        f"WHERE {column} IN ({_placeholders(len(names))}) GROUP BY {column}",
        names,
    ).fetchall()
    return {row[0]: int(row[1]) for row in rows}


def _model_rows(conn: sqlite3.Connection) -> tuple[list[dict], list[dict]]:
    """返回 (命中目标因子的模型行, JSON 损坏告警)。"""

    if not _table_exists(conn, "factor_model_snapshots"):
        return [], []
    matched: list[dict] = []
    warnings: list[dict] = []
    rows = conn.execute(
        "SELECT model_name, model_version, factor_versions_json "
        "FROM factor_model_snapshots ORDER BY model_name, model_version"
    ).fetchall()
    targets = set(TARGET_FACTOR_NAMES)
    for model_name, model_version, versions_json in rows:
        try:
            versions = json.loads(versions_json)
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            warnings.append({"model_name": model_name, "model_version": model_version,
                             "reason": "malformed_json",
                             "error": f"{type(exc).__name__}: {exc}"})
            continue
        if not isinstance(versions, dict):
            warnings.append({"model_name": model_name, "model_version": model_version,
                             "reason": "malformed_json", "error": "not_an_object"})
            continue
        hits = sorted(name for name in versions if name in targets)
        if not hits:
            continue
        matched.append({
            "model_name": model_name,
            "model_version": model_version,
            "matched_factors": hits,
            "mixed": any(name not in targets for name in versions),
        })
    return matched, warnings


def _count_targets(conn: sqlite3.Connection) -> dict:
    names = TARGET_FACTOR_NAMES
    counts: dict = {}
    counts["prep_snapshots"] = {
        "rows": _scalar(conn, "SELECT COUNT(*) FROM prep_snapshots")
        if _table_exists(conn, "prep_snapshots") else 0,
    }
    values_by = _by_factor(conn, "factor_values", "factor_name")
    counts["factor_values"] = {
        "rows": sum(values_by.values()), "by_factor": values_by}
    registry_by = _by_factor(conn, "factor_registry", "factor_name")
    counts["factor_registry"] = {
        "rows": sum(registry_by.values()), "by_factor": registry_by}
    models, _ = _model_rows(conn)
    counts["factor_model_snapshots"] = {"rows": len(models), "models": models}
    return counts


def _count_protected(conn: sqlite3.Connection) -> dict:
    names = TARGET_FACTOR_NAMES

    def _other(table: str, column: str) -> int:
        if not _table_exists(conn, table):
            return 0
        if not names:
            return _scalar(conn, f"SELECT COUNT(*) FROM {table}")
        return _scalar(
            conn,
            f"SELECT COUNT(*) FROM {table} "
            f"WHERE {column} NOT IN ({_placeholders(len(names))})",
            names,
        )

    models, _ = _model_rows(conn)
    total_models = (_scalar(conn, "SELECT COUNT(*) FROM factor_model_snapshots")
                    if _table_exists(conn, "factor_model_snapshots") else 0)
    return {
        "prep_assets": (_scalar(conn, "SELECT COUNT(*) FROM prep_assets")
                        if _table_exists(conn, "prep_assets") else 0),
        "auction_assets": (_scalar(conn, "SELECT COUNT(*) FROM auction_assets")
                           if _table_exists(conn, "auction_assets") else 0),
        "auction_snapshots": (_scalar(conn, "SELECT COUNT(*) FROM auction_snapshots")
                              if _table_exists(conn, "auction_snapshots") else 0),
        "factor_values_other": _other("factor_values", "factor_name"),
        "factor_registry_other": _other("factor_registry", "factor_name"),
        "factor_build_runs": (_scalar(conn, "SELECT COUNT(*) FROM factor_build_runs")
                              if _table_exists(conn, "factor_build_runs") else 0),
        "factor_model_snapshots_other": total_models - len(models),
    }


def _rebuild_steps(prep_dates: list[str]) -> list[str]:
    first = prep_dates[0] if prep_dates else "<D0>"
    last = prep_dates[-1] if prep_dates else "<Dn>"
    names = ",".join(REBUILD_FACTOR_NAMES)
    return [
        "python -m dragon_quant prep --recompute --date <D>  # 按 prep_assets 日期升序逐日",
        f"python -m dragon_quant factor build --date <D> --only {names}",
        f"python -m dragon_quant factor decision-review --source prep "
        f"--from {first} --to {last}",
    ]


def _prep_dates(conn: sqlite3.Connection) -> list[str]:
    if not _table_exists(conn, "prep_assets"):
        return []
    rows = conn.execute(
        "SELECT DISTINCT trade_date FROM prep_assets ORDER BY trade_date"
    ).fetchall()
    return [row[0] for row in rows if row[0]]


def plan_purge(db_path: str | None = None) -> PurgePlan:
    """只读 dry-run：目标计数 + 保护计数 + 受影响模型 + 重建提示。"""

    path = _resolve_path(db_path)
    conn = _open_read(path)
    if conn is None:
        return PurgePlan(
            db_path=path, db_exists=False,
            counts={
                "prep_snapshots": {"rows": 0},
                "factor_values": {"rows": 0, "by_factor": {}},
                "factor_registry": {"rows": 0, "by_factor": {}},
                "factor_model_snapshots": {"rows": 0, "models": []},
            },
            protected={
                "prep_assets": 0, "auction_assets": 0, "auction_snapshots": 0,
                "factor_values_other": 0, "factor_registry_other": 0,
                "factor_build_runs": 0, "factor_model_snapshots_other": 0,
            },
            models=[], rebuild_steps=_rebuild_steps([]), warnings=[],
        )
    try:
        counts = _count_targets(conn)
        protected = _count_protected(conn)
        _, warnings = _model_rows(conn)
        dates = _prep_dates(conn)
    finally:
        conn.close()
    return PurgePlan(
        db_path=path, db_exists=True, counts=counts, protected=protected,
        models=list(counts["factor_model_snapshots"]["models"]),
        rebuild_steps=_rebuild_steps(dates),
        warnings=warnings, prep_dates=dates)


def _delete_models(conn: sqlite3.Connection) -> int:
    """删除引用任一目标因子的模型快照；JSON 损坏行不删。"""

    matched, _ = _model_rows(conn)
    deleted = 0
    for row in matched:
        cur = conn.execute(
            "DELETE FROM factor_model_snapshots "
            "WHERE model_name = ? AND model_version = ?",
            (row["model_name"], row["model_version"]),
        )
        deleted += int(cur.rowcount or 0)
    return deleted


def apply_purge(plan: PurgePlan) -> dict:
    """单事务复核 + 固定顺序删除；计数变化/异常即回滚并返回审计。"""

    path = plan.db_path or _resolve_path(None)
    conn = _open_read(path)
    if conn is not None:
        conn.close()
    else:
        return {"ok": False, "reason": "db_missing", "db_path": path,
                "recheck": None, "deleted": None}

    conn = _open_write(path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        recheck_counts = _count_targets(conn)
        recheck_protected = _count_protected(conn)
        recheck = {"counts": recheck_counts, "protected": recheck_protected}
        if recheck_counts != plan.counts or recheck_protected != plan.protected:
            conn.rollback()
            return {"ok": False, "reason": "counts_changed", "recheck": recheck,
                    "before": {"counts": plan.counts, "protected": plan.protected},
                    "deleted": None}

        deleted = {
            "factor_model_snapshots": _delete_models(conn),
            "factor_values": 0,
            "factor_registry": 0,
            "prep_snapshots": 0,
        }
        names = TARGET_FACTOR_NAMES
        if names:
            ph = _placeholders(len(names))
            deleted["factor_values"] = int(conn.execute(
                f"DELETE FROM factor_values WHERE factor_name IN ({ph})",
                names).rowcount or 0)
            deleted["factor_registry"] = int(conn.execute(
                f"DELETE FROM factor_registry WHERE factor_name IN ({ph})",
                names).rowcount or 0)
        deleted["prep_snapshots"] = int(
            conn.execute("DELETE FROM prep_snapshots").rowcount or 0)

        # 提交前复核保护表计数未变（防止清理过程中被并发写入）。
        if _count_protected(conn) != plan.protected:
            conn.rollback()
            return {"ok": False, "reason": "protected_changed",
                    "recheck": recheck, "deleted": None}
        conn.commit()
    except Exception as exc:
        conn.rollback()
        return {"ok": False, "reason": "transaction_failed",
                "recheck": None, "deleted": None,
                "failed": [{"reason": f"{type(exc).__name__}: {exc}"}]}
    finally:
        conn.close()

    return {
        "ok": True,
        "reason": None,
        "recheck": recheck,
        "deleted": deleted,
        "model_rebuild_required": bool(deleted["factor_model_snapshots"]),
        "models": list(plan.models),
    }


__all__ = [
    "PurgePlan",
    "REBUILD_FACTOR_NAMES",
    "RETIRED_FACTOR_NAMES",
    "TARGET_FACTOR_NAMES",
    "apply_purge",
    "plan_purge",
]
