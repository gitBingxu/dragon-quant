"""财务原始链路 SQL 层（assets.db 的 ``fin_*`` 五表）。

职责边界（方案 §3.4 / §5.4）：
  - 本模块只认「行 dict」，不 import ``dragon_quant.factors``（存储层不知道因子语义）；
  - 经 ``assets_db._connect`` / ``_ensure_schema`` 的模块属性访问调用，保证测试 patch
    ``assets_db._connect`` 生效；
  - 读路径 ``create=False``：库文件不存在返回空结构，绝不建库；
  - JSON 统一走 ``assets_db._dumps``（``allow_nan=False`` + 稳定键序）。

写入语义：
  - 观测：``(trade_date, code)`` UPSERT；``updated_date`` 回退时**只更新 captured_at**，
    不覆盖 period/values（期次倒退不能伪造新事实）。
  - 报告期账本：**追加式 vintage**。``(report_period, code, file_md5)`` 冲突 DO NOTHING；
    新 vintage 缺某 code → 旧行记 ``retracted_at``（as-of 此后不再选中）；
    **空 records 不删除任何行**（解析失败 ≠ 该期无数据）。
  - as-of 选取：同 code 取 ``available_at ≤ as_of`` 的最新 report_period；
    ``retracted_at ≤ as_of`` 的 vintage 不参与。``as_of`` 由调用方按 aware datetime
    算好后传入（本层不 import factors，不重复 phase 口径）。
"""

from __future__ import annotations

import json
from typing import Iterable, Optional

from dragon_quant.storage import assets_db

FIN_TABLES = (
    "fin_observed_snapshots",
    "fin_report_files",
    "fin_report_records",
    "fin_field_calibrations",
    "fin_xdxr_records",
)


def _loads(text, default):
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


def _placeholders(count: int) -> str:
    return ",".join("?" * count)


# ─── 日度观测 ──────────────────────────────────────────────────────────────


