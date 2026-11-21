"""BS 研究只读显示投影（纯函数：无 I/O、无 SQL、无时钟、不改写领域结果）。

领域报告（Advice / ledger / experiment）保持原样；本模块只把 C2 真实 schema
转成普通中文视图 DTO：能力/限制、分母、政策矩阵、指标单位/定义/样本与成熟、
账本状态与事件。前端只渲染，不重算交易或统计。原始 JSON 由调用方放入审计折叠。
"""

from __future__ import annotations

from typing import Any, Mapping

REASON_LABELS = {
    "policy_config_missing": "缺少策略配置（仅观察，不新增风险）",
    "account_cash_unknown": "账户现金未知（不伪造账户）",
    "market_data_missing": "缺少市场数据",
    "execution_coverage_missing": "缺少可成交事件覆盖",
    "intraday_bars_missing": "缺少分钟观测（不能用日 K 冒充成交）",
    "evidence_not_available": "所需证据在时点不可用",
    "freshness_model_missing": "缺少报价新鲜度模型",
    "no_price_reference": "缺少可用参考价",
    "insufficient_cash": "现金不足",
    "account_risk_exceeded": "账户风险预算不足",
    "per_trade_risk_exceeded": "单笔风险预算不足",
    "risk_unknown_holding": "存量持仓风险未知，新增风险受限",
    "no_sellable": "无可卖数量（T+1/锁定）",
    "trigger_not_met": "触发条件未满足",
    "depends_on_unfilled": "依赖订单未成交",
}

PLAN_STATUS_LABELS = {
    "selected": "已选",
    "rejected": "放弃",
    "wait": "等待",
    "protect": "保护",
}

_METRIC_LABELS = (
    ("net_return_pct", "净收益", "percent"),
    ("final_equity", "期末权益", "cny"),
    ("max_drawdown_pct", "最大回撤", "percent"),
    ("max_drawdown_duration_trade_days", "回撤持续", "trade_days"),
    ("fees", "费用", "cny"),
    ("slippage_cost_estimate", "滑点（模型估计）", "cny"),
    ("turnover", "换手（名义额/平均权益）", "ratio"),
    ("tail_loss_count", "尾部亏损笔数", "count"),
    ("tail_loss_severity", "尾部最大亏损", "cny"),
    ("right_tail_max", "右尾最大盈利", "cny"),
    ("right_tail_share", "右尾占比", "ratio"),
    ("risk_budget_used_peak", "风险占用峰值", "cny"),
    ("risk_budget_used_time_weighted", "风险占用时间加权", "cny"),
    ("excess_risk_budget_peak", "超额风险预算", "cny"),
    ("unfilled_orders", "未成交订单", "count"),
    ("unfilled_ratio", "未成交订单占比", "ratio"),
    ("unfilled_quantity_ratio", "未成交数量占比", "ratio"),
    ("recovery_active_trade_days", "恢复期交易日", "trade_days"),
    ("qualified_sample_n", "合格样本 N", "count"),
    ("qualified_win_rate", "合格样本胜率", "ratio"),
)

_UNIT_LABELS = {
    "percent": "%", "cny": "元", "trade_days": "交易日",
    "count": "笔", "ratio": "比例",
}


_METRIC_EXPLAIN = {
    "net_return_pct": "期末权益相对期初权益的收益；期初未知时不显示。",
    "final_equity": "冻结回放期末权益；含持仓合法估值，未知则待证据。",
    "max_drawdown_pct": "交易日权益曲线峰到谷的最大回撤。",
    "max_drawdown_duration_trade_days": "自峰日（含）到谷/恢复的交易日数；平盘为 0。",
    "fees": "已实现成交费用合计（按 fills 样本）。",
    "slippage_cost_estimate": "参考价缺失时的分边模型估计，不是实测。",
    "turnover": "成交名义额 / 期初期末平均权益。",
    "tail_loss_count": "合格成熟闭合交易中亏损超过冻结阈值的笔数。",
    "tail_loss_severity": "合格样本里最大单笔尾部亏损。",
    "right_tail_max": "合格样本里最大单笔盈利。",
    "right_tail_share": "最大盈利 / 全部正收益（合格样本内）。",
    "risk_budget_used_peak": "共享 RiskBook 峰值占用；持仓风险未知时待证据。",
    "risk_budget_used_time_weighted": "按交易日末快照的平均风险占用（未知日排除）。",
    "excess_risk_budget_peak": "峰值风险占用超出账户预算的金额。",
    "unfilled_orders": "期末仍有剩余数量的订单（含取消/拒绝/部分/挂单）。",
    "unfilled_ratio": "未成交订单 / 唯一订单数。",
    "unfilled_quantity_ratio": "未成交数量 / 请求数量。",
    "recovery_active_trade_days": "存在恢复阶段的交易日数（同日多阶段去重）。",
    "qualified_sample_n": "成熟且合格、可进入该指标统计的闭合样本数。",
    "qualified_win_rate": "合格成熟闭合样本中的盈利比例。",
}


