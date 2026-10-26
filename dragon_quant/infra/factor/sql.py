"""因子层 SQL 层（assets.db）。

职责边界（方案 §3.4）：
  - 本模块只认「行 dict」，不 import ``dragon_quant.infra.factor``（存储层不知道因子语义）；
  - 经 `assets_db._connect` / `_ensure_schema` 的**模块属性访问**调用，保证测试对
    `assets_db._connect` 的 patch 生效（与 `storage/_base.py` 转发约定等价）；
  - 读路径 `create=False`：库文件不存在返回空结构，绝不建库；
  - JSON 统一走 `assets_db._dumps`（`allow_nan=False` + 稳定键序）。

写入语义（方案 §5.4）：
  - `replace_factor_day(trade_date, factor_name, factor_version, rows)` 单事务
    「DELETE 该日该因子该版本旧行 → 批量 INSERT」。**空结果也执行 DELETE**
    （因子被显式声明为「当日无值」时，残留旧行会被评估当成现值）。
    「源整体不可用」不走本函数（由 engine 跳过，不删旧行）。
  - `factor_registry` 必须 `ON CONFLICT DO NOTHING`：`resolve_versions` 按最新
    `created_at` 解析默认版本，重插旧版本若刷新时间戳会劫持默认版本。
"""

from __future__ import annotations

import json
from typing import Iterable, Optional

from dragon_quant.infra.assets import db as assets_db

FACTOR_TABLES = (
    "factor_values",
    "factor_registry",
    "factor_model_snapshots",
    "factor_build_runs",
    "factor_sector_snapshots",
)

_FACTOR_VALUE_COLUMNS = (
    "trade_date, code, factor_name, factor_version, value, available_at, "
    "source, source_scan_id, source_meta_json"
)


def _loads(text, default):
    """逐行解析 JSON；损坏时返回 (default, error)，绝不打断整批读取。"""
    if text is None:
        return default, None
    try:
        return json.loads(text), None
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        return default, f"{type(exc).__name__}: {exc}"


def _open_read(db_path=None):
    return assets_db._connect(create=False, path=db_path)


def _open_write(db_path=None):
    conn = assets_db._connect(path=db_path)
    assets_db._ensure_schema(conn)
    return conn


# ─── 因子值长表 ────────────────────────────────────────────────────────────


def replace_factor_day(trade_date: str, factor_name: str, factor_version: str,
                       rows: Iterable[dict], *, db_path=None) -> int:
    """整日整因子原子替换，返回写入行数。

    rows 元素需含：code / value / available_at / source；
    可选 source_scan_id / source_meta_json。
    空 rows → 只执行 DELETE（语义 = 该因子当日无值）。
    """
    payload = list(rows)
    with assets_db._lock:
        conn = _open_write(db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "DELETE FROM factor_values "
                "WHERE trade_date = ? AND factor_name = ? AND factor_version = ?",
                (trade_date, factor_name, factor_version),
            )
            written = 0
            for row in payload:
                conn.execute(
                    "INSERT INTO factor_values(" + _FACTOR_VALUE_COLUMNS + ") "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        trade_date,
                        row["code"],
                        factor_name,
                        factor_version,
                        float(row["value"]),
                        row["available_at"],
                        row["source"],
                        row.get("source_scan_id"),
                        row.get("source_meta_json"),
                    ),
                )
                written += 1
            conn.commit()
            return written
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def list_value_names(trade_date: str, *, codes: Optional[Iterable[str]] = None,
                     db_path=None) -> list[str]:
    """某日 ``factor_values`` 中出现过的因子名（供版本解析；不建库）。"""
    conn = _open_read(db_path)
    if conn is None:
        return []
    try:
        assets_db._ensure_schema(conn, migrate=False)
        sql = "SELECT DISTINCT factor_name FROM factor_values WHERE trade_date = ?"
        params: list = [trade_date]
        codes = [c for c in (codes or []) if c]
        if codes:
            sql += " AND code IN (" + ",".join("?" * len(codes)) + ")"
            params.extend(codes)
        sql += " ORDER BY factor_name"
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    return [r[0] for r in rows]


