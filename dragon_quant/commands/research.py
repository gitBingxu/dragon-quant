"""``research`` 命令：本地只读研究 Facade 的 CLI 适配（默认人读，``--json`` 完整对象）。"""

from __future__ import annotations

import json
import sys


def _json(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def _human_datasets(payload: dict) -> None:
    rows = payload.get("data") or []
    print(f"datasets: {len(rows)}（可用 {payload['meta'].get('available_count')}）")
    for row in rows:
        availability = row.get("availability") or {}
        reason = availability.get("reason") or ""
        dates = availability.get("date_range") or {}
        span = ""
        if dates:
            span = f" [{dates.get('from')}..{dates.get('to')}]"
        print(f"- {row['name']:<20} {availability.get('status'):<16} {row['title']}"
              f"{span}{('  ' + reason) if reason else ''}")
        print(f"  filters: {', '.join(row.get('filters') or []) or '无'}  "
              f"fields: {len(row.get('fields') or [])}")


def _human_schema(payload: dict) -> None:
    data = payload.get("data") or {}
    print(f"{data.get('name')}: {data.get('title')}")
    print(f"db={data.get('db')} table={data.get('table')} "
          f"filters={','.join(data.get('filters') or []) or '无'} "
          f"sort={', '.join(data.get('sort') or [])}")
    for item in data.get("fields") or []:
        flag = " large" if item.get("large") else ""
        nullable = "" if item.get("nullable") else " not-null"
        unit = f" [{item['unit']}]" if item.get("unit") else ""
        print(f"- {item['name']:<20} {item['type']:<8}{flag}{nullable} "
              f"{item['description']}{unit}")


def _human_rows(payload: dict) -> None:
    meta = payload.get("meta") or {}
    rows = payload.get("data")
    if not isinstance(rows, list):
        print(json.dumps(rows, ensure_ascii=False, indent=2, default=str))
        return
    print(f"returned={meta.get('returned')} truncated={meta.get('truncated')} "
          f"dataset={meta.get('dataset')} quality={meta.get('data_quality')}")
    if meta.get("empty_reason"):
        print(f"empty_reason: {meta['empty_reason']}")
    for row in rows:
        print("- " + json.dumps(row, ensure_ascii=False, default=str))
    if meta.get("next_cursor"):
        print(f"next_cursor: {meta['next_cursor']}")


def _human_scan_quality(payload: dict) -> None:
    data = payload.get("data") or {}
    coverage = data.get("coverage") or {}
    print(f"status={data.get('status')} reason={data.get('reason')}")
    print(f"days_with_scan={coverage.get('days_with_scan')} "
          f"raw={coverage.get('raw_contributions')} "
          f"canonical={coverage.get('canonical_candidates')} "
          f"mature={coverage.get('mature_samples')}")
    baseline = data.get("baseline") or {}
    print(f"baseline mean={baseline.get('mean_return_pct')} "
          f"samples={baseline.get('samples')}")
    for name, group in (data.get("groups") or {}).items():
        print(f"- {name}: samples={group.get('samples')} "
              f"mean={group.get('mean_return_pct')} "
              f"win_rate={group.get('win_rate')}")
    for error in data.get("read_errors") or []:
        print(f"warning: {error}")


def _human_account(payload: dict) -> None:
    data = payload.get("data") or {}
    if isinstance(data, list):
        _human_rows(payload)
        return
    summary = data.get("summary") or {}
    run = data.get("run") or {}
    run_id = data.get("run_id") or run.get("run_id")
    print(f"run_id={run_id} available={data.get('available', True)} "
          f"ledger_truncated={data.get('ledger_truncated', False)}")
    if summary:
        for key in ("start_equity", "final_equity", "total_return", "max_drawdown",
                    "sharpe", "trade_count", "win_rate", "fees", "period_pnl_amount",
                    "contribution_basis"):
            print(f"- {key}: {summary.get(key)}")
    if payload["meta"].get("empty_reason"):
        print(f"empty_reason: {payload['meta']['empty_reason']}")


def _human_strategy(payload: dict) -> None:
    data = payload.get("data") or {}
    print(f"kind={data.get('kind')} basis={data.get('basis')} "
          f"strategy={data.get('strategy_name') or data.get('scorer')}")
    for key in ("weights", "floors", "params", "sell_rules"):
        if key in data:
            print(f"- {key}: {json.dumps(data[key], ensure_ascii=False, default=str)}")


def _human_experiments(payload: dict) -> None:
    data = payload.get("data")
    if isinstance(data, list):
        _human_rows(payload)
        return
    if isinstance(data, dict) and "experiment_id" in data:
        print(f"experiment_id={data.get('experiment_id')} status={data.get('status')} "
              f"version={data.get('experiment_version')} promoted={data.get('promoted')}")
        for name, variant in (data.get("variants") or {}).items():
            print(f"- {name}: {json.dumps(variant, ensure_ascii=False, default=str)[:240]}")
        return
    print(json.dumps(data, ensure_ascii=False, indent=2, default=str))


def _print_human(payload: dict) -> None:
    command = payload.get("command", "")
    if command == "research datasets":
        _human_datasets(payload)
    elif command == "research schema":
        _human_schema(payload)
    elif command in ("research query", "research signals", "research snapshots"):
        _human_rows(payload)
    elif command == "research scan-quality":
        _human_scan_quality(payload)
    elif command == "research account":
        _human_account(payload)
    elif command == "research strategy":
        _human_strategy(payload)
    elif command.startswith("research experiments"):
        _human_experiments(payload)
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def _request(args) -> dict:
    action = getattr(args, "research_action", None)
    if action == "query":
        return {"dataset": args.dataset}
    if action == "schema":
        return {"dataset": args.dataset}
    if action == "scan-quality":
        return {"date_from": args.from_date, "date_to": args.to_date}
    if action == "account":
        return {"run_id": args.run_id, "view": args.view}
    if action == "signals":
        return {"status": args.status}
    if action == "snapshots":
        return {"kind": args.kind, "date": args.date}
    if action == "strategy":
        return {"kind": args.kind, "run_id": args.run_id}
    if action == "experiments":
        return {"experiments_action": getattr(args, "experiments_action", None),
                "run_id": getattr(args, "run_id", None),
                "experiment_id": getattr(args, "experiment_id", None)}
    return {}


def _dispatch(args) -> dict:
    from dragon_quant.research import main as research

    action = getattr(args, "research_action", None)
    if action == "datasets":
        return research.datasets()
    if action == "schema":
        return research.schema(args.dataset)
    if action == "query":
        filters = {
            "date_from": args.from_date,
            "date_to": args.to_date,
            "code": args.code,
            "scan_id": args.scan_id,
            "run_id": args.run_id,
        }
        return research.query(name=args.dataset, filters=filters,
                              fields=args.fields, limit=args.limit,
                              cursor=args.cursor)
    if action == "scan-quality":
        return research.scan_quality(
            date_from=args.from_date, date_to=args.to_date,
            include_samples=bool(getattr(args, "include_samples", False)),
            sample_limit=getattr(args, "sample_limit", None),
            sample_cursor=getattr(args, "sample_cursor", None))
    if action == "account":
        return research.account(run_id=args.run_id, view=args.view,
                                limit=args.limit, cursor=args.cursor)
    if action == "signals":
        return research.signals(date_from=args.from_date, date_to=args.to_date,
                                code=args.code, status=args.status,
                                limit=args.limit, cursor=args.cursor)
    if action == "snapshots":
        return research.snapshots(kind=args.kind, date=args.date,
                                  date_from=args.from_date, date_to=args.to_date,
                                  code=args.code, limit=args.limit,
                                  cursor=args.cursor)
    if action == "strategy":
        return research.strategy(kind=args.kind, run_id=args.run_id)
    if action == "experiments":
        sub = getattr(args, "experiments_action", None)
        if sub == "list":
            return research.experiments.list(args.run_id, limit=args.limit,
                                             cursor=args.cursor)
        if sub == "show":
            return research.experiments.show(args.experiment_id, run_id=args.run_id)
        if sub == "run":
            from dragon_quant.research.envelope import ResearchError
            from dragon_quant.research.experiments import (
                ExperimentBusy,
                ExperimentInputError,
            )

            try:
                return research.experiments.run(
                    args.run_id, confirm=args.confirm,
                    sell_rules=args.sell_rule or None)
            except ExperimentBusy as exc:
                busy = ResearchError(str(exc))
                busy.code = "experiment_busy"
                busy.exit_code = 1
                raise busy from exc
            except ExperimentInputError as exc:
                failure = ResearchError(str(exc))
                failure.code = exc.code
                failure.exit_code = 1
                raise failure from exc
        raise SystemExit(2)
    raise SystemExit(2)


def handle(args, services) -> None:
    """执行一个 research 叶子命令；``--json`` 时 stdout 只输出一个完整对象。"""
    from dragon_quant.research import main as research
    from dragon_quant.research.envelope import ResearchError

    command = f"research {getattr(args, 'research_action', '')}".strip()
    request = _request(args)
    want_json = bool(getattr(args, "json", False))
    try:
        payload = _dispatch(args)
        if command == "research experiments" and getattr(args, "experiments_action", None):
            command = f"research experiments {args.experiments_action}"
            payload["command"] = command
    except ResearchError as exc:
        payload, code = research.handle_error(command=command, request=request, exc=exc)
        if want_json:
            _json(payload)
        else:
            print(f"错误（{payload['error']['code']}）：{payload['error']['message']}",
                  file=sys.stderr)
        raise SystemExit(code)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - 结构化故障，不吞成空集
        payload, code = research.handle_error(command=command, request=request, exc=exc)
        if want_json:
            _json(payload)
        else:
            print(f"错误（{payload['error']['code']}）：{payload['error']['message']}",
                  file=sys.stderr)
        raise SystemExit(code)
    if want_json:
        _json(payload)
    else:
        _print_human(payload)
    raise SystemExit(0)
