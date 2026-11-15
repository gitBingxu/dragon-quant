"""strategy 命令：列出 / 查看当前 / 持久切换 / 校验策略。

``list`` / ``current`` 只读 descriptor，不导入用户 Python。``use`` 持久切换
workspace 的保存选择（CAS，冲突返回非零退出）。``validate`` 是显式验证路径。
"""

from __future__ import annotations

import json
import sys


def handle(args, services):
    action = getattr(args, "strategy_action", None)
    as_json = bool(getattr(args, "json", False))
    workspace = getattr(args, "workspace", None)
    try:
        if action == "list":
            from dragon_quant.workspace.main import list_strategies

            _emit_list(list_strategies(workspace), as_json)
        elif action == "current":
            from dragon_quant.workspace.main import current_selection

            _emit_current(current_selection(workspace), as_json)
        elif action == "use":
            from dragon_quant.workspace.main import select_strategy

            result = select_strategy(workspace, args.strategy_id)
            _emit_current(result, as_json)
        elif action == "validate":
            from dragon_quant.workspace.main import validate_strategy

            result = validate_strategy(args.strategy_id, workspace)
            _emit_validate(result, as_json)
        else:  # pragma: no cover
            print("用法: dragon-quant strategy {list,current,use,validate} ...",
                  file=sys.stderr)
            sys.exit(2)
    except ValueError as exc:
        _fail(exc, as_json)


def _emit_list(result: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    print(f"workspace: {result.get('workspace') or '（内建默认，未配置 workspace）'}")
    print(f"当前策略: {result['current']}")
    for item in result.get("strategies") or []:
        mark = "▶" if item.get("current") else " "
        status = "✓" if item.get("valid") else "✗"
        print(f" {mark} {status} {item.get('id')}  {item.get('label', '')}"
              f"  [{item.get('source')}]")
        for error in item.get("errors") or []:
            print(f"       ⚠️ {error}")


def _emit_current(result: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps({"ok": True, **result}, ensure_ascii=False, indent=2))
        return
    print(f"当前策略: {result.get('current') or result.get('strategy_id')}")
    if result.get("parameters"):
        print(f"参数: {json.dumps(result['parameters'], ensure_ascii=False)}")
    print(f"workspace: {result.get('workspace') or '（未配置）'}"
          f"  revision: {result.get('revision')}")


def _emit_validate(result: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    print(f"✅ 策略校验通过：{result['strategy'].get('id')}")
    detail = result.get("detail") or {}
    if detail.get("parameters"):
        print(f"  参数: {json.dumps(detail['parameters'], ensure_ascii=False)}")


def _fail(exc: Exception, as_json: bool) -> None:
    if as_json:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
    else:
        print(f"❌ {exc}", file=sys.stderr)
    sys.exit(1)