def _sample_n(key: str, denominator: Mapping) -> tuple:
    if key.startswith(("tail", "right_tail", "qualified")):
        return denominator.get("qualified_closed_trades"), "合格成熟闭合样本"
    if key in ("fees", "slippage_cost_estimate", "turnover"):
        return denominator.get("fills"), "成交 fills"
    if key.startswith("unfilled") or key in ("order_denominator",):
        return denominator.get("orders"), "唯一订单"
    if key.startswith("recovery"):
        return denominator.get("events"), "事件"
    return denominator.get("events"), "冻结事件"


_REJECT_REASON_LABELS = {
    **REASON_LABELS,
    "history_auction_path_missing": "缺少历史竞价路径",
    "theme_membership_missing": "缺少主题归属",
    "missing_api_key": "缺少数据源密钥（按能力降级）",
    "probability_missing": "缺少概率样本",
    "teaching_fixture_parameters": "教学参数（非推荐）",
    "evidence_missing": "缺少确认证据",
    "evidence_not_usable": "证据不可用",
    "probe_not_filled": "试探未成交",
    "trade_calendar_insufficient": "交易日历不足",
    "risk_unknown_holding": "存量持仓风险未知",
}

_CAPABILITY_LABELS = {
    "intraday_bars": "分钟K",
    "daily_bars": "日K",
    "history_auction_path": "历史竞价路径",
    "auction_path": "竞价路径",
    "auction_final": "竞价收盘快照",
    "theme_membership": "主题归属",
    "account_state": "账户状态",
    "market_context": "市场环境",
    "probability_sample": "概率样本",
    "tradability": "可交易性",
    "evidence_snapshot": "证据快照",
    "calibration": "校准",
}


def _reason(code: str) -> str:
    text = str(code or "")
    if not text:
        return "未识别原因"
    if text in _REJECT_REASON_LABELS:
        return _REJECT_REASON_LABELS[text]
    head, sep, tail = text.partition(":")
    if sep and head in _REJECT_REASON_LABELS:
        return f"{_REJECT_REASON_LABELS[head]}：{tail}"
    return f"未识别原因（{text}）"


def _capability_label(code: str) -> str:
    return _CAPABILITY_LABELS.get(str(code), f"未识别能力（{code}）")


def _reason_labels(item: Mapping) -> list[str]:
    """只解释 reasons/独立 reason；绝不把股票代码 code 当拒因。"""
    raw = []
    reasons = item.get("reasons")
    if isinstance(reasons, (list, tuple)):
        raw.extend(str(value) for value in reasons if value)
    single = item.get("reason")
    if single and str(single) not in raw:
        raw.append(str(single))
    return [_reason(value) for value in raw]


def _engine_label(status: str) -> str:
    return {"completed": "工程完成", "failed": "工程失败",
            "running": "运行中", "insufficient_data": "工程受限",
            "evaluated": "已评估"}.get(str(status), str(status) or "未知")


def _effect_label(status: str) -> str:
    return ("效果合格" if status == "evaluated"
            else "工程完成但效果证据不足")


def _capabilities(raw: Mapping) -> dict:
    caps = raw.get("capabilities") or {}
    usable = list(caps.get("usable_capabilities")
                  or raw.get("used_capabilities") or ())
    missing = list(caps.get("missing_capabilities")
                   or raw.get("missing_capabilities") or ())
    return {
        "usable": usable,
        "missing": missing,
        "usable_labels": [_capability_label(item) for item in usable],
        "missing_labels": [_capability_label(item) for item in missing],
        "usability": "完整" if not missing else "受限",
    }


