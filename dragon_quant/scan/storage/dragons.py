"""龙头物化 / 回测 / Review 查询（dragons）持久化。

``vpa_analysis`` 已迁至 :mod:`dragon_quant.vpa.storage`，本模块不再承载量价分析。

**双写契约**（见技术方案 §4.3）：
  - ``dragon.db.dragons_v2`` 是权威，主写在 ``with _lock:`` 内提交；
  - ``assets.db.dragons`` 是镜像，fan-out **在锁退出之后**执行（禁止持
    ``dragon._lock`` 时调用任何 assets.db 写，避免跨库锁序与 5s busy_timeout
    阻塞整库写入）；
  - 镜像失败只 stderr 警告 + 主流程继续，绝不回滚主写；``assets backfill-dragons``
    幂等修复。
  - v1 不进镜像（镜像只承载 v2），``source="v1"`` 时跳过 fan-out。

SQL 规则只有一份：``infra/storage/dragons_rows.py``（表名参数化，两库共用）。
"""

import json
import sys
from typing import Optional

from dragon_quant.infra.storage import dragons_rows
from dragon_quant.infra.storage._base import _connect, _ensure_schema, _lock, _normalize_source, _tables
from dragon_quant.scan.storage import review_strict


def _mirror_write(label: str, source: str, action) -> None:
    """锁外 best-effort 镜像写：失败只警告（含修复命令），不阻断主流程。"""
    if source != "v2":
        return
    try:
        from dragon_quant.infra.assets import dragons_mirror
        action(dragons_mirror)
    except Exception as exc:  # 镜像任何失败都不得影响主写结果
        print(f"[mirror] assets.db.dragons 写入失败（{label}）："
              f"{type(exc).__name__}: {exc}"
              "（可运行 assets backfill-dragons 修复）", file=sys.stderr)


def save_dragons(trade_date: str, dragons: list[dict], version: str = "",
                 source: str = "v2"):
    """保存或更新 dragons（UPSERT，不覆盖 review 字段）。

    source: 本次评分器体系（"v1"/"v2"），写入对应 dragons_v1 / dragons_v2；
    v2 主写提交后锁外镜像到 assets.db.dragons。
    """
    source = _normalize_source(source)
    with _lock:
        conn = _connect()
        try:
            _ensure_schema(conn)
            t = _tables(source)
            dragons_rows.upsert_dragons(conn, t["dragons"], trade_date, dragons,
                                       version, source)
            conn.commit()
        finally:
            conn.close()
    _mirror_write(
        "save_dragons", source,
        lambda mirror: mirror.save_dragons(trade_date, dragons,
                                           version=version, source="v2"))


def get_dragon_meta(trade_date: str, code: str, source: str = "v2") -> Optional[dict]:
    """返回指定 trade_date+code 的 rank/review_status，用于重建逻辑。"""
    conn = _connect()
    try:
        _ensure_schema(conn)
        source = _normalize_source(source)
        t = _tables(source)
        row = conn.execute(
            f"SELECT rank, review_status FROM {t['dragons']} WHERE trade_date = ? AND code = ?",
            (trade_date, code),
        ).fetchone()
        if not row:
            return None
        return {"rank": row[0], "review_status": row[1]}
    finally:
        conn.close()


def delete_pending_dragons_not_in(trade_date: str, keep_codes: set[str], source: str = "v2") -> int:
    """删除某 trade_date 下不在 keep_codes 内且 review_status='pending' 的记录。

    镜像必须同步删除，否则残留 pending 行会被窗口重新当成候选（静默污染买点）。
    """
    source = _normalize_source(source)
    with _lock:
        conn = _connect()
        try:
            _ensure_schema(conn)
            t = _tables(source)
            deleted = dragons_rows.delete_pending_not_in(
                conn, t["dragons"], trade_date, keep_codes)
            conn.commit()
        finally:
            conn.close()
    _mirror_write(
        "delete_pending_dragons_not_in", source,
        lambda mirror: mirror.delete_pending_dragons_not_in(trade_date, keep_codes))
    return deleted


