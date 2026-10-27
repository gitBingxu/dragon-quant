"""``scan.storage.main`` — 扫描持久化公共入口 + Phase F 收口。

Phase F 的固定写入顺序（与迁移前 ``orchestrator`` 逐项一致）::

    save_scan_logs → count_scan_logs → write_scan_report → save_scan → save_dragons

失败语义：

- 日志写入失败：隔离进 ``PersistResult.errors``，``log_count`` 回退为
  ``len(log_entries)``，后续步骤继续；
- 报告文件写入失败：**上抛**（旧实现中报告写不在隔离 try 内）；
- 扫描 / 5 日去重 / 龙头写入失败：整段隔离进 ``errors``（与旧实现同一个
  ``try`` 块一致：``save_scan`` 失败时不再尝试 ``save_dragons``）。

本模块不 import ``scan.logger`` / ``scan.orchestrator`` / ``scan.scores`` /
``scan.cache``，也不直接打开 SQLite（连接只有 ``infra.storage`` 两处）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from dragon_quant.scan.storage import blacklist as _blacklist
from dragon_quant.scan.storage import dragons as _dragons
from dragon_quant.scan.storage import logs as _logs
from dragon_quant.scan.storage import scans as _scans
from dragon_quant.scan.storage.results import write_scan_report

__all__ = [
    "PersistResult",
    "persist_scan_result",
    "write_scan_report",
    "save_scan",
    "save_dragons",
    "save_scan_logs",
    "count_scan_logs",
    "get_sector_blacklist",
    "get_last_entry_with_rank",
    "get_latest_scan_by_date",
    "get_scan",
    "get_scan_stocks",
    "get_scans_by_date",
    "list_scan_dates",
]


@dataclass
class PersistResult:
    """Phase F 持久化结果（用于回填 ``scan()`` 输出与 verbose 打印）。"""

    log_count: int
    report_path: Optional[Path]
    saved_dragons: int
    skipped_dragons: int
    updated_dragons: int
    errors: list[str] = field(default_factory=list)


# ─── 子模块转发（patch 任意一层都能改变运行期行为）─────────────────────────
# ``persist`` 通过模块级全局名调用本层包装，因此
# ``patch("dragon_quant.scan.storage.scans.save_scan")`` 与
# ``patch("dragon_quant.scan.storage.main.save_scan")`` 两种写法都生效。


def save_scan(scan_id: str, scan_date: str, elapsed_s: float,
              top_n: int, candidates_n: int, workers: int,
              stocks: list[dict], raw_output: Optional[str] = None,
              source: str = "v2"):
    return _scans.save_scan(
        scan_id, scan_date, elapsed_s, top_n, candidates_n, workers,
        stocks, raw_output=raw_output, source=source)


def save_dragons(trade_date: str, dragons: list[dict], version: str = "",
                 source: str = "v2"):
    return _dragons.save_dragons(
        trade_date, dragons, version=version, source=source)


def save_scan_logs(scan_id: str, entries: list[dict], source: str = "v2"):
    return _logs.save_scan_logs(scan_id, entries, source=source)


def count_scan_logs(scan_id: str, source: str = "v2") -> int:
    return _logs.count_scan_logs(scan_id, source=source)


def get_sector_blacklist() -> list[str]:
    return _blacklist.get_sector_blacklist()


def get_last_entry_with_rank(code: str, source: str = "v2") -> Optional[tuple]:
    return _dragons.get_last_entry_with_rank(code, source=source)


def get_latest_scan_by_date(scan_date: str, top_n: int,
                            source: str = "v2") -> Optional[dict]:
    return _scans.get_latest_scan_by_date(scan_date, top_n, source=source)


def get_scan(scan_id: str, source: str = "v2") -> Optional[dict]:
    return _scans.get_scan(scan_id, source=source)


def get_scan_stocks(scan_id: str, source: str = "v2") -> list[dict]:
    return _scans.get_scan_stocks(scan_id, source=source)


def get_scans_by_date(scan_date: str, source: str = "v2") -> list[dict]:
    return _scans.get_scans_by_date(scan_date, source=source)


def list_scan_dates(date_from: Optional[str] = None,
                    date_to: Optional[str] = None,
                    source: str = "v2") -> list[str]:
    return _scans.list_scan_dates(
        date_from=date_from, date_to=date_to, source=source)


# ─── Phase F 收口 ──────────────────────────────────────────────────────────


def _resolve_timestamp(raw_output, timestamp: str) -> str:
    if timestamp:
        return timestamp
    if isinstance(raw_output, dict):
        return str(raw_output.get("timestamp") or "")
    if isinstance(raw_output, str):
        try:
            payload = json.loads(raw_output)
        except (TypeError, ValueError):
            return ""
        if isinstance(payload, dict):
            return str(payload.get("timestamp") or "")
    return ""


def _apply_raw_meta(raw_output, *, log_count: int, report_path: Path) -> None:
    """只在既有键上回填，保持 raw_output 的键顺序与旧实现一致。"""
    if not isinstance(raw_output, dict):
        return
    if "log_count" in raw_output:
        raw_output["log_count"] = log_count
    if "report_path" in raw_output:
        raw_output["report_path"] = str(report_path)


def _serialize_raw_output(raw_output) -> Optional[str]:
    if raw_output is None or isinstance(raw_output, str):
        return raw_output
    return json.dumps(raw_output, ensure_ascii=False)


def _dedupe_dragon_rows(
        rows: list[dict], *, scan_date_fmt: str, source: str,
        calendar: set[str] | Callable[[], Optional[set[str]]] | None,
) -> tuple[list[dict], int, int]:
    """5 日去重：跨日重复入选且距上次 < 5 个交易日时保留 rank 更优者。

    ``calendar`` 可为交易日集合或返回集合的零参 callable。``None`` 表示调用方
    未提供交易日历，此时不施加去重门（生产 orchestrator 始终传入 callable；显式
    None 仅用于不触网的单元测试）。
    """
    if callable(calendar):
        calendar = calendar()
    if calendar is None:
        return list(rows), 0, 0

    from dragon_quant.infra.market_calendar import trade_days_between

    kept: list[dict] = []
    skipped = 0
    updated = 0
    for row in rows:
        code = row["code"]
        new_rank = row["rank"]
        last_info = get_last_entry_with_rank(code, source=source)
        if last_info:
            last_date, old_rank = last_info
            if last_date < scan_date_fmt and \
                    trade_days_between(last_date, scan_date_fmt, calendar) < 5:
                if old_rank is not None and new_rank < old_rank:
                    updated += 1
                else:
                    skipped += 1
                    continue
        kept.append(row)
    return kept, skipped, updated


def _record_error(errors: list[str], message: str,
                  on_error: Optional[Callable[[str], None]]) -> None:
    """记录错误并在发生点通知调用方（内部 seam，用于恢复 Phase F verbose 顺序）。"""
    errors.append(message)
    if on_error is not None:
        on_error(message)


def persist_scan_result(
    *,
    scan_id: str,
    scan_date_fmt: str,
    elapsed_s: float,
    top_n: int,
    candidates_n: int,
    workers: int,
    results: list[dict],
    raw_output,
    log_entries: list[dict],
    report_text: str,
    dragon_rows: list[dict],
    source: str = "v2",
    trade_calendar=None,
    version: str = "",
    timestamp: str = "",
    on_error: Optional[Callable[[str], None]] = None,
    on_dedupe: Optional[Callable[[int, int], None]] = None,
) -> PersistResult:
    """收口 Phase F：日志 → 报告 → 扫描 → 5 日去重/龙头。

    ``report_text`` 是写入报告文件的完整文本（汇总 + 逐只报告）；
    ``raw_output`` 为待持久化的输出 dict（回填 ``log_count``/``report_path``
    后序列化），也接受已序列化字符串。``timestamp`` 缺省时从 ``raw_output``
    推导（批准签名未列出该参数，但报告文件名需要完整时间戳）。

    ``trade_calendar`` 可为交易日集合或零参 callable；orchestrator 传 callable
    以保持「日历构建失败与 DB 写入同样隔离」的旧 Phase F 语义。

    ``on_error`` / ``on_dedupe`` 是**内部展示 seam**：错误在发生点回调、去重计数
    在 save_dragons 之前回调，使 orchestrator 能复刻旧 verbose 顺序（日志错误 →
    去重信息 → DB 错误），并保证 log failure + report failure 时日志错误不丢失。
    公共 CLI / JSON 契约不变；默认 None 时只累积到 ``PersistResult.errors``。
    """
    errors: list[str] = []

    # 1) 结构化日志（失败隔离，错误在发生点回调）
    try:
        save_scan_logs(scan_id, log_entries, source=source)
        log_count = count_scan_logs(scan_id, source=source)
    except Exception as exc:
        _record_error(errors, f"日志持久化失败: {exc}", on_error)
        log_count = len(log_entries)

    # 2) 报告文件（失败上抛，与旧实现一致）
    report_path = write_scan_report(
        source=source,
        timestamp=_resolve_timestamp(raw_output, timestamp),
        text=report_text,
    )

    # 3) 扫描 + 5 日去重 + 龙头（整段隔离：save_scan 失败时不再写 dragons）
    saved = 0
    skipped = 0
    updated = 0
    try:
        _apply_raw_meta(raw_output, log_count=log_count, report_path=report_path)
        save_scan(
            scan_id=scan_id,
            scan_date=scan_date_fmt,
            elapsed_s=elapsed_s,
            top_n=top_n,
            candidates_n=candidates_n,
            workers=workers,
            stocks=results,
            raw_output=_serialize_raw_output(raw_output),
            source=source,
        )
        kept, skipped, updated = _dedupe_dragon_rows(
            dragon_rows,
            scan_date_fmt=scan_date_fmt,
            source=source,
            calendar=trade_calendar,
        )
        if on_dedupe is not None:
            on_dedupe(skipped, updated)
        save_dragons(scan_date_fmt, kept, version=version, source=source)
        saved = len(kept)
    except Exception as exc:
        _record_error(errors, f"SQLite 持久化失败: {exc}", on_error)

    return PersistResult(
        log_count=log_count,
        report_path=report_path,
        saved_dragons=saved,
        skipped_dragons=skipped,
        updated_dragons=updated,
        errors=errors,
    )
