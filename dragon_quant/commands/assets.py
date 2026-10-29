"""assets 命令：数据资产云端同步的薄 CLI adapter。

M6 起装配与动作分派在 :mod:`dragon_quant.infra.assets.sync.cli_service`（默认
``S3Backend`` + 可注入后端；日期/错误协调）。本模块只保留参数映射、人读/JSON
输出与进程退出码，**不构造后端、不调用同步函数、不拼资产源**。

输出契约（M6 前后逐字节保持）：同步动作的 JSON 到 stdout、人读摘要到 stderr；
``doctor`` 只输出 JSON（stderr 为空）；``backfill-dragons`` 的 ``--json`` 输出
完整 JSON，否则只输出人读摘要/失败行到 stderr。退出码 0/1/2 语义不变。
"""

from __future__ import annotations

import json
import sys


def _emit_assets(payload: dict, exit_code: int) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    summary = payload.get("summary") or {}
    if payload.get("ok"):
        if summary:
            print(f"{payload.get('command', 'assets')} 完成："
                  f"ok={payload['ok']} fatal={summary.get('fatal', False)}",
                  file=sys.stderr)
    else:
        reasons = [item.get("reason", str(item)) for item in payload.get("failed") or []]
        if reasons:
            print(f"{payload.get('command', 'assets')} 失败：{reasons[0]}",
                  file=sys.stderr)
    sys.exit(exit_code)


def _assets_human_summary(command: str, result: dict) -> str:
    summary = result.get("summary") or {}
    if command.endswith("push"):
        return (f"推送：pushed={summary.get('pushed', 0)} "
                f"skipped={summary.get('skipped', 0)} "
                f"blobs={summary.get('blobs_uploaded', 0)} "
                f"manifests={summary.get('manifests_uploaded', 0)}")
    if command.endswith("pull"):
        text = (f"恢复：restored={summary.get('restored', 0)} "
                f"skipped={summary.get('skipped', 0)} "
                f"已判定冲突={len(summary.get('conflicts') or [])} "
                f"source={summary.get('manifest_source', 'remote')}")
        pending = int(summary.get("merge_pending") or 0)
        if pending:
            text += (f"\n待判定：{pending} 个行级分片需 --apply 下载后逐行比对"
                     "（dry-run 不代表 0 冲突）")
        deferred = summary.get("deferred") or []
        if deferred:
            text += f"\n延后补齐：{len(deferred)} 条 raw_blob（元数据行缺失）"
        return text
    if command.endswith("status"):
        tables = summary.get("tables") or {}
        synced = sum(1 for item in tables.values() if item.get("remote_entries"))
        text = (f"状态：missing={len(summary.get('dates_missing') or [])} "
                f"mismatched={len(summary.get('dates_mismatched') or [])} "
                f"drift={len(summary.get('dates_drift') or [])} "
                f"local={summary.get('bytes_local', 0)}B "
                f"remote={summary.get('bytes_remote', 0)}B\n"
                f"同步覆盖：{synced}/{len(tables)} 个同步单元有远端数据")
        if summary.get("not_synced"):
            for name, state in summary["not_synced"].items():
                text += f"\n{name}: {state}"
        else:
            text += "\nfactor_* / fin_*: 已纳入同步"
        if summary.get("dates_drift"):
            text += "\ndrift 仅为弱信号（本地行数更多）；定性请用 verify"
        return text
    return (f"校验：dates={summary.get('checked_dates', 0)} "
            f"blobs={summary.get('checked_blobs', 0)} "
            f"mismatched={len(summary.get('mismatched') or [])}")


def _period_filter_hint(summary: dict) -> str | None:
    """显式 --from/--to 排除报告期分区时的一行人读提示（不失败、不改退出码）。"""
    excluded = summary.get("period_excluded") or []
    if not excluded:
        return None
    names = "、".join(item["partition"] for item in excluded[:3])
    suffix = " 等" if len(excluded) > 3 else ""
    return (f"提示：--from/--to 按报告期末过滤，已排除 {len(excluded)} 个报告期分区"
            f"（{names}{suffix}）；如需同步请放宽区间。")


