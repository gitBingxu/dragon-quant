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


def _human_bs_advice(payload: dict) -> None:
    data = payload.get("data") or {}
    print(f"status={data.get('status')} cutoff={data.get('cutoff')} "
          f"strategy={data.get('strategy_id')} snapshot={data.get('snapshot_id')}")
    missing = data.get("missing_capabilities") or []
    if missing:
        print(f"缺项: {', '.join(missing)}")
    print(f"- 决策: {'有' if data.get('decision') else '无（观察/受限）'}")
    print(f"- 保护义务: {len(data.get('protections') or [])} "
          f"意图: {len(data.get('intents') or [])} "
          f"受限: {len(data.get('restrictions') or [])}")
    for note in data.get("notes") or []:
        print(f"- {note}")


def _human_bs_ledger(payload: dict) -> None:
    data = payload.get("data") or {}
    print(f"ledger={data.get('ledger_id')} revision={data.get('revision')} "
          f"orders={len(data.get('orders') or [])} "
          f"fills={len(data.get('fills') or [])} "
          f"batches={len(data.get('batches') or [])}")
    final = data.get("final_state") or {}
    print(f"- 期末现金: {final.get('cash')} 权益: {final.get('equity')}")
    history = data.get("history") or {}
    print(f"- 历史记录: {len(history.get('records') or [])} "
          f"has_more={history.get('has_more')} "
          f"next={history.get('next_cursor')}")


def _human_bs_experiment(payload: dict) -> None:
    data = payload.get("data") or {}
    if isinstance(data, list):
        _human_rows(payload)
        return
    print(f"experiment_id={data.get('id')} status={data.get('status')} "
          f"expected={data.get('expected_events')} "
          f"completed={data.get('completed_events')}")
    metrics = (data.get("report") or {}).get("metrics") or {}
    for name in ("bs", "baseline_no_new_risk"):
        item = metrics.get(name) or {}
        print(f"- {name}: status={item.get('status')} "
              f"net_return_pct={item.get('net_return_pct')} "
              f"max_dd={item.get('max_drawdown_pct')} "
              f"closed={ (item.get('denominator') or {}).get('closed_trades')}")
    comparison = metrics.get("comparison") or {}
    print(f"- delta_net_return_pct={comparison.get('delta_net_return_pct')}")


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
    elif command == "research bs-advice":
        _human_bs_advice(payload)
    elif command == "research bs-ledger":
        _human_bs_ledger(payload)
    elif command == "research bs-experiment":
        _human_bs_experiment(payload)
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
    if action == "bs-advice":
        return {"account_file": getattr(args, "account_file", None),
                "policy_file": getattr(args, "bs_policy", None),
                "cutoff": getattr(args, "cutoff", None),
                "strict": bool(getattr(args, "strict", False))}
    if action == "bs-ledger":
        return {"ledger_id": getattr(args, "ledger_id", None),
                "limit": getattr(args, "limit", None)}
    if action == "bs-experiment":
        return {"experiments_action": getattr(args, "bs_experiments_action", None),
                "experiment_id": getattr(args, "experiment_id", None),
                "include_frozen": bool(getattr(args, "include_frozen", False)),
                "date_from": getattr(args, "from_date", None),
                "date_to": getattr(args, "to_date", None)}
    return {}


def _bs_advice(args) -> dict:
    """BS 只读建议：复用同一证据/政策用例，包成 research_v1 envelope。"""
    from dragon_quant.research import bs_advice
    from dragon_quant.research.envelope import (
        InvalidRequest, ReadonlyUnavailable, envelope,
    )

    try:
        advice = bs_advice.build_advice_from_files(
            account_file=args.account_file, policy_file=args.bs_policy,
            cutoff=args.cutoff, strict=bool(getattr(args, "strict", False)))
    except (bs_advice.BsAdviceError, bs_advice.PolicyFileError,
            bs_advice.AccountFileError) as exc:
        code = getattr(exc, "code", "bs_advice_error")
        cls = (InvalidRequest if int(getattr(exc, "exit_code", 2)) == 2
               else ReadonlyUnavailable)
        error = cls(str(exc))
        error.code = code
        raise error from exc
    policy = advice.get("policy") or {}
    return envelope(
        command="research bs-advice", request=_request(args), data=advice,
        meta={
            "data_quality": ("ok" if advice.get("status") in ("ok", "ready")
                             else "degraded"),
            "snapshot_version": advice.get("snapshot_id"),
            "strategy_version": policy.get("profile_version"),
            "policy_hash": policy.get("content_hash"),
            "calibration_version": (advice.get("capabilities") or {}).get(
                "calibration_version"),
            "returned": 1,
            "truncated": False,
            "next_cursor": None,
        })