def upsert_observed_snapshots(trade_date: str, rows: Iterable[dict], *, db_path=None) -> int:
    """按 ``(trade_date, code)`` UPSERT 观测快照，返回处理行数。

    ``updated_date`` 回退（新值 < 已有值）时只刷新 ``captured_at``，保留原
    ``period/values/values_hash``——防止期次倒退伪造新事实。
    """
    payload = list(rows)
    if not payload:
        return 0
    processed = 0
    with assets_db._lock:
        conn = _open_write(db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            for row in payload:
                code = str(row["code"])
                new_updated = row.get("updated_date")
                existing = conn.execute(
                    "SELECT updated_date FROM fin_observed_snapshots "
                    "WHERE trade_date = ? AND code = ?",
                    (trade_date, code),
                ).fetchone()
                if (existing and existing[0] is not None and new_updated is not None
                        and int(new_updated) < int(existing[0])):
                    conn.execute(
                        "UPDATE fin_observed_snapshots "
                        "SET captured_at = COALESCE(?, captured_at) "
                        "WHERE trade_date = ? AND code = ?",
                        (row.get("captured_at"), trade_date, code),
                    )
                else:
                    conn.execute(
                        "INSERT INTO fin_observed_snapshots("
                        "trade_date, code, updated_date, period, values_json, "
                        "values_hash, captured_at) VALUES (?, ?, ?, ?, ?, ?, ?) "
                        "ON CONFLICT(trade_date, code) DO UPDATE SET "
                        "updated_date=excluded.updated_date, period=excluded.period, "
                        "values_json=excluded.values_json, "
                        "values_hash=excluded.values_hash, captured_at=excluded.captured_at",
                        (
                            trade_date, code, new_updated, row.get("period"),
                            assets_db._dumps(row.get("values") or {}),
                            row["values_hash"], row.get("captured_at"),
                        ),
                    )
                processed += 1
            conn.commit()
            return processed
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def get_observed_snapshots(trade_date: str, *, codes: Optional[Iterable[str]] = None,
                           db_path=None) -> dict[str, dict]:
    """某日观测快照 → ``{code: {updated_date, period, values, values_hash, captured_at}}``。"""
    conn = _open_read(db_path)
    if conn is None:
        return {}
    try:
        assets_db._ensure_schema(conn, migrate=False)
        sql = ("SELECT code, updated_date, period, values_json, values_hash, captured_at "
               "FROM fin_observed_snapshots WHERE trade_date = ?")
        params: list = [trade_date]
        wanted = [c for c in (codes or []) if c]
        if wanted:
            sql += " AND code IN (" + _placeholders(len(wanted)) + ")"
            params.extend(wanted)
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    result: dict[str, dict] = {}
    for code, updated, period, values_json, values_hash, captured in rows:
        values, error = _loads(values_json, {})
        result[code] = {
            "code": code, "updated_date": updated, "period": period,
            "values": values, "values_hash": values_hash,
            "captured_at": captured, "json_error": error,
        }
    return result


def has_observed_snapshots(trade_date: str, *, db_path=None) -> bool:
    conn = _open_read(db_path)
    if conn is None:
        return False
    try:
        assets_db._ensure_schema(conn, migrate=False)
        row = conn.execute(
            "SELECT 1 FROM fin_observed_snapshots WHERE trade_date = ? LIMIT 1",
            (trade_date,)).fetchone()
        return bool(row)
    finally:
        conn.close()


def find_observed_match(code: str, values_hash: str, *, before: str,
                        db_path=None) -> Optional[str]:
    """``captured_at ≤ before`` 且哈希相等的**最早**观测时刻（值级 PIT 证据）。"""
    conn = _open_read(db_path)
    if conn is None:
        return None
    try:
        assets_db._ensure_schema(conn, migrate=False)
        row = conn.execute(
            "SELECT captured_at FROM fin_observed_snapshots "
            "WHERE code = ? AND values_hash = ? AND datetime(captured_at) <= datetime(?) "
            "ORDER BY datetime(captured_at) ASC LIMIT 1",
            (code, values_hash, before),
        ).fetchone()
    finally:
        conn.close()
    return row[0] if row else None


def latest_observed_before(codes: Iterable[str], as_of: str, *,
                           db_path=None) -> dict[str, dict]:
    """每 code 在 ``captured_at ≤ as_of`` 的**最新**观测（跨观测日 as-of 回退）。"""
    wanted = sorted({str(c) for c in (codes or []) if c})
    if not wanted:
        return {}
    conn = _open_read(db_path)
    if conn is None:
        return {}
    try:
        assets_db._ensure_schema(conn, migrate=False)
        rows = conn.execute(
            "SELECT trade_date, code, updated_date, period, values_json, "
            "values_hash, captured_at FROM fin_observed_snapshots "
            "WHERE code IN (" + _placeholders(len(wanted)) + ") "
            "AND datetime(captured_at) <= datetime(?) "
            "ORDER BY datetime(captured_at), trade_date, code",
            [*wanted, as_of],
        ).fetchall()
    finally:
        conn.close()
    result: dict[str, dict] = {}
    for trade_date, code, updated, period, values_json, values_hash, captured in rows:
        values, error = _loads(values_json, {})
        if error:
            continue
        # 升序遍历 → 后者覆盖前者 = captured_at 最大者胜出
        result[code] = {
            "trade_date": trade_date, "code": code, "updated_date": updated,
            "period": period, "values": values, "values_hash": values_hash,
            "captured_at": captured,
        }
    return result


def list_observed_for_code(code: str, *, db_path=None) -> list[dict]:
    """某 code 的全部观测快照（期次推导 / 哈希失配统计用），按观测日升序。"""
    conn = _open_read(db_path)
    if conn is None:
        return []
    try:
        assets_db._ensure_schema(conn, migrate=False)
        rows = conn.execute(
            "SELECT trade_date, updated_date, period, values_json, values_hash, captured_at "
            "FROM fin_observed_snapshots WHERE code = ? ORDER BY trade_date",
            (code,)).fetchall()
    finally:
        conn.close()
    out = []
    for trade_date, updated, period, values_json, values_hash, captured in rows:
        values, error = _loads(values_json, {})
        if error:
            continue
        out.append({
            "trade_date": trade_date, "updated_date": updated, "period": period,
            "values": values, "values_hash": values_hash, "captured_at": captured,
        })
    return out


# ─── 期文件元数据 ──────────────────────────────────────────────────────────


def upsert_report_file(report_period: str, *, filename: str, md5: str,
                       filesize: Optional[int], first_seen_at: str, status: str,
                       records_count: int = 0, raw_blob: Optional[bytes] = None,
                       error: Optional[str] = None, db_path=None) -> None:
    """写入期文件元数据；同一 ``(period, md5)`` 保留首次 ``first_seen_at``。

    md5 变化 = 新行（旧行保留供审计）；同 md5 重跑只刷新状态/计数/错误。
    """
    with assets_db._lock:
        conn = _open_write(db_path)
        try:
            conn.execute(
                "INSERT INTO fin_report_files("
                "report_period, filename, md5, filesize, first_seen_at, status, "
                "records_count, raw_blob, error) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(report_period, md5) DO UPDATE SET "
                "filename=excluded.filename, filesize=excluded.filesize, "
                "status=excluded.status, records_count=excluded.records_count, "
                "raw_blob=excluded.raw_blob, error=excluded.error",
                (report_period, filename, md5, filesize, first_seen_at, status,
                 int(records_count), raw_blob, error),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def list_report_files(*, report_period: Optional[str] = None, db_path=None) -> list[dict]:
    conn = _open_read(db_path)
    if conn is None:
        return []
    try:
        assets_db._ensure_schema(conn, migrate=False)
        sql = ("SELECT report_period, filename, md5, filesize, first_seen_at, status, "
               "records_count, error FROM fin_report_files")
        params: list = []
        if report_period:
            sql += " WHERE report_period = ?"
            params.append(report_period)
        sql += " ORDER BY report_period, first_seen_at"
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    return [
        {"report_period": r[0], "filename": r[1], "md5": r[2], "filesize": r[3],
         "first_seen_at": r[4], "status": r[5], "records_count": r[6], "error": r[7]}
        for r in rows
    ]


def get_raw_blob(report_period: str, md5: str, *, db_path=None) -> Optional[bytes]:
    conn = _open_read(db_path)
    if conn is None:
        return None
    try:
        assets_db._ensure_schema(conn, migrate=False)
        row = conn.execute(
            "SELECT raw_blob FROM fin_report_files WHERE report_period = ? AND md5 = ?",
            (report_period, md5)).fetchone()
    finally:
        conn.close()
    return bytes(row[0]) if row and row[0] is not None else None


# ─── 报告期账本 ────────────────────────────────────────────────────────────


def has_prior_records(report_period: str, file_md5: str, *, db_path=None) -> bool:
    """该报告期是否已有**其他 md5** 的账本行（判断首版 / 修订）。"""
    conn = _open_read(db_path)
    if conn is None:
        return False
    try:
        assets_db._ensure_schema(conn, migrate=False)
        row = conn.execute(
            "SELECT 1 FROM fin_report_records "
            "WHERE report_period = ? AND file_md5 != ? LIMIT 1",
            (report_period, file_md5)).fetchone()
        return bool(row)
    finally:
        conn.close()


def latest_vintage_values(report_period: str, *, db_path=None) -> dict[str, dict]:
    """该报告期每 code 最新 vintage 的语义值（用于「值未变不产新行」比对）。"""
    conn = _open_read(db_path)
    if conn is None:
        return {}
    try:
        assets_db._ensure_schema(conn, migrate=False)
        rows = conn.execute(
            "SELECT code, file_md5, values_json, first_seen_at, available_at "
            "FROM fin_report_records WHERE report_period = ?",
            (report_period,)).fetchall()
    finally:
        conn.close()
    result: dict[str, dict] = {}
    for code, md5, values_json, first_seen, available in rows:
        values, error = _loads(values_json, {})
        if error:
            continue
        current = result.get(code)
        key = (str(first_seen or ""), str(available or ""), str(md5))
        if current is None or key > current["_key"]:
            result[code] = {"md5": md5, "values": values, "_key": key}
    for entry in result.values():
        entry.pop("_key", None)
    return result


def insert_report_vintage(report_period: str, records: Iterable[dict], *,
                          file_md5: str, layout_key: str,
                          present_codes: Optional[Iterable[str]] = None,
                          retracted_at: Optional[str] = None, db_path=None) -> dict:
    """追加式写入一期 vintage，返回 ``{inserted, retracted, vintages}``。

    - ``(period, code, md5)`` 已存在 → DO NOTHING（同日重跑零新增）；
    - **撤销集合 = 本期新文件的全部 code**（``present_codes``，含值未变/映射失败
      但文件里仍存在的 code），不是「本次实际插入的 code」——值未变的 code 不产
      新行，但**绝不能被撤销**（方案 §6.4.2 / §7）；
    - 空 records 不删除任何行（解析失败 ≠ 该期无数据）。
    """
    payload = list(records)
    present = ({str(code) for code in present_codes if code}
               if present_codes is not None
               else {str(row["code"]) for row in payload})
    inserted = 0
    retracted = 0
    with assets_db._lock:
        conn = _open_write(db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            for row in payload:
                cur = conn.execute(
                    "INSERT INTO fin_report_records("
                    "report_period, code, file_md5, layout_key, values_json, "
                    "available_at, pit_basis, first_seen_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(report_period, code, file_md5) DO NOTHING",
                    (
                        report_period, str(row["code"]), file_md5, layout_key,
                        assets_db._dumps(row.get("values") or {}),
                        row["available_at"], row["pit_basis"], row.get("first_seen_at"),
                    ),
                )
                if cur.rowcount > 0:
                    inserted += 1
            if present:
                existing = {r[0] for r in conn.execute(
                    "SELECT DISTINCT code FROM fin_report_records "
                    "WHERE report_period = ? AND retracted_at IS NULL",
                    (report_period,)).fetchall()}
                missing = sorted(existing - present)
                # 分批 UPDATE：单期数千 code 时避免 SQLite 变量上限
                for start in range(0, len(missing), 500):
                    batch = missing[start:start + 500]
                    cur = conn.execute(
                        "UPDATE fin_report_records SET retracted_at = ? "
                        "WHERE report_period = ? AND retracted_at IS NULL "
                        f"AND code IN ({_placeholders(len(batch))})",
                        [retracted_at, report_period, *batch],
                    )
                    retracted += int(cur.rowcount or 0)
            vintages = conn.execute(
                "SELECT COUNT(DISTINCT file_md5) FROM fin_report_records "
                "WHERE report_period = ?", (report_period,)).fetchone()[0]
            conn.commit()
            return {"inserted": inserted, "retracted": retracted,
                    "vintages": int(vintages or 0)}
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def _select_asof(rows, as_of: str, *, period: Optional[str] = None) -> dict[str, dict]:
    """行集 → 每 code 的 as-of 选中记录（最新 period；同 period 最新 vintage）。"""
    best: dict[str, dict] = {}
    for row in rows:
        code = row["code"]
        key = (row["report_period"], row["available_at"], row["file_md5"])
        current = best.get(code)
        if current is None or key > current["_key"]:
            best[code] = {**row, "_key": key}
    for entry in best.values():
        entry.pop("_key", None)
    return best


def get_report_asof(codes: Iterable[str], as_of: str, *, db_path=None) -> dict[str, dict]:
    """``{code: {period, values, available_at, pit_basis, layout_key, file_md5}}``。

    ``as_of`` 为调用方算好的 aware 时点（ISO 或 SQLite 格式）。
    选取规则：``available_at ≤ as_of``；``retracted_at ≤ as_of`` 的 vintage 不参与；
    同 code 取最新报告期，同报告期取最新 vintage。
    """
    wanted = sorted({str(c) for c in (codes or []) if c})
    if not wanted:
        return {}
    conn = _open_read(db_path)
    if conn is None:
        return {}
    try:
        assets_db._ensure_schema(conn, migrate=False)
        rows = conn.execute(
            "SELECT report_period, code, file_md5, layout_key, values_json, "
            "available_at, pit_basis, first_seen_at FROM fin_report_records "
            "WHERE code IN (" + _placeholders(len(wanted)) + ") "
            "AND datetime(available_at) <= datetime(?) "
            "AND (retracted_at IS NULL OR datetime(retracted_at) > datetime(?))",
            [*wanted, as_of, as_of],
        ).fetchall()
    finally:
        conn.close()
    parsed = []
    for period, code, md5, layout_key, values_json, available, basis, first_seen in rows:
        values, error = _loads(values_json, {})
        if error:
            continue
        parsed.append({
            "report_period": period, "code": code, "file_md5": md5,
            "layout_key": layout_key, "values": values,
            "available_at": available, "pit_basis": basis,
            "first_seen_at": first_seen,
        })
    return _select_asof(parsed, as_of)


def count_retracted(codes: Iterable[str], as_of: str, *, db_path=None) -> int:
    """截至 ``as_of`` 已撤回（``retracted_at`` 生效）的 vintage 行数。

    只用于评估报告的 ``retracted_count``；撤回行不进入 as-of 样本。
    """
    wanted = sorted({str(c) for c in (codes or []) if c})
    if not wanted:
        return 0
    conn = _open_read(db_path)
    if conn is None:
        return 0
    try:
        assets_db._ensure_schema(conn, migrate=False)
        row = conn.execute(
            "SELECT COUNT(*) FROM fin_report_records "
            "WHERE code IN (" + _placeholders(len(wanted)) + ") "
            "AND retracted_at IS NOT NULL "
            "AND datetime(retracted_at) <= datetime(?)",
            [*wanted, as_of],
        ).fetchone()
    finally:
        conn.close()
    return int(row[0] or 0)


def get_period_asof(codes: Iterable[str], period: str, as_of: str, *,
                    db_path=None) -> dict[str, dict]:
    """指定报告期的 as-of 记录（同比 P-4Q 用）；选取规则同 ``get_report_asof``。"""
    wanted = sorted({str(c) for c in (codes or []) if c})
    if not wanted:
        return {}
    conn = _open_read(db_path)
    if conn is None:
        return {}
    try:
        assets_db._ensure_schema(conn, migrate=False)
        rows = conn.execute(
            "SELECT report_period, code, file_md5, layout_key, values_json, "
            "available_at, pit_basis, first_seen_at FROM fin_report_records "
            "WHERE report_period = ? AND code IN (" + _placeholders(len(wanted)) + ") "
            "AND datetime(available_at) <= datetime(?) "
            "AND (retracted_at IS NULL OR datetime(retracted_at) > datetime(?))",
            [period, *wanted, as_of, as_of],
        ).fetchall()
    finally:
        conn.close()
    parsed = []
    for row_period, code, md5, layout_key, values_json, available, basis, first_seen in rows:
        values, error = _loads(values_json, {})
        if error:
            continue
        parsed.append({
            "report_period": row_period, "code": code, "file_md5": md5,
            "layout_key": layout_key, "values": values,
            "available_at": available, "pit_basis": basis,
            "first_seen_at": first_seen,
        })
    return _select_asof(parsed, as_of)


def get_report_periods(*, db_path=None) -> list[str]:
    conn = _open_read(db_path)
    if conn is None:
        return []
    try:
        assets_db._ensure_schema(conn, migrate=False)
        rows = conn.execute(
            "SELECT DISTINCT report_period FROM fin_report_records "
            "ORDER BY report_period").fetchall()
    finally:
        conn.close()
    return [r[0] for r in rows]


def list_records_for_code(code: str, *, db_path=None) -> list[dict]:
    """某 code 的全部 vintage 行（按报告期降序），供观测→期次值级匹配。"""
    conn = _open_read(db_path)
    if conn is None:
        return []
    try:
        assets_db._ensure_schema(conn, migrate=False)
        rows = conn.execute(
            "SELECT report_period, code, file_md5, layout_key, values_json, "
            "available_at, pit_basis, first_seen_at, retracted_at "
            "FROM fin_report_records WHERE code = ? "
            "ORDER BY report_period DESC, available_at DESC",
            (code,)).fetchall()
    finally:
        conn.close()
    out = []
    for (period, code_value, md5, layout_key, values_json, available, basis,
         first_seen, retracted) in rows:
        values, error = _loads(values_json, {})
        if error:
            continue
        out.append({
            "report_period": period, "code": code_value, "file_md5": md5,
            "layout_key": layout_key, "values": values,
            "available_at": available, "pit_basis": basis,
            "first_seen_at": first_seen, "retracted_at": retracted,
        })
    return out


# ─── 字段校准 / 除权除息 ────────────────────────────────────────────────────


def upsert_field_calibration(layout_key: str, payload: dict, *, db_path=None) -> None:
    with assets_db._lock:
        conn = _open_write(db_path)
        try:
            conn.execute(
                "INSERT INTO fin_field_calibrations("
                "layout_key, num_fields, mapping_json, confidence_json, status, sample_n) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(layout_key) DO UPDATE SET "
                "num_fields=excluded.num_fields, mapping_json=excluded.mapping_json, "
                "confidence_json=excluded.confidence_json, status=excluded.status, "
                "sample_n=excluded.sample_n, "
                "calibrated_at=datetime('now','localtime')",
                (
                    layout_key, int(payload["num_fields"]),
                    assets_db._dumps(payload.get("mapping") or {}),
                    assets_db._dumps(payload.get("confidence") or {}),
                    payload["status"], int(payload.get("sample_n") or 0),
                ),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def _calibration_row(layout_key, num_fields, mapping_json, confidence_json,
                     status, sample_n, calibrated_at) -> dict:
    mapping, mapping_error = _loads(mapping_json, {})
    confidence, confidence_error = _loads(confidence_json, {})
    return {
        "layout_key": layout_key, "num_fields": num_fields,
        "mapping": mapping, "confidence": confidence, "status": status,
        "sample_n": sample_n, "calibrated_at": calibrated_at,
        "json_error": mapping_error or confidence_error,
    }


def get_field_calibration(layout_key: str, *, db_path=None) -> Optional[dict]:
    conn = _open_read(db_path)
    if conn is None:
        return None
    try:
        assets_db._ensure_schema(conn, migrate=False)
        row = conn.execute(
            "SELECT layout_key, num_fields, mapping_json, confidence_json, status, "
            "sample_n, calibrated_at FROM fin_field_calibrations WHERE layout_key = ?",
            (layout_key,)).fetchone()
    finally:
        conn.close()
    return _calibration_row(*row) if row else None


def list_field_calibrations(*, db_path=None) -> list[dict]:
    conn = _open_read(db_path)
    if conn is None:
        return []
    try:
        assets_db._ensure_schema(conn, migrate=False)
        rows = conn.execute(
            "SELECT layout_key, num_fields, mapping_json, confidence_json, status, "
            "sample_n, calibrated_at FROM fin_field_calibrations "
            "ORDER BY layout_key").fetchall()
    finally:
        conn.close()
    return [_calibration_row(*row) for row in rows]


def insert_xdxr_records(rows: Iterable[dict], *, db_path=None) -> int:
    payload = list(rows)
    if not payload:
        return 0
    written = 0
    with assets_db._lock:
        conn = _open_write(db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            for row in payload:
                cur = conn.execute(
                    "INSERT INTO fin_xdxr_records(code, ex_date, category, payload_json) "
                    "VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(code, ex_date, category) DO UPDATE SET "
                    "payload_json=excluded.payload_json",
                    (str(row["code"]), row["ex_date"], int(row["category"]),
                     assets_db._dumps(row.get("payload") or {})),
                )
                if cur.rowcount > 0:
                    written += 1
            conn.commit()
            return written
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


# ─── 状态 ──────────────────────────────────────────────────────────────────


def finance_status(*, db_path=None) -> dict:
    """``factor fin status`` 的存储统计；库缺失返回全零结构。"""
    empty = {
        "observed": {"rows": 0, "dates": 0, "last_date": None},
        "report_files": {"rows": 0, "periods": 0, "by_status": {}},
        "report_records": {"rows": 0, "periods": 0, "codes": 0, "retracted": 0,
                           "by_basis": {}},
        "calibrations": {"rows": 0, "verified": 0},
        "xdxr": {"rows": 0},
        "periods": [],
    }
    conn = _open_read(db_path)
    if conn is None:
        return empty
    try:
        assets_db._ensure_schema(conn, migrate=False)
        observed = conn.execute(
            "SELECT COUNT(*), COUNT(DISTINCT trade_date), MAX(trade_date) "
            "FROM fin_observed_snapshots").fetchone()
        files = conn.execute(
            "SELECT COUNT(*), COUNT(DISTINCT report_period) FROM fin_report_files"
        ).fetchone()
        by_status = {r[0]: r[1] for r in conn.execute(
            "SELECT status, COUNT(*) FROM fin_report_files GROUP BY status "
            "ORDER BY status").fetchall()}
        records = conn.execute(
            "SELECT COUNT(*), COUNT(DISTINCT report_period), COUNT(DISTINCT code), "
            "SUM(CASE WHEN retracted_at IS NOT NULL THEN 1 ELSE 0 END) "
            "FROM fin_report_records").fetchone()
        by_basis = {r[0]: r[1] for r in conn.execute(
            "SELECT pit_basis, COUNT(*) FROM fin_report_records GROUP BY pit_basis "
            "ORDER BY pit_basis").fetchall()}
        calib = conn.execute(
            "SELECT COUNT(*), SUM(CASE WHEN status = 'verified' THEN 1 ELSE 0 END) "
            "FROM fin_field_calibrations").fetchone()
        xdxr = conn.execute("SELECT COUNT(*) FROM fin_xdxr_records").fetchone()
        periods = [r[0] for r in conn.execute(
            "SELECT DISTINCT report_period FROM fin_report_records "
            "ORDER BY report_period").fetchall()]
        return {
            "observed": {"rows": observed[0] or 0, "dates": observed[1] or 0,
                         "last_date": observed[2]},
            "report_files": {"rows": files[0] or 0, "periods": files[1] or 0,
                             "by_status": by_status},
            "report_records": {"rows": records[0] or 0, "periods": records[1] or 0,
                               "codes": records[2] or 0,
                               "retracted": int(records[3] or 0),
                               "by_basis": by_basis},
            "calibrations": {"rows": calib[0] or 0, "verified": int(calib[1] or 0)},
            "xdxr": {"rows": xdxr[0] or 0},
            "periods": periods,
        }
    finally:
        conn.close()
