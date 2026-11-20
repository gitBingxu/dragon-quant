"""review-account 命令：账户级模拟交易回测，或账户 Web UI。"""

from __future__ import annotations

import json
import sys


def handle(args, services):
    """账户级模拟交易 review 命令（退出码/错误通道与原 handler 一致）。"""
    if args.ui_only:
        _start_ui(args)
        return

    if not args.date_from or not args.date_to:
        print("错误: review-account 需要 --from 和 --to，或使用 --ui-only", file=sys.stderr)
        return

    from dragon_quant.commands import _shared
    from dragon_quant.review_account.main import run_review_account

    options: dict = (
        {"strategy_params": _shared.load_strategy_params(args.config)}
        if args.config else {}
    )
    selection = _resolve_selection(getattr(args, "workspace", None),
                                   getattr(args, "strategy", None))
    if selection is not None and _is_bs_strategy(selection.strategy_id):
        _run_bs(args)
        return
    want_json = bool(getattr(args, "json", False))
    call_kwargs = dict(
        date_from=_normalize_cli_date(args.date_from),
        date_to=_normalize_cli_date(args.date_to),
        initial_cash=args.capital,
        source=args.source,
        strategy_name=args.strategy,
        verbose=not want_json,
        services=services,
    )
    # 无 workspace 选择时不传 selection，保持既有调用签名（旧测试/兼容层逐位兼容）。
    if selection is not None:
        call_kwargs["selection"] = selection
    result = run_review_account(**call_kwargs, **(options or {}))
    if want_json:
        # 兼容回执：只新增 JSON 输出，人读默认格式不变；供 Agent 取得真实 run_id
        # 后再用 `research account --run-id` 读回。
        print(json.dumps({
            "ok": True,
            "run_id": result.get("run_id"),
            "source": result.get("source") or args.source,
            "strategy_name": result.get("strategy_name") or args.strategy,
            "date_from": result.get("date_from"),
            "date_to": result.get("date_to"),
            "date_to_clamped": bool(result.get("date_to_clamped")),
            "initial_cash": result.get("initial_cash"),
            "final_equity": result.get("final_equity"),
            "total_return": result.get("total_return"),
            "max_drawdown": result.get("max_drawdown"),
            "trade_count": result.get("trade_count"),
            "win_rate": result.get("win_rate"),
            "strategy_id": result.get("strategy_id"),
            "strategy_ref": result.get("strategy_ref"),
        }, ensure_ascii=False, indent=2))

    if args.ui:
        _start_ui(args)


def _is_bs_strategy(strategy_id: str) -> bool:
    return str(strategy_id or "") in ("builtin:bs_account_decision",
                                      "bs_account_decision")


def _run_bs(args) -> None:
    """BS 账户决策分支：完整本地事件/账户用例，不复用旧 SDK entry/exit。"""
    from dragon_quant.research.bs_advice import (
        AccountFileError, BsReviewError, BsRunError, PolicyFileError,
        load_account_file, load_policy_file, run_bs_review,
    )

    policy_path = getattr(args, "bs_policy", None)
    account_path = getattr(args, "account_file", None)
    if not policy_path or not account_path:
        print(
            "错误: 选择 builtin:bs_account_decision 需要 --bs-policy 与 "
            "--account-file；只读建议请用 research bs-advice，"
            "不会静默套用旧 buy-all 逻辑",
            file=sys.stderr)
        raise SystemExit(2)
    try:
        policy = load_policy_file(policy_path)
        account = load_account_file(account_path, policy_hash=policy.content_hash)
        result = run_bs_review(
            account=account, policy=policy,
            date_from=_normalize_cli_date(args.date_from),
            date_to=_normalize_cli_date(args.date_to),
            persist=not bool(getattr(args, "no_persist", False)))
    except (PolicyFileError, AccountFileError, BsReviewError, BsRunError) as exc:
        print(f"错误（{exc.code}）：{exc}", file=sys.stderr)
        raise SystemExit(getattr(exc, "exit_code", 1)) from exc
    if getattr(args, "json", False):
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    print(f"模式={result['mode']} ledger={result.get('ledger_id') or '-'} "
          f"revision={result.get('revision')}")
    for note in result.get("notes") or ():
        print(f"- {note}")
    print(f"- 事件数: {len(result.get('events') or ())} "
          f"成交数: {len(result.get('fills') or ())}")
    final = result.get("final_state") or {}
    print(f"- 期末现金: {final.get('cash')} 权益: {final.get('equity')} "
          f"回撤%: {final.get('drawdown_pct')}")
    if result.get("persisted"):
        print(f"- 只读读回: dragon-quant research bs-ledger "
              f"--ledger-id {result['ledger_id']} --json")


def _start_ui(args):
    """启动账户级 review Web UI（deprecated 别名，转发 dashboard 语义）。"""
    from dragon_quant.commands.dashboard import ensure_web_extra
    from web_ui.server import start_server

    ensure_web_extra(auto=False)
    print(
        "review-account: --ui/--ui-only 已弃用，"
        "请改用 dragon-quant dashboard（后续版本将移除）",
        file=sys.stderr,
    )
    start_server(
        port=args.port,
        open_browser=not args.no_browser,
        default_source=getattr(args, "source", "v2"),
        default_page="account",
    )


def _normalize_cli_date(d: str) -> str:
    from dragon_quant.commands._shared import normalize_cli_date
    return normalize_cli_date(d)


_LEGACY_STRATEGY_NAMES = {None, "", "dragon_pullback_daily"}


def _resolve_selection(workspace, strategy_name):
    """解析本次回测的策略选择；无 workspace 且未给出策略 ID 时返回 None（旧行为）。"""
    explicit = None if strategy_name in _LEGACY_STRATEGY_NAMES else strategy_name
    from dragon_quant.workspace.config import resolve_workspace_path

    looks_like_id = bool(explicit and ":" in explicit)
    if not (workspace or looks_like_id or resolve_workspace_path(None) is not None):
        return None
    from dragon_quant.workspace.main import resolve_selection

    return resolve_selection(explicit_workspace=workspace,
                             explicit_strategy_id=explicit)
