"""dragons 全量镜像（``assets.db.dragons``）：写 / 读 / 逐日对账 / 回填 / 差异检测。

契约（见技术方案 §4.2 / §4.3 / §4.9）：
  - **权威是 ``dragon.db.dragons_v2``**；镜像 best-effort，失败经
    ``assets backfill-dragons`` 幂等自愈。
  - **模块级 import ``infra.assets.db``、``infra.storage.connection`` 与
    ``infra.storage.dragons_rows``**。源库连接一律经 ``connection._connect()``
    模块属性调用，测试 patch ``connection._connect`` 能在运行期生效；不
    ``from ... import _connect``（否则 patch 目标被提前绑定）。
  - 镜像只承载 v2；``source != "v2"`` → :class:`DragonsMirrorSourceError`
    （``--source v1`` 由 ``market.candidate_loader`` 分派回源库，不走到这里）。
  - 读路径 ``_connect(create=False)``：assets.db 不存在时返回 ``None``，
    绝不因查询建库。
  - 锁序：本模块只取 ``assets_db._lock``；调用方**禁止**持 ``dragon._lock``
    时调用这里的任何写函数（``storage/dragons.py`` 的 fan-out 一律在锁外）。
"""

from __future__ import annotations

import json
from typing import Iterable, Optional

from dragon_quant.infra.storage import connection, dragons_rows
from dragon_quant.infra.assets import db as assets_db

MIRROR_TABLE = "dragons"
SOURCE_TABLE = "dragons_v2"

BACKFILL_HINT = "python -m dragon_quant assets backfill-dragons"

# 一致性比对列：源库与镜像的业务列（排除本机自增 id 与本地写入时间 created_at）。
BUSINESS_COLUMNS = dragons_rows.BUSINESS_COLUMNS
_SELECT_COLUMNS = ", ".join(BUSINESS_COLUMNS)
_CODE_INDEX = BUSINESS_COLUMNS.index("code")

# 回填批大小：按 trade_date 分事务，单日一次 executemany。
BACKFILL_BATCH = 1000


class DragonsMirrorError(RuntimeError):
    """镜像层错误基类。"""


class DragonsMirrorEmpty(DragonsMirrorError):
    """镜像整体为空而源库有行（尚未回填或镜像库损坏）。"""


class DragonsMirrorStale(DragonsMirrorError):
    """某日源库有行而镜像 0 行（取数/写入故障）。"""


class DragonsMirrorSourceError(ValueError):
    """镜像只承载 v2；传入其它 source 属调用方错误。"""


def _require_v2(source: str) -> str:
    normalized = (source or "v2").lower().strip()
    if normalized != "v2":
        raise DragonsMirrorSourceError(
            f"dragons 镜像只承载 v2（收到 source={source!r}）；"
            "--source v1 应经 candidate_loader 读 dragon.db.dragons_v1")
    return normalized


# ─── 连接门面 ──────────────────────────────────────────────────────────────


def _mirror_conn(*, create: bool, path=None):
    """assets.db 连接；``create=False`` 且库不存在 → None（绝不建库）。"""
    conn = assets_db._connect(create=create, path=path)
    if conn is None:
        return None
    try:
        assets_db._ensure_schema(conn, migrate=create)
    except Exception:
        conn.close()
        raise
    return conn


def _source_conn():
    """源库连接：经 ``connection`` 模块属性调用，保持 patch 语义。"""
    conn = connection._connect()
    connection._ensure_schema(conn)
    return conn


# ─── 写（供 storage/dragons.py 锁外调用；失败向上抛，由门面转警告）─────────


def save_dragons(trade_date: str, dragons: list[dict],
                 version: str = "", source: str = "v2") -> int:
    """镜像 UPSERT；返回写入行数。"""
    _require_v2(source)
    with assets_db._lock:
        conn = _mirror_conn(create=True)
        try:
            dragons_rows.upsert_dragons(conn, MIRROR_TABLE, trade_date, dragons,
                                       version, source)
            conn.commit()
        finally:
            conn.close()
    return len(dragons or [])


def update_dragon_review(trade_date: str, code: str, **fields) -> int:
    """镜像回写 review 字段；返回 rowcount（0 = 镜像缺行，需回填补齐）。"""
    with assets_db._lock:
        conn = _mirror_conn(create=True)
        try:
            rowcount = dragons_rows.update_review(
                conn, MIRROR_TABLE, trade_date, code, **fields)
            conn.commit()
        finally:
            conn.close()
    return rowcount


