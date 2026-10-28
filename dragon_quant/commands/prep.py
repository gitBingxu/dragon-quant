"""prep 命令：盘后复盘（增量采集数据资产 + 计算候选池/情绪快照）。"""

from __future__ import annotations

import json
import sys
from zoneinfo import ZoneInfo

from dragon_quant.infra import clock

SHANGHAI = ZoneInfo("Asia/Shanghai")


def handle(args, services):
    """盘后复盘：增量采集数据资产 + 计算候选池/情绪快照，落 assets.db。

    默认输出 4 行中文摘要（是否买入/仓位/等级/理由）；--json 输出完整 JSON。
    """
    from dragon_quant.review_account.prep.main import PrepError, run_prep
    from dragon_quant.infra.assets.main import dragons_mirror

    as_json = getattr(args, "json", False)
    auto_date = not args.date
    if args.date:
        trade_date = _normalize_cli_date(args.date)
    else:
        # 读源切 assets.db.dragons 镜像（AC5）；最新日源库有行而镜像缺行时直接报错，
        # **不得**静默回退到上一交易日（池会静默换日且变小）。
        dates = dragons_mirror.list_dragon_trade_dates(source="v2")
        if not dates:
            # 镜像为空但源库有行 = 尚未回填（事实错误不能报成「无龙头记录」）
            try:
                dragons_mirror.ensure_populated()
            except dragons_mirror.DragonsMirrorEmpty as exc:
                _emit_prep_error({"error": str(exc)}, as_json)
            except Exception as exc:   # 源库不可用/schema 异常：统一错误通道，不抛栈
                _emit_prep_error(
                    {"error": f"龙头镜像检查失败：{type(exc).__name__}: {exc}"}, as_json)
            _emit_prep_error(
                {"error": "无 v2 龙头记录，无法确定复盘日"
                          f"（assets.db.dragons 为空；请运行 {dragons_mirror.BACKFILL_HINT}）"},
                as_json)
        trade_date = dates[-1]
        try:
            stale = dragons_mirror.stale_dates([trade_date])
        except Exception as exc:
            _emit_prep_error(
                {"error": f"龙头镜像对账失败：{type(exc).__name__}: {exc}"}, as_json)
        if stale:
            _emit_prep_error(
                {"error": f"最新交易日 {trade_date} 源库有龙头记录而镜像缺行"
                          "（assets.db.dragons），请运行 assets backfill-dragons；"
                          "已拒绝静默回退到上一交易日"}, as_json)

    try:
        result = run_prep(trade_date, recompute=args.recompute,
                          force=getattr(args, "force", False),
                          on_plan=lambda plan: _prep_plan_hint(plan, trade_date, auto_date),
                          services=services)
    except PrepError as e:
        _emit_prep_error({"error": str(e)}, as_json)
    except Exception as e:  # 与其它命令一致：错误而非抛栈
        _emit_prep_error({"error": f"{type(e).__name__}: {e}"}, as_json)
    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    # 降级信息已并入理由行（details 见 prep_assets.collection_report / --json）
    from dragon_quant.review_account.prep.main import (
        format_advice_lines, format_candidate_lines, format_review_lines,
    )
    try:
        lines = format_advice_lines(result)
    except Exception as e:  # 快照已落库：展示失败不应让 cron 误判整次复盘失败
        print(f"复盘已完成并落库，但摘要生成失败：{type(e).__name__}: {e}"
              f"（可用 --json 查看完整结果）", file=sys.stderr)
        return
    try:
        lines = lines + [""] + format_candidate_lines(result)
    except Exception as e:  # 清单失败仍打印 4 行摘要，不让整次输出丢失
        print(f"标的清单生成失败：{type(e).__name__}: {e}"
              f"（可用 --json 查看完整结果）", file=sys.stderr)
    # 复盘摘要（M4）：旧 4 行与逐只清单逐字不变，新段只追加；新段失败降级一行。
    try:
        review_lines = format_review_lines(result)
    except Exception as e:
        print(f"复盘摘要生成失败：{type(e).__name__}: {e}"
              f"（可用 --json 查看完整结果）", file=sys.stderr)
        review_lines = ["复盘摘要不可用（render_error）"]
    print("\n".join(lines + [""] + review_lines))


def _normalize_cli_date(d: str) -> str:
    from dragon_quant.commands._shared import normalize_cli_date
    return normalize_cli_date(d)


def _prep_plan_hint(plan, trade_date=None, auto_date=False):
    """增量采集计划提示（stderr）。

    走 stderr 是为了保持 stdout 的「4 行摘要 + 标的清单」契约不被污染；
    cron 只按退出码判失败，stderr 输出不改变退出码。
    ``trade_date`` 展示复盘锚定日期；``auto_date``（未显式传 --date）且锚定日早于
    今天时追加原因说明，避免「资产已就绪」被误读为「该采的没采」。
    """
    collect = list(getattr(plan, "collect", None) or [])
    repairs = sorted(getattr(plan, "repairs", None) or {})
    parts = []
    if collect:
        parts.append(f"增量补采 {len(collect)} 项资产：{', '.join(collect)}")
    if repairs:
        prefix = "" if collect else "资产已就绪，"
        parts.append(f"{prefix}修复 {len(repairs)} 项历史状态标记：{', '.join(repairs)}")
    if not parts:
        parts.append("资产已就绪（无需采集），直接重算快照")
    msg = "；".join(parts)
    if trade_date:
        msg = f"复盘日期 {trade_date}：{msg}"
        now = clock.now(SHANGHAI)
        # 仅工作日追加补救提示：周末/节假日锚定日必然早于今天，且当日无法补 scan，
        # 提示「先运行 scan」会形成「怎么弄都没用」的循环（节假日残留由假设语气覆盖）。
        if auto_date and trade_date < now.strftime("%Y-%m-%d") and now.weekday() < 5:
            msg += "（已锚定最新有龙头数据的交易日；若今日为交易日，16:40 后先运行 scan 再 prep 即可复盘今日）"
    print(msg, file=sys.stderr)


def _emit_prep_error(payload: dict, as_json: bool):
    """prep 错误输出：--json 保持原 JSON 到 stdout；默认人读一行到 stderr。"""
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"❌ 复盘失败：{payload.get('error', '未知错误')}", file=sys.stderr)
    sys.exit(1)
