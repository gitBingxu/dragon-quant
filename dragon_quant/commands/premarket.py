"""premarket 命令 — 只读盘前上下文（T0）的薄 CLI adapter。

编排在 ``dragon_quant.review_account.premarket``（Facade + Adapter + 纯投影）；
本模块只负责日期 patch 点、人读 / JSON 输出与进程退出码：

* exit 0：成功（含结构化降级）；
* exit 2：参数错误（日期格式 / 未来日期）；
* exit 1：运行错误。
"""

from __future__ import annotations

import json
import sys


def _emit_error(payload: dict, as_json: bool, *, prefix: str) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"❌ {prefix}：{payload.get('error', '未知错误')}", file=sys.stderr)


def handle(args, services):
    """构建并输出只读盘前上下文；默认 human 摘要，``--json`` 输出完整 JSON。"""

    from dragon_quant.review_account.premarket.main import (
        PremarketError, run_premarket,
    )

    as_json = getattr(args, "json", False)
    try:
        context = run_premarket(getattr(args, "date", None), services=services)
    except PremarketError as exc:
        _emit_error({"ok": False, "error": str(exc)}, as_json,
                    prefix="盘前预案参数错误")
        sys.exit(2)
    except Exception as exc:  # 与其它命令一致：错误而非抛栈
        _emit_error({"ok": False, "error": f"{type(exc).__name__}: {exc}"},
                    as_json, prefix="盘前预案失败")
        sys.exit(1)

    if as_json:
        print(json.dumps(context, ensure_ascii=False, indent=2))
        return
    from dragon_quant.review_account.premarket import render_report

    print("\n".join(render_report(context)))