def rebuild_dragons_for_date(
    trade_date: str,
    *,
    version: str,
    calendar: set[str],
    apply_5day_gate: bool = True,
    keep_completed: bool = True,
    source: str = "v2",
) -> dict:
    """按 trade_date 重建 dragons 为“当日所有扫描结果的并集”物化。

    注意：该过程会对 dragons 做 UPSERT，并会清理不再属于并集的 pending 记录。

    Returns:
        {"contrib_codes": int, "upserted": int, "kept": int, "deleted": int}
    """
    from dragon_quant.infra.market_calendar import trade_days_between
    from dragon_quant.scan.storage.scans import list_scan_stock_contributions_by_date

    source = _normalize_source(source)
    contribs = [c for c in list_scan_stock_contributions_by_date(trade_date, source=source)
                if c.get("is_true_dragon") is not False and c.get("rank") is not None
                and c["rank"] <= c["scan_top_n"]]
    if not contribs:
        deleted = delete_pending_dragons_not_in(trade_date, set(), source=source)
        return {"contrib_codes": 0, "upserted": 0, "kept": 0, "deleted": deleted}

    # 选“最佳贡献”：rank 最小 -> 分数最高 -> created_at 最新
    best_by_code: dict[str, dict] = {}
    for c in contribs:
        code = c.get("code")
        if not code:
            continue
        cur = best_by_code.get(code)
        if not cur:
            best_by_code[code] = c
            continue

        r1 = c.get("rank") if c.get("rank") is not None else 9999
        r0 = cur.get("rank") if cur.get("rank") is not None else 9999
        if r1 != r0:
            if r1 < r0:  # type: ignore[operator]
                best_by_code[code] = c
            continue
        s1 = c.get("composite_score") if c.get("composite_score") is not None else 0
        s0 = cur.get("composite_score") if cur.get("composite_score") is not None else 0
        if s1 != s0:
            if s1 > s0:  # type: ignore[operator]
                best_by_code[code] = c
            continue
        if (c.get("scan_created_at") or "") > (cur.get("scan_created_at") or ""):
            best_by_code[code] = c

    keep_codes: set[str] = set()
    to_upsert: list[dict] = []
    kept = 0
    gate_blocked = 0
    gate_kept_existing = 0
    gate_blocked_samples: list[str] = []

    for code, b in best_by_code.items():
        new_rank = b.get("rank") if b.get("rank") is not None else 9999

        allow_upsert = True
        if apply_5day_gate:
            last_info = get_last_entry_with_rank(code, source=source)
            if last_info:
                last_date, old_rank = last_info
                if last_date and trade_days_between(last_date, trade_date, calendar) < 5:
                    if old_rank is not None and new_rank < old_rank:
                        allow_upsert = True
                    else:
                        allow_upsert = False

        if allow_upsert:
            keep_codes.add(code)
            to_upsert.append({
                "code": code,
                "name": b.get("name", ""),
                "scan_id": b.get("scan_id", ""),
                "rank": new_rank,
                "composite_score": b.get("composite_score") or 0,
                "board_count": b.get("board_count") or 0,
                "open_px": b.get("open_px"),
                "close_px": b.get("close_px"),
                "high_px": b.get("high_px"),
                "low_px": b.get("low_px"),
                "pct": b.get("pct"),
                "turnover_rate": b.get("turnover_rate"),
                "amount": b.get("amount"),
                "market_cap": b.get("market_cap"),
                "concepts": b.get("concepts", []),
                "report_text": b.get("report_text", ""),
                **({"is_true_dragon": b["is_true_dragon"]} if b.get("is_true_dragon") is not None else {}),
            })
        else:
            meta = get_dragon_meta(trade_date, code, source=source)
            if meta:
                # 同日已存在记录，则保留（不更新）
                keep_codes.add(code)
                kept += 1
                gate_kept_existing += 1
            else:
                gate_blocked += 1
                if len(gate_blocked_samples) < 10:
                    gate_blocked_samples.append(code)

    if keep_completed:
        # completed 的永远不删，但 delete_pending 只删 pending，本身无需加进 keep_codes。
        pass

    if to_upsert:
        save_dragons(trade_date, to_upsert, version=version, source=source)
    deleted = delete_pending_dragons_not_in(trade_date, keep_codes, source=source)
    return {
        "contrib_codes": len(best_by_code),
        "upserted": len(to_upsert),
        "kept": kept,
        "deleted": deleted,
        "gate_blocked": gate_blocked,
        "gate_kept_existing": gate_kept_existing,
        "gate_blocked_samples": gate_blocked_samples,
    }


def get_dragons(trade_date: str, source: str = "v2") -> list[dict]:
    """获取某日的 dragons 数据"""
    conn = _connect()
    try:
        _ensure_schema(conn)
        source = _normalize_source(source)
        t = _tables(source)
        rows = conn.execute(
            "SELECT code, name, scan_id, rank, composite_score, board_count, "
            "open_px, close_px, high_px, low_px, pct, turnover_rate, amount, market_cap, "
            "concepts_json, report_text, "
            "buy_date, buy_price, max_return_5d, max_drawdown_5d, "
            "max_return_hold_days, review_status, version, is_true_dragon "
            f"FROM {t['dragons']} WHERE trade_date = ? ORDER BY composite_score DESC",
            (trade_date,),
        ).fetchall()
        
        return [
            {
                "code": r[0], "name": r[1], "scan_id": r[2], "rank": r[3],
                "composite_score": r[4], "board_count": r[5],
                "open_px": r[6], "close_px": r[7], "high_px": r[8], "low_px": r[9],
                "pct": r[10], "turnover_rate": r[11], "amount": r[12], "market_cap": r[13],
                "concepts": json.loads(r[14]) if r[14] else [],
                "report_text": r[15] or "",
                "buy_date": r[16], "buy_price": r[17],
                "max_return_5d": r[18], "max_drawdown_5d": r[19],
                "max_return_hold_days": r[20],
                "review_status": r[21],
                "version": r[22] or "",
                "is_true_dragon": bool(r[23]) if r[23] is not None else None,
                "source": source,
            }
            for r in rows
        ]
    finally:
        conn.close()