def delete_pending_dragons_not_in(trade_date: str, keep_codes: set[str]) -> int:
    """镜像同步删除被源库清理的 pending 行（否则残留行会污染候选池）。"""
    with assets_db._lock:
        conn = _mirror_conn(create=True)
        try:
            rowcount = dragons_rows.delete_pending_not_in(
                conn, MIRROR_TABLE, trade_date, keep_codes)
            conn.commit()
        finally:
            conn.close()
    return rowcount


# ─── 读（供消费方；签名与 db.* 兼容，source 只接受 v2）──────────────────────


def _row_to_dict(r) -> dict:
    """与 ``db.get_dragons`` / ``db.get_dragons_by_date`` 完全一致的返回形状。"""
    return {
        "code": r[0], "name": r[1], "scan_id": r[2], "rank": r[3],
        "composite_score": r[4], "board_count": r[5],
        "open_px": r[6], "close_px": r[7], "high_px": r[8], "low_px": r[9],
        "pct": r[10], "turnover_rate": r[11], "amount": r[12], "market_cap": r[13],
        "concepts": r[14], "report_text": r[15] or "",
        "buy_date": r[16], "buy_price": r[17],
        "max_return_5d": r[18], "max_drawdown_5d": r[19],
        "max_return_hold_days": r[20],
        "review_status": r[21],
        "version": r[22] or "",
        "is_true_dragon": bool(r[23]) if r[23] is not None else None,
        "source": "v2",
    }


def _decode_concepts(raw) -> list:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


def get_dragons(trade_date: str, source: str = "v2") -> list[dict]:
    """纯读取（**不抛 stale**）：prep 逐日读用它，由调用方做对账。"""
    _require_v2(source)
    conn = _mirror_conn(create=False)
    if conn is None:
        return []
    try:
        rows = conn.execute(
            "SELECT code, name, scan_id, rank, composite_score, board_count, "
            "open_px, close_px, high_px, low_px, pct, turnover_rate, amount, market_cap, "
            "concepts_json, report_text, "
            "buy_date, buy_price, max_return_5d, max_drawdown_5d, "
            "max_return_hold_days, review_status, version, is_true_dragon "
            f"FROM {MIRROR_TABLE} WHERE trade_date = ? ORDER BY composite_score DESC",
            (trade_date,),
        ).fetchall()
    finally:
        conn.close()
    out = []
    for r in rows:
        values = list(r)
        values[14] = _decode_concepts(r[14])
        out.append(_row_to_dict(values))
    return out


def get_dragons_by_date(trade_date: str, top_n: int = 5,
                        source: str = "v2") -> list[dict]:
    """候选池 loader：0 行且源库该日有行 → :class:`DragonsMirrorStale`（fail-fast）。

    理由：候选池缺一天会静默减少买点，比显式报错更危险（方案 §4.4 第 6 条）。
    """
    _require_v2(source)
    conn = _mirror_conn(create=False)
    rows: list = []
    if conn is not None:
        try:
            sql = (
                "SELECT trade_date, code, name, scan_id, rank, composite_score, "
                "board_count, open_px, close_px, high_px, low_px, pct, "
                "turnover_rate, amount, market_cap, concepts_json, report_text, "
                "is_true_dragon, version "
                f"FROM {MIRROR_TABLE} WHERE trade_date = ? "
                "ORDER BY COALESCE(rank, 999999) ASC, composite_score DESC"
            )
            params: list = [trade_date]
            if top_n:
                sql += " LIMIT ?"
                params.append(top_n)
            rows = conn.execute(sql, params).fetchall()
        finally:
            conn.close()
    if not rows and _source_day_count(trade_date):
        raise DragonsMirrorStale(
            f"{trade_date} 源库有龙头记录而镜像缺行（assets.db.dragons）；"
            f"请运行 {BACKFILL_HINT}")
    return [
        {
            "trade_date": r[0], "code": r[1], "name": r[2],
            "scan_id": r[3], "rank": r[4], "composite_score": r[5],
            "board_count": r[6], "open_px": r[7], "close_px": r[8],
            "high_px": r[9], "low_px": r[10], "pct": r[11],
            "turnover_rate": r[12], "amount": r[13], "market_cap": r[14],
            "concepts": _decode_concepts(r[15]),
            "report_text": r[16] or "",
            "is_true_dragon": bool(r[17]) if r[17] is not None else None,
            "version": r[18] or "",
            "source": "v2",
        }
        for r in rows
    ]


def list_dragon_trade_dates(source: str = "v2") -> list[str]:
    """镜像中有候选记录的交易日（升序）。"""
    _require_v2(source)
    conn = _mirror_conn(create=False)
    if conn is None:
        return []
    try:
        rows = conn.execute(
            f"SELECT DISTINCT trade_date FROM {MIRROR_TABLE} ORDER BY trade_date ASC"
        ).fetchall()
    finally:
        conn.close()
    return [r[0] for r in rows]


