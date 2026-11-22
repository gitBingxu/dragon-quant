"""原子写入：观测/bar 插入、commit_topic 与运行收尾。"""

from __future__ import annotations

import sqlite3
from typing import Iterable, Optional

from dragon_quant.infra.assets import db as assets_db

from .runs import _validate_lease_row
from .schema import (
    SCHEMA_VERSION,
    CorruptEvidence,
)
from .store import _iso_now, _loads_strict, _safe_rollback, canonical_json, content_hash

def _insert_observation_tx(conn: sqlite3.Connection, *, trade_date: str,
                           market: str, code: str, obs_type: str, source: str,
                           payload, received_at: str, available_at: str,
                           units: dict, quality: str,
                           first_received_at: Optional[str] = None,
                           source_event_time: Optional[str] = None,
                           valid_until: Optional[str] = None,
                           semantic_key: Optional[str] = None,
                           schema_version: str = SCHEMA_VERSION,
                           run_id: Optional[str] = None,
                           calibration: Optional[dict] = None) -> dict:
    """事务内插入一条观测（调用方持有 BEGIN IMMEDIATE）。"""
    digest = content_hash({"payload": payload, "units": units})
    key = semantic_key or (
        f"{obs_type}|{source}|{market}|{code or ''}|{trade_date}")
    row = conn.execute(
        "SELECT obs_id, revision, content_hash, first_received_at "
        "FROM market_observations WHERE semantic_key = ? "
        "ORDER BY revision DESC LIMIT 1",
        (key,),
    ).fetchone()
    if row is not None and row[2] == digest:
        return {"status": "deduped", "obs_id": row[0], "revision": row[1],
                "content_hash": digest}
    revision = (row[1] + 1) if row is not None else 1
    first_seen = (row[3] if row is not None and row[3]
                  else (first_received_at or received_at))
    obs_id = f"{key}#r{revision}"
    conn.execute(
        "INSERT INTO market_observations(obs_id, trade_date, market, code, "
        "obs_type, source, source_event_time, received_at, first_received_at, "
        "available_at, valid_until, units_json, quality, schema_version, "
        "revision, content_hash, semantic_key, payload_json, run_id, "
        "calibration_json, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (obs_id, trade_date, market, code, obs_type, source, source_event_time,
         received_at, first_seen, available_at, valid_until,
         canonical_json(units), quality, schema_version, revision, digest, key,
         canonical_json(payload), run_id,
         (canonical_json(calibration) if calibration else None), received_at),
    )
    return {"status": "written", "obs_id": obs_id, "revision": revision,
            "content_hash": digest}


def _insert_bar_tx(conn: sqlite3.Connection, *, trade_date: str, market: str,
                   code: str, period: str, interval_start: str,
                   interval_end: str, open: float, high: float, low: float,
                   close: float, volume: Optional[float], amount: Optional[float],
                   source: str, observed_at: str, available_at: str,
                   completeness: str, time_semantics: str = "unverified",
                   unit_semantics: str = "unverified", adjustment: str = "none",
                   derived: bool = False,
                   schema_version: str = SCHEMA_VERSION,
                   run_id: Optional[str] = None,
                   calibration: Optional[dict] = None) -> dict:
    """事务内插入一个 bar 版本；身份含 market/source/复权口径。"""
    body = {
        "open": open, "high": high, "low": low, "close": close,
        "volume": volume, "amount": amount, "adjustment": adjustment,
        "source": source, "completeness": completeness,
        "time_semantics": time_semantics, "unit_semantics": unit_semantics,
    }
    digest = content_hash(body)
    row = conn.execute(
        "SELECT bar_id, revision, content_hash FROM market_bar_versions "
        "WHERE market = ? AND code = ? AND period = ? AND adjustment = ? "
        "AND interval_end = ? ORDER BY revision DESC LIMIT 1",
        (market, code, period, adjustment, interval_end),
    ).fetchone()
    if row is not None and row[2] == digest:
        return {"status": "deduped", "bar_id": row[0], "revision": row[1],
                "content_hash": digest}
    revision = (row[1] + 1) if row is not None else 1
    bar_id = (f"{market}|{code}|{period}|{adjustment}|{interval_end}"
              f"#r{revision}")
    conn.execute(
        "INSERT INTO market_bar_versions(bar_id, trade_date, market, code, "
        "period, interval_start, interval_end, open, high, low, close, volume, "
        "amount, adjustment, source, observed_at, available_at, revision, "
        "content_hash, completeness, time_semantics, unit_semantics, derived, "
        "schema_version, run_id, calibration_json, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
        "?, ?, ?, ?, ?, ?)",
        (bar_id, trade_date, market, code, period, interval_start, interval_end,
         open, high, low, close, volume, amount, adjustment, source, observed_at,
         available_at, revision, digest, completeness, time_semantics,
         unit_semantics, 1 if derived else 0, schema_version, run_id,
         (canonical_json(calibration) if calibration else None), observed_at),
    )
    return {"status": "written", "bar_id": bar_id, "revision": revision,
            "content_hash": digest}