def get_last_entry(code: str, source: str = "v2") -> Optional[str]:
    """返回该 code 最近一次入选的 trade_date，无记录则返回 None。"""
    conn = _connect()
    try:
        _ensure_schema(conn)
        source = _normalize_source(source)
        t = _tables(source)
        row = conn.execute(
            f"SELECT trade_date FROM {t['dragons']} WHERE code = ? "
            "ORDER BY trade_date DESC LIMIT 1",
            (code,),
        ).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def get_last_entry_with_rank(code: str, source: str = "v2") -> Optional[tuple]:
    """返回该 code 最近一次入选的 (trade_date, rank)，无记录则返回 None。"""
    conn = _connect()
    try:
        _ensure_schema(conn)
        source = _normalize_source(source)
        t = _tables(source)
        row = conn.execute(
            f"SELECT trade_date, rank FROM {t['dragons']} WHERE code = ? "
            "ORDER BY trade_date DESC LIMIT 1",
            (code,),
        ).fetchone()
        return (row[0], row[1]) if row else None
    finally:
        conn.close()


def get_pending_dragons(trade_date: Optional[str] = None,
                        top_n: Optional[int] = None,
                        review_status: Optional[str] = "pending",
                        source: str = "v2") -> list[dict]:
    """获取待 review 的 dragons 记录。

    review_status='pending' 时只取待回测记录；传入 None 则不做状态过滤。
    可按 trade_date 和 top_n 过滤。
    """
    conn = _connect()
    try:
        _ensure_schema(conn)
        source = _normalize_source(source)
        t = _tables(source)
        sql = (
            "SELECT trade_date, code, name, scan_id, rank, composite_score, "
            "board_count, open_px, close_px, high_px, low_px, pct, "
            "turnover_rate, amount, market_cap, concepts_json, report_text, "
            "buy_date, buy_price, max_return_5d, max_drawdown_5d, "
            "max_return_hold_days, review_status, version, is_true_dragon "
            f"FROM {t['dragons']}"
        )
        params: list = []
        conditions: list[str] = []
        if review_status is not None:
            conditions.append("review_status = ?")
            params.append(review_status)
        if trade_date:
            conditions.append("trade_date = ?")
            params.append(trade_date)
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        if top_n:
            sql += " ORDER BY composite_score DESC LIMIT ?"
            params.append(top_n)
        else:
            sql += " ORDER BY trade_date DESC, composite_score DESC"

        rows = conn.execute(sql, params).fetchall()
        return [
            {
                "trade_date": r[0], "code": r[1], "name": r[2],
                "scan_id": r[3], "rank": r[4], "composite_score": r[5],
                "board_count": r[6], "open_px": r[7], "close_px": r[8],
                "high_px": r[9], "low_px": r[10], "pct": r[11],
                "turnover_rate": r[12], "amount": r[13], "market_cap": r[14],
                "concepts": json.loads(r[15]) if r[15] else [],
                "report_text": r[16] or "",
                "buy_date": r[17], "buy_price": r[18],
                "max_return_5d": r[19], "max_drawdown_5d": r[20],
                "max_return_hold_days": r[21],
                "review_status": r[22],
                "version": r[23] or "",
                "is_true_dragon": bool(r[24]) if r[24] is not None else None,
                "source": source,
            }
            for r in rows
        ]
    finally:
        conn.close()