# ─── 逐日对账 / 守卫 ────────────────────────────────────────────────────────


def _source_day_count(trade_date: str) -> int:
    conn = _source_conn()
    try:
        row = conn.execute(
            f"SELECT COUNT(*) FROM {SOURCE_TABLE} WHERE trade_date = ?",
            (trade_date,),
        ).fetchone()
        return int(row[0]) if row else 0
    finally:
        conn.close()


def stale_dates(dates: Iterable[str]) -> dict[str, int]:
    """逐日对账 → ``{date: 源库行数}``，只含「源有行、镜像 0 行」的日期。

    零网络；每日期最多 2 次 COUNT（两侧 ``trade_date`` 均有索引）。
    这是**单向下限**：只发现「源有镜像无」，孤儿行（镜像有、源无）需
    ``backfill-dragons --dry-run`` 的 ``orphan_rows`` 或 ``--prune`` 发现。
    """
    wanted = sorted({str(d) for d in (dates or []) if d})
    if not wanted:
        return {}

    mirror_counts: dict[str, int] = {day: 0 for day in wanted}
    conn = _mirror_conn(create=False)
    if conn is not None:
        try:
            for day in wanted:
                row = conn.execute(
                    f"SELECT COUNT(*) FROM {MIRROR_TABLE} WHERE trade_date = ?",
                    (day,),
                ).fetchone()
                mirror_counts[day] = int(row[0]) if row else 0
        finally:
            conn.close()

    stale: dict[str, int] = {}
    source = _source_conn()
    try:
        for day in wanted:
            row = source.execute(
                f"SELECT COUNT(*) FROM {SOURCE_TABLE} WHERE trade_date = ?",
                (day,),
            ).fetchone()
            count = int(row[0]) if row else 0
            if count and not mirror_counts.get(day):
                stale[day] = count
    finally:
        source.close()
    return stale


def ensure_populated() -> None:
    """镜像 0 行且源库有行 → :class:`DragonsMirrorEmpty`（附可执行修复命令）。"""
    mirror_count = 0
    conn = _mirror_conn(create=False)
    if conn is not None:
        try:
            row = conn.execute(f"SELECT COUNT(*) FROM {MIRROR_TABLE}").fetchone()
            mirror_count = int(row[0]) if row else 0
        finally:
            conn.close()
    if mirror_count:
        return
    source = _source_conn()
    try:
        row = source.execute(f"SELECT COUNT(*) FROM {SOURCE_TABLE}").fetchone()
        source_count = int(row[0]) if row else 0
    finally:
        source.close()
    if source_count:
        raise DragonsMirrorEmpty(
            f"assets.db.dragons 为空而源库有 {source_count} 行；"
            f"请运行 {BACKFILL_HINT}")


# ─── 回填 / 差异检测 ────────────────────────────────────────────────────────


def _normalize_range(date_from, date_to) -> tuple[Optional[str], Optional[str]]:
    if date_from and date_to and date_from > date_to:
        raise ValueError(f"--from({date_from}) 不能晚于 --to({date_to})")
    return date_from, date_to


def _in_range(day: str, date_from, date_to) -> bool:
    if date_from and day < date_from:
        return False
    if date_to and day > date_to:
        return False
    return True


def _source_dates(conn) -> list[str]:
    return [r[0] for r in conn.execute(
        f"SELECT DISTINCT trade_date FROM {SOURCE_TABLE} ORDER BY trade_date").fetchall()]


def _mirror_dates(conn) -> list[str]:
    return [r[0] for r in conn.execute(
        f"SELECT DISTINCT trade_date FROM {MIRROR_TABLE} ORDER BY trade_date").fetchall()]


def _read_day_rows(conn, table: str, trade_date: str) -> dict[str, dict]:
    rows = conn.execute(
        f"SELECT {_SELECT_COLUMNS} FROM {table} WHERE trade_date = ? ORDER BY code",
        (trade_date,),
    ).fetchall()
    return {r[_CODE_INDEX]: dict(zip(BUSINESS_COLUMNS, r)) for r in rows}


def _diff_day(source_rows: dict[str, dict],
              mirror_rows: dict[str, dict]) -> dict[str, list[str]]:
    inserted = sorted(code for code in source_rows if code not in mirror_rows)
    orphans = sorted(code for code in mirror_rows if code not in source_rows)
    updated, unchanged = [], []
    for code in sorted(set(source_rows) & set(mirror_rows)):
        src, dst = source_rows[code], mirror_rows[code]
        if any(src.get(col) != dst.get(col) for col in BUSINESS_COLUMNS):
            updated.append(code)
        else:
            unchanged.append(code)
    return {"inserted": inserted, "updated": updated,
            "unchanged": unchanged, "orphans": orphans}