def _bs_ledger(args) -> dict:
    """有界只读读回显式 BS review 账本（跨重启可用）。"""
    from dragon_quant.research import bs_advice
    from dragon_quant.research.envelope import (
        InvalidRequest, ReadonlyUnavailable, envelope,
    )

    try:
        data = bs_advice.read_run(
            args.ledger_id, limit=getattr(args, "limit", 100) or 100,
            cursor=getattr(args, "cursor", None))
    except bs_advice.BsRunError as exc:
        error = InvalidRequest(str(exc))
        error.code = getattr(exc, "code", "bs_run_error")
        raise error from exc
    if data is None:
        error = ReadonlyUnavailable(f"未找到 BS 账本 {args.ledger_id}")
        error.code = "ledger_missing"
        raise error
    return envelope(
        command="research bs-ledger", request=_request(args), data=data,
        meta={"data_quality": "ok", "snapshot_version": None,
              "returned": len(data.get("history", {}).get("records") or ()),
              "truncated": bool(data.get("history", {}).get("has_more")),
              "next_cursor": data.get("history", {}).get("next_cursor")})


def _bs_lock_path():
    from dragon_quant.infra.storage import paths

    return paths.DATA_DIR / "locks" / "bs_experiment.lock"


def _read_json_argument(path: str, flag: str) -> dict:
    """读显式 JSON 文件为结构化内容；只把内容带入执行，不把路径存为权威。"""
    import json
    from pathlib import Path

    from dragon_quant.research.envelope import InvalidRequest

    try:
        raw = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        error = InvalidRequest(f"{flag} 无法读取: {exc}")
        error.code = "invalid_request"
        raise error from exc
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        error = InvalidRequest(f"{flag} 不是合法 JSON: {exc}")
        error.code = "invalid_request"
        raise error from exc
    if not isinstance(payload, dict):
        error = InvalidRequest(f"{flag} 必须是 JSON 对象")
        error.code = "invalid_request"
        raise error
    return payload


