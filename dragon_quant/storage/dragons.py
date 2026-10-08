"""龙头物化 / 回测 / VPA / Review 查询（dragons / vpa_analysis）持久化。"""

import json
from typing import Optional

from dragon_quant.storage._base import _connect, _ensure_schema, _lock, _normalize_source, _tables


def save_dragons(trade_date: str, dragons: list[dict], version: str = "",
                 source: str = "v2"):
    """保存或更新 dragons（UPSERT，不覆盖 review 字段）。

    source: 本次评分器体系（"v1"/"v2"），写入对应 dragons_v1 / dragons_v2。
    """
    with _lock:
        conn = _connect()
        try:
            _ensure_schema(conn)
            source = _normalize_source(source)
            t = _tables(source)
            
            rows = []
            for i, s in enumerate(dragons):
                concepts = s.get("concepts", [])
                rows.append((
                    trade_date,
                    s.get("code", ""),
                    s.get("name", ""),
                    s.get("scan_id", ""),
                    s.get("rank", i + 1),
                    s.get("composite_score", 0),
                    s.get("board_count", 0),
                    s.get("open_px"),
                    s.get("close_px"),
                    s.get("high_px"),
                    s.get("low_px"),
                    s.get("pct"),
                    s.get("turnover_rate"),
                    s.get("amount"),
                    s.get("market_cap"),
                    json.dumps(concepts, ensure_ascii=False),
                    s.get("report_text", ""),
                    1 if s.get("is_true_dragon") else 0 if "is_true_dragon" in s else None,
                    version,
                    source,
                ))
            
            conn.executemany(
                f"INSERT INTO {t['dragons']}("
                "trade_date, code, name, scan_id, rank, composite_score, board_count, "
                "open_px, close_px, high_px, low_px, pct, turnover_rate, amount, market_cap, "
                "concepts_json, report_text, is_true_dragon, version, source"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(trade_date, code) DO UPDATE SET "
                "name=excluded.name, "
                "scan_id=excluded.scan_id, "
                "rank=excluded.rank, "
                "composite_score=excluded.composite_score, "
                "board_count=excluded.board_count, "
                f"open_px=COALESCE(excluded.open_px, {t['dragons']}.open_px), "
                f"close_px=COALESCE(excluded.close_px, {t['dragons']}.close_px), "
                f"high_px=COALESCE(excluded.high_px, {t['dragons']}.high_px), "
                f"low_px=COALESCE(excluded.low_px, {t['dragons']}.low_px), "
                f"pct=COALESCE(excluded.pct, {t['dragons']}.pct), "
                f"turnover_rate=COALESCE(excluded.turnover_rate, {t['dragons']}.turnover_rate), "
                f"amount=COALESCE(excluded.amount, {t['dragons']}.amount), "
                f"market_cap=COALESCE(excluded.market_cap, {t['dragons']}.market_cap), "
                "concepts_json=excluded.concepts_json, "
                "report_text=excluded.report_text, "
                "is_true_dragon=excluded.is_true_dragon, "
                "version=excluded.version, "
                "source=excluded.source",
                rows,
            )
            conn.commit()
        finally:
            conn.close()


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
    """删除某 trade_date 下不在 keep_codes 内且 review_status='pending' 的记录。"""
    with _lock:
        conn = _connect()
        try:
            _ensure_schema(conn)
            source = _normalize_source(source)
            t = _tables(source)
            if not keep_codes:
                cur = conn.execute(
                    f"DELETE FROM {t['dragons']} WHERE trade_date = ? AND review_status = 'pending'",
                    (trade_date,),
                )
                conn.commit()
                return cur.rowcount

            placeholders = ",".join(["?"] * len(keep_codes))
            params = [trade_date] + sorted(keep_codes)
            cur = conn.execute(
                f"DELETE FROM {t['dragons']} WHERE trade_date = ? AND review_status = 'pending' AND code NOT IN ({placeholders})",
                params,
            )
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()


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
    from dragon_quant.utils.trading import trade_days_between
    from dragon_quant.storage.scans import list_scan_stock_contributions_by_date

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
            if r1 < r0:
                best_by_code[code] = c
            continue
        s1 = c.get("composite_score") if c.get("composite_score") is not None else 0
        s0 = cur.get("composite_score") if cur.get("composite_score") is not None else 0
        if s1 != s0:
            if s1 > s0:
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
    """更新单条 dragon 的 review 字段。"""
    with _lock:
        conn = _connect()
        try:
            _ensure_schema(conn)
            source = _normalize_source(source)
            t = _tables(source)
            conn.execute(
                f"UPDATE {t['dragons']} SET "
                "buy_date = ?, "
                "buy_price = ?, "
                "max_return_5d = ?, "
                "max_drawdown_5d = ?, "
                "max_return_hold_days = ?, "
                "review_status = ? "
                "WHERE trade_date = ? AND code = ?",
                (buy_date, buy_price, max_return_5d, max_drawdown_5d,
                 max_return_hold_days, review_status, trade_date, code),
            )
            conn.commit()
        finally:
            conn.close()