def _denominators(raw: Mapping) -> dict:
    universe = raw.get("universe") or {}
    decision = raw.get("decision") or {}
    rejected = list(raw.get("rejected") or decision.get("rejected") or ())
    intents = list(raw.get("intents") or decision.get("selected_actions") or ())
    codes = universe.get("items") or universe.get("codes") or ()
    return {
        "universe_codes": len(codes),
        "candidates": len(raw.get("candidates") or ()),
        "rejected_policies": len(rejected),
        "intents": len(intents),
        "unfilled": None,
        "unfilled_label": "建议未执行，不适用（尚未撮合）",
        "note": ("拒绝数是政策/计划拒绝条数，不等于母集股票数；"
                 "意图未撮合与实际未成交分开，空样本不显示为成功。"),
    }


def _plans(raw: Mapping) -> dict:
    decision = raw.get("decision") or {}
    plans = list(decision.get("plans") or ())
    selected = decision.get("selected_plan_id")
    rejected = list(decision.get("rejected") or raw.get("rejected") or ())
    abandoned = []
    for item in rejected:
        labels = _reason_labels(item)
        abandoned.append({
            "plan_id": item.get("plan_id") or item.get("rule_id"),
            "code": item.get("code"),
            "status": "放弃",
            "reasons": item.get("reasons") or ([] if not item.get("reason") else [item.get("reason")]),
            "reasons_labels": labels,
            "reason_summary": "；".join(labels) or "未识别原因",
        })
    return {
        "selected_plan_id": selected,
        "selected_status": "已选" if selected else "未选（等待/受限）",
        "abandoned": abandoned,
        "plans": [{"plan_id": item.get("plan_id"),
                   "status": item.get("status")} for item in plans],
    }


def _preference(raw: Mapping) -> dict:
    decision = raw.get("decision") or {}
    audit = decision.get("audit") or {}
    probability = audit.get("probability") or decision.get("probability") or {}
    status = str(probability.get("status") or "")
    qualified = bool(probability.get("qualified") is True
                     and status not in ("unqualified", "unknown", "missing"))
    reasons = list(probability.get("reasons") or ())
    ev_value = None
    if qualified:
        for key in ("ev", "expected_value", "ev_value"):
            value = probability.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                ev_value = float(value)
                break
    rho = None
    policy = raw.get("policy") or {}
    normalized = policy.get("normalized") or policy.get("config") or {}
    preference = normalized.get("preference") if isinstance(normalized, dict) else None
    if isinstance(preference, dict):
        rho = preference.get("rho")
    status_label = ("合格" if qualified else
                    {"unqualified": "不合格", "unknown": "未知",
                     "missing": "缺失"}.get(status, status or "缺失"))
    if not qualified:
        note = "概率样本不合格：不显示 EV 数值；rho 偏好分不是 EV。"
    elif ev_value is None:
        note = "概率样本合格，但该策略/结果未提供可展示 EV 数值（不伪造）。"
    else:
        note = "概率样本合格，EV 可作描述性参考；rho 偏好分不是 EV。"
    return {
        "rho": rho,
        "is_ev": False,
        "probability_status": status or "missing",
        "qualified": qualified,
        "ev_qualified": qualified,
        "ev_available": ev_value is not None,
        "ev_value": ev_value,
        "status_label": status_label,
        "reasons": reasons,
        "reasons_labels": [_reason(item) for item in reasons],
        "note": note,
    }


def present_advice(raw: Mapping) -> dict:
    raw = dict(raw or {})
    protections = []
    for item in raw.get("protections") or ():
        data = dict(item)
        labels = _reason_labels(data)
        if not labels and data.get("code"):
            labels = [_reason(str(data["code"]))]
        data["reason_labels"] = labels
        protections.append(data)
    restrictions = []
    for item in raw.get("restrictions") or ():
        data = dict(item)
        labels = _reason_labels(data)
        if not labels and data.get("code"):
            labels = [_reason(str(data["code"]))]
        data["reason_labels"] = labels
        data["reason_label"] = "；".join(labels) or "未识别原因（未知受限）"
        restrictions.append(data)
    return {
        "kind": "advice",
        "status": raw.get("status"),
        "cutoff": raw.get("cutoff"),
        "strategy_id": raw.get("strategy_id"),
        "input": {
            "kind": "用户输入快照",
            "persisted": bool(raw.get("persisted", False)),
            "orders_placed": bool(raw.get("orders_placed", False)),
            "policy_provided": bool((raw.get("policy") or {}).get("provided",
                                                                 True)),
            "snapshot_id": raw.get("snapshot_id"),
        },
        "capabilities": _capabilities(raw),
        "restrictions": restrictions,
        "protections": protections,
        "unknown_protection": any(
            bool(item.get("unknown")) or "unknown" in "；".join(
                item.get("reason_labels") or [])
            for item in restrictions),
        "denominators": _denominators(raw),
        "plans": _plans(raw),
        "preference": _preference(raw),
        "notes": list(raw.get("notes") or ()),
        "audit": raw,
    }