def list_dragon_trade_dates(date_from: Optional[str] = None,
                            date_to: Optional[str] = None,
                            source: str = "v2") -> list[str]:
    """返回 dragons 表中有候选记录的交易日列表。"""
    conn = _connect()
    try:
        _ensure_schema(conn)
        source = _normalize_source(source)
        t = _tables(source)
        sql = f"SELECT DISTINCT trade_date FROM {t['dragons']}"
        params: list = []
        conditions: list[str] = []
        if date_from:
            conditions.append("trade_date >= ?")
            params.append(date_from)
        if date_to:
            conditions.append("trade_date <= ?")
            params.append(date_to)
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        sql += " ORDER BY trade_date ASC"
        return [r[0] for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def get_dragons_by_date(trade_date: str,
                        top_n: int = 5,
                        source: str = "v2") -> list[dict]:
    """返回某交易日入选龙头，供账户级 review 作为每日候选池。"""
    conn = _connect()
    try:
        _ensure_schema(conn)
        source = _normalize_source(source)
        t = _tables(source)
        sql = (
            "SELECT trade_date, code, name, scan_id, rank, composite_score, "
            "board_count, open_px, close_px, high_px, low_px, pct, "
            "turnover_rate, amount, market_cap, concepts_json, report_text, "
            "is_true_dragon, version "
            f"FROM {t['dragons']} WHERE trade_date = ? "
            "ORDER BY COALESCE(rank, 999999) ASC, composite_score DESC"
        )
        params: list = [trade_date]
        if top_n:
            sql += " LIMIT ?"
            params.append(top_n)
        rows = conn.execute(sql, params).fetchall()
        return [
            {
                "trade_date": r[0], "code": r[1], "name": r[2],
                "scan_id": r[3], "rank": r[4], "composite_score": r[5],
                "board_count": r[6], "open_px": r[7], "close_px": r[8],
                "high_px": r[9], "low_px": r[10], "pct": r[11],
                "turnover_rate": r[12], "amount": r[13], "market_cap": r[14],
                "concepts": json.loads(r[15]) if r[15] else [],
                "report_text": r[16] or "",
                "is_true_dragon": bool(r[17]) if r[17] is not None else None,
                "version": r[18] or "",
                "source": source,
            }
            for r in rows
        ]
    finally:
        conn.close()


def update_dragon_review(trade_date: str, code: str,
                         buy_date: Optional[str] = None,
                         buy_price: Optional[float] = None,
                         max_return_5d: Optional[float] = None,
                         max_drawdown_5d: Optional[float] = None,
                         max_return_hold_days: Optional[int] = None,
                         review_status: str = "completed",
                         source: str = "v2"):
    """更新单条 dragon 的 review 字段（主写 + 锁外镜像）。

    镜像影响 0 行 → 记 ``mirror_missing_row`` 警告（回填可补齐），不抛异常。
    """
    source = _normalize_source(source)
    fields = {
        "buy_date": buy_date,
        "buy_price": buy_price,
        "max_return_5d": max_return_5d,
        "max_drawdown_5d": max_drawdown_5d,
        "max_return_hold_days": max_return_hold_days,
        "review_status": review_status,
    }
    with _lock:
        conn = _connect()
        try:
            _ensure_schema(conn)
            t = _tables(source)
            dragons_rows.update_review(conn, t["dragons"], trade_date, code, **fields)
            conn.commit()
        finally:
            conn.close()

    if source == "v2":
        def _mirror(mirror):
            rowcount = mirror.update_dragon_review(trade_date, code, **fields)
            if rowcount == 0:
                print(f"[mirror] mirror_missing_row: assets.db.dragons 缺行 "
                      f"{trade_date}/{code}（review 字段未镜像，可用 "
                      "assets backfill-dragons 修复）", file=sys.stderr)

        _mirror_write("update_dragon_review", source, _mirror)


def save_dual_review(trade_date: str, code: str, *,
                     loose: dict,
                     strict: Optional[dict] = None,
                     source: str = "v2"):
    """在同一 ``dragon.db`` 事务内写宽松（dragons_*）+ 严格（侧表）结果。

    ``strict=None`` 表示本次不写严格结果（传输失败 / 历史不足等），已有严格行
    保持不变。任何写入失败整体回滚，避免只写一半。镜像仍在锁外 best-effort。
    """
    source = _normalize_source(source)
    fields = {
        "buy_date": loose.get("buy_date"),
        "buy_price": loose.get("buy_price"),
        "max_return_5d": loose.get("max_return_5d"),
        "max_drawdown_5d": loose.get("max_drawdown_5d"),
        "max_return_hold_days": loose.get("max_return_hold_days"),
        "review_status": loose.get("status", "completed"),
    }
    with _lock:
        conn = _connect()
        try:
            _ensure_schema(conn)
            t = _tables(source)
            dragons_rows.update_review(conn, t["dragons"], trade_date, code, **fields)
            if strict is not None:
                review_strict.upsert(
                    conn,
                    trade_date=trade_date,
                    code=code,
                    source=source,
                    buy_date=strict.get("buy_date"),
                    buy_price=strict.get("buy_price"),
                    max_return_5d=strict.get("max_return_5d"),
                    max_drawdown_5d=strict.get("max_drawdown_5d"),
                    max_return_hold_days=strict.get("max_return_hold_days"),
                    review_status=strict["review_status"],
                    reason=strict.get("reason"),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    if source == "v2":
        def _mirror(mirror):
            rowcount = mirror.update_dragon_review(trade_date, code, **fields)
            if rowcount == 0:
                print(f"[mirror] mirror_missing_row: assets.db.dragons 缺行 "
                      f"{trade_date}/{code}（review 字段未镜像，可用 "
                      "assets backfill-dragons 修复）", file=sys.stderr)

        _mirror_write("save_dual_review", source, _mirror)


def _parse_version(v: str, parts: Optional[int] = None) -> Optional[tuple]:
    """解析版本号字符串为整数元组，无法解析返回 None。

    "1.2.3" -> (1, 2, 3)；"1" -> (1,)；"v0.2.4" 前导 v 会被忽略。
    parts 指定截断/补齐到几位（不足补 0，超过则截断）。
    """
    if not v:
        return None
    v = v.strip().lstrip("vV")
    if not v:
        return None
    nums: list[int] = []
    for seg in v.split("."):
        seg = seg.strip()
        if seg == "":
            continue
        try:
            nums.append(int(seg))
        except ValueError:
            return None
    if not nums:
        return None
    if parts is not None:
        nums = (nums + [0] * parts)[:parts]
    return tuple(nums)


def _version_in_range(version: str,
                      vmin: Optional[str],
                      vmax: Optional[str]) -> bool:
    """按用户输入的精度比较版本号是否落在 [vmin, vmax] 内。

    用户输入位数决定比较精度：输入 "2" 只比 major，"2.1" 比 major.minor，
    依此类推；超过 3 位只看前三位。空界限表示该侧不限制。
    """
    for bound, is_min in ((vmin, True), (vmax, False)):
        if not bound:
            continue
        precision = min(len([s for s in bound.strip().lstrip("vV").split(".") if s != ""]), 3)
        if precision <= 0:
            continue
        b = _parse_version(bound, precision)
        rec = _parse_version(version, precision)
        if b is None:
            continue
        if rec is None:
            return False
        if is_min and rec < b:
            return False
        if not is_min and rec > b:
            return False
    return True


def query_dragons(filters: Optional[dict] = None,
                  sort_by: str = "composite_score",
                  sort_dir: str = "desc",
                  source: str = "v2") -> list[dict]:
    """灵活查询 dragons 表（供 Web UI /api/dragons 使用）。

    v2 额外 LEFT JOIN ``dragon_review_strict``：收益 / 回撤 / 状态筛选与排序
    **固定使用严格口径**；v1 没有严格口径，继续使用 ``dragons_v1`` 旧字段。
    旧的宽松字段原样保留，新增 ``strict_*`` 字段与 ``strict_reviewed``。

    Args:
        filters 支持的 key:
            code_like, name_like: LIKE 模糊匹配
            date_from, date_to: trade_date 范围
            score_min, score_max: composite_score 范围
            return_min, return_max: v2=严格收益 / v1=宽松收益
            drawdown_min, drawdown_max: v2=严格回撤 / v1=宽松回撤
            status: list[str] 过滤派生状态
        sort_by: 排序字段（v2 的严格键见 ``_SORT_MAP``）
        sort_dir: asc / desc
    """
    if filters is None:
        filters = {}

    conn = _connect()
    try:
        _ensure_schema(conn)
        source = _normalize_source(source)
        t = _tables(source)

        base = _query_base_sql(t["dragons"], source)
        params: list = []
        conditions: list[str] = []

        if filters.get("code_like"):
            conditions.append("code LIKE ?")
            params.append(f"%{filters['code_like']}%")
        if filters.get("name_like"):
            conditions.append("name LIKE ?")
            params.append(f"%{filters['name_like']}%")
        if filters.get("date_from"):
            conditions.append("trade_date >= ?")
            params.append(filters["date_from"])
        if filters.get("date_to"):
            conditions.append("trade_date <= ?")
            params.append(filters["date_to"])
        if "score_min" in filters:
            conditions.append("composite_score >= ?")
            params.append(filters["score_min"])
        if "score_max" in filters:
            conditions.append("composite_score <= ?")
            params.append(filters["score_max"])

        return_col = "strict_max_return_5d" if source == "v2" else "max_return_5d"
        drawdown_col = "strict_max_drawdown_5d" if source == "v2" else "max_drawdown_5d"
        if "return_min" in filters:
            conditions.append(f"COALESCE({return_col}, -9999) >= ?")
            params.append(filters["return_min"])
        if "return_max" in filters:
            conditions.append(f"COALESCE({return_col}, 9999) <= ?")
            params.append(filters["return_max"])
        if "drawdown_min" in filters:
            conditions.append(f"COALESCE({drawdown_col}, -9999) >= ?")
            params.append(filters["drawdown_min"])
        if "drawdown_max" in filters:
            conditions.append(f"COALESCE({drawdown_col}, 9999) <= ?")
            params.append(filters["drawdown_max"])
        if filters.get("status"):
            placeholders = ",".join("?" * len(filters["status"]))
            conditions.append(f"derived_status IN ({placeholders})")
            params.extend(filters["status"])

        sql = f"SELECT * FROM ({base}) q"
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)

        order_col = _SORT_MAP[source].get(sort_by, "composite_score")
        order_dir = "DESC" if sort_dir.lower() == "desc" else "ASC"
        sql += f" ORDER BY {order_col} {order_dir} NULLS LAST"

        rows = conn.execute(sql, params).fetchall()
        result = []
        for raw in rows:
            r = dict(zip(_RESULT_COLUMNS, raw))
            concepts_raw = r["concepts_json"]
            result.append({
                "trade_date": r["trade_date"], "code": r["code"],
                "name": r["name"], "scan_id": r["scan_id"], "rank": r["rank"],
                "composite_score": r["composite_score"],
                "board_count": r["board_count"],
                "open_px": r["open_px"], "close_px": r["close_px"],
                "high_px": r["high_px"], "low_px": r["low_px"], "pct": r["pct"],
                "turnover_rate": r["turnover_rate"], "amount": r["amount"],
                "market_cap": r["market_cap"],
                "concepts": json.loads(concepts_raw) if concepts_raw else [],
                "report_text": r["report_text"] or "",
                "buy_date": r["buy_date"], "buy_price": r["buy_price"],
                "max_return_5d": r["max_return_5d"],
                "max_drawdown_5d": r["max_drawdown_5d"],
                "max_return_hold_days": r["max_return_hold_days"],
                "review_status": r["review_status"],
                "version": r["version"] or "",
                "is_true_dragon": (bool(r["is_true_dragon"])
                                   if r["is_true_dragon"] is not None else None),
                "source": source,
                "strict_buy_date": r["strict_buy_date"],
                "strict_buy_price": r["strict_buy_price"],
                "strict_max_return_5d": r["strict_max_return_5d"],
                "strict_max_drawdown_5d": r["strict_max_drawdown_5d"],
                "strict_max_return_hold_days": r["strict_max_return_hold_days"],
                "strict_review_status": r["strict_review_status"],
                "strict_review_reason": r["strict_review_reason"],
                "strict_reviewed": r["strict_review_status"] is not None,
                # 派生状态：completed / pending / pending_recompute /
                # cannot_evaluate / error（v1 等于旧 review_status）。
                "derived_status": r["derived_status"],
            })

        # 版本号范围过滤（语义化比较，需在 Python 端处理）
        vmin = filters.get("version_min")
        vmax = filters.get("version_max")
        if vmin or vmax:
            result = [
                d for d in result
                if _version_in_range(d["version"], vmin, vmax)
            ]
        return result
    finally:
        conn.close()


_BASE_COLUMNS: tuple[str, ...] = (
    "trade_date", "code", "name", "scan_id", "rank", "composite_score",
    "board_count", "open_px", "close_px", "high_px", "low_px", "pct",
    "turnover_rate", "amount", "market_cap", "concepts_json", "report_text",
    "buy_date", "buy_price", "max_return_5d", "max_drawdown_5d",
    "max_return_hold_days", "review_status", "version", "is_true_dragon",
)

_STRICT_COLUMNS: tuple[str, ...] = (
    "strict_buy_date", "strict_buy_price", "strict_max_return_5d",
    "strict_max_drawdown_5d", "strict_max_return_hold_days",
    "strict_review_status", "strict_review_reason", "derived_status",
)

_RESULT_COLUMNS: tuple[str, ...] = _BASE_COLUMNS + _STRICT_COLUMNS

_SORT_MAP: dict[str, dict[str, str]] = {
    "v2": {
        "trade_date": "trade_date", "code": "code", "name": "name",
        "rank": "rank", "composite_score": "composite_score",
        "board_count": "board_count", "pct": "pct",
        "buy_date": "strict_buy_date",
        "buy_price": "strict_buy_price",
        "max_return_5d": "strict_max_return_5d",
        "max_drawdown_5d": "strict_max_drawdown_5d",
        "max_return_hold_days": "strict_max_return_hold_days",
        "review_status": "derived_status",
        "strict_buy_date": "strict_buy_date",
        "strict_buy_price": "strict_buy_price",
        "strict_max_return_5d": "strict_max_return_5d",
        "strict_max_drawdown_5d": "strict_max_drawdown_5d",
        "strict_max_return_hold_days": "strict_max_return_hold_days",
        "strict_review_status": "derived_status",
    },
    "v1": {
        "trade_date": "trade_date", "code": "code", "name": "name",
        "rank": "rank", "composite_score": "composite_score",
        "board_count": "board_count", "pct": "pct",
        "buy_date": "buy_date", "buy_price": "buy_price",
        "max_return_5d": "max_return_5d",
        "max_drawdown_5d": "max_drawdown_5d",
        "max_return_hold_days": "max_return_hold_days",
        "review_status": "derived_status",
        # 前端统一用严格列 key；v1 映射回旧字段（无严格口径）。
        "strict_buy_date": "buy_date",
        "strict_buy_price": "buy_price",
        "strict_max_return_5d": "max_return_5d",
        "strict_max_drawdown_5d": "max_drawdown_5d",
        "strict_max_return_hold_days": "max_return_hold_days",
        "strict_review_status": "derived_status",
    },
}


def _query_base_sql(table: str, source: str) -> str:
    """构造统一结果集的子查询（v2 JOIN 严格侧表 / v1 全部 NULL）。"""
    if source == "v2":
        strict_cols = (
            "s.buy_date AS strict_buy_date, "
            "s.buy_price AS strict_buy_price, "
            "s.max_return_5d AS strict_max_return_5d, "
            "s.max_drawdown_5d AS strict_max_drawdown_5d, "
            "s.max_return_hold_days AS strict_max_return_hold_days, "
            "s.review_status AS strict_review_status, "
            "s.reason AS strict_review_reason, "
            "CASE "
            "WHEN s.review_status IS NOT NULL THEN s.review_status "
            "WHEN d.review_status = 'pending' THEN 'pending' "
            "WHEN d.review_status = 'error' THEN 'error' "
            "ELSE 'pending_recompute' END AS derived_status "
        )
        join = (
            " FROM {table} d LEFT JOIN dragon_review_strict s "
            "ON s.trade_date = d.trade_date AND s.code = d.code AND s.source = 'v2'"
        ).format(table=table)
    else:
        strict_cols = (
            "NULL AS strict_buy_date, NULL AS strict_buy_price, "
            "NULL AS strict_max_return_5d, NULL AS strict_max_drawdown_5d, "
            "NULL AS strict_max_return_hold_days, NULL AS strict_review_status, "
            "NULL AS strict_review_reason, "
            "d.review_status AS derived_status "
        )
        join = f" FROM {table} d"

    cols = ", ".join(f"d.{name}" for name in _BASE_COLUMNS)
    return f"SELECT {cols}, {strict_cols}{join}"


def _row_derived_status(row: dict, source: str) -> Optional[str]:
    """读取 rows 携带的派生状态，缺失时按同一 CASE 规则回算。"""
    status = row.get("derived_status")
    if status is not None:
        return status
    if source == "v1":
        return row.get("review_status")
    if row.get("strict_review_status"):
        return row["strict_review_status"]
    if row.get("review_status") == "pending":
        return "pending"
    if row.get("review_status") == "error":
        return "error"
    return "pending_recompute"


def _mean_or_none(values: list[float]) -> Optional[float]:
    """均值并保留两位小数；空样本返回 ``None``（不伪造 0）。"""
    if not values:
        return None
    return round(sum(values) / len(values), 2)


def _win(row: dict, ret_key: str, drawdown_key: str) -> bool:
    """胜利条件（两种口径一致）：收益 > 0 且回撤 > -5（严格大于）。

    NULL 参与比较时 SQL 结果为 NULL（不计入），因此这里也直接返回 False。
    """
    ret = row.get(ret_key)
    drawdown = row.get(drawdown_key)
    return (ret is not None and drawdown is not None
            and ret > 0 and drawdown > -5.0)


def _best_row(rows: list[dict], ret_key: str) -> Optional[dict]:
    """收益最高的一行；并列按 code / trade_date 升序，保证确定性。"""
    candidates = [r for r in rows if r.get(ret_key) is not None]
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda r: (-r[ret_key], r.get("code") or "",
                       r.get("trade_date") or ""),
    )


