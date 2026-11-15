"""用户策略本地执行 worker（独立子进程，版本化 JSON 协议）。

HTTP 进程绝不导入/执行用户代码：账户回测通过本 worker 在子进程加载冻结快照
并逐事件返回决策。协议（每行一个 JSON）：

- 请求 init: ``{"op":"init","protocol":1,"workspace_root":...,"snapshot_id":...,
  "descriptor_rel":...,"params":{...}}``
- 请求 eval: ``{"op":"eval","kind":"entry"|"exit","context":{...}}``
- 请求 shutdown: ``{"op":"shutdown"}``
- 响应: ``{"ok":true,"protocol":1,...}`` 或 ``{"ok":false,"error":"..."}``

worker 不是安全沙箱：它只是把用户代码与 HTTP 进程隔离并提供超时/取消/清理。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROTOCOL_VERSION = 1
_OUT = sys.stdout


def _reply(payload: dict) -> None:
    _OUT.write(json.dumps(payload, ensure_ascii=False) + "\n")
    _OUT.flush()


def _load_strategy(request: dict):
    from dragon_quant.workspace import catalog
    from dragon_quant.workspace.config import WorkspaceConfig
    from dragon_quant.workspace.loader import create_strategy

    root = Path(request["workspace_root"])
    descriptor_rel = request["descriptor_rel"]
    config = WorkspaceConfig(root=root, workspace_id=str(request.get("workspace_id") or ""),
                             revision=1, strategies=[descriptor_rel])
    descriptor = catalog.load_user_descriptor(root, descriptor_rel)
    if not descriptor.valid:
        raise ValueError("descriptor 非法：" + "；".join(descriptor.errors))
    instance, _merged = create_strategy(
        config, descriptor, dict(request.get("params") or {}),
        keep_workspace_root=True)  # worker 生命周期内保留 helpers 导入路径
    return instance


def main() -> int:
    from dragon_quant.strategy_sdk import (
        context_from_payload, intent_to_payload, validate_intent,
    )
    from dragon_quant.workspace.loader import coerce_intent

    # 用户代码的 print/日志绝不能污染协议 stdout：重定向到 stderr。
    sys.stdout = sys.stderr
    instance = None
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError as exc:
            _reply({"ok": False, "error": f"bad json: {exc}"})
            continue
        op = request.get("op")
        try:
            if op == "init":
                if request.get("protocol") != PROTOCOL_VERSION:
                    raise ValueError(f"协议版本不兼容：{request.get('protocol')}")
                instance = _load_strategy(request)
                _reply({"ok": True, "protocol": PROTOCOL_VERSION})
            elif op == "eval":
                if instance is None:
                    raise ValueError("worker 尚未 init")
                kind = request.get("kind")
                method = "evaluate_entry" if kind == "entry" else "evaluate_exit"
                expected = "buy" if kind == "entry" else "sell"
                ctx = context_from_payload(request["context"])
                result = getattr(instance, method)(ctx)
                intent = coerce_intent(result, expected_action=expected)
                if intent is not None:
                    intent = validate_intent(intent, expected_action=expected)
                _reply({"ok": True, "intent": intent_to_payload(intent)})
            elif op == "shutdown":
                _reply({"ok": True, "bye": True})
                return 0
            else:
                raise ValueError(f"未知 op: {op!r}")
        except Exception as exc:  # noqa: BLE001 - 协议错误逐条返回，不污染 stdout 日志
            _reply({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
