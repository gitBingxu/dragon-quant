"""账户批次账本持久化（dragon.db）：revision/CAS 提交、命名空间隔离、只读查询。

固定事务结构（主验收 L1/L5）：``load_state`` 携带 ``revision``；提交时以
``expected_revision`` 做 CAS，过期/乱序状态显式拒绝（调用方 reload 后重算）。
所有子表主键按 ``(ledger_id, …)`` 命名空间，跨账户不可 REPLACE 窃取；批次/订单
的不可变身份字段以内容哈希校验，同 ID 异内容显式冲突；状态/剩余等可迁移字段
才允许更新。事件表记录 ``source_hash``（实际输入行情/时间/版本），同事件同内容
幂等、异内容冲突。
"""

from __future__ import annotations

import hashlib
import json
from typing import Iterable, Optional

from dragon_quant.infra.storage._base import (
    _connect, _connect_readonly, _ensure_schema,
)
from .facts_store import (
    mark_facts_payload, read_batch_facts, read_mark_facts, write_batch_facts,
)
from .models import (
    SELLABLE_AFTER, SELLABLE_ON, SELLABLE_UNKNOWN, Batch, Fill,
    LedgerConflictError, LedgerError, LedgerIntegrityError, LedgerRevisionError,
    LedgerState, Order, Reservation,
)

LEDGER_SCHEMA_VERSION = 6


def _canonical(payload) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False)


def _header(header, name: str, default=None):
    """旧库缺新列时按 unknown 读回（不迁移猜历史）。"""
    try:
        keys = header.keys()
    except AttributeError:
        return default
    if name not in keys:
        return default
    return header[name]


def _hash(payload) -> str:
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def _loads(text, *, column: str, default):
    if text is None or text == "":
        return default
    try:
        return json.loads(text)
    except (TypeError, ValueError) as exc:
        raise LedgerIntegrityError(f"损坏的 {column}: {exc}") from exc


def _row_factory():
    import sqlite3
    return sqlite3.Row