def get_day_values(trade_date: str, *, names: Optional[Iterable[str]] = None,
                   codes: Optional[Iterable[str]] = None,
                   version: Optional[str] = None,
                   version_pairs: Optional[Iterable[tuple[str, str]]] = None,
                   db_path=None) -> list[dict]:
    """点查某日因子值；库/表缺失返回空列表（不建库）。

    ``version_pairs`` 用于「版本=None 时已解析出默认版本」的场景：SQL 必须按
    ``(factor_name, factor_version)`` 精确过滤，避免同名多版本并存时返回多行
    （上层按 code/name 聚合会被版本排序末位静默覆盖）。
    """
    conn = _open_read(db_path)
    if conn is None:
        return []
    pairs = [(str(n), str(v)) for n, v in (version_pairs or []) if n and v]
    if version_pairs is not None and not pairs:
        return []
    try:
        assets_db._ensure_schema(conn, migrate=False)
        sql = (
            "SELECT trade_date, code, factor_name, factor_version, value, available_at, "
            "source, source_scan_id, source_meta_json FROM factor_values WHERE trade_date = ?"
        )
        params: list = [trade_date]
        names = [n for n in (names or []) if n]
        codes = [c for c in (codes or []) if c]
        if names:
            sql += " AND factor_name IN (" + ",".join("?" * len(names)) + ")"
            params.extend(names)
        if codes:
            sql += " AND code IN (" + ",".join("?" * len(codes)) + ")"
            params.extend(codes)
        if pairs:
            sql += " AND (factor_name, factor_version) IN (" + \
                ",".join("(?, ?)" for _ in pairs) + ")"
            for name, ver in pairs:
                params.extend([name, ver])
        elif version:
            sql += " AND factor_version = ?"
            params.append(version)
        sql += " ORDER BY code, factor_name, factor_version"
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    return [
        {
            "trade_date": r[0], "code": r[1], "factor_name": r[2],
            "factor_version": r[3], "value": r[4], "available_at": r[5],
            "source": r[6], "source_scan_id": r[7],
            "source_meta_json": r[8],
        }
        for r in rows
    ]


def get_series(name: str, codes: Optional[Iterable[str]], date_from: str, date_to: str, *,
               version: Optional[str] = None, db_path=None) -> list[dict]:
    """单因子时序（按 code + 日期区间）；``codes=None`` 表示全部 code。"""
    wanted = None if codes is None else [c for c in codes if c]
    if wanted is not None and not wanted:
        return []
    conn = _open_read(db_path)
    if conn is None:
        return []
    try:
        assets_db._ensure_schema(conn, migrate=False)
        sql = (
            "SELECT trade_date, code, factor_version, value, available_at, "
            "source, source_scan_id, source_meta_json FROM factor_values "
            "WHERE factor_name = ? AND trade_date >= ? AND trade_date <= ?"
        )
        params: list = [name, date_from, date_to]
        if wanted is not None:
            sql += " AND code IN (" + ",".join("?" * len(wanted)) + ")"
            params.extend(wanted)
        if version:
            sql += " AND factor_version = ?"
            params.append(version)
        sql += " ORDER BY trade_date, code"
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    return [
        {
            "trade_date": r[0], "code": r[1], "factor_version": r[2],
            "value": r[3], "available_at": r[4], "source": r[5],
            "source_scan_id": r[6], "source_meta_json": r[7],
        }
        for r in rows
    ]


