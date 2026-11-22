"""覆盖报告组装与仓储错误载荷（纯输出）。"""

from __future__ import annotations

from dragon_quant.infra.assets import observations as store

from .types import OBSERVATION_SCHEMA_VERSION

MAX_GAPS = 2000


def _report_payload(*, plans, runs, observations, bars, interval_coverage,
                    gaps, truncation_gaps, gaps_truncated, date_from, date_to,
                    total_obs, total_bars, interval_skipped, db_path):
    return {
        "schema_version": OBSERVATION_SCHEMA_VERSION,
        "store_state": store.READY,
        "date_from": date_from,
        "date_to": date_to,
        "plans": [
            {
                "plan_id": plan["plan_id"],
                "trade_date": plan["trade_date"],
                "revision": plan["revision"],
                "config_version": plan["config_version"],
                "created_at": plan["created_at"],
                "members": len(plan.get("members") or []),
                "topics": list((plan.get("task_config") or {}).get("topics") or ()),
                "sectors": list((plan.get("task_config") or {}).get("sectors") or ()),
                "pool_gaps": list((plan.get("pool") or {}).get("gaps") or ()),
            }
            for plan in plans
        ],
        "runs": [
            {
                "run_id": run["run_id"],
                "plan_id": run["plan_id"],
                "trade_date": run["trade_date"],
                "phase": run["phase"],
                "slot": run["slot"],
                "status": run["status"],
                "started_at": run["started_at"],
                "finished_at": run["finished_at"],
                "reason": (run.get("report") or {}).get("reason"),
            }
            for run in runs
        ],
        "observations": observations,
        "bar_groups": bars,
        "interval_coverage": interval_coverage,
        "gaps": gaps[:MAX_GAPS],
        "totals": {"plans": store.count_plans(date_from, date_to,
                                              db_path=db_path),
                   "observation_groups": int(total_obs),
                   "bar_groups": int(total_bars),
                   "interval_coverage_skipped": int(interval_skipped)},
        "truncated": bool(truncation_gaps),
        "gaps_truncated": gaps_truncated,
        # 任一类缺口（池/时槽/区间/质量/截断）都不得给“完整”印象。
        "coverage_complete": not gaps and not interval_skipped,
    }


def _error_payload(exc: store.ObservationStoreError) -> dict:
    return {
        "schema_version": OBSERVATION_SCHEMA_VERSION,
        "store_state": "error",
        "error": {"reason": exc.reason, "message": str(exc)},
        "plans": [],
        "runs": [],
        "observations": [],
        "bar_groups": [],
        "interval_coverage": [],
        "gaps": [],
        "totals": {"plans": 0, "observation_groups": 0, "bar_groups": 0},
        "truncated": False,
        "gaps_truncated": False,
        "coverage_complete": False,
    }