class _WriteBatch:
    """一个 worker 的一批写入意图（观测 + bar）。"""

    def __init__(self, observations: Iterable[dict], bars: Iterable[dict]) -> None:
        self.observations = list(observations)
        self.bars = list(bars)


def commit_topic(*, run_id: str, owner: str, token: int, now: str,
                 observations: Iterable[dict], bars: Iterable[dict],
                 topic_report: dict, db_path=None) -> dict:
    """**原子**提交一批原始数据：同事务校验 run/owner/token/有效期后写入。

    网络请求必须在事务外完成；这里只做短事务。校验失败 → 一行不写并抛
    :class:`LeaseLostError`，保证旧 worker 不能把原始数据写进库。
    """
    _iso_now(now)
    batch = _WriteBatch(observations, bars)
    counts = {"observations": {"written": 0, "deduped": 0},
              "bars": {"written": 0, "deduped": 0}}
    conn = assets_db._connect(path=db_path)
    try:
        assets_db._ensure_schema(conn)
        conn.isolation_level = None
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                "SELECT lease_owner, lease_token, lease_expires_at, report_json "
                "FROM capture_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            _validate_lease_row(row[:3], owner=owner, token=token, now=now,
                                run_id=run_id)
            report = _loads_strict(row[3], column="capture_runs.report_json",
                                   default={})
            for item in batch.observations:
                fields = dict(item)
                fields.pop("run_id", None)
                outcome = _insert_observation_tx(conn, run_id=run_id, **fields)
                counts["observations"][outcome["status"]] = (
                    counts["observations"].get(outcome["status"], 0) + 1)
            for item in batch.bars:
                fields = dict(item)
                fields.pop("run_id", None)
                outcome = _insert_bar_tx(conn, run_id=run_id, **fields)
                counts["bars"][outcome["status"]] = (
                    counts["bars"].get(outcome["status"], 0) + 1)
            if not isinstance(report, dict):
                raise CorruptEvidence("capture_runs.report_json 顶层不是对象",
                                      reason="corrupt_json")
            topics = list(report.get("topics") or [])
            topics.append(dict(topic_report))
            gaps = list(report.get("gaps") or [])
            for gap in topic_report.get("gaps") or []:
                gaps.append(gap)
            report["topics"] = topics
            report["gaps"] = gaps
            conn.execute(
                "UPDATE capture_runs SET report_json = ? WHERE run_id = ?",
                (canonical_json(report), run_id),
            )
            conn.execute("COMMIT")
        except BaseException:
            _safe_rollback(conn)
            raise
        return counts
    finally:
        conn.close()


def finalize_run(run_id: str, *, owner: str, token: int, status: str,
                 report: dict, finished_at: str, db_path=None) -> None:
    """带 owner/token/有效期校验的收尾；提交后租约失效，重复提交被拒。"""
    _iso_now(finished_at)
    conn = assets_db._connect(path=db_path)
    try:
        assets_db._ensure_schema(conn)
        conn.isolation_level = None
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                "SELECT lease_owner, lease_token, lease_expires_at FROM capture_runs "
                "WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            _validate_lease_row(row, owner=owner, token=token, now=finished_at,
                                run_id=run_id)
            conn.execute(
                "UPDATE capture_runs SET status = ?, finished_at = ?, "
                "report_json = ?, lease_owner = NULL, lease_expires_at = NULL, "
                "lease_token = lease_token + 1 WHERE run_id = ?",
                (status, finished_at, canonical_json(report), run_id),
            )
            conn.execute("COMMIT")
        except BaseException:
            _safe_rollback(conn)
            raise
    finally:
        conn.close()


