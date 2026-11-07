"""研究查询用的 prep / auction 快照只读仓储（``mode=ro``，不采集、不重算）。"""

from __future__ import annotations

import json
from typing import Optional

from dragon_quant.infra.assets import db as _assets_db
from dragon_quant.infra.storage.readonly import open_readonly


def _loads(text):
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        return {"_corrupt": f"{type(exc).__name__}: {exc}"}


def _nested_corrupt(value) -> bool:
    return isinstance(value, dict) and "_corrupt" in value


def _clamp(limit: int, offset: int) -> tuple[int, int]:
    return max(1, min(int(limit), 1001)), max(0, int(offset))


class CodeFilterUnsupported(ValueError):
    """prep payload 结构无法确定股票身份，不能做 code 过滤。"""


#: prep 快照中受支持的「结构化股票身份」路径。只认明确 code 键，绝不按
#: 金额/自由文本/指数代码做包含匹配。
_PREP_CODE_PATHS = (("candidates",), ("review_candidates",),
                    ("strategy_context", "positions"))


def _prep_codes(payload) -> Optional[set[str]]:
    """Return set of codes from supported structures, or ``None`` when unknown."""
    if not isinstance(payload, dict):
        return None
    found = False
    codes: set[str] = set()
    for path in _PREP_CODE_PATHS:
        node = payload
        for key in path:
            if not isinstance(node, dict):
                node = None
                break
            node = node.get(key)
        if isinstance(node, list):
            found = True
            for item in node:
                if isinstance(item, dict) and isinstance(item.get("code"), str):
                    codes.add(item["code"])
    return codes if found else None


def prep_snapshots(*, date_from: Optional[str] = None,
                   date_to: Optional[str] = None, code: Optional[str] = None,
                   include_payload: bool = False, limit: int = 100,
                   offset: int = 0) -> Optional[dict]:
    """Stored prep snapshots (source=v2 only).

    Returns ``{"rows": [...], "warnings": [...], "empty_reason": ...}``. With a
    ``code`` filter, rows are selected by structured ``candidates[]`` /
    ``review_candidates[]`` / ``strategy_context.positions[]`` code keys; an
    unrecognised historical structure is reported as ``code_filter_unsupported``
    instead of falling back to text search.
    """
    conn = open_readonly(_assets_db.ASSETS_DB_PATH)
    if conn is None:
        return None
    try:
        warnings: list[str] = []
        empty_reason = None
        limit, offset = _clamp(limit, offset)
        if code:
            sql = ("SELECT trade_date, source, created_at, result_json "
                   "FROM prep_snapshots "
                   "WHERE source = 'v2' "
                   "AND trade_date >= COALESCE(?, trade_date) "
                   "AND trade_date <= COALESCE(?, trade_date) "
                   "ORDER BY trade_date DESC, source ASC")
            all_rows = conn.execute(sql, [date_from, date_to]).fetchall()
            selected = []
            unsupported = 0
            corrupt = 0
            for row in all_rows:
                payload = _loads(row[3])
                if isinstance(payload, dict) and "_corrupt" in payload:
                    corrupt += 1
                    continue
                codes = _prep_codes(payload)
                if codes is None:
                    unsupported += 1
                    continue
                if code in codes:
                    selected.append(row)
            if unsupported:
                warnings.append(
                    f"prep_code_filter_unsupported_rows={unsupported}: "
                    "旧结构无法确定股票身份，已排除而不是文本匹配")
            if corrupt:
                warnings.append(
                    f"prep_snapshot_corrupt_rows={corrupt}: "
                    "result_json 损坏，无法确定股票身份")
            if not all_rows:
                empty_reason = "no_rows_in_scope"
            elif not selected and corrupt == len(all_rows):
                empty_reason = "corrupt_payload"
            elif not selected and unsupported == len(all_rows):
                empty_reason = "code_filter_unsupported"
            page = selected[offset: offset + limit]
        else:
            sql = ("SELECT trade_date, source, created_at, result_json "
                   "FROM prep_snapshots "
                   "WHERE source = 'v2' "
                   "AND trade_date >= COALESCE(?, trade_date) "
                   "AND trade_date <= COALESCE(?, trade_date) "
                   "ORDER BY trade_date DESC, source ASC LIMIT ? OFFSET ?")
            page = conn.execute(sql, [date_from, date_to, limit, offset]).fetchall()
        result = []
        for trade_date, source, created_at, payload_text in page:
            item = {"trade_date": trade_date, "source": source, "created_at": created_at}
            if include_payload:
                item["result"] = _loads(payload_text)
                item["_corrupt"] = _nested_corrupt(item["result"])
            result.append(item)
        return {"rows": result, "warnings": warnings, "empty_reason": empty_reason}
    finally:
        conn.close()