def present_review(raw: Mapping) -> dict:
    raw = dict(raw or {})
    final = raw.get("final_state") or {}
    return {
        "kind": "review",
        "status": raw.get("status") or "simulated",
        "ledger_id": raw.get("ledger_id"),
        "mode": raw.get("mode"),
        "input": {"kind": "模拟成交（本地账本）",
                  "persisted": bool(raw.get("persisted", False)),
                  "orders_placed": bool(raw.get("orders_placed", False))},
        "summary": {
            "events": len(raw.get("events") or ()),
            "fills": len(raw.get("fills") or ()),
            "cash": final.get("cash"),
            "equity": final.get("equity"),
            "orders": len(final.get("orders") or ()),
            "resume_hint": ("失败/部分运行可从已存 ID 显式恢复"
                            if str(raw.get("status") or "") in
                            ("failed", "running") else None),
        },
        "notes": list(raw.get("notes") or ()),
        "audit": raw,
    }


def _ledger_record(item: Mapping) -> dict:
    """按真实 history record schema（decision/execution/event_time）投影。"""
    decision = dict(item.get("decision") or item.get("decision_record") or {})
    execution = dict(item.get("execution") or item.get("execution_record") or {})
    decided = bool(decision)
    bundle = dict(decision.get("bundle") or {})
    state = item.get("state")
    rejected = []
    for row in execution.get("rejected") or ():
        data = dict(row)
        labels = _reason_labels(data)
        data["reason_labels"] = labels
        data["reason_label"] = "；".join(labels) or data.get("reason") or "未识别原因"
        rejected.append(data)
    return {
        "event_id": item.get("event_id"),
        "seq": item.get("seq"),
        "revision_after": item.get("revision_after"),
        "event_time": item.get("event_time") or item.get("timestamp"),
        "trade_date": item.get("trade_date"),
        "status": item.get("status") or ("已提交" if decided else "初始化/无决策"),
        "decided": decided,
        "decision_status": bundle.get("status") or (decision.get("status") if decision else None),
        "selected_plan_id": (bundle.get("selected_plan_id")
                             or decision.get("selected_plan_id")),
        "intents": [dict(row) for row in decision.get("intents") or ()],
        "fills": [dict(row) for row in execution.get("fills") or ()],
        "orders": [dict(row) for row in execution.get("orders") or ()],
        "canceled": [dict(row) if isinstance(row, Mapping) else row
                     for row in execution.get("canceled") or ()],
        "rejected": rejected,
        "matches": [dict(row) for row in execution.get("matches") or ()],
        "state_available": state is not None,
        "cash": (state or {}).get("cash") if state is not None else None,
        "equity": (state or {}).get("equity") if state is not None else None,
        "recovery_stage": ((state or {}).get("recovery_stage")
                           if state is not None else None),
    }


