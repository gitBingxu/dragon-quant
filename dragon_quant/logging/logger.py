"""
结构化日志引擎 — 支持 agent 查询和报告输出

用法:
  logger = ScanLogger()
  logger.phase("A", "板块排行完成", count=10)
  logger.scorer("drive", "600519", score=85.0, details={...})
  logger.api("eastmoney", "sector_ranking", ok=True, elapsed_ms=350)

查询:
  logs = logger.query(category="scorer:drive", code="600519")
  errors = logger.query(level="error")
  report = logger.report_context("600519")  # 聚合该股全部日志
"""

import time, json, threading
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, field


@dataclass
class LogEntry:
    timestamp: float
    category: str       # phase / api / scorer:drive / scorer:anti_drop / ...
    level: str          # info / warn / error
    message: str
    code: str           # 关联股票代码
    data: dict = field(default_factory=dict)


class ScanLogger:
    """单次扫描的日志记录器"""

    def __init__(self):
        self._entries: list[LogEntry] = []
        self._lock = threading.Lock()
        self._start_ts = time.time()

    # ─── 写入 ───

    def _log(self, category: str, level: str, message: str,
             code: str = "", **data):
        entry = LogEntry(
            timestamp=time.time(),
            category=category,
            level=level,
            message=message,
            code=code,
            data=data,
        )
        with self._lock:
            self._entries.append(entry)

    def phase(self, name: str, message: str, **data):
        self._log(f"phase:{name}", "info", message, **data)

    def api(self, provider: str, endpoint: str, ok: bool,
            elapsed_ms: float = 0, code: str = "", error: str = "", **data):
        self._log(
            f"api:{provider}:{endpoint}",
            "info" if ok else "error",
            f"{provider}/{endpoint} {'✅' if ok else '❌'} ({elapsed_ms:.0f}ms)",
            code=code, ok=ok, elapsed_ms=elapsed_ms, error=error, **data,
        )

    def scorer(self, dim: str, code: str, score: float = 0,
               weight: float = 0, **details):
        """记录评分维度结果（自动展平 details 到 data）"""
        self._log(
            f"scorer:{dim}", "info",
            f"{dim} {code} = {score:.1f}",
            code=code, score=score, weight=weight, **details,
        )

    def warn(self, category: str, message: str, code: str = "", **data):
        self._log(category, "warn", message, code=code, **data)

    def error(self, category: str, message: str, code: str = "",
              exception: str = "", **data):
        self._log(category, "error", message, code=code,
                  exception=exception, **data)

    # ─── 查询 ───

    def query(self, category: Optional[str] = None,
              level: Optional[str] = None,
              code: Optional[str] = None,
              dim: Optional[str] = None) -> list[LogEntry]:
        """按条件过滤日志条目"""
        results = []
        with self._lock:
            for e in self._entries:
                if category and not (e.category == category or
                                     e.category.startswith(category)):
                    continue
                if level and e.level != level:
                    continue
                if code and e.code != code:
                    continue
                if dim and f"scorer:{dim}" not in e.category:
                    continue
                results.append(e)
        return results

    def errors(self) -> list[LogEntry]:
        return self.query(level="error")

    def api_stats(self) -> dict:
        """API 调用统计"""
        stats = {"total": 0, "ok": 0, "error": 0, "total_ms": 0, "by_provider": {}}
        for e in self.query(category="api"):
            stats["total"] += 1
            if e.data.get("ok"):
                stats["ok"] += 1
            else:
                stats["error"] += 1
            elapsed = e.data.get("elapsed_ms", 0)
            stats["total_ms"] += elapsed
            provider = e.category.split(":")[1] if ":" in e.category else "unknown"
            if provider not in stats["by_provider"]:
                stats["by_provider"][provider] = {"count": 0, "total_ms": 0}
            stats["by_provider"][provider]["count"] += 1
            stats["by_provider"][provider]["total_ms"] += elapsed
        return stats

    def report_context(self, code: str) -> dict:
        """聚合指定股票的全部评分上下文，供报告生成器使用"""
        dims = {}
        for e in self.query(code=code, category="scorer"):
            dim_name = e.category.replace("scorer:", "")
            dims[dim_name] = {
                "score": e.data.get("score", 0),
                "weight": e.data.get("weight", 0),
                "details": {k: v for k, v in e.data.items()
                            if k not in ("score", "weight")},
                "message": e.message,
            }

        errors = [(e.timestamp, e.message) for e in self.query(code=code, level="error")]
        warnings = [(e.timestamp, e.message) for e in self.query(code=code, level="warn")]

        return {
            "code": code,
            "dimensions": dims,
            "errors": errors,
            "warnings": warnings,
        }

    def summary(self) -> dict:
        """扫描摘要"""
        phases = {}
        scorers = {"total": 0, "by_dim": {}}
        for e in self._entries:
            if e.category.startswith("phase:"):
                phase_name = e.category.replace("phase:", "")
                phases[phase_name] = e.message
            if e.category.startswith("scorer:"):
                scorers["total"] += 1
                dim = e.category.replace("scorer:", "")
                scorers["by_dim"].setdefault(dim, 0)
                scorers["by_dim"][dim] += 1

        return {
            "elapsed_s": round(time.time() - self._start_ts, 1),
            "total_entries": len(self._entries),
            "phases": phases,
            "scorers": scorers,
            "api": self.api_stats(),
            "error_count": len(self.errors()),
        }

    # ─── 持久化 ───

    def dump_jsonl(self, path: Path):
        """导出为 JSONL 文件"""
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            for record in self.to_dicts():
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def to_dicts(self) -> list[dict]:
        """导出内存中的全部日志条目为 dict 列表"""
        with self._lock:
            return [
                {
                    "ts": e.timestamp,
                    "category": e.category,
                    "level": e.level,
                    "message": e.message,
                    "code": e.code,
                    "data": e.data,
                }
                for e in self._entries
            ]
