"""append-only 事件/决策历史的有界只读读回（A4，窄 Facade）。

每个真实事件在 ``account_ledger_events`` 里保留完整 DecisionRecord（含未入选/
受限/未成交方案与原因）与执行结果；本模块只提供**只读分页**：稳定游标、显式
上限、按 ``ledger_id`` 隔离、事件/日期/修订范围过滤。

纪律：

- 查询不存在的 ledger **不建库/目录、不迁移、不写**（``_connect_readonly``）；
- 旧库缺新列时按 unknown 读回（``decision`` 为 ``None``，不伪造决策）；
- 坏 JSON/坏游标显式报错（``LedgerIntegrityError`` / ``LedgerError``），
  不返回空当成功；
- 重复事件由 ``(ledger_id, event_id)`` 主键保证不重复 append。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional

from dragon_quant.infra.storage._base import _connect_readonly

from .models import LedgerError, LedgerIntegrityError

MAX_PAGE = 200
DEFAULT_PAGE = 50
#: 本读取器能解释的记录版本；更高版本拒绝（避免把未来字段当已知语义）。
SUPPORTED_RECORD_VERSION = 1


def _loads(text, *, column: str):
    if text is None or text == "":
        return None
    try:
        value = json.loads(text)
    except (TypeError, ValueError) as exc:
        raise LedgerIntegrityError(f"损坏的 {column}: {exc}") from exc
    return value


@dataclass(frozen=True)
class HistoryRecord:
    """一条已提交事件的审计（decision 为 ``None`` 表示该事件无决策）。"""

    ledger_id: str
    event_id: str
    revision_after: int
    seq: int
    event_time: str
    trade_date: str
    source_hash: str
    content_hash: str
    decision: Optional[dict]
    execution: Optional[dict]
    record_version: Optional[int] = None

    def to_dict(self) -> dict:
        return {
            "ledger_id": self.ledger_id, "event_id": self.event_id,
            "revision_after": self.revision_after, "seq": self.seq,
            "event_time": self.event_time, "trade_date": self.trade_date,
            "source_hash": self.source_hash, "content_hash": self.content_hash,
            "decision": self.decision, "execution": self.execution,
            "record_version": self.record_version,
        }

    @property
    def cursor(self) -> str:
        return f"{self.revision_after}:{self.event_id}"


@dataclass(frozen=True)
class HistoryPage:
    records: tuple[HistoryRecord, ...]
    next_cursor: Optional[str]
    has_more: bool

    def to_dict(self) -> dict:
        return {"records": [item.to_dict() for item in self.records],
                "next_cursor": self.next_cursor, "has_more": self.has_more}


def _limit(value) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise LedgerError("history limit 必须是整数")
    if value <= 0 or value > MAX_PAGE:
        raise LedgerError(f"history limit 必须在 1..{MAX_PAGE} 内")
    return value


def _parse_cursor(cursor) -> Optional[tuple[int, str]]:
    if cursor is None:
        return None
    if not isinstance(cursor, str):
        raise LedgerError("history cursor 必须是字符串")
    revision, sep, event_id = cursor.partition(":")
    if not sep or not event_id:
        raise LedgerError(f"非法 history cursor: {cursor!r}")
    try:
        value = int(revision)
    except ValueError as exc:
        raise LedgerError(f"非法 history cursor revision: {revision!r}") from exc
    if value < 0:
        raise LedgerError(f"history cursor revision 不能为负: {revision!r}")
    return value, event_id


class LedgerHistory:
    """一个账本的有界只读历史仓储（不创建/不迁移/不写）。"""

    def __init__(self, ledger_id: str) -> None:
        if not isinstance(ledger_id, str) or not ledger_id:
            raise LedgerError("ledger_id 必须是非空字符串")
        self.ledger_id = ledger_id

    def read(self, *, limit: int = DEFAULT_PAGE,
             cursor: Optional[str] = None, event_id: Optional[str] = None,
             trade_date: Optional[str] = None, since_revision: Optional[int] = None,
             until_revision: Optional[int] = None) -> HistoryPage:
        limit = _limit(limit)
        after = _parse_cursor(cursor)
        conn = _connect_readonly()
        if conn is None:
            return HistoryPage(records=(), next_cursor=None, has_more=False)
        conn.row_factory = _row_factory()
        try:
            columns = _columns(conn)
            if not columns:
                return HistoryPage(records=(), next_cursor=None,
                                   has_more=False)
            sql = ["SELECT * FROM account_ledger_events WHERE ledger_id = ?"]
            params: list = [self.ledger_id]
            if event_id:
                sql.append("AND event_id = ?")
                params.append(str(event_id))
            if trade_date:
                sql.append("AND trade_date = ?")
                params.append(str(trade_date))
            if since_revision is not None:
                sql.append("AND revision_after >= ?")
                params.append(int(since_revision))
            if until_revision is not None:
                sql.append("AND revision_after <= ?")
                params.append(int(until_revision))
            if after is not None:
                sql.append(
                    "AND (revision_after > ? OR "
                    "(revision_after = ? AND event_id > ?))")
                params.extend([after[0], after[0], after[1]])
            sql.append("ORDER BY revision_after, event_id LIMIT ?")
            params.append(limit + 1)
            rows = conn.execute(" ".join(sql), params).fetchall()
        finally:
            conn.close()
        records = tuple(_record(row) for row in rows[:limit])
        has_more = len(rows) > limit
        next_cursor = records[-1].cursor if has_more and records else None
        return HistoryPage(records=records, next_cursor=next_cursor,
                           has_more=has_more)


def _columns(conn) -> Optional[set[str]]:
    """旧库确无该表 → ``None``（按 unknown 读回、不迁移）。

    P3：真库损坏（非法 SQLite / 权限 / I/O）不能吞成「空历史」——显式
    ``LedgerIntegrityError`` 并保留原始原因。
    """
    try:
        return {row[1] for row in conn.execute(
            "PRAGMA table_info(account_ledger_events)")}
    except Exception as exc:  # noqa: BLE001
        if "no such table" in str(exc).lower():
            return None
        raise LedgerIntegrityError(
            f"读取事件历史失败（存储损坏或不可读）: {exc}") from exc


def _row_factory():
    import sqlite3
    return sqlite3.Row


def _value(row, name: str, default=None):
    keys = row.keys() if hasattr(row, "keys") else ()
    return row[name] if name in keys else default


def _record(row) -> HistoryRecord:
    # 旧库缺 record_version/decision_json/execution_json：按 unknown 读回。
    decision = _loads(_value(row, "decision_json"), column="decision_json")
    execution = _loads(_value(row, "execution_json"), column="execution_json")
    version = _value(row, "record_version")
    if version is not None:
        if isinstance(version, bool) or not isinstance(version, int):
            raise LedgerIntegrityError(f"非法 record_version: {version!r}")
        if version > SUPPORTED_RECORD_VERSION:
            raise LedgerIntegrityError(
                f"不支持的 record_version {version}（本读取器支持 "
                f"{SUPPORTED_RECORD_VERSION}）")
    return HistoryRecord(
        ledger_id=str(row["ledger_id"]), event_id=str(row["event_id"]),
        revision_after=int(row["revision_after"]), seq=int(row["seq"] or 0),
        event_time=_value(row, "event_time", "") or "",
        trade_date=_value(row, "trade_date", "") or "",
        source_hash=str(row["source_hash"]),
        content_hash=str(row["content_hash"]),
        decision=decision if isinstance(decision, dict) and decision else None,
        execution=execution if isinstance(execution, dict) and execution else None,
        record_version=(None if version is None else int(version)))


__all__ = [
    "HistoryRecord", "HistoryPage", "LedgerHistory", "MAX_PAGE", "DEFAULT_PAGE",
]
