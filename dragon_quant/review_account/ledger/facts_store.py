"""账本运行事实的窄持久化门面（A1/A3）：标记与逐批事实的读写/序列化。

从 ``store.py`` 拆出，保持存储适配职责单一：本模块只接受已打开的连接与
``ledger_id``，不做事务控制、不建库、不 import 策略/运行时。事务边界仍由
:class:`~dragon_quant.review_account.ledger.store.AccountLedger` 统一持有。
"""

from __future__ import annotations

import json

from dragon_quant.review_account.execution_core.runtime_facts import (
    BatchFacts, MarkFact, RuntimeFactError,
)

from .models import LedgerIntegrityError


def _loads(text, *, column: str, default):
    if text is None or text == "":
        return default
    try:
        return json.loads(text)
    except (TypeError, ValueError) as exc:
        raise LedgerIntegrityError(f"损坏的 {column}: {exc}") from exc


def mark_facts_payload(facts) -> dict:
    return {code: fact.to_row() for code, fact in (facts or {}).items()}


def read_mark_facts(header) -> dict[str, MarkFact]:
    keys = header.keys() if hasattr(header, "keys") else ()
    text = header["mark_facts_json"] if "mark_facts_json" in keys else None
    raw = _loads(text, column="mark_facts_json", default={})
    if not isinstance(raw, dict):
        raise LedgerIntegrityError("mark_facts_json 必须是对象")
    try:
        return {str(code): MarkFact.from_row(dict(value))
                for code, value in raw.items()}
    except (RuntimeFactError, TypeError) as exc:
        raise LedgerIntegrityError(f"损坏的 mark facts: {exc}") from exc


def read_batch_facts(conn, ledger_id: str) -> dict[str, BatchFacts]:
    # 旧库尚未写运行事实时表可能不存在：读回 unknown（不建表、不猜历史）。
    if not table_exists(conn, "account_batch_facts"):
        return {}
    rows = conn.execute(
        "SELECT * FROM account_batch_facts WHERE ledger_id = ?",
        (ledger_id,)).fetchall()
    try:
        result: dict[str, BatchFacts] = {}
        for row in rows:
            data = dict(row)
            data["version"] = data.pop("facts_version", 1)
            result[row["batch_id"]] = BatchFacts.from_row(data)
        return result
    except RuntimeFactError as exc:
        raise LedgerIntegrityError(f"损坏的 batch facts: {exc}") from exc


def write_batch_facts(conn, ledger_id: str, facts) -> None:
    for fact in (facts or {}).values():
        _apply_batch_facts(conn, ledger_id, fact)


def _apply_batch_facts(conn, ledger_id: str, fact) -> None:
    row = conn.execute(
        "SELECT 1 FROM account_batch_facts WHERE ledger_id = ? AND batch_id = ?",
        (ledger_id, fact.batch_id)).fetchone()
    values = (fact.entry_source_ts, fact.peak_price, fact.peak_source_ts,
              fact.peak_available_at, int(bool(fact.took_profit_half)),
              int(bool(fact.first_day_seen)), fact.last_fill_event,
              int(fact.version))
    if row is not None:
        conn.execute(
            "UPDATE account_batch_facts SET entry_source_ts = ?, peak_price = ?, "
            "peak_source_ts = ?, peak_available_at = ?, took_profit_half = ?, "
            "first_day_seen = ?, last_fill_event = ?, facts_version = ?, "
            "updated_at = datetime('now','localtime') "
            "WHERE ledger_id = ? AND batch_id = ?",
            (*values, ledger_id, fact.batch_id))
        return
    conn.execute(
        "INSERT INTO account_batch_facts(ledger_id, batch_id, entry_source_ts, "
        "peak_price, peak_source_ts, peak_available_at, took_profit_half, "
        "first_day_seen, last_fill_event, facts_version) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (ledger_id, fact.batch_id, *values))


def table_exists(conn, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?",
        (name,)).fetchone()
    return row is not None


__all__ = ["mark_facts_payload", "read_mark_facts", "read_batch_facts",
           "write_batch_facts", "table_exists"]