def upsert_vpa(trade_date: str, code: str,
               name: str = "",
               source: str = "",
               health_score: Optional[float] = None,
               signal: str = "",
               summary: str = "",
               factors_json: Optional[str] = None,
               version: str = ""):
    """写入/更新单条量价分析结果（按 trade_date + code 去重覆盖）。"""
    with _lock:
        conn = _connect()
        try:
            _ensure_schema(conn)
            conn.execute(
                "INSERT INTO vpa_analysis "
                "(trade_date, code, name, source, health_score, signal, "
                " summary, factors_json, version) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(trade_date, code) DO UPDATE SET "
                "name=excluded.name, source=excluded.source, "
                "health_score=excluded.health_score, signal=excluded.signal, "
                "summary=excluded.summary, factors_json=excluded.factors_json, "
                "version=excluded.version, "
                "created_at=datetime('now','localtime')",
                (trade_date, code, name, source, health_score, signal,
                 summary, factors_json, version),
            )
            conn.commit()
        finally:
            conn.close()


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


def query_dragons(filters: dict = None,
                  sort_by: str = "composite_score",
                  sort_dir: str = "desc",
                  source: str = "v2") -> list[dict]:
    """灵活查询 dragons 表（供 Web UI /api/dragons 使用）。

    Args:
        filters 支持的 key:
            code_like, name_like: LIKE 模糊匹配
            date_from, date_to: trade_date 范围
            score_min, score_max: composite_score 范围
            return_min, return_max: max_return_5d 范围
            status: list[str] 过滤 review_status
        sort_by: 排序字段
        sort_dir: asc / desc
    """
    if filters is None:
        filters = {}

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
        if "return_min" in filters:
            conditions.append("COALESCE(max_return_5d, -9999) >= ?")
            params.append(filters["return_min"])
        if "return_max" in filters:
            conditions.append("COALESCE(max_return_5d, 9999) <= ?")
            params.append(filters["return_max"])
        if "drawdown_min" in filters:
            conditions.append("COALESCE(max_drawdown_5d, -9999) >= ?")
            params.append(filters["drawdown_min"])
        if "drawdown_max" in filters:
            conditions.append("COALESCE(max_drawdown_5d, 9999) <= ?")
            params.append(filters["drawdown_max"])
        if filters.get("status"):
            placeholders = ",".join("?" * len(filters["status"]))
            conditions.append(f"review_status IN ({placeholders})")
            params.extend(filters["status"])

        if conditions:
            sql += " WHERE " + " AND ".join(conditions)

        # 排序字段白名单
        ALLOWED = {
            "trade_date", "code", "name", "rank", "composite_score",
            "board_count", "pct", "buy_date", "buy_price",
            "max_return_5d", "max_drawdown_5d", "max_return_hold_days",
            "review_status", "source",
        }
        order_col = sort_by if sort_by in ALLOWED and sort_by != "source" else "composite_score"
        order_dir = "DESC" if sort_dir.lower() == "desc" else "ASC"
        sql += f" ORDER BY {order_col} {order_dir} NULLS LAST"

        rows = conn.execute(sql, params).fetchall()
        result = [
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


def get_review_summary(source: str = "v2") -> dict:
    """返回 dragons 表中的汇总统计（供 Web UI /api/summary 使用）。"""
    conn = _connect()
    try:
        _ensure_schema(conn)
        source = _normalize_source(source)
        t = _tables(source)
        total = conn.execute(f"SELECT COUNT(*) FROM {t['dragons']}").fetchone()[0]

        completed = conn.execute(
            f"SELECT COUNT(*) FROM {t['dragons']} WHERE review_status = 'completed'"
        ).fetchone()[0]

        pending = conn.execute(
            f"SELECT COUNT(*) FROM {t['dragons']} WHERE review_status = 'pending'"
        ).fetchone()[0]

        avg_row = conn.execute(
            f"SELECT AVG(max_return_5d) FROM {t['dragons']} WHERE review_status = 'completed' AND max_return_5d IS NOT NULL"
        ).fetchone()

        win_row = conn.execute(
            f"SELECT COUNT(*) FROM {t['dragons']} "
            "WHERE review_status = 'completed' "
            "AND max_return_5d > 0 "
            "AND max_drawdown_5d > -5.0"
        ).fetchone()

        best = conn.execute(
            f"SELECT code, name, max_return_5d FROM {t['dragons']} "
            "WHERE review_status = 'completed' AND max_return_5d IS NOT NULL "
            "ORDER BY max_return_5d DESC LIMIT 1"
        ).fetchone()

        avg_return = round(avg_row[0], 2) if avg_row and avg_row[0] is not None else None
        win_count = win_row[0] if win_row else 0
        completed_count = completed if completed else 0
        win_rate = round(win_count / completed_count * 100, 1) if completed_count > 0 else None

        return {
            "total": total,
            "completed": completed,
            "pending": pending,
            "avg_return": avg_return,
            "win_rate": win_rate,
            "best_stock_code": best[0] if best else None,
            "best_stock_name": best[1] if best else None,
            "best_return": round(best[2], 2) if best and best[2] is not None else None,
            "source": source,
        }
    finally:
        conn.close()