def mark_run(run_id: str, *, status: str, report: dict, finished_at: str,
             db_path=None) -> bool:
    """无租约的诊断写入（``missed`` 等）。

    只更新**没有活跃租约、且尚未进入终态**的运行行：绝不覆盖仍在跑的任务，
    也绝不用迟到诊断覆盖已 ``complete`` / ``partial`` 的报告（R5，等价 CAS）。
    ``finished_at`` 已写入同样视为终态；合法未开始的运行（无 lease、无
    ``finished_at``、状态非终态）仍可记录。返回是否真正写入，便于调用方发现
    被拒绝的迟到诊断而不是静默丢失。
    """
    _iso_now(finished_at)
    conn = assets_db._connect(path=db_path)
    try:
        assets_db._ensure_schema(conn)
        cursor = conn.execute(
            "UPDATE capture_runs SET status = ?, finished_at = ?, report_json = ? "
            "WHERE run_id = ? AND lease_owner IS NULL "
            "AND finished_at IS NULL "
            "AND status NOT IN ('complete', 'partial')",
            (status, finished_at, canonical_json(report), run_id),
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def append_observation(*, trade_date: str, market: str, code: str,
                       obs_type: str, source: str, payload,
                       received_at: str, available_at: str, units: dict,
                       quality: str, first_received_at: Optional[str] = None,
                       source_event_time: Optional[str] = None,
                       valid_until: Optional[str] = None,
                       semantic_key: Optional[str] = None,
                       schema_version: str = SCHEMA_VERSION,
                       run_id: Optional[str] = None,
                       calibration: Optional[dict] = None,
                       db_path=None) -> dict:
    """独立事务写一条观测（工具/测试入口；采集会话用 :func:`commit_topic`）。"""
    _iso_now(received_at)
    _iso_now(available_at)
    conn = assets_db._connect(path=db_path)
    try:
        assets_db._ensure_schema(conn)
        conn.isolation_level = None
        conn.execute("BEGIN IMMEDIATE")
        try:
            outcome = _insert_observation_tx(
                conn, trade_date=trade_date, market=market, code=code,
                obs_type=obs_type, source=source, payload=payload,
                received_at=received_at, available_at=available_at, units=units,
                quality=quality, first_received_at=first_received_at,
                source_event_time=source_event_time, valid_until=valid_until,
                semantic_key=semantic_key, schema_version=schema_version,
                run_id=run_id, calibration=calibration)
            conn.execute("COMMIT")
            return outcome
        except BaseException:
            _safe_rollback(conn)
            raise
    finally:
        conn.close()


def append_bar(*, trade_date: str, market: str, code: str, period: str,
               interval_start: str, interval_end: str, open: float, high: float,
               low: float, close: float, volume: Optional[float],
               amount: Optional[float], source: str, observed_at: str,
               available_at: str, completeness: str,
               time_semantics: str = "unverified",
               unit_semantics: str = "unverified",
               adjustment: str = "none", derived: bool = False,
               schema_version: str = SCHEMA_VERSION,
               run_id: Optional[str] = None,
               calibration: Optional[dict] = None,
               db_path=None) -> dict:
    """独立事务写一个 bar 版本（工具/测试入口；会话用 :func:`commit_topic`）。"""
    _iso_now(observed_at)
    _iso_now(available_at)
    conn = assets_db._connect(path=db_path)
    try:
        assets_db._ensure_schema(conn)
        conn.isolation_level = None
        conn.execute("BEGIN IMMEDIATE")
        try:
            outcome = _insert_bar_tx(
                conn, trade_date=trade_date, market=market, code=code,
                period=period, interval_start=interval_start,
                interval_end=interval_end, open=open, high=high, low=low,
                close=close, volume=volume, amount=amount, source=source,
                observed_at=observed_at, available_at=available_at,
                completeness=completeness, time_semantics=time_semantics,
                unit_semantics=unit_semantics, adjustment=adjustment,
                derived=derived, schema_version=schema_version, run_id=run_id,
                calibration=calibration)
            conn.execute("COMMIT")
            return outcome
        except BaseException:
            _safe_rollback(conn)
            raise
    finally:
        conn.close()


# ─── 读取（覆盖 / 回放 / 证据）───────────────────────────────────────────────
