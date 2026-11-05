"""``bs-eval`` 命令：薄 handler（参数/输出/退出码）。

可再次执行本命令增量更新；``/bs`` 页面「重置买点」等价 ``--force`` 全量重算。
"""

from __future__ import annotations

import json
import sys


def _normalize(raw, field):
    from dragon_quant.bs_eval.main import _normalize_date

    return _normalize_date(raw, field)


def handle(args, services):
    from dragon_quant.bs_eval.main import run_bs_eval

    as_json = getattr(args, "json", False)
    try:
        date_from = _normalize(getattr(args, "date_from", None), "--from")
        date_to = _normalize(getattr(args, "date_to", None), "--to")
        if date_from and date_to and date_from > date_to:
            raise ValueError("--from 不能晚于 --to")
        limit = getattr(args, "limit", None)
        if limit is not None and int(limit) <= 0:
            raise ValueError("--limit 必须大于 0")
    except ValueError as exc:
        print(f"❌ bs-eval 参数错误：{exc}", file=sys.stderr)
        raise SystemExit(2)

    try:
        result = run_bs_eval(
            date_from=date_from,
            date_to=date_to,
            limit=limit,
            force=bool(getattr(args, "force", False)),
            source=getattr(args, "source", "v2"),
            services=services,
        )
    except Exception as exc:  # noqa: BLE001 - 运行期失败统一 exit 1
        print(f"❌ bs-eval 失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1)

    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    counters = result["counters"]
    span_from = result.get("range_from") or "全量"
    span_to = result.get("range_to") or "全量"
    print(f"BS 回放完成：run #{result['run_id']} · 入选日 {span_from} ~ {span_to} · "
          f"回放截止 {result.get('replay_end') or '—'}")
    print(f"扫描 {counters['rows_scanned']} · 评估 {counters['rows_evaluated']} · "
          f"跳过终态 {counters['rows_skipped_terminal']} · "
          f"无信号 {counters['rows_no_signal']}")
    print(f"买入 {counters['rows_bought']} · 卖出 {counters['rows_sold']} · "
          f"窗口未成熟 {counters['rows_partial']} · 错误 {counters['rows_error']}")
    print(f"策略 {result['strategy_hash']} · 评测器 {result['evaluator_version']} · "
          f"耗时 {result['elapsed_s']}s")
    if result.get("note"):
        print(f"注意：{result['note']}")
    print("提示：可再次执行 bs-eval 增量更新，或在 /bs 页面点击「重置买点」全量重算。")
    print("提示：首次使用建议先 `--limit 5` 试跑。")


__all__ = ["handle"]