def present_ledger(raw: Mapping) -> dict:
    raw = dict(raw or {})
    run = dict(raw.get("run_status") or {})
    history = raw.get("history") or {}
    records = [_ledger_record(item) for item in history.get("records") or ()]
    final_state = raw.get("final_state") or {}
    sellable = {str(item.get("batch_id")): item.get("sellable")
                for item in final_state.get("batches") or ()
                if item.get("batch_id") is not None}
    batches = []
    for item in raw.get("batches") or ():
        data = dict(item)
        key = str(data.get("batch_id"))
        if key in sellable:
            data["sellable_qty"] = sellable[key]
        batches.append(data)
    expected = int(run.get("expected_events") or 0)
    completed = int(run.get("completed_events") or 0)
    status = run.get("status") or "unknown"
    return {
        "kind": "ledger",
        "ledger_id": raw.get("ledger_id"),
        "run": {
            "status": status,
            "status_label": {"completed": "完整", "failed": "失败",
                             "running": "运行中"}.get(status, status),
            "expected_events": expected,
            "completed_events": completed,
            "watermark": run.get("watermark"),
            "failure_reason": run.get("failure_reason"),
            "failed_cursor": run.get("failed_cursor"),
            "is_partial": status != "completed" or completed < expected,
            "resume_available": status in ("failed", "running")
            or completed < expected,
            "resume_boundary": ("实验 resume 走共享 POST /api/research/"
                                "bs-experiments/{id}/resume；账本本身只读，"
                                "恢复需通过 CLI 共享用例显式执行"),
        },
        "batches": batches,
        "orders": [dict(item) for item in raw.get("orders") or ()],
        "fills": [dict(item) for item in raw.get("fills") or ()],
        "events": records,
        "state_note": "历史事件未保存事件级权益/恢复状态时显示「未保存」，不回填期末状态。",
        "page": {"next_cursor": history.get("next_cursor"),
                 "has_more": bool(history.get("has_more"))},
        "audit": raw,
    }


def _metric_rows(metric: Mapping) -> list[dict]:
    definitions = metric.get("definitions") or {}
    denominator = metric.get("denominator") or {}
    maturity = metric.get("maturity") or {}
    rows = []
    for key, label, unit in _METRIC_LABELS:
        value = metric.get(key)
        definition = definitions.get(key) or {}
        sample_n, sample_basis = _sample_n(key, denominator)
        rows.append({
            "key": key,
            "label": label,
            "value": value,
            "display": "待证据" if value is None else _format_value(value, unit),
            "unit": _UNIT_LABELS.get(unit, unit),
            "explain": _METRIC_EXPLAIN.get(
                key, definition.get("definition") or "后端未提供定义"),
            "definition": definition.get("definition"),
            "denominator": definition.get("denominator"),
            "sample_n": sample_n,
            "sample_basis": sample_basis,
            "maturity_cutoff": maturity.get("evaluation_cutoff"),
            "exclusions": maturity.get("exclusions") or {},
            "blockers": maturity.get("blockers") or [],
        })
    return rows


def _format_value(value: Any, unit: str) -> str:
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, (int, float)):
        if unit == "percent":
            return f"{value:+.2f}%"
        if unit == "ratio":
            return f"{value * 100:.1f}%"
        if unit == "cny":
            return f"{value:,.2f} 元"
        if unit == "trade_days":
            return f"{value:.0f} 交易日"
        return f"{value:,.0f}"
    return str(value)


def _variant_matrix(report: Mapping) -> dict:
    study = ((report.get("metrics") or {}).get("comparison") or {}).get("study") or {}
    by_variant = study.get("by_variant") or {}
    rows = []
    for name, item in by_variant.items():
        capability = item.get("capability") or {}
        rows.append({
            "name": name,
            "kind": item.get("kind"),
            "engineering_status": item.get("engineering_status"),
            "engineering_label": _engine_label(item.get("engineering_status")),
            "effect_status": item.get("effect_status"),
            "effect_label": (f"{_engine_label(item.get('engineering_status'))}"
                             f" · {_effect_label(item.get('effect_status'))}"),
            "final_equity": item.get("final_equity"),
            "fills": item.get("fills"),
            "unfilled_ratio": item.get("unfilled_ratio"),
            "executable": bool(item.get("executable")),
            "executable_label": ("可执行" if item.get("executable")
                                 else "不可执行（证据/资金受限）"),
            "comparable": bool(item.get("comparable")),
            "comparable_label": ("可比" if item.get("comparable")
                                 else "不可比（资源/期限/母集不同）"),
            "qualified": bool(item.get("qualified")),
            "qualified_missed_amount": item.get("qualified_missed_amount"),
            "descriptive_equity_diff": item.get("descriptive_equity_diff"),
            "capability": ({
                "status": capability.get("status"),
                "used_added": capability.get("used_added") or [],
                "used_added_labels": [_capability_label(x) for x in
                                      capability.get("used_added") or []],
                "missing_removed": capability.get("missing_removed") or [],
                "missing_removed_labels": [_capability_label(x) for x in
                                           capability.get("missing_removed") or []],
                "evidence_distinct": capability.get("evidence_distinct"),
            } if capability else None),
            "maturity_status": (item.get("maturity") or {}).get("status"),
        })
    return {
        "study_id": study.get("study_id"),
        "study_digest": study.get("study_digest"),
        "spec": study.get("spec") or {},
        "rows": rows,
    }


