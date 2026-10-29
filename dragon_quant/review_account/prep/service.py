"""prep 编排段：日期/时点门禁、增量计划、采集→计算编排与 ``run_prep``。

架构（M5B 最终边界，见 archived_md/prep/2026-10-21-prep-incremental-rerun-design.md）：
  - 采集器、分类/健康判定与 collection_report 合并在 :mod:`.collectors`；
  - 纯计算在 :mod:`.compute`；dragons 窗口资产与 recompute 在 :mod:`.rebuild`；
  - 本模块只保留「什么时候能跑（门禁）、这次要采什么（增量计划）、采完怎么算
    （编排）」。依赖方向单向：``service → collectors → rebuild → compute``。
  - ``--recompute`` 零检测零采集，用已存资产重算（算法升级可重放历史口径）。
  - same_day_only 资产仅 ``trade_date == today`` 可采；历史日不采集、不写 report，
    由 compute 按缺失降级，防止时点污染。

旧 patch 目标（``_validate_trade_date`` / ``plan_prep`` / ``_run_collection`` /
provider 类）经 :func:`rebuild.legacy` 在调用期从 prep 包门面解析，因此包拆分后
monkeypatch 仍真实改变运行时行为。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Callable, Optional

from dragon_quant.infra.provider.macro import MACRO_INDEX_KEYS
from dragon_quant.review_account.candidate_view import project_candidates
from dragon_quant.infra import clock
from dragon_quant.infra.assets import db as assets_db
from dragon_quant.infra.market_calendar import tdx_trade_dates

from .collectors import (
    _DRAGON_DEPENDENTS, _REVIEW_ONLY_ASSETS, _asset_codes, _collectors,
    _mark_review_failure, _prepare_assets, _providers_from_services,
    _report_status, _uncovered_assets, collection_health, is_collectable,
    merge_report_entries, plan_repairs, plan_review_repairs,
    review_assets_enabled,
)
from .compute import (
    SHANGHAI, _dragon_source, _empty_macro, compute_candidates,
    compute_feedback, compute_macro, compute_main_boards, compute_plan_review,
    compute_review, compute_sentiment, effective_prev_plan,
)
from .rebuild import (
    WINDOW_TRADING_DAYS, _same_dragons_payload, legacy,
    rebuild_dragons_for_recompute,
)

if TYPE_CHECKING:  # 仅注解用：运行期一律经 legacy 从包门面解析（旧 patch 目标）
    from dragon_quant.infra.provider.tdx import TdxProvider

# 盘后运行门禁：当日 ≥16:40 才允许（A50 期指日盘 16:30 收盘 + 缓冲）
_AFTER_CLOSE = (16, 40)


def _review_v2_enabled() -> bool:
    """复盘分计算开关（默认开启；``DQ_REVIEW_V2_ENABLED=0/false`` 只写 disabled 状态键）。"""

    raw = os.environ.get("DQ_REVIEW_V2_ENABLED", "")
    return raw.strip().lower() not in ("0", "false", "no", "off")


class PrepError(ValueError):
    """prep 日期门禁/资产缺失等可预期错误（CLI 捕获后打印并退出）。"""


@dataclass(frozen=True)
class PrepPlan:
    """增量采集计划。

    ``collect`` = 本次要跑的 legacy collector；``repairs`` = 只改 report 的修复项；
    ``review_collect`` = review 独立修复类型（与 legacy ``collect`` 完全分离，
    只补详细池缺失代码、不写 ``collection_report``、不改旧 candidates）。
    """

    collect: list[str]
    repairs: dict[str, dict]
    review_collect: list[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not self.collect and not self.repairs and not self.review_collect


# ─── 日期 / 时点门禁 ───────────────────────────────────────────────────────

def _trading_dates(tdx: TdxProvider) -> list[str]:
    """TDX 指数日K → 升序交易日列表（共享 ``utils.trading.tdx_trade_dates``）。"""
    try:
        return tdx_trade_dates(tdx)
    except ValueError as exc:
        raise PrepError(str(exc)) from exc


def resolve_window_dates(trade_date: str, calendar: list,
                         size: int = WINDOW_TRADING_DAYS) -> list[str]:
    """取 `<= trade_date` 的最近 `size` 个交易日（升序）。

    纯函数：不做 I/O；日历不足时返回全部可用日期，由 compute 段标
    ``dragons_window_short`` 降级。
    """
    eligible = sorted(d for d in (calendar or []) if d <= trade_date)
    return eligible[-size:] if size > 0 else []


def _validate_trade_date(trade_date: str, tdx: TdxProvider,
                         now: Optional[datetime] = None) -> list[str]:
    """prep 本地日期门禁（不依赖雪球日历），返回完整交易日列表。

    - T > 今日 → 报错；
    - T == 今日：<16:40 报「盘后数据尚未就绪」（A50 期指 16:30 收盘 + 缓冲；
      不查日历，雪球日历盘中不含当日会误判）；
      ≥16:40 用 TDX 指数日K校验当日确为交易日（排除周末/节假日盘后误跑）；
    - T < 今日：TDX 指数日K自建日历校验；日历不可用与非交易日报错严格区分。
    """
    now = now or clock.now(SHANGHAI)
    today = now.strftime("%Y-%m-%d")
    if trade_date > today:
        raise PrepError(f"复盘日 {trade_date} 是未来日期")
    if trade_date == today and (now.hour, now.minute) < _AFTER_CLOSE:
        raise PrepError(f"{trade_date} 盘后数据尚未就绪，请 16:40 后运行 prep")
    dates = _trading_dates(tdx)
    if trade_date not in dates:
        raise PrepError(f"{trade_date} 非交易日")
    return dates


# ─── 增量采集计划（默认模式：只补采不健康项）─────────────────────────────────

def plan_prep(assets: dict, trade_date: str, today: str, *,
              force: bool = False) -> PrepPlan:
    """产出增量采集计划（按 ASSET_COLLECTORS 注册表顺序）。

    - ``force``：collect = 全部可采资产；repairs 仍按 C3 计算（force 不抑制修复）。
    - 默认：collect = collection_health 不健康且可采的资产；窗口降级的 dragons
      会触发本地重读（零网络）。dragons 不在 collect 时，对 code 覆盖不足的
      fund_flow/cninfo 追加补采。
    - 历史日 same_day_only 且不可采、但满足 C3 的旧 unavailable → 进 repairs。
    - dragons 重采成功后的依赖级联由 ``run_prep`` 依据 code 集合变化处理。
    """
    repairs = plan_repairs(assets, trade_date, today)
    review_plan = plan_review_repairs(assets, trade_date, today)
    enabled = review_assets_enabled()

    def _allowed(asset_type: str) -> bool:
        return enabled or asset_type not in _REVIEW_ONLY_ASSETS

    if force:
        collect = [t for t in _collectors()
                   if is_collectable(t, trade_date, today) and _allowed(t)]
        return PrepPlan(collect, repairs, list(review_plan.collect))
    report = assets.get("collection_report")
    selected: set[str] = set()
    for asset_type in _collectors():
        if not _allowed(asset_type):
            continue
        if collection_health(asset_type, assets.get(asset_type),
                             _report_status(report, asset_type), trade_date) is None:
            continue
        if asset_type in repairs:      # 已判定可修复：视作就绪，不采集
            continue
        if is_collectable(asset_type, trade_date, today):
            selected.add(asset_type)
    if "dragons" not in selected:
        codes = _asset_codes(assets.get("dragons"))
        for asset_type in _uncovered_assets(assets, codes, selected):
            if _allowed(asset_type) and is_collectable(asset_type, trade_date, today):
                selected.add(asset_type)
    return PrepPlan([t for t in _collectors() if t in selected], repairs,
                    list(review_plan.collect))


# ─── 计算编排 ──────────────────────────────────────────────────────────────

def _compute_all(trade_date: str, assets: dict, *,
                 prev: dict | None = None,
                 prev_meta: dict | None = None) -> dict:
    """compute 段：先按 collection_report/as_of 过滤不可用资产，再逐因子错误隔离。

    固定顺序（技术方案 §4.1）：原始事实装配 → 六维 review（唯一权威）→
    candidates 兼容投影；禁止任何反向依赖。

    collection_report 中 error/unavailable/empty_suspicious（及必然非空资产的 empty）
    一律按缺失降级——残缺数据绝不进入 position_pct 等下游信号。
    """
    usable, degraded = _prepare_assets(assets, trade_date)
    errors: dict[str, str] = {}

    def _guard(name: str, fn, default):
        try:
            return fn()
        except Exception as e:
            errors[name] = f"{type(e).__name__}: {e}"
            return default

    sentiment_result = _guard("sentiment", lambda: compute_sentiment(usable),
                              {"degraded": ["compute_error"]})
    # 降级兜底与 compute_sentiment 的返回点恒为 dict；显式窄化后写入异构键
    # （factor_errors / degraded）不再被推断成 dict[str, list[str]]。
    sentiment: dict[str, object] = (
        dict(sentiment_result) if isinstance(sentiment_result, dict)
        else {"degraded": ["compute_error"]})
    main_boards = _guard("main_boards", lambda: compute_main_boards(usable), [])
    # 原始事实装配：窗口真龙去重 / 入选日 / THS 归因 / 资金公告 / facts；
    # 不再运行旧 evaluate_candidates 推荐规则链（唯一权威是 review）。
    raw_candidates = _guard(
        "candidates", lambda: compute_candidates(usable, trade_date), [])
    macro = _guard("macro", lambda: compute_macro(usable, trade_date), _empty_macro())

    macro_marks: list[str] = []
    if not macro.get("available"):
        macro_marks.append("macro_global")
    else:
        if macro.get("degraded"):
            macro_marks.append("macro_partial")
        # 预期同日但未拿到（源端延迟/品种休市）：显式降级，避免 AC2/AC3 静默失败
        instruments = macro.get("instruments") or {}
        same_day_ok = all(
            isinstance(instruments.get(key), dict)
            and instruments[key].get("session_kind") == "same_day"
            for key in MACRO_INDEX_KEYS)
        if not same_day_ok:
            macro_marks.append("macro_same_day_missing")

    if errors:
        existing_errors = sentiment.get("factor_errors")
        merged_errors = dict(existing_errors) if isinstance(existing_errors, dict) else {}
        merged_errors.update(errors)
        sentiment["factor_errors"] = merged_errors
    existing_degraded = sentiment.get("degraded")
    base_degraded = existing_degraded if isinstance(existing_degraded, list) else []
    sentiment["degraded"] = sorted(
        set(base_degraded) | set(degraded) | set(macro_marks))

    # 复盘分（M3）：唯一权威；旧键之后追加，整体异常只影响自身键。
    review_market = None
    review_mainlines = None
    review_candidates: list = []
    review_available = False
    if not _review_v2_enabled():
        result_review: dict = {"enabled": False, "reason": "disabled"}
    else:
        review_assets = {**usable, "candidates": raw_candidates}
        review_payload = _guard(
            "review",
            lambda: compute_review(review_assets, trade_date,
                                   prev=prev, prev_meta=prev_meta),
            None)
        if not isinstance(review_payload, dict):
            result_review = {"enabled": True,
                             "compute_error": errors.get("review")}
        else:
            review_available = True
            candidate_list = review_payload.get("review_candidates")
            review_candidates = (
                candidate_list if isinstance(candidate_list, list) else [])
            result_review = {
                key: value for key, value in review_payload.items()
                if key not in ("review_candidates", "strategy_context")
            }
            strategy_context = review_payload.get("strategy_context")
            if isinstance(strategy_context, dict):
                result_review["strategy_context"] = strategy_context
            market = review_payload.get("market")
            review_market = market if isinstance(market, dict) else None
            mainlines = review_payload.get("mainlines")
            review_mainlines = mainlines if isinstance(mainlines, list) else None

    # candidates[] = review_candidates 的同结果兼容投影；raw 只补齐事实 / 截断兜底。
    candidates = _guard(
        "candidates_projection",
        lambda: project_candidates(
            review_candidates, raw_candidates, trade_date,
            review_available=review_available),
        [])

    result: dict = {
        "trade_date": trade_date,
        "version": "0.8",
        "sentiment": sentiment,
        "main_boards": main_boards,
        "macro": macro,
        "candidates": candidates,
        "dragon_source": _dragon_source(usable),
    }
    if review_available:
        result["review"] = result_review
        result["review_candidates"] = review_candidates
        strategy_context = result_review.pop("strategy_context", None)
        if isinstance(strategy_context, dict):
            result["strategy_context"] = strategy_context
    else:
        result["review"] = result_review
        result["review_candidates"] = []

    # T+1 反馈（M5）：有效 T-1 计划 = 上一快照 strategy_context（优先）
    # → 已保留的 prev_plan 资产（回退）；首日无计划 → 不写该键。
    plan = effective_prev_plan(prev, usable.get("prev_plan"))
    positions = plan.get("positions") if isinstance(plan, dict) else None
    if isinstance(positions, list) and positions:
        feedback = None
        auction = usable.get("auction_feedback")
        if isinstance(auction, dict):
            feedback = _guard(
                "feedback",
                lambda: compute_feedback(
                    plan, auction, usable.get("t1_intraday"),
                    usable.get("stock_daily", {}), trade_date,
                    current_mainlines=review_mainlines),
                None)
            if isinstance(feedback, dict):
                result["feedback"] = feedback
        plan_review = _guard(
            "plan_review",
            lambda: compute_plan_review(
                plan, feedback or {}, usable.get("t1_intraday"),
                usable.get("stock_daily", {}), trade_date,
                holdings=usable.get("holdings"), current_market=review_market),
            None)
        if isinstance(plan_review, dict):
            result["plan_review"] = plan_review
    return result


# ─── 入口 ──────────────────────────────────────────────────────────────────

def _run_collection(trade_date: str, tdx: TdxProvider, calendar: list,
                    stored: dict, plan: PrepPlan, today: str,
                    now: datetime, services=None) -> dict:
    """执行 plan.collect（dragons 两阶段 + code 变化级联），返回读回后的完整资产。

    阶段 1 只采 dragons（若在计划内），成功后比较新旧 code 集合决定是否级联
    fund_flow/cninfo；阶段 2 按注册表顺序采集其余项，existing 已含新 dragons。
    两次 ``_collect_all`` 共用同一 ``now``（跨零点不产生不同 as_of）。
    ``services`` 原样透传给两次 ``_collect_all``（M2C 可选装配注入）。
    """
    collect_all = legacy("_collect_all")
    window = resolve_window_dates(trade_date, calendar, WINDOW_TRADING_DAYS)
    payloads: dict[str, dict] = {}
    report: dict[str, dict] = {}
    to_run = set(plan.collect)
    if "dragons" in to_run:
        p, r = collect_all(trade_date, window_trade_dates=window,
                           window_days=WINDOW_TRADING_DAYS, tdx=tdx,
                           asset_types=["dragons"], existing=stored, now=now,
                           services=services)
        payloads.update(p)
        report.update(r)
        to_run.discard("dragons")
        if "dragons" in payloads:
            # 级联条件 = dragons 候选 code 集合发生变化（窗口缺日本身不触发网络补采）
            changed = (set(_asset_codes(payloads["dragons"]))
                       != set(_asset_codes(stored.get("dragons"))))
            if changed:
                for dep in _DRAGON_DEPENDENTS:
                    if (dep not in to_run and dep not in payloads
                            and is_collectable(dep, trade_date, today)):
                        to_run.add(dep)
    targets = [t for t in _collectors() if t in to_run]
    if targets:
        # 预热用「存量 + 本次已采 payload + 本次已产 report」：阶段 1 新 dragons 的
        # report 尚未落库，必须一并覆盖，否则旧 error 状态会让依赖采集器误判未就绪。
        base_report = stored.get("collection_report")
        base_report = base_report if isinstance(base_report, dict) else {}
        seed = {**stored, **payloads,
                "collection_report": {**base_report, **report}}
        p, r = collect_all(trade_date, window_trade_dates=window,
                           window_days=WINDOW_TRADING_DAYS, tdx=tdx,
                           asset_types=targets, existing=seed, now=now,
                           services=services)
        payloads.update(p)
        report.update(r)
    merged = merge_report_entries(stored, report, payloads, trade_date, now)
    merged.update(plan.repairs)          # 修复条目最后写入（与本次采集不重叠）
    # review 独立修复：在 legacy 采集与 report 合并**之后**执行；只补缺失代码、
    # 按键合并 payload，不写 collection_report、不改 plan.collect / candidates。
    seed = {**stored, **payloads, "collection_report": merged}
    review_plan = plan_review_repairs(seed, trade_date, today)
    for asset_type in review_plan.collect:
        missing = review_plan.missing.get(asset_type, [])
        try:
            repaired, repair_report = collect_all(
                trade_date, window_trade_dates=window,
                window_days=WINDOW_TRADING_DAYS, tdx=tdx,
                asset_types=[asset_type], existing=seed, now=now,
                services=services, review_only=True, review_codes=missing)
        except Exception as e:
            repaired = {}
            repair_report = {
                asset_type: {"status": "error", "error": f"{type(e).__name__}: {e}"}}
        if asset_type in repaired:
            payloads.update(repaired)
            seed.update(repaired)
            continue
        base = seed.get(asset_type)
        if isinstance(base, dict):
            failed = _mark_review_failure(
                base, repair_report.get(asset_type, {}), missing)
            payloads[asset_type] = failed
            seed[asset_type] = failed
    assets_db.save_prep_assets(trade_date, payloads, merged)
    return assets_db.get_prep_assets(trade_date)   # 读回：快照 = 资产的纯函数


def run_prep(trade_date: str, recompute: bool = False, *,
             force: bool = False, now: Optional[datetime] = None,
             on_plan: Optional[Callable[[PrepPlan], None]] = None,
             services=None) -> dict:
    """执行盘后复盘并落库，返回完整复盘结果 dict。

    正常模式（默认**增量**）：日期门禁 → 读存量资产 → 健康判定 → 只补采不健康且可采的
    资产（dragons 重采且 code 集合变化时级联 fund_flow/cninfo）→ report 按键合并
    （失败补采不降级 + 窄口径修复）→ 读回 → 计算 → 覆盖快照。
    资产完好且无可修复项时零采集（含 dragons 本地读取），report 完全不动。
    ``force=True`` 只影响 collect（重采全部可采资产），repairs 仍按 C3 计算。

    recompute 模式：零检测零采集，用已存资产重算（无资产时报错，不写空快照）。
    重算前先用 ``assets.db.dragons`` 镜像**本地重建** 5 日窗口并回写 dragons 资产
    （仅该资产行 + ``rebuilt_at``/``rebuilt_from_window_days``/``window_source="local"``
    审计键，不写 collection_report）；旧单日资产不重建，按单日口径重放。
    recompute 只读本地已存资产、不触网采集，故不做交易日/时点门禁（仅拒绝未来日期），
    计算段的 as_of 校验仍会拦下时点不符的资产。

    ``services`` 为可选 M2 装配注入口：传入且其 ``providers`` 非空时，交易日历、
    宏观与四个采集 provider 全部取自 bundle；不传（或 bundle 为空）时保持既有
    ``TdxProvider()`` 等构造与 patch 目标。recompute 纯读本地资产，从不构造 provider。
    """
    if recompute:
        if trade_date > clock.now(SHANGHAI).strftime("%Y-%m-%d"):
            raise PrepError(f"复盘日 {trade_date} 是未来日期")
        assets = assets_db.get_prep_assets(trade_date)
        if not assets:
            raise PrepError(f"{trade_date} 无可重算资产（请先正常采集 prep）")
        refreshed = rebuild_dragons_for_recompute(trade_date, assets)
        if refreshed is not None and not _same_dragons_payload(
                assets.get("dragons"), refreshed):
            assets_db.save_prep_assets(trade_date, {"dragons": refreshed})
            assets = assets_db.get_prep_assets(trade_date)
    else:
        now = now or clock.now(SHANGHAI)
        today = now.strftime("%Y-%m-%d")
        bundle = _providers_from_services(services)
        tdx = bundle.tdx if bundle is not None else legacy("TdxProvider")()
        calendar = legacy("_validate_trade_date")(trade_date, tdx, now)  # 与采集共用同一时钟
        stored = assets_db.get_prep_assets(trade_date)
        plan = legacy("plan_prep")(stored, trade_date, today, force=force)
        if on_plan is not None:
            on_plan(plan)
        if plan.is_empty():
            assets = stored                    # 零采集零写入（AC1）
        else:
            assets = legacy("_run_collection")(
                trade_date, tdx, calendar, stored, plan, today, now,
                services=services)
    # prev 注入（修复「主线永远 unknown」）：严格早于 trade_date 的最近快照，
    # 正常与 --recompute 两种模式都接线；绝不返回当日快照（禁止自己和自己比）。
    prev, prev_meta = assets_db.get_previous_prep_snapshot(trade_date, source="v2")
    result = _compute_all(trade_date, assets, prev=prev, prev_meta=prev_meta)
    assets_db.save_prep_snapshot(trade_date, result, source="v2")
    return result