def _bs_experiment(args) -> dict:
    from dragon_quant.research import bs_advice
    from dragon_quant.research.bs_advice import service
    from dragon_quant.research.envelope import (
        InvalidRequest, envelope, normalize_date,
    )
    from dragon_quant.research.experiments import (
        ConfirmationRequired, ExperimentBusy,
    )

    sub = getattr(args, "bs_experiments_action", None)
    if sub == "list":
        page = bs_advice.list_bs_experiments(
            limit=getattr(args, "limit", 20) or 20,
            cursor=getattr(args, "cursor", None))
        return envelope(
            command="research bs-experiment", request=_request(args),
            data=page["items"],
            meta={"data_quality": "ok", "returned": len(page["items"]),
                  "truncated": page["has_more"],
                  "next_cursor": page["next_cursor"],
                  "total": page["total"]})
    if sub == "show":
        row = bs_advice.show_bs_experiment(
            int(args.experiment_id),
            include_frozen=bool(getattr(args, "include_frozen", False)),
            include_details=bool(getattr(args, "include_details", False)))
        if row is None:
            error = InvalidRequest(f"未找到 BS 实验 {args.experiment_id}")
            error.code = "experiment_not_found"
            raise error
        return envelope(command="research bs-experiment", request=_request(args),
                        data=row, meta={"data_quality": "ok", "returned": 1})
    if sub in ("replay", "resume"):
        try:
            result = (bs_advice.replay_bs_experiment(int(args.experiment_id))
                      if sub == "replay" else
                      service.resume_experiment_flow(
                          experiment_id=int(args.experiment_id),
                          lock_path=_bs_lock_path()))
        except bs_advice.BsExperimentError as exc:
            error = InvalidRequest(str(exc))
            error.code = exc.code
            error.exit_code = 2 if exc.status == 400 else 1
            raise error from exc
        return envelope(command="research bs-experiment replay",
                        request=_request(args), data=result,
                        meta={"data_quality": "ok",
                              "returned": len(result.get("events") or ())})
    if sub != "run":
        raise InvalidRequest("bs-experiment 需要 run/list/show/replay/resume")
    if not getattr(args, "confirm", False):
        raise ConfirmationRequired("BS 对照实验会消耗本地算力，需显式 --confirm")
    try:
        policy = bs_advice.load_policy_file(args.bs_policy)
        account = bs_advice.load_account_file(
            args.account_file, policy_hash=policy.content_hash)
    except (bs_advice.PolicyFileError, bs_advice.AccountFileError) as exc:
        error = InvalidRequest(str(exc))
        error.code = getattr(exc, "code", "invalid_request")
        raise error from exc
    try:
        date_from = normalize_date(args.from_date, "--from")
        date_to = normalize_date(args.to_date, "--to")
    except ValueError as exc:
        raise InvalidRequest(str(exc)) from exc
    if not date_from or not date_to:
        raise InvalidRequest("bs-experiment run 需要 --from 与 --to")
    from dragon_quant.infra.storage import paths

    study = None
    study_file = getattr(args, "study_file", None)
    if study_file:
        study = _read_json_argument(study_file, "--study-file")
    enhanced = None
    enhanced_id = getattr(args, "enhanced_experiment_id", None)
    enhanced_file = getattr(args, "enhanced_file", None)
    if enhanced_id and enhanced_file:
        raise InvalidRequest("--enhanced-experiment-id 与 --enhanced-file 二选一")
    if enhanced_id:
        try:
            enhanced = service.enhanced_freeze_for_experiment(
                experiment_id=int(enhanced_id))
        except bs_advice.BsExperimentError as exc:
            error = InvalidRequest(str(exc))
            error.code = exc.code
            error.exit_code = 2 if exc.status == 400 else 1
            raise error from exc
    if enhanced_file:
        enhanced = _read_json_argument(enhanced_file, "--enhanced-file")

    try:
        data = service.run_experiment_flow(
            account=account, policy=policy, date_from=date_from,
            date_to=date_to, confirm=True,
            lock_path=paths.DATA_DIR / "locks" / "bs_experiment.lock",
            cohort_mode=getattr(args, "cohort_mode", "rolling"),
            new_attempt=bool(getattr(args, "new_attempt", False)),
            study=study, enhanced=enhanced)
    except ExperimentBusy as exc:
        error = InvalidRequest("已有对照实验在运行，请稍后再试")
        error.code = "experiment_busy"
        error.exit_code = 1
        raise error from exc
    except bs_advice.BsExperimentError as exc:
        error = InvalidRequest(str(exc))
        error.code = exc.code
        error.exit_code = 2 if exc.status == 400 else 1
        raise error from exc
    return envelope(
        command="research bs-experiment", request=_request(args), data=data,
        meta={"data_quality": ("ok" if data.get("status") == "completed"
                               else "degraded"),
              "expected_events": data.get("expected_events"),
              "completed_events": data.get("completed_events")})


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
    if action == "bs-advice":
        return _bs_advice(args)
    if action == "bs-ledger":
        return _bs_ledger(args)
    if action == "bs-experiment":
        return _bs_experiment(args)
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
                    sell_rules=args.sell_rule or None,
                    strategy_params=_parse_strategy_params(
                        getattr(args, "strategy_param", None)))
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


def _parse_strategy_params(items):
    """``--strategy-param k=v`` → 单个参数变体 dict（值尝试 JSON，失败按字符串）。"""
    if not items:
        return None
    import json as _json
    params = {}
    for item in items:
        if "=" not in item:
            raise ValueError(f"--strategy-param 需要 k=v 形式，得到 {item!r}")
        key, raw = item.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError("--strategy-param 参数名不能为空")
        try:
            params[key] = _json.loads(raw)
        except _json.JSONDecodeError:
            params[key] = raw
    return [params]
