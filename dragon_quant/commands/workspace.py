"""workspace 命令：初始化与检查用户策略工作区。

``init`` 会显式创建目录并登记本机活动 workspace；``check`` 是显式验证路径，
可导入用户策略。普通查询/list/import 不经过本模块。
"""

from __future__ import annotations

import json
import sys


def handle(args, services):
    action = getattr(args, "workspace_action", None)
    as_json = bool(getattr(args, "json", False))
    try:
        if action == "init":
            from dragon_quant.workspace.main import init_workspace

            result = init_workspace(args.path)
            _emit(result, as_json)
        elif action == "check":
            from dragon_quant.workspace.main import check_workspace

            result = check_workspace(getattr(args, "workspace", None))
            _emit(result, as_json)
        else:  # pragma: no cover - argparse 保证
            print("用法: dragon-quant workspace {init,check} ...", file=sys.stderr)
            sys.exit(2)
    except ValueError as exc:
        _fail(exc, as_json)


def _emit(result: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    if result.get("ok") is False:
        print("❌ workspace 检查未通过")
    else:
        print("✅ workspace 就绪")
    if result.get("workspace"):
        print(f"  workspace: {result['workspace']}")
    if result.get("workspace_id"):
        print(f"  id: {result['workspace_id']}  revision: {result.get('revision')}")
    if result.get("created_files"):
        print(f"  新建文件: {len(result['created_files'])} 个")
    if result.get("current"):
        print(f"  当前策略: {result['current']}")
    for entry in result.get("strategies") or []:
        status = "✓" if entry.get("valid") else "✗"
        print(f"  {status} {entry.get('id')}  {entry.get('label', '')}")
    for error in result.get("errors") or []:
        print(f"  ⚠️ {error}")


def _fail(exc: Exception, as_json: bool) -> None:
    if as_json:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
    else:
        print(f"❌ {exc}", file=sys.stderr)
    sys.exit(1)
