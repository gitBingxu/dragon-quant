"""``research strategy``：当前运行时规则元信息 vs 账户批次冻结参数。"""

from __future__ import annotations

from .envelope import InvalidRequest, envelope, normalize_int


def _scan_metadata() -> dict:
    from dragon_quant.scan.scores import registry

    return {
        "kind": "scan",
        "basis": "current_runtime",
        "scorer": "five_dim_v2",
        "weights": dict(registry.DIM_WEIGHTS),
        "floors": dict(registry.DIM_FLOORS),
        "market_symbol": registry.MARKET_SYMBOL,
        "source": "v2",
        "note": "阈值/权重来自当前代码；历史扫描不按当前规则重算",
    }


def _account_current() -> dict:
    from dataclasses import fields

    from dragon_quant.review_account.models import SELL_RULE_NAMES, StrategyConfig

    cfg = StrategyConfig()
    return {
        "kind": "account",
        "basis": "current_runtime",
        "strategy_name": cfg.strategy_name,
        "params": {item.name: getattr(cfg, item.name) for item in fields(cfg)},
        "sell_rules": list(SELL_RULE_NAMES),
        "source": "v2",
        "note": "当前默认参数；历史 run 必须用 --run-id 读取其冻结参数",
    }


def strategy(*, kind: str, run_id: int | None = None) -> dict:
    if kind not in ("scan", "account"):
        raise InvalidRequest(f"--kind 只接受 scan/account（收到 {kind!r}）")
    if kind == "account" and run_id is not None:
        from dragon_quant.review_account import research_reads

        from .query import read_with_status

        value = normalize_int(run_id, "--run-id")
        rows, reason, warnings = read_with_status(
            research_reads.account_runs, run_id=value, limit=1, offset=0)
        if rows is None:
            return envelope(
                command="research strategy",
                request={"kind": kind, "run_id": value},
                data={"kind": kind, "basis": "historical_unknown",
                      "run_id": value, "available": False},
                ok=True,
                meta={"data_quality": "degraded", "empty_reason": reason,
                      "returned": 0, "truncated": False, "next_cursor": None},
                warnings=warnings + ["历史规则不可得；不用当前默认参数补历史"],
            )
        if not rows:
            return envelope(
                command="research strategy",
                request={"kind": kind, "run_id": value},
                data={"kind": kind, "basis": "historical_unknown",
                      "run_id": value, "available": False},
                ok=True,
                meta={"data_quality": "degraded",
                      "empty_reason": "run_not_found",
                      "returned": 0, "truncated": False, "next_cursor": None},
                warnings=["run 不存在；不用当前默认参数补历史"],
            )
        run = rows[0]
        if run.get("_corrupt"):
            return envelope(
                command="research strategy",
                request={"kind": kind, "run_id": value},
                data={"kind": kind, "basis": "historical_corrupt",
                      "run_id": value, "available": False},
                ok=True,
                meta={"data_quality": "degraded",
                      "empty_reason": "corrupt_strategy_params",
                      "returned": 0, "truncated": False, "next_cursor": None},
                warnings=warnings + ["冻结策略 JSON 损坏；不冒充可用历史参数"],
            )
        return envelope(
            command="research strategy",
            request={"kind": kind, "run_id": value},
            data={
                "kind": kind,
                "basis": "historical_run",
                "run_id": value,
                "strategy_name": run.get("strategy_name"),
                "params": run.get("strategy_params") or {},
                "source": run.get("source"),
                "note": "冻结在该 run 的 strategy_params；当前代码规则不会回填",
            },
            meta={"data_quality": "ok", "returned": 1, "truncated": False,
                  "next_cursor": None},
        )
    data = _scan_metadata() if kind == "scan" else _account_current()
    return envelope(
        command="research strategy",
        request={"kind": kind, "run_id": None},
        data=data,
        meta={"data_quality": "ok", "returned": 1, "truncated": False,
              "next_cursor": None},
        warnings=["current 与 historical 口径不同，不能混用"],
    )