def get_matrix_rows(date_from: str, date_to: str, *,
                    names: Optional[Iterable[str]] = None,
                    version_pairs: Optional[Iterable[tuple[str, str]]] = None,
                    codes: Optional[Iterable[str]] = None,
                    db_path=None) -> list[dict]:
    """矩阵导出 / 评估用的区间读取（``date × code × factor``）。

    ``version_pairs`` 为 ``(factor_name, factor_version)`` 精确过滤；同名多版本
    并存时绝不让 SQL 把两条都返回（否则矩阵行会被静默覆盖）。
    """
    pairs = [(str(n), str(v)) for n, v in (version_pairs or []) if n and v]
    if version_pairs is not None and not pairs:
        return []
    conn = _open_read(db_path)
    if conn is None:
        return []
    try:
        assets_db._ensure_schema(conn, migrate=False)
        sql = (
            "SELECT trade_date, code, factor_name, factor_version, value, "
            "available_at, source, source_scan_id, source_meta_json "
            "FROM factor_values WHERE trade_date >= ? AND trade_date <= ?"
        )
        params: list = [date_from, date_to]
        wanted = [n for n in (names or []) if n]
        if wanted:
            sql += " AND factor_name IN (" + ",".join("?" * len(wanted)) + ")"
            params.extend(wanted)
        code_filter = [c for c in (codes or []) if c]
        if code_filter:
            sql += " AND code IN (" + ",".join("?" * len(code_filter)) + ")"
            params.extend(code_filter)
        if pairs:
            sql += " AND (factor_name, factor_version) IN (" + \
                ",".join("(?, ?)" for _ in pairs) + ")"
            for name, ver in pairs:
                params.extend([name, ver])
        sql += " ORDER BY trade_date, code, factor_name"
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    return [
        {
            "trade_date": r[0], "code": r[1], "factor_name": r[2],
            "factor_version": r[3], "value": r[4], "available_at": r[5],
            "source": r[6], "source_scan_id": r[7], "source_meta_json": r[8],
        }
        for r in rows
    ]


def list_build_ids(date_from: str, date_to: str, *, db_path=None) -> list[int]:
    """区间内每个交易日最近一次 build 的 id（升序，去重）。"""
    conn = _open_read(db_path)
    if conn is None:
        return []
    try:
        assets_db._ensure_schema(conn, migrate=False)
        rows = conn.execute(
            "SELECT MAX(id) FROM factor_build_runs "
            "WHERE trade_date >= ? AND trade_date <= ? GROUP BY trade_date "
            "ORDER BY trade_date",
            (date_from, date_to),
        ).fetchall()
    finally:
        conn.close()
    return [int(row[0]) for row in rows if row[0] is not None]


def list_value_dates(date_from: str, date_to: str, *, db_path=None) -> set[str]:
    """区间内 ``factor_values`` 出现过的交易日集合（P2 build 完成率分母用）。

    只读、不建库；库缺失返回空集合（调用方据此判定完成率 0，绝不伪造 100%）。
    """
    conn = _open_read(db_path)
    if conn is None:
        return set()
    try:
        assets_db._ensure_schema(conn, migrate=False)
        rows = conn.execute(
            "SELECT DISTINCT trade_date FROM factor_values "
            "WHERE trade_date >= ? AND trade_date <= ?",
            (date_from, date_to),
        ).fetchall()
    finally:
        conn.close()
    return {r[0] for r in rows if r[0]}


# ─── 模型快照（P2 决策接入）────────────────────────────────────────────────