def _alternatives(metric: Mapping) -> list[dict]:
    return [
        {"name": name,
         "status": item.get("status"),
         "executable": bool(item.get("executable")),
         "comparable": bool(item.get("comparable")),
         "qualified": bool(item.get("qualified")),
         "qualified_missed_amount": item.get("qualified_missed_amount"),
         "missed_return_pct": item.get("missed_return_pct"),
         "descriptive_equity_diff": item.get("descriptive_equity_diff"),
         "label": ("合格错过成本（主策略−替代，正值=主策略保留更多）"
                   if item.get("qualified")
                   else "描述性差值（未合格，不与合格错过混用）")}
        for name, item in (metric.get("alternatives") or {}).items()
    ]


def present_experiment(raw: Mapping) -> dict:
    raw = dict(raw or {})
    report = dict(raw.get("report") or raw)
    metrics = report.get("metrics") or {}
    primary = metrics.get("bs") or {}
    base = metrics.get("baseline_no_new_risk") or {}
    enhanced = metrics.get("enhanced_frozen") or {}
    maturity = primary.get("maturity") or {}
    splits = primary.get("splits") or {}
    return {
        "kind": "experiment",
        "id": raw.get("id"),
        "status": report.get("status") or raw.get("status"),
        "engineering_status": primary.get("engineering_status"),
        "effect_status": primary.get("effect_status"),
        "effect_label": ("效果合格" if primary.get("effect_status") == "evaluated"
                         else "工程完成但效果证据不足"),
        "identity": {
            "pack_digest": report.get("pack_digest"),
            "study_digest": report.get("study_digest"),
            "policy_hash": report.get("policy_hash"),
            "policy_profile": report.get("policy_profile"),
            "cohort_mode": report.get("cohort_mode"),
            "metrics_version": report.get("metrics_version"),
            "date_from": report.get("date_from"),
            "date_to": report.get("date_to"),
            "expected_events": report.get("expected_events"),
            "completed_events": report.get("completed_events"),
            "watermark": report.get("watermark"),
            "parameter_attempts": report.get("parameter_attempts") or [],
        },
        "data_modes": report.get("data_modes") or {},
        "capability_effect": metrics.get("capability_effect") or {},
        "metrics": {
            "primary": _metric_rows(primary),
            "baseline": _metric_rows(base),
            "enhanced": _metric_rows(enhanced) if enhanced else [],
            "denominators": primary.get("denominator") or {},
            "slippage": primary.get("slippage") or {},
            "recovery": primary.get("recovery_segments") or [],
            "maturity": maturity,
            "splits": splits,
            "execution_support": primary.get("execution_support") or {},
        },
        "alternatives": _alternatives(primary),
        "matrix": _variant_matrix(report),
        "variants": (report.get("variants") if "variants" in report else None),
        "notes": list(primary.get("notes") or ()),
        "audit": report,
    }


def present_experiment_list(page: Mapping, rows: list) -> dict:
    views = []
    for row in rows or ():
        report = dict(row.get("report") or {})
        primary = ((report.get("metrics") or {}).get("bs") or {})
        study = ((report.get("metrics") or {}).get("comparison")
                 or {}).get("study") or {}
        views.append({
            "id": row.get("id"),
            "status": row.get("status"),
            "effect_status": primary.get("effect_status"),
            "study_id": study.get("study_id"),
            "pack_digest": report.get("pack_digest"),
            "metrics_version": report.get("metrics_version"),
            "created_at": row.get("created_at"),
            "updated_at": row.get("updated_at"),
            "expected_events": report.get("expected_events"),
            "completed_events": report.get("completed_events"),
        })
    return {
        "kind": "experiment_list",
        "items": views,
        "page": {"total": page.get("total"), "has_more": page.get("has_more"),
                 "next_cursor": page.get("next_cursor")},
    }


__all__ = [
    "REASON_LABELS", "present_advice", "present_ledger",
    "present_experiment", "present_experiment_list", "present_review",
]