def summarize_review_rows(rows: list[dict],
                          source: str = "v2",
                          *,
                          include_derived_counts: bool = False) -> dict:
    """从 ``query_dragons`` 的完整 rows 计算汇总统计（筛选后同源）。

    Web UI 的 7 张卡片必须与列表使用同一份筛选结果，因此汇总只消费同一批
    rows，不再单独发 SQL 统计。口径与 ``get_review_summary`` 的历史实现逐键
    等价（正常数据无孤儿严格行）：

    - 旧键（``avg_return`` / ``win_rate`` / ``best_*``）为**宽松口径**；
      平均收益只取非 NULL，胜率分母是全部宽松 completed；
    - 严格键（``avg_return_strict`` / ``win_rate_strict``）取严格侧表列；
    - 宽松胜率分母 = 宽松 completed **且存在严格行且严格非 cannot_evaluate**；
    - ``pending_recompute`` / ``failed_recompute`` 只统计无严格行的记录；
    - v1 没有严格侧表，严格键返回 ``None`` / ``0``。

    空集或无可评测样本返回 ``None``（显示为 —），真实无赢家返回 ``0.0``。
    ``include_derived_counts`` 为 True 时附加 ``derived_completed`` /
    ``derived_pending``（``pending`` + ``pending_recompute``）——仅供 UI 卡片
    在同一响应内展示派生状态计数，不属于 legacy ``/api/summary`` 契约。
    """
    source = _normalize_source(source)
    total = len(rows)

    loose_completed = [r for r in rows if r.get("review_status") == "completed"]
    pending = sum(1 for r in rows if r.get("review_status") == "pending")
    loose_wins = sum(1 for r in loose_completed
                     if _win(r, "max_return_5d", "max_drawdown_5d"))
    best = _best_row(loose_completed, "max_return_5d")

    summary = {
        "total": total,
        "completed": len(loose_completed),
        "pending": pending,
        "avg_return": _mean_or_none([
            r["max_return_5d"] for r in loose_completed
            if r.get("max_return_5d") is not None
        ]),
        "win_rate": (round(loose_wins / len(loose_completed) * 100, 1)
                     if loose_completed else None),
        "best_stock_code": best.get("code") if best else None,
        "best_stock_name": best.get("name") if best else None,
        "best_return": (round(best["max_return_5d"], 2)
                        if best and best.get("max_return_5d") is not None
                        else None),
        "source": source,
    }

    if source == "v2":
        strict_completed = [
            r for r in rows
            if r.get("strict_reviewed")
            and r.get("strict_review_status") == "completed"
        ]
        strict_wins = sum(1 for r in strict_completed
                          if _win(r, "strict_max_return_5d",
                                  "strict_max_drawdown_5d"))
        loose_den = [
            r for r in loose_completed
            if r.get("strict_reviewed")
            and r.get("strict_review_status") != "cannot_evaluate"
        ]
        loose_den_wins = sum(1 for r in loose_den
                             if _win(r, "max_return_5d", "max_drawdown_5d"))
        best_strict = _best_row(strict_completed, "strict_max_return_5d")
        summary.update({
            "avg_return_strict": _mean_or_none([
                r["strict_max_return_5d"] for r in strict_completed
                if r.get("strict_max_return_5d") is not None
            ]),
            "win_rate_loose": (round(loose_den_wins / len(loose_den) * 100, 1)
                               if loose_den else None),
            "win_rate_strict": (round(strict_wins / len(strict_completed) * 100, 1)
                                if strict_completed else None),
            "win_rate_loose_n": len(loose_den),
            "win_rate_strict_n": len(strict_completed),
            "cannot_evaluate": sum(
                1 for r in rows
                if r.get("strict_review_status") == "cannot_evaluate"),
            "pending_recompute": sum(
                1 for r in rows
                if not r.get("strict_reviewed")
                and r.get("review_status") in ("completed", "no_entry")),
            "failed_recompute": sum(
                1 for r in rows
                if not r.get("strict_reviewed")
                and r.get("review_status") == "error"),
            "best_stock_code_strict": (
                best_strict.get("code") if best_strict else None),
            "best_stock_name_strict": (
                best_strict.get("name") if best_strict else None),
            "best_return_strict": (
                round(best_strict["strict_max_return_5d"], 2)
                if best_strict
                and best_strict.get("strict_max_return_5d") is not None
                else None),
        })
    else:
        summary.update({
            "avg_return_strict": None,
            "win_rate_loose": None,
            "win_rate_strict": None,
            "win_rate_loose_n": 0,
            "win_rate_strict_n": 0,
            "cannot_evaluate": 0,
            "pending_recompute": 0,
            "failed_recompute": 0,
            "best_stock_code_strict": None,
            "best_stock_name_strict": None,
            "best_return_strict": None,
        })

    if include_derived_counts:
        statuses = [_row_derived_status(r, source) for r in rows]
        summary["derived_completed"] = sum(
            1 for s in statuses if s == "completed")
        summary["derived_pending"] = sum(
            1 for s in statuses if s in ("pending", "pending_recompute"))

    return summary


def get_review_summary(source: str = "v2") -> dict:
    """返回 dragons 表的全量汇总统计（供 Web UI legacy ``/api/summary``）。

    兼容 wrapper：与列表使用同一 ``query_dragons`` 全量 rows，再由
    :func:`summarize_review_rows` 计算，保证正常数据下旧键形状与值不变。
    孤儿严格行不属于权威集合（严格侧只统计能 join 到 dragon 行的记录）。
    """
    source = _normalize_source(source)
    rows = query_dragons({}, source=source)
    return summarize_review_rows(rows, source=source)
