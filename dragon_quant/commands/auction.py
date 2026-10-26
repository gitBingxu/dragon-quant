"""auction 命令 — 集合竞价采集/重算的薄 CLI adapter。

采集/重算编排在 ``dragon_quant.review_account.auction``（main facade +
collect/recompute/scoring/dates；Task 13）；本模块只负责日期 patch 点、人读/JSON
输出与进程退出码，行为、JSON 键序与 stdout/stderr 通道保持不变。
"""

from __future__ import annotations

import json
import sys

# 稳定公共入口 re-export（既有测试直接从这里取）：同一实现，不复制。
from dragon_quant.review_account.auction.main import (  # noqa: F401
    _score_auction_assets,
    _trade_calendar,
)


def _resolve_trade_date(raw):
    from dragon_quant.commands._shared import resolve_trade_date
    return resolve_trade_date(raw)


def _emit_auction_error(payload: dict, as_json: bool):
    """auction 错误输出：--json 保持原 JSON 到 stdout；默认人读一行到 stderr。"""
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"❌ 集合竞价失败：{payload.get('error', '未知错误')}", file=sys.stderr)
    sys.exit(1)


def _render_report(outcome):
    """采集/重算成功结果的人读摘要（纯展示；失败不改变退出码）。"""
    from dragon_quant.review_account.auction.main import format_report

    payload = outcome.payload
    if outcome.mode == "recompute":
        kwargs = {
            "trade_date": payload["trade_date"],
            "mode": "recompute",
            "failed_count": len(payload["failed"]),
            "empty_count": len(payload["skipped_empty"]),
            "saved_count": payload["scored_count"],
            "names": outcome.render_names,
        }
    else:
        kwargs = {
            "trade_date": payload["trade_date"],
            "mode": "collect",
            "pool_source": payload["pool_source"],
            "candidate_count": payload["candidate_codes"],
            "fallback_reason": payload["fallback_reason"],
            "failed_count": len(payload["failed"]),
            "empty_count": len(payload["empty"]),
            "saved_count": payload["saved_count"],
            "names": outcome.render_names,
        }
    try:
        print("\n".join(format_report(payload["snapshots"], **kwargs)))
    except Exception as exc:  # 展示失败不应改变退出码（与 prep 同策略）
        print(f"⚠️ 人读摘要生成失败：{type(exc).__name__}: {exc}"
              "（可用 --json 查看完整结果）", file=sys.stderr)


def _emit(outcome, as_json: bool):
    for warning in outcome.warnings:
        print(warning, file=sys.stderr)

    payload = outcome.payload
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    elif outcome.mode == "error":
        _emit_auction_error(payload, False)   # 立即退出 1
    elif outcome.mode == "empty":
        from dragon_quant.review_account.auction.main import format_report
        print("\n".join(format_report(
            [], trade_date=payload["trade_date"],
            pool_source=payload["pool_source"],
            fallback_reason=payload["fallback_reason"],
            candidate_count=0, empty_count=0,
        )))
    else:
        _render_report(outcome)

    if outcome.exit_code:
        if outcome.mode == "collect" and not as_json:
            # 原始资产一条都没写成功通常是系统性故障（schema/磁盘/权限），
            # 不能让定时任务把「一条都没写成功」当作成功。
            detail = f"候选 {payload['candidate_codes']} 只，失败 {len(payload['failed'])} 条"
            if payload.get("snapshot_error"):
                detail += f"，快照错误：{payload['snapshot_error']}"
            print(f"❌ 集合竞价失败：{detail}（详情加 --json）", file=sys.stderr)
        sys.exit(outcome.exit_code)


def handle(args, services):
    """采集最近 N 日龙头的当日集合竞价盘口 + 行情，落原始资产并计算快照。

    默认输出人读摘要（参与标的强弱 + 汇总）；--json 输出完整 JSON。
    """
    from dragon_quant.review_account.auction.main import run_auction, run_auction_recompute

    as_json = getattr(args, "json", False)
    recompute = getattr(args, "recompute", None)
    today = _resolve_trade_date(None)
    if recompute is not None:
        outcome = run_auction_recompute(recompute, services=services, today=today)
    else:
        outcome = run_auction(
            pool=getattr(args, "pool", "prep"),
            days=getattr(args, "days", None),
            services=services,
            today=today,
        )
    _emit(outcome, as_json)