def auction_snapshots(*, date_from: Optional[str] = None,
                      date_to: Optional[str] = None, code: Optional[str] = None,
                      include_payload: bool = False, limit: int = 100,
                      offset: int = 0) -> Optional[list[dict]]:
    conn = open_readonly(_assets_db.ASSETS_DB_PATH)
    if conn is None:
        return None
    try:
        sql = ("SELECT trade_date, code, source, created_at, analysis_json "
               "FROM auction_snapshots WHERE source = 'v2'")
        params: list = []
        if date_from:
            sql += " AND trade_date >= ?"
            params.append(date_from)
        if date_to:
            sql += " AND trade_date <= ?"
            params.append(date_to)
        if code:
            sql += " AND code = ?"
            params.append(code)
        limit, offset = _clamp(limit, offset)
        sql += " ORDER BY trade_date DESC, code ASC, source ASC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        result = []
        for trade_date, row_code, source, created_at, analysis in conn.execute(sql, params):
            item = {"trade_date": trade_date, "code": row_code,
                    "source": source, "created_at": created_at}
            if include_payload:
                item["analysis"] = _loads(analysis)
                item["_corrupt"] = _nested_corrupt(item["analysis"])
            result.append(item)
        return result
    finally:
        conn.close()


def table_present(table: str) -> Optional[bool]:
    conn = open_readonly(_assets_db.ASSETS_DB_PATH)
    if conn is None:
        return None
    try:
        return conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,)).fetchone() is not None
    finally:
        conn.close()


# ─── 本地日 K（与 assets_db.list_stock_daily_* 同口径的严格只读版本）─────


def _load_json(text):
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None


def stock_daily_bar_dates(date_from: Optional[str] = None,
                          date_to: Optional[str] = None) -> list[str]:
    conn = open_readonly(_assets_db.ASSETS_DB_PATH)
    if conn is None:
        return []
    try:
        clauses = ["asset_type = 'stock_daily'"]
        params: list = []
        if date_from is not None:
            clauses.append("trade_date >= ?")
            params.append(date_from)
        if date_to is not None:
            clauses.append("trade_date <= ?")
            params.append(date_to)
        payloads = conn.execute(
            "SELECT payload_json FROM prep_assets WHERE "
            + " AND ".join(clauses) + " ORDER BY trade_date", tuple(params)).fetchall()
    finally:
        conn.close()
    dates: set[str] = set()
    for (payload_json,) in payloads:
        payload = _load_json(payload_json)
        rows = payload.get("rows") if isinstance(payload, dict) else None
        if not isinstance(rows, dict):
            continue
        for bars in rows.values():
            if not isinstance(bars, list):
                continue
            for bar in bars:
                if not isinstance(bar, dict):
                    continue
                day = str(bar.get("date") or "")
                if not day:
                    continue
                if date_from is not None and day < date_from:
                    continue
                if date_to is not None and day > date_to:
                    continue
                dates.add(day)
    return sorted(dates)


def stock_daily_bars(codes, date_from: str, date_to: str,
                     max_bars: int = 200000) -> dict:
    wanted = {str(code) for code in (codes or []) if code}
    if not wanted:
        return {"rows": {}, "truncated": False, "bar_dates": [], "codes": []}
    conn = open_readonly(_assets_db.ASSETS_DB_PATH)
    if conn is None:
        return {"rows": {}, "truncated": False, "bar_dates": [], "codes": []}
    try:
        payloads = conn.execute(
            "SELECT trade_date, payload_json FROM prep_assets "
            "WHERE asset_type = 'stock_daily' AND trade_date >= ? AND trade_date <= ? "
            "ORDER BY trade_date", (date_from, date_to)).fetchall()
    finally:
        conn.close()
    merged: dict[tuple[str, str], dict] = {}
    for _trade_date, payload_json in payloads:
        payload = _load_json(payload_json)
        rows = payload.get("rows") if isinstance(payload, dict) else None
        if not isinstance(rows, dict):
            continue
        for code, bars in rows.items():
            if str(code) not in wanted or not isinstance(bars, list):
                continue
            for bar in bars:
                if not isinstance(bar, dict):
                    continue
                day = str(bar.get("date") or "")
                if not day or day < date_from or day > date_to:
                    continue
                merged[(str(code), day)] = bar
    grouped: dict[str, list[dict]] = {}
    for (code, _day), bar in merged.items():
        grouped.setdefault(code, []).append(bar)
    for bars in grouped.values():
        bars.sort(key=lambda item: str(item.get("date") or ""))
    total = sum(len(bars) for bars in grouped.values())
    truncated = total > int(max_bars)
    if truncated:
        budget = max(0, int(max_bars))
        kept: dict[str, list[dict]] = {}
        ordered = sorted(grouped,
                         key=lambda code: (str(grouped[code][-1].get("date") or ""), code),
                         reverse=True)
        for code in ordered:
            bars = grouped[code]
            if len(bars) <= budget:
                kept[code] = bars
                budget -= len(bars)
        grouped = kept
    bar_dates = sorted({str(bar.get("date") or "") for bars in grouped.values()
                        for bar in bars})
    return {"rows": grouped, "truncated": truncated,
            "bar_dates": bar_dates, "codes": sorted(grouped)}