class AccountLedger:
    """一个账户事件账本（``ledger_id`` 为命名空间，唯一）。"""

    def __init__(self, ledger_id: str) -> None:
        if not isinstance(ledger_id, str) or not ledger_id:
            raise LedgerError("ledger_id 必须是非空字符串")
        self.ledger_id = ledger_id

    # ── 写：创建 / CAS 原子事件提交 ─────────────────────────────────────────
    def create(self, *, initial_cash: float, strategy_ref=None,
               config_hash: str = "", snapshot_id: str = "",
               status: str = "running", expected_events: int = 0) -> None:
        if isinstance(initial_cash, bool) or initial_cash < 0:
            raise LedgerError("initial_cash 不能为负")
        conn = _connect()
        try:
            _ensure_schema(conn)
            row = conn.execute(
                "SELECT initial_cash, strategy_ref_json, config_hash, snapshot_id "
                "FROM account_ledgers WHERE ledger_id = ?",
                (self.ledger_id,)).fetchone()
            payload = (_canonical(strategy_ref or {}), config_hash, snapshot_id)
            if row is not None:
                if (float(row[0]) != float(initial_cash)
                        or (row[1], row[2], row[3]) != payload):
                    raise LedgerConflictError(
                        "同 ledger 已存在且初始现金/归属/配置不同，拒绝静默忽略")
                return
            conn.execute(
                "INSERT INTO account_ledgers(ledger_id, revision, initial_cash, "
                "cash, config_hash, snapshot_id, strategy_ref_json, run_status, "
                "expected_events, completed_events, watermark, failed_cursor) "
                "VALUES (?, 0, ?, ?, ?, ?, ?, ?, ?, 0, -1, -1)",
                (self.ledger_id, float(initial_cash), float(initial_cash),
                 config_hash, snapshot_id, payload[0], str(status),
                 int(expected_events)))
            conn.commit()
        finally:
            conn.close()

    def commit_event(self, *, expected_revision: int, event_id: str,
                     source_hash: str, content_hash: str, state: LedgerState,
                     fills: Iterable[Fill] = (),
                     decision_record: Optional[dict] = None,
                     execution_record: Optional[dict] = None) -> str:
        """CAS 提交一个事件；返回 ``applied`` / ``duplicate``。

        真实事件、标记新鲜度、逐批运行事实、闭合交易统计、历史 DecisionRecord
        与订单/成交/批次在**同一**事务里落账（A3）；冲突/失败不提前推进。
        """
        if not event_id:
            raise LedgerError("event_id 不能为空")
        conn = _connect()
        try:
            _ensure_schema(conn)
            conn.isolation_level = None
            conn.execute("BEGIN IMMEDIATE")
            try:
                header = conn.execute(
                    "SELECT revision FROM account_ledgers WHERE ledger_id = ?",
                    (self.ledger_id,)).fetchone()
                if header is None:
                    conn.execute("ROLLBACK")
                    raise LedgerError(f"账本 {self.ledger_id} 尚未创建")
                if int(header[0]) != int(expected_revision):
                    conn.execute("ROLLBACK")
                    raise LedgerRevisionError(
                        f"账本 revision 已变（期望 {expected_revision}，实际 "
                        f"{header[0]}）；请 reload 后重算")
                existing = conn.execute(
                    "SELECT source_hash, content_hash FROM account_ledger_events "
                    "WHERE ledger_id = ? AND event_id = ?",
                    (self.ledger_id, event_id)).fetchone()
                if existing is not None:
                    conn.execute("ROLLBACK")
                    if existing[0] == source_hash and existing[1] == content_hash:
                        return "duplicate"
                    raise LedgerConflictError(
                        f"事件 {event_id} 已存在但输入/内容不同（拒绝覆盖）")
                self._verify_and_apply(conn, state, fills)
                new_revision = int(expected_revision) + 1
                conn.execute(
                    "UPDATE account_ledgers SET revision = ?, cash = ?, "
                    "peak_equity = ?, drawdown_pct = ?, consecutive_losses = ?, "
                    "event_cursor = ?, event_time = ?, trade_date = ?, "
                    "recovery_stage = ?, policy_states_json = ?, marks_json = ?, "
                    "recovery_json = ?, last_decision_json = ?, "
                    "mark_facts_json = ?, trade_stats_json = ?, "
                    "closed_trades_json = ?, fund_credits_json = ?, "
                    "config_hash = ?, "
                    "snapshot_id = ?, strategy_ref_json = ?, "
                    "updated_at = datetime('now','localtime') "
                    "WHERE ledger_id = ? AND revision = ?",
                    (new_revision, float(state.cash), float(state.peak_equity),
                     float(state.drawdown_pct), int(state.consecutive_losses),
                     int(state.event_cursor), state.event_time, state.trade_date,
                     int(state.recovery_stage),
                     _canonical(list(state.policy_states)),
                     _canonical(dict(state.marks)),
                     _canonical(dict(state.recovery or {})),
                     _canonical(dict(state.last_decision or {})),
                     _canonical(mark_facts_payload(state.mark_facts)),
                     _canonical(dict(state.trade_stats or {})),
                     _canonical(list(state.closed_trades or ())),
                     _canonical(dict(state.fund_credits or {})),
                     state.config_hash, state.snapshot_id,
                     _canonical(_thaw(state.strategy_ref)),
                     self.ledger_id, int(expected_revision)))
                conn.execute(
                    "INSERT INTO account_ledger_events(ledger_id, event_id, "
                    "source_hash, content_hash, revision_after, seq, event_time, "
                    "trade_date, record_version, decision_json, execution_json) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (self.ledger_id, event_id, source_hash, content_hash,
                     new_revision, int(state.event_cursor), state.event_time,
                     state.trade_date, 1,
                     _canonical(dict(decision_record or {})),
                     _canonical(dict(execution_record or {}))))
                conn.execute("COMMIT")
                return "applied"
            except BaseException:
                try:
                    conn.execute("ROLLBACK")
                except Exception:  # noqa: BLE001
                    pass
                raise
        finally:
            conn.close()

    def _verify_and_apply(self, conn, state: LedgerState,
                          fills: Iterable[Fill]) -> None:
        for batch in state.batches:
            self._apply_batch(conn, batch)
        for order in state.orders:
            self._apply_order(conn, order)
        for reservation in state.reservations:
            self._apply_reservation(conn, reservation)
        for fill in fills:
            self._apply_fill(conn, fill)
        write_batch_facts(conn, self.ledger_id, state.batch_facts)

    def _apply_batch(self, conn, batch: Batch) -> None:
        row = conn.execute(
            "SELECT content_hash, remaining FROM account_batches "
            "WHERE ledger_id = ? AND batch_id = ?",
            (self.ledger_id, batch.batch_id)).fetchone()
        if row is not None:
            if row[0] != batch.content_hash:
                raise LedgerConflictError(
                    f"批次 {batch.batch_id} 不可变身份与既有记录冲突")
            conn.execute(
                "UPDATE account_batches SET remaining = ? "
                "WHERE ledger_id = ? AND batch_id = ?",
                (batch.remaining, self.ledger_id, batch.batch_id))
            return
        item = batch.to_row()
        conn.execute(
            "INSERT INTO account_batches(ledger_id, batch_id, code, qty, "
            "remaining, entry_date, sellable_mode, sellable_date, cost_price, "
            "fee, initial_risk, risk_themes_json, strategy_ref_json, plan_id, "
            "obligation_id, fill_id, source, family_id, content_hash) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (self.ledger_id, item["batch_id"], item["code"], item["qty"],
             item["remaining"], item["entry_date"], item["sellable_mode"],
             item["sellable_date"], item["cost_price"], item["fee"],
             item["initial_risk"], _canonical(item["risk_themes"]),
             _canonical(item["strategy_ref"]),
             item["plan_id"], item["obligation_id"], item["fill_id"],
             item["source"], item.get("family_id", ""), item["content_hash"]))

    def _apply_order(self, conn, order: Order) -> None:
        row = conn.execute(
            "SELECT content_hash FROM account_orders "
            "WHERE ledger_id = ? AND order_id = ?",
            (self.ledger_id, order.order_id)).fetchone()
        if row is not None:
            if row[0] != order.content_hash:
                raise LedgerConflictError(
                    f"订单 {order.order_id} 不可变身份与既有记录冲突")
            conn.execute(
                "UPDATE account_orders SET filled_qty = ?, status = ?, "
                "reserved_amount = ?, reserved_risk = ?, locked_qty = ? "
                "WHERE ledger_id = ? AND order_id = ?",
                (order.filled_qty, order.status, order.reserved_amount,
                 order.reserved_risk, order.locked_qty, self.ledger_id,
                 order.order_id))
            return
        item = order.to_row()
        conn.execute(
            "INSERT INTO account_orders(ledger_id, order_id, code, side, qty, "
            "filled_qty, status, limit_price, reserved_amount, reserved_risk, "
            "locked_qty, created_event, valid_until_event, trigger_kind, "
            "trigger_price, evidence_rule, evidence_ids_json, depends_on_json, "
            "sequence, risk_amount, risk_themes_json, strategy_ref_json, plan_id, "
            "obligation_id, batch_ids_json, family_id, invalidate_below, reason, "
            "content_hash) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
            "?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (self.ledger_id, item["order_id"], item["code"], item["side"],
             item["qty"], item["filled_qty"], item["status"],
             item["limit_price"], item["reserved_amount"], item["reserved_risk"],
             item["locked_qty"], item["created_event"],
             item["valid_until_event"], item["trigger_kind"],
             item["trigger_price"], item["evidence_rule"],
             _canonical(item["evidence_ids"]), _canonical(item["depends_on"]),
             item["sequence"], item["risk_amount"],
             _canonical(item["risk_themes"]),
             _canonical(item["strategy_ref"]),
             item["plan_id"], item["obligation_id"],
             _canonical(item["batch_ids"]), item["family_id"],
             item["invalidate_below"], item["reason"],
             item["content_hash"]))

    def _apply_reservation(self, conn, reservation: Reservation) -> None:
        row = conn.execute(
            "SELECT code, side, price FROM account_reservations "
            "WHERE ledger_id = ? AND order_id = ?",
            (self.ledger_id, reservation.order_id)).fetchone()
        if row is not None:
            # 不可变身份：code/side/price；qty/amount/status 可随部分成交迁移。
            if (row[0], row[1], row[2]) != (
                    reservation.code, reservation.side, reservation.price):
                raise LedgerConflictError(
                    f"预留 {reservation.order_id} 不可变身份与既有记录冲突")
            conn.execute(
                "UPDATE account_reservations SET qty = ?, amount = ?, status = ?, "
                "purpose = ?, pressure_pct = ?, risk_amount = ?, "
                "valid_until_event = ? WHERE ledger_id = ? AND order_id = ?",
                (reservation.qty, reservation.amount, reservation.status,
                 reservation.purpose, reservation.pressure_pct,
                 reservation.risk_amount, reservation.valid_until_event,
                 self.ledger_id, reservation.order_id))
            return
        item = reservation.to_row()
        conn.execute(
            "INSERT INTO account_reservations(ledger_id, order_id, code, side, "
            "qty, amount, status, purpose, price, theme_ids_json, pressure_pct, "
            "risk_amount, valid_until_event) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (self.ledger_id, item["order_id"], item["code"], item["side"],
             item["qty"], item["amount"], item["status"], item["purpose"],
             item["price"], _canonical(item["theme_ids"]),
             item["pressure_pct"], item["risk_amount"],
             item["valid_until_event"]))

    def _apply_fill(self, conn, fill: Fill) -> None:
        digest = fill.content_hash
        row = conn.execute(
            "SELECT content_hash FROM account_fills "
            "WHERE ledger_id = ? AND fill_id = ?",
            (self.ledger_id, fill.fill_id)).fetchone()
        if row is not None:
            if row[0] == digest:
                return
            raise LedgerConflictError(
                f"成交腿 {fill.fill_id} 已存在但内容不同（拒绝覆盖）")
        item = fill.to_row(include_hash=False)
        conn.execute(
            "INSERT INTO account_fills(ledger_id, fill_id, order_id, code, side, "
            "price, qty, fee, event_id, event_hash, batch_id, realized_pnl, "
            "content_hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (self.ledger_id, item["fill_id"], item["order_id"], item["code"],
             item["side"], item["price"], item["qty"], item["fee"],
             item["event_id"], item["event_hash"], item["batch_id"],
             item["realized_pnl"], digest))

    # ── 读：不创建、不初始化 ────────────────────────────────────────────────
    def update_run_status(self, *, status: Optional[str] = None,
                          completed_events: Optional[int] = None,
                          watermark: Optional[int] = None,
                          failure_reason: Optional[str] = None,
                          failed_cursor: Optional[int] = None) -> None:
        """更新运行生命周期（completed/failed/水位），不改变账务 revision。"""
        conn = _connect()
        try:
            _ensure_schema(conn)
            fields, params = [], []
            for name, value in (("run_status", status),
                                ("completed_events", completed_events),
                                ("watermark", watermark),
                                ("failure_reason", failure_reason),
                                ("failed_cursor", failed_cursor)):
                if value is not None:
                    fields.append(f"{name} = ?")
                    params.append(value)
            if not fields:
                return
            params.append(self.ledger_id)
            conn.execute(
                f"UPDATE account_ledgers SET {', '.join(fields)}, "
                "updated_at = datetime('now','localtime') WHERE ledger_id = ?",
                tuple(params))
            conn.commit()
        finally:
            conn.close()

    def run_status(self) -> Optional[dict]:
        conn = _connect_readonly()
        if conn is None:
            return None
        conn.row_factory = _row_factory()
        try:
            row = conn.execute(
                "SELECT run_status, expected_events, completed_events, "
                "watermark, failure_reason, failed_cursor FROM account_ledgers "
                "WHERE ledger_id = ?", (self.ledger_id,)).fetchone()
            if row is None:
                return None
            return {"status": row[0], "expected_events": int(row[1]),
                    "completed_events": int(row[2]), "watermark": int(row[3]),
                    "failure_reason": row[4], "failed_cursor": int(row[5])}
        finally:
            conn.close()

    def load_state(self) -> Optional[LedgerState]:
        conn = _connect_readonly()
        if conn is None:
            return None
        conn.row_factory = _row_factory()
        try:
            try:
                header = conn.execute(
                    "SELECT * FROM account_ledgers WHERE ledger_id = ?",
                    (self.ledger_id,)).fetchone()
            except Exception as exc:  # noqa: BLE001
                if "no such table" in str(exc):
                    return None
                raise LedgerIntegrityError(f"读取账本头失败: {exc}") from exc
            if header is None:
                return None
            return LedgerState(
                cash=float(header["cash"]),
                revision=int(header["revision"]),
                batches=self._read_batches(conn),
                orders=self._read_orders(conn),
                reservations=self._read_reservations(conn),
                marks=_loads(_header(header, "marks_json"),
                             column="marks_json", default={}),
                event_cursor=int(header["event_cursor"]),
                event_time=header["event_time"] or "",
                trade_date=header["trade_date"] or "",
                peak_equity=float(header["peak_equity"] or 0.0),
                drawdown_pct=float(header["drawdown_pct"] or 0.0),
                consecutive_losses=int(header["consecutive_losses"] or 0),
                recovery_stage=int(header["recovery_stage"] or 0),
                recovery=_loads(_header(header, "recovery_json"),
                                column="recovery_json", default={}),
                policy_states=list(_loads(_header(header, "policy_states_json"),
                                          column="policy_states_json",
                                          default=[])),
                last_decision=_loads(_header(header, "last_decision_json"),
                                     column="last_decision_json", default={}),
                mark_facts=read_mark_facts(header),
                batch_facts=read_batch_facts(conn, self.ledger_id),
                trade_stats=_loads(_header(header, "trade_stats_json"),
                                   column="trade_stats_json", default={}),
                closed_trades=list(_loads(
                    _header(header, "closed_trades_json"),
                    column="closed_trades_json", default=[])),
                fund_credits=_loads(_header(header, "fund_credits_json"),
                                    column="fund_credits_json", default={}),
                config_hash=header["config_hash"] or "",
                snapshot_id=header["snapshot_id"] or "",
                strategy_ref=_loads(header["strategy_ref_json"],
                                    column="strategy_ref_json", default={}))
        finally:
            conn.close()

    def query_orders(self) -> list[dict]:
        return self._query("SELECT * FROM account_orders WHERE ledger_id = ? "
                           "ORDER BY created_event, order_id")

    def query_fills(self) -> list[dict]:
        return self._query("SELECT * FROM account_fills WHERE ledger_id = ? "
                           "ORDER BY created_at, fill_id")

    def query_batches(self) -> list[dict]:
        return self._query("SELECT * FROM account_batches WHERE ledger_id = ? "
                           "ORDER BY batch_id")

    def event_source_hash(self, event_id: str) -> Optional[str]:
        """已应用事件的来源哈希（只读）；不存在返回 None。"""
        conn = _connect_readonly()
        if conn is None:
            return None
        try:
            row = conn.execute(
                "SELECT source_hash FROM account_ledger_events "
                "WHERE ledger_id = ? AND event_id = ?",
                (self.ledger_id, event_id)).fetchone()
            return row[0] if row is not None else None
        except Exception as exc:  # noqa: BLE001
            if "no such table" in str(exc):
                return None
            raise
        finally:
            conn.close()

    def _query(self, sql: str) -> list[dict]:
        conn = _connect_readonly()
        if conn is None:
            return []
        conn.row_factory = _row_factory()
        try:
            rows = conn.execute(sql, (self.ledger_id,)).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def _read_batches(self, conn) -> list[Batch]:
        rows = conn.execute(
            "SELECT * FROM account_batches WHERE ledger_id = ? ORDER BY batch_id",
            (self.ledger_id,)).fetchall()
        return [Batch(
            batch_id=row["batch_id"], code=row["code"], qty=int(row["qty"]),
            remaining=int(row["remaining"]), entry_date=row["entry_date"] or "",
            cost_price=float(row["cost_price"]), fee=float(row["fee"] or 0.0),
            initial_risk=float(row["initial_risk"] or 0.0),
            risk_themes=tuple(_loads(row["risk_themes_json"],
                                     column="risk_themes_json", default=[])),
            strategy_ref=_loads(row["strategy_ref_json"], column="strategy_ref_json",
                                default={}),
            plan_id=row["plan_id"] or "", obligation_id=row["obligation_id"] or "",
            fill_id=row["fill_id"] or "", source=row["source"] or "runtime",
            sellable_mode=row["sellable_mode"] or SELLABLE_UNKNOWN,
            sellable_date=row["sellable_date"],
            family_id=_header(row, "family_id", "") or "")
            for row in rows]

    def _read_orders(self, conn) -> list[Order]:
        rows = conn.execute(
            "SELECT * FROM account_orders WHERE ledger_id = ? "
            "ORDER BY created_event, order_id", (self.ledger_id,)).fetchall()
        return [Order(
            order_id=row["order_id"], code=row["code"], side=row["side"],
            qty=int(row["qty"]), status=row["status"],
            created_event=int(row["created_event"]),
            filled_qty=int(row["filled_qty"] or 0),
            limit_price=row["limit_price"],
            reserved_amount=float(row["reserved_amount"] or 0.0),
            reserved_risk=float(row["reserved_risk"] or 0.0),
            locked_qty=int(row["locked_qty"] or 0),
            valid_until_event=row["valid_until_event"],
            trigger_kind=row["trigger_kind"] or "",
            trigger_price=row["trigger_price"],
            evidence_rule=row["evidence_rule"] or "",
            evidence_ids=tuple(_loads(row["evidence_ids_json"],
                                      column="evidence_ids_json", default=[])),
            depends_on=tuple(_loads(row["depends_on_json"],
                                    column="depends_on_json", default=[])),
            sequence=int(row["sequence"] or 0),
            risk_amount=float(row["risk_amount"] or 0.0),
            risk_themes=tuple(_loads(row["risk_themes_json"],
                                     column="risk_themes_json", default=[])),
            strategy_ref=_loads(row["strategy_ref_json"],
                                column="strategy_ref_json", default={}),
            plan_id=row["plan_id"] or "", obligation_id=row["obligation_id"] or "",
            batch_ids=tuple(_loads(row["batch_ids_json"],
                                   column="batch_ids_json", default=[])),
            family_id=_header(row, "family_id", "") or "",
            invalidate_below=_header(row, "invalidate_below", None),
            reason=row["reason"] or "")
            for row in rows]

    def _read_reservations(self, conn) -> list[Reservation]:
        rows = conn.execute(
            "SELECT * FROM account_reservations WHERE ledger_id = ?",
            (self.ledger_id,)).fetchall()
        return [Reservation(
            order_id=row["order_id"], code=row["code"], side=row["side"],
            qty=int(row["qty"]), amount=float(row["amount"]),
            status=row["status"], purpose=row["purpose"] or "",
            price=row["price"],
            theme_ids=tuple(_loads(row["theme_ids_json"],
                                   column="theme_ids_json", default=[])),
            pressure_pct=row["pressure_pct"],
            risk_amount=float(row["risk_amount"] or 0.0),
            valid_until_event=row["valid_until_event"])
            for row in rows]

    def read_history(self, **kwargs):
        """有界只读历史读回（A4）；委托 :class:`LedgerHistory`，不建库/不写。"""
        from .history import LedgerHistory

        return LedgerHistory(self.ledger_id).read(**kwargs)

    def delete(self) -> bool:
        conn = _connect()
        try:
            _ensure_schema(conn)
            try:
                conn.execute("BEGIN IMMEDIATE")
                for table in ("account_fills", "account_reservations",
                              "account_orders", "account_batches",
                              "account_batch_facts", "account_ledger_events"):
                    conn.execute(f"DELETE FROM {table} WHERE ledger_id = ?",
                                 (self.ledger_id,))
                cursor = conn.execute(
                    "DELETE FROM account_ledgers WHERE ledger_id = ?",
                    (self.ledger_id,))
                conn.execute("COMMIT")
                return cursor.rowcount > 0
            except BaseException:
                conn.rollback()
                raise
        finally:
            conn.close()


def _thaw(value):
    from dragon_quant.review_account.evidence import thaw
    return thaw(value)


# 兼容：旧代码可能直接 import 这两个常量做可卖判断。
__all__ = ["AccountLedger", "LEDGER_SCHEMA_VERSION", "SELLABLE_UNKNOWN",
           "SELLABLE_ON", "SELLABLE_AFTER"]