def _backfill_dragons_human_summary(result: dict) -> str:
    prefix = "体检（dry-run）" if result.get("dry_run") else "回填"
    text = (f"{prefix}：dates={len(result.get('dates') or [])} "
            f"rows_source={result.get('rows_source', 0)} "
            f"inserted={result.get('inserted', 0)} "
            f"updated={result.get('updated', 0)} "
            f"unchanged={result.get('unchanged', 0)} "
            f"missing={result.get('missing_rows', 0)} "
            f"orphan={result.get('orphan_rows', 0)}")
    if result.get("pruned"):
        text += f" pruned={result['pruned']}"
    if result.get("failed"):
        text += f" failed={len(result['failed'])}"
    return text


def _emit_backfill(outcome, *, as_json: bool) -> None:
    """``backfill-dragons`` 输出：JSON 模式原样输出；人读模式走 stderr。"""
    payload = outcome.payload
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        if "dates" in payload:
            # 回填结果（成功或单日部分失败）：先给人读摘要与逐日失败行。
            print(_backfill_dragons_human_summary(payload), file=sys.stderr)
            for item in payload.get("failed") or []:
                if isinstance(item, dict):
                    print(f"  - {item.get('date') or '(全局)'}: {item.get('reason')}",
                          file=sys.stderr)
        if not payload.get("ok"):
            reasons = [reason for item in payload.get("failed") or []
                       if isinstance(item, dict)
                       and isinstance(reason := item.get("reason"), str) and reason]
            detail = "；".join(reasons[:3]) or payload.get("error") or "未知错误"
            print(f"❌ {outcome.command} 失败：{detail}", file=sys.stderr)
    sys.exit(outcome.exit_code)


def _emit_purge(payload: dict, exit_code: int, as_json: bool) -> None:
    """``purge-derived`` 输出：JSON 到 stdout，人读摘要到 stderr。"""

    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        if payload.get("ok"):
            counts = payload.get("counts") or {}
            prefix = "体检（dry-run）" if payload.get("dry_run") else "清理"
            print(f"{prefix}：prep_snapshots={counts.get('prep_snapshots', {}).get('rows', 0)} "
                  f"factor_values={counts.get('factor_values', {}).get('rows', 0)} "
                  f"factor_registry={counts.get('factor_registry', {}).get('rows', 0)} "
                  f"model_snapshots={counts.get('factor_model_snapshots', {}).get('rows', 0)}",
                  file=sys.stderr)
            if payload.get("dry_run"):
                print("dry-run 未写库；确认后执行 --apply --yes", file=sys.stderr)
        else:
            reasons = [item.get("reason", str(item))
                       for item in payload.get("failed") or []]
            print(f"❌ 清理失败：{reasons[0] if reasons else '未知错误'}",
                  file=sys.stderr)
    sys.exit(exit_code)


def handle(args, services):
    """数据资产云端同步：doctor / push / pull / status / verify / backfill-dragons。

    另含本地派生清理 ``purge-derived``（默认 dry-run，不触云端）。
    """
    from dragon_quant.infra.assets.main import SYNC_ACTIONS, run

    action = getattr(args, "assets_action", None)
    outcome = run(action, args, services=services)
    if action == "purge-derived":
        _emit_purge(outcome.payload, outcome.exit_code,
                    bool(getattr(args, "json", False)))
        return
    if action in SYNC_ACTIONS:
        payload = outcome.payload
        if outcome.completed:
            # A completed service call always renders its summary, even when it
            # reports ok=False (partial failure / conflicts still have counts).
            print(_assets_human_summary(outcome.command, payload), file=sys.stderr)
            hint = _period_filter_hint(payload.get("summary") or {})
            if hint:
                print(hint, file=sys.stderr)
        _emit_assets(payload, outcome.exit_code)
        return
    if action == "backfill-dragons":
        _emit_backfill(outcome, as_json=bool(getattr(args, "json", False)))
        return
    # doctor / 未知动作：只输出 JSON（stderr 保持为空）。
    print(json.dumps(outcome.payload, ensure_ascii=False, indent=2))
    sys.exit(outcome.exit_code)