def _insert_or_replace(conn, trade_date: str, rows: dict[str, dict]) -> None:
    """把源库行**逐列原样**写入镜像（不改写 concepts_json 等文本）。"""
    # BUSINESS_COLUMNS 首列即 trade_date，无需额外拼接（避免重复列名）。
    columns = BUSINESS_COLUMNS
    placeholders = ", ".join("?" * len(columns))
    assignments = ", ".join(
        f"{col}=excluded.{col}" for col in BUSINESS_COLUMNS
        if col not in ("trade_date", "code"))
    sql = (f"INSERT INTO {MIRROR_TABLE}({', '.join(columns)}) "
           f"VALUES ({placeholders}) "
           f"ON CONFLICT(trade_date, code) DO UPDATE SET {assignments}")
    payload = []
    for row in rows.values():
        # 统一以入参 trade_date 为准（避免源行脏日期写入错误分区）。
        values = dict(row)
        values["trade_date"] = trade_date
        payload.append(tuple(values.get(col) for col in columns))
    if payload:
        conn.executemany(sql, payload)


def diff_dragons(*, date_from=None, date_to=None, db_path=None,
                 dry_run: bool = True, prune: bool = False) -> dict:
    """源库 ↔ 镜像逐日差异；``dry_run=False`` 时按源库收敛镜像（可选 prune）。

    唯一受支持的收敛路径是「源库 → 镜像」；镜像有、源库无的行只能被 ``prune``
    清除（源库是权威，dragons 行可由源库重建，不属不可再生资产）。
    """
    date_from, date_to = _normalize_range(date_from, date_to)
    result = {
        "ok": True, "mode": "backfill-dragons", "dry_run": bool(dry_run),
        "from": date_from, "to": date_to, "dates": [],
        "rows_source": 0, "inserted": 0, "updated": 0, "unchanged": 0,
        "missing_rows": 0, "orphan_rows": 0, "pruned": 0,
        "failed": [], "warnings": [],
    }

    source = _source_conn()
    try:
        mirror = _mirror_conn(create=not dry_run, path=db_path)
        if mirror is None:
            if dry_run:
                result["failed"].append({
                    "date": None,
                    "reason": f"assets.db 不存在（回填需先创建）：{BACKFILL_HINT}"})
                result["ok"] = False
                return result
            raise RuntimeError("assets.db 无法创建")
        try:
            source_dates = set(_source_dates(source))
            mirror_dates = set(_mirror_dates(mirror))
            dates = sorted(d for d in (source_dates | mirror_dates)
                           if _in_range(d, date_from, date_to))
            result["dates"] = dates
            for day in dates:
                try:
                    src_rows = _read_day_rows(source, SOURCE_TABLE, day)
                    dst_rows = _read_day_rows(mirror, MIRROR_TABLE, day)
                    diff = _diff_day(src_rows, dst_rows)
                    result["rows_source"] += len(src_rows)
                    result["inserted"] += len(diff["inserted"])
                    result["updated"] += len(diff["updated"])
                    result["unchanged"] += len(diff["unchanged"])
                    result["missing_rows"] += len(diff["inserted"])
                    result["orphan_rows"] += len(diff["orphans"])
                    if dry_run:
                        continue
                    changed = {code: src_rows[code]
                               for code in diff["inserted"] + diff["updated"]}
                    _insert_or_replace(mirror, day, changed)
                    if prune and diff["orphans"]:
                        placeholders = ", ".join("?" * len(diff["orphans"]))
                        cur = mirror.execute(
                            f"DELETE FROM {MIRROR_TABLE} WHERE trade_date = ? "
                            f"AND code IN ({placeholders})",
                            [day] + diff["orphans"])
                        result["pruned"] += cur.rowcount
                    mirror.commit()
                except Exception as exc:   # 单日失败继续，退出码由 CLI 判 1
                    mirror.rollback()
                    result["failed"].append({
                        "date": day, "reason": f"{type(exc).__name__}: {exc}"})
        finally:
            mirror.close()
    finally:
        source.close()

    if not dry_run and (result["orphan_rows"] and not prune):
        result["warnings"].append(
            f"orphan_rows={result['orphan_rows']}：镜像有、源库无的行（未被 prune）；"
            "确认后运行 backfill-dragons --prune 收敛")
    result["ok"] = not result["failed"]
    return result


def backfill_dragons(*, date_from=None, date_to=None, dry_run=False,
                     prune=False, db_path=None) -> dict:
    """幂等历史回填入口：``dragon.db.dragons_v2 → assets.db.dragons``。"""
    return diff_dragons(date_from=date_from, date_to=date_to,
                        db_path=db_path, dry_run=dry_run, prune=prune)
