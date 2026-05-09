#!/usr/bin/env python3
"""
PersistLogger — 持久化打点日志模块

JSON Lines 格式，每日滚动，线程安全，写入失败静默降级。
纯 Python 标准库，零外部依赖。

用法:
    from persist_logger import PersistLogger
    logger = PersistLogger(log_dir="./logs", max_days=7)
    logger.session_start(command="main.py", args=sys.argv[1:])
    logger.stage("preload", elapsed_ms=1234, in_count=67, out_count=42)
    logger.api(name="get_stock_kline(002192)", elapsed_ms=234, ok=True)
    logger.session_end(total_elapsed_ms=42000, success=14, failed=6)
"""

import json
import os
import time
import glob
import threading
import traceback
from datetime import datetime, timedelta


class PersistLogger:
    """JSON Lines 持久化日志，线程安全。"""

    def __init__(self, log_dir: str = "./logs", max_days: int = 7, verbose_stderr: bool = False):
        os.makedirs(log_dir, exist_ok=True)
        self._log_dir = log_dir
        self._max_days = max_days
        self._verbose_stderr = verbose_stderr
        self._lock = threading.Lock()
        self._file = None
        self._current_date = None
        self._fallback = False
        self._cleanup()

    # ─── 内部 ─────────────────────────────────────────────

    def _cleanup(self):
        cutoff = datetime.now() - timedelta(days=self._max_days)
        for f in glob.glob(os.path.join(self._log_dir, "lsa_*.jsonl")):
            try:
                if datetime.fromtimestamp(os.path.getmtime(f)) < cutoff:
                    os.remove(f)
            except OSError:
                pass

    def _open_for_date(self, date_str: str):
        path = os.path.join(self._log_dir, f"lsa_{date_str}.jsonl")
        self._file = open(path, "a", encoding="utf-8")
        self._current_date = date_str

    def _rotate_if_needed(self):
        today = datetime.now().strftime("%Y%m%d")
        if self._current_date != today:
            if self._file:
                self._file.close()
            self._open_for_date(today)

    def _emit(self, entry: dict):
        if self._fallback:
            return
        self._rotate_if_needed()
        line = json.dumps(entry, ensure_ascii=False, default=str)
        try:
            with self._lock:
                self._file.write(line + "\n")
                self._file.flush()
        except (OSError, IOError, ValueError) as e:
            self._fallback = True
            if self._verbose_stderr:
                import sys
                print(f"  [PersistLogger] 写入失败，后续日志静默丢弃: {e}", file=sys.stderr)

    def _now(self) -> str:
        return datetime.now().isoformat()

    # ─── 通用打点 ─────────────────────────────────

    def log(self, event: str, elapsed_ms: float = 0.0, ok: bool = True, **meta):
        self._emit({
            "ts": self._now(),
            "event": event,
            "elapsed_ms": round(elapsed_ms, 1),
            "ok": ok,
            "meta": meta,
        })

    # ─── 语义方法 ─────────────────────────────────

    def session_start(self, command: str = "", args: list = None):
        self.log("session_start",
                 command=command, args=args or [], pid=os.getpid())

    def session_end(self, total_elapsed_ms: float, success: int = 0,
                    failed: int = 0, top_scores: list = None):
        self.log("session_end", elapsed_ms=total_elapsed_ms,
                 ok=(failed == 0),
                 success=success, failed=failed,
                 top_scores=top_scores or [])

    def stage(self, name: str, elapsed_ms: float, **counts):
        self.log("pipeline_stage", elapsed_ms=elapsed_ms,
                 stage=name, **counts)

    def api(self, name: str, elapsed_ms: float, ok: bool,
            attempts: int = 1, reason: str = "",
            last_http_status: int = None,
            last_body_snippet: str = None):
        meta = {"name": name, "attempts": attempts}
        if not ok:
            meta["reason"] = reason
            if last_http_status is not None:
                meta["last_http_status"] = last_http_status
            if last_body_snippet:
                meta["last_body_snippet"] = str(last_body_snippet)[:300]
        self.log("api_call", elapsed_ms=elapsed_ms, ok=ok, **meta)

    def subprocess(self, code: str, status: str, elapsed_ms: float,
                   exit_code: int = None, reason: str = ""):
        meta = {"code": code, "status": status}
        ok = (status == "ok")
        if exit_code is not None:
            meta["exit_code"] = exit_code
        if reason:
            meta["reason"] = reason[:500]
        self.log("subprocess", elapsed_ms=elapsed_ms, ok=ok, **meta)

    def dimension_score(self, code: str, dim: str, score: float,
                        elapsed_ms: float = 0.0, fallback: bool = False,
                        reason: str = ""):
        meta = {"code": code, "dim": dim, "score": score, "fallback": fallback}
        if fallback and reason:
            meta["reason"] = reason
        self.log("dimension_score", elapsed_ms=elapsed_ms, ok=not fallback, **meta)

    def error(self, context: str, error_type: str = "",
              message: str = "", stack_summary: str = ""):
        self.log("error", elapsed_ms=0, ok=False,
                 context=context,
                 error_type=error_type,
                 message=message[:500],
                 stack_summary=stack_summary[:800])

    def error_from_exc(self, context: str, exc: Exception):
        tb_lines = traceback.format_exception(type(exc), exc, exc.__traceback__)
        stack_summary = "".join(tb_lines[-3:]) if len(tb_lines) > 3 else "".join(tb_lines)
        self.error(
            context=context,
            error_type=type(exc).__name__,
            message=str(exc),
            stack_summary=stack_summary,
        )

    def shared_data(self, source: str, age_seconds: float = 0,
                    filepath: str = ""):
        ok = (source != "missing")
        self.log("shared_data", ok=ok,
                 source=source, age_seconds=round(age_seconds, 0),
                 filepath=filepath)

    def close(self):
        if self._file:
            try:
                self._file.close()
            except OSError:
                pass
            self._file = None
        self._cleanup()