def save_model_snapshot(model_name: str, model_version: str, params: dict,
                        factor_versions: dict, *, db_path=None) -> int:
    """写入模型快照；同一 ``(model_name, model_version)`` 已存在则 DO NOTHING。

    「同一版本语义不可变」：版本升级 = 新行，绝不就地改写旧快照。返回 1=新插入，
    0=已存在（调用方据此区分「复用已有快照」）。
    """
    if not model_name or not model_version:
        raise ValueError("model_name / model_version 必须非空")
    with assets_db._lock:
        conn = _open_write(db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            cur = conn.execute(
                "INSERT INTO factor_model_snapshots("
                "model_name, model_version, params_json, factor_versions_json) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(model_name, model_version) DO NOTHING",
                (model_name, model_version, assets_db._dumps(dict(params or {})),
                 assets_db._dumps(dict(factor_versions or {}))),
            )
            inserted = int(cur.rowcount or 0)
            conn.commit()
            return 1 if inserted else 0
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def get_model_snapshot(model_name: str, model_version: Optional[str] = None, *,
                       db_path=None) -> Optional[dict]:
    """读取模型快照；``model_version`` 为空 → 取最新（``created_at DESC, version DESC``）。

    JSON 损坏按「无快照」处理（返回 None → 决策降级 ``model_snapshot_missing``），
    绝不抛出，也绝不猜一个默认模型。读路径不建库。
    """
    conn = _open_read(db_path)
    if conn is None:
        return None
    try:
        assets_db._ensure_schema(conn, migrate=False)
        if model_version:
            row = conn.execute(
                "SELECT model_name, model_version, params_json, "
                "factor_versions_json, created_at FROM factor_model_snapshots "
                "WHERE model_name = ? AND model_version = ?",
                (model_name, model_version),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT model_name, model_version, params_json, "
                "factor_versions_json, created_at FROM factor_model_snapshots "
                "WHERE model_name = ? "
                "ORDER BY created_at DESC, model_version DESC LIMIT 1",
                (model_name,),
            ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    params, params_error = _loads(row[2], None)
    factor_versions, versions_error = _loads(row[3], None)
    if params_error or versions_error or not isinstance(params, dict) \
            or not isinstance(factor_versions, dict):
        return None
    return {
        "model_name": row[0], "model_version": row[1],
        "params": params, "factor_versions": factor_versions,
        "created_at": row[4],
    }


# ─── 注册表快照 / build 审计 ───────────────────────────────────────────────


def save_registry_snapshots(rows: Iterable[dict], *, db_path=None) -> int:
    """写入因子元数据快照；同一 (name, version) 已存在则 DO NOTHING。

    返回本次实际插入行数（已存在的不计）。**不可 UPSERT**：重插旧版本若刷新
    `created_at` 会劫持 `resolve_versions` 的默认版本解析。
    """
    payload = list(rows)
    if not payload:
        return 0
    inserted = 0
    with assets_db._lock:
        conn = _open_write(db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            for row in payload:
                cur = conn.execute(
                    "INSERT INTO factor_registry("
                    "factor_name, factor_version, label, frequency, source, "
                    "depends_on_json, lookback, direction, unit, available_phase, "
                    "pit_rule, scope_json, params_json, default_enabled, valid_from, "
                    "deprecated_at, owner, description, spec_hash) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(factor_name, factor_version) DO NOTHING",
                    (
                        row["factor_name"], row["factor_version"], row.get("label"),
                        row["frequency"], row["source"],
                        row.get("depends_on_json"), int(row.get("lookback") or 0),
                        row["direction"], row.get("unit"), row["available_phase"],
                        row["pit_rule"], row.get("scope_json"), row.get("params_json"),
                        1 if row.get("default_enabled") else 0, row.get("valid_from"),
                        row.get("deprecated_at"), row.get("owner"),
                        row.get("description"), row["spec_hash"],
                    ),
                )
                if cur.rowcount > 0:
                    inserted += 1
            conn.commit()
            return inserted
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def list_registry(*, names: Optional[Iterable[str]] = None, db_path=None) -> list[dict]:
    conn = _open_read(db_path)
    if conn is None:
        return []
    try:
        assets_db._ensure_schema(conn, migrate=False)
        sql = (
            "SELECT factor_name, factor_version, label, frequency, source, "
            "depends_on_json, lookback, direction, unit, available_phase, pit_rule, "
            "scope_json, params_json, default_enabled, valid_from, deprecated_at, "
            "owner, description, spec_hash, created_at FROM factor_registry"
        )
        params: list = []
        wanted = [n for n in (names or []) if n]
        if wanted:
            sql += " WHERE factor_name IN (" + ",".join("?" * len(wanted)) + ")"
            params.extend(wanted)
        sql += " ORDER BY factor_name, created_at DESC, factor_version DESC"
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    result = []
    for r in rows:
        depends_on, depends_error = _loads(r[5], [])
        scope, scope_error = _loads(r[11], [])
        params_json, params_error = _loads(r[12], {})
        result.append({
            "factor_name": r[0], "factor_version": r[1], "label": r[2],
            "frequency": r[3], "source": r[4], "depends_on": depends_on,
            "lookback": r[6], "direction": r[7], "unit": r[8],
            "available_phase": r[9], "pit_rule": r[10], "scope": scope,
            "params": params_json, "default_enabled": bool(r[13]),
            "valid_from": r[14], "deprecated_at": r[15], "owner": r[16],
            "description": r[17], "spec_hash": r[18], "created_at": r[19],
            "json_error": depends_error or scope_error or params_error,
        })
    return result


def resolve_versions(names: Iterable[str], *, db_path=None) -> dict[str, str]:
    """解析各因子默认版本：最新 `created_at`，tie → 版本字典序最大。

    未注册（从未 build 过）的 name 不出现在返回值中，由调用方显式标注。
    """
    wanted = sorted({n for n in (names or []) if n})
    if not wanted:
        return {}
    conn = _open_read(db_path)
    if conn is None:
        return {}
    try:
        assets_db._ensure_schema(conn, migrate=False)
        rows = conn.execute(
            "SELECT factor_name, factor_version FROM factor_registry "
            "WHERE factor_name IN (" + ",".join("?" * len(wanted)) + ") "
            "ORDER BY factor_name, created_at DESC, factor_version DESC",
            wanted,
        ).fetchall()
    finally:
        conn.close()
    resolved: dict[str, str] = {}
    for name, version in rows:
        resolved.setdefault(name, version)
    return resolved


def record_build_run(trade_date: str, report: dict, *, started_at: Optional[str] = None,
                     finished_at: Optional[str] = None, db_path=None) -> int:
    """写入一次 build 审计报告，返回自增 id。"""
    with assets_db._lock:
        conn = _open_write(db_path)
        try:
            cur = conn.execute(
                "INSERT INTO factor_build_runs(trade_date, started_at, finished_at, report_json) "
                "VALUES (?, ?, ?, ?)",
                (trade_date, started_at, finished_at, assets_db._dumps(report)),
            )
            conn.commit()
            return int(cur.lastrowid or 0)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def list_build_runs(*, trade_date: Optional[str] = None, limit: int = 20,
                    db_path=None) -> list[dict]:
    """列出 build 审计；单行 report_json 损坏只标 ``report_error``，不中断整批。"""
    conn = _open_read(db_path)
    if conn is None:
        return []
    try:
        assets_db._ensure_schema(conn, migrate=False)
        sql = (
            "SELECT id, trade_date, started_at, finished_at, report_json, created_at "
            "FROM factor_build_runs"
        )
        params: list = []
        if trade_date:
            sql += " WHERE trade_date = ?"
            params.append(trade_date)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(max(1, int(limit)))
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    result = []
    for r in rows:
        report, error = _loads(r[4], None)
        result.append({
            "id": r[0], "trade_date": r[1], "started_at": r[2],
            "finished_at": r[3], "report": report, "report_error": error,
            "created_at": r[5],
        })
    return result


# ─── 主板块快照 ────────────────────────────────────────────────────────────


def replace_sector_day(trade_date: str, rows: Iterable[dict], *, db_path=None) -> int:
    """整日原子替换主板块快照（DELETE 当日 → 批量 INSERT），返回写入行数。"""
    payload = list(rows)
    with assets_db._lock:
        conn = _open_write(db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("DELETE FROM factor_sector_snapshots WHERE trade_date = ?",
                         (trade_date,))
            written = 0
            for row in payload:
                conn.execute(
                    "INSERT INTO factor_sector_snapshots("
                    "trade_date, code, industry_code, industry_name, category, source, scan_id) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(trade_date, code, category) DO UPDATE SET "
                    "industry_code=excluded.industry_code, "
                    "industry_name=excluded.industry_name, "
                    "source=excluded.source, scan_id=excluded.scan_id",
                    (
                        trade_date, row["code"], row.get("industry_code"),
                        row.get("industry_name"), row["category"],
                        row.get("source"), row.get("scan_id"),
                    ),
                )
                written += 1
            conn.commit()
            return written
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def get_sector_map(trade_date: str, *, db_path=None) -> dict[str, dict]:
    """当日主板块快照 → {code: {code,name,category,source}}；同 code 多行取字典序最小 category。"""
    conn = _open_read(db_path)
    if conn is None:
        return {}
    try:
        assets_db._ensure_schema(conn, migrate=False)
        rows = conn.execute(
            "SELECT code, industry_code, industry_name, category, source "
            "FROM factor_sector_snapshots WHERE trade_date = ? ORDER BY code, category",
            (trade_date,),
        ).fetchall()
    finally:
        conn.close()
    result: dict[str, dict] = {}
    for code, industry_code, industry_name, category, source in rows:
        result.setdefault(code, {
            "code": industry_code, "name": industry_name,
            "category": category, "source": source,
        })
    return result


# ─── 状态 ──────────────────────────────────────────────────────────────────


def factor_status(*, db_path=None) -> dict:
    """只读统计（`factor status` 消费）；库缺失返回全零结构。"""
    empty = {
        "factor_values": {"rows": 0, "dates": 0, "factors": 0,
                          "first_date": None, "last_date": None},
        "factor_registry": {"rows": 0, "factors": 0},
        "factor_sector_snapshots": {"rows": 0, "dates": 0, "by_category": {}},
        "build_runs": {"rows": 0, "last": None},
    }
    conn = _open_read(db_path)
    if conn is None:
        return empty
    try:
        assets_db._ensure_schema(conn, migrate=False)
        row = conn.execute(
            "SELECT COUNT(*), COUNT(DISTINCT trade_date), COUNT(DISTINCT factor_name), "
            "MIN(trade_date), MAX(trade_date) FROM factor_values"
        ).fetchone()
        values = {
            "rows": row[0] or 0, "dates": row[1] or 0, "factors": row[2] or 0,
            "first_date": row[3], "last_date": row[4],
        }
        reg = conn.execute(
            "SELECT COUNT(*), COUNT(DISTINCT factor_name) FROM factor_registry"
        ).fetchone()
        registry = {"rows": reg[0] or 0, "factors": reg[1] or 0}
        sector_rows = conn.execute(
            "SELECT COUNT(*), COUNT(DISTINCT trade_date) FROM factor_sector_snapshots"
        ).fetchone()
        by_category = {
            r[0]: r[1] for r in conn.execute(
                "SELECT category, COUNT(*) FROM factor_sector_snapshots GROUP BY category "
                "ORDER BY category"
            ).fetchall()
        }
        sector = {
            "rows": sector_rows[0] or 0, "dates": sector_rows[1] or 0,
            "by_category": by_category,
        }
        runs_row = conn.execute("SELECT COUNT(*) FROM factor_build_runs").fetchone()
        last_row = conn.execute(
            "SELECT id, trade_date, started_at, finished_at, created_at "
            "FROM factor_build_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        runs = {
            "rows": runs_row[0] or 0,
            "last": ({
                "id": last_row[0], "trade_date": last_row[1],
                "started_at": last_row[2], "finished_at": last_row[3],
                "created_at": last_row[4],
            } if last_row else None),
        }
        return {
            "factor_values": values,
            "factor_registry": registry,
            "factor_sector_snapshots": sector,
            "build_runs": runs,
        }
    finally:
        conn.close()
