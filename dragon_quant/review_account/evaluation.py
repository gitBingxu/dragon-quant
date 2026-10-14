from __future__ import annotations

from dataclasses import replace

from dragon_quant.review_account.market import DataCoverageError
from dragon_quant.review_account.models import StrategyConfig
from dragon_quant.review_account.simulator import AccountSimulator


def metrics(result: dict) -> dict:
    closed = [p for p in result["positions"] if p.status == "closed"]
    gains = sum(max(0, p.realized_return) for p in closed)
    losses = -sum(min(0, p.realized_return) for p in closed)
    sell_pnl = [t.realized_pnl for t in result["trades"] if t.side == "SELL"]
    profit = sum(max(0, v) for v in sell_pnl)
    return {"total_return": result["total_return"], "max_drawdown": result["max_drawdown"],
            "trade_count": result["trade_count"], "closed_count": len(closed),
            "win_rate": result["win_rate"], "profit_factor_returns": gains / losses if losses else None,
            "fees": sum(t.fee for t in result["trades"]),
            "turnover": sum(t.amount for t in result["trades"]) / result["initial_cash"],
            "largest_win_share": max(sell_pnl, default=0) / profit if profit else None,
            "open_positions": len(result["account_state"]["positions"])}


def compare_strategies(date_from: str, date_to: str, data, cfg=None, variant_grid: dict | None = None) -> dict:
    cfg = cfg or StrategyConfig()
    try:
        days = data.calendar(date_from, date_to)
    except DataCoverageError as exc:
        return {"status": "insufficient_data", "reason": str(exc), "promoted": False}
    if len(days) < 30:
        return {"status": "insufficient_data", "reason": "至少需要30个完整交易日才能划分训练/验证/测试",
                "available_days": len(days), "promoted": False}
    a, b = int(len(days) * .6), int(len(days) * .8)
    splits = {"train": (days[0], days[a - 1]), "validation": (days[a], days[b - 1]), "test": (days[b], days[-1])}
    variants = {
        "baseline": cfg,
        "risk_budget": replace(cfg, max_position_pct=25, risk_per_trade_pct=1),
        "entry_quality": replace(cfg, max_position_pct=25, risk_per_trade_pct=1,
                                 ma5_min_distance_pct=-1, require_rising_ma5=True),
        "adaptive_exit": replace(cfg, max_position_pct=25, risk_per_trade_pct=1,
                                 ma5_min_distance_pct=-1, require_rising_ma5=True,
                                 trailing_atr_multiple=2, next_day_half_enabled=False),
    }
    if variant_grid:
        for name, vcfg in variant_grid.items():
            variants[name] = replace(cfg, **vcfg) if isinstance(vcfg, dict) else vcfg
    report = {"status": "evaluated", "splits": splits, "variants": {}, "promoted": False,
              "method": "chronological_60_20_20_fresh_accounts_mark_open_positions",
              "minimum_closed_trades_per_split": 10, "trial_count": len(variants)}
    for name, variant in variants.items():
        report["variants"][name] = {}
        for split in ("train", "validation"):
            try:
                result = AccountSimulator(variant, data=data).run(*splits[split])
            except DataCoverageError as exc:
                return {**report, "status": "insufficient_data", "reason": str(exc)}
            report["variants"][name][split] = metrics(result)
    eligible = [name for name in variants if all(
        report["variants"][name][s]["closed_count"] >= 10
        and report["variants"][name][s]["max_drawdown"] >= -15 for s in ("train", "validation"))]
    if not eligible:
        return {**report, "status": "insufficient_evidence", "reason": "样本量或回撤门槛未通过，独立测试集保持未使用"}
    selected = max(eligible, key=lambda name: report["variants"][name]["validation"]["total_return"])
    report["selected"] = selected
    for name in dict.fromkeys(("baseline", selected)):
        try:
            report["variants"][name]["test"] = metrics(AccountSimulator(variants[name], data=data).run(*splits["test"]))
            stress = replace(variants[name], buy_slippage=cfg.buy_slippage * 2, sell_slippage=cfg.sell_slippage * 2)
            report["variants"][name]["stress_test"] = metrics(AccountSimulator(stress, data=data).run(*splits["test"]))
        except DataCoverageError as exc:
            return {**report, "status": "insufficient_data", "reason": str(exc)}
    test = report["variants"][selected]["test"]
    base = report["variants"]["baseline"]["test"]
    stress = report["variants"][selected]["stress_test"]
    report["passes_holdout"] = (selected != "baseline" and test["closed_count"] >= 10
        and test["total_return"] > max(0, base["total_return"]) and test["max_drawdown"] >= -15
        and stress["total_return"] > 0)
    report["selected_params"] = variants[selected].to_json_dict()
    if report["trial_count"] > 20:
        report["overfitting_risk"] = "试验次数 >20，结论可信度降一级"
    return report


def drawdown_decomposition(result: dict) -> dict:
    """按卖出 reason_code 聚合已实现盈亏，粗分「止损/止盈/其它」三桶（纯函数，可离线测试）。"""
    stop = {"first_day_stop_loss", "hard_stop_loss", "soft_time_stop", "hard_time_stop"}
    tp = {"trailing_take_profit", "profit_back_to_cost_take_profit", "next_day_close_below_open",
          "close_below_open_stop", "break_intraday_ma_stop", "next_day_limit_up_half",
          "volume_spike_take_profit", "high_open_7pct_no_limit_5m_clear", "high_open_5pct_no_limit_30m_clear"}
    buckets = {"stop_loss": 0.0, "take_profit": 0.0, "other": 0.0}
    for t in result.get("trades", []):
        if getattr(t, "side", None) != "SELL":
            continue
        code = getattr(t, "reason_code", "")
        if code in stop:
            buckets["stop_loss"] += t.realized_pnl
        elif code in tp:
            buckets["take_profit"] += t.realized_pnl
        else:
            buckets["other"] += t.realized_pnl
    return buckets


def _run_metric(date_from, date_to, data, cfg, metric):
    try:
        result = AccountSimulator(cfg, data=data).run(date_from, date_to)
    except DataCoverageError:
        return None
    return metrics(result).get(metric)


def plateau_scan(date_from, date_to, data, cfg, param, values, metric="total_return") -> dict:
    """单参数网格扫描：返回 {value: metric} 与高原判定（最佳参数的两个邻域参数仍在 80% 水平以上，视为高原而非尖峰）。"""
    grid = {v: _run_metric(date_from, date_to, data, replace(cfg, **{param: v}), metric) for v in values}
    finite = {k: v for k, v in grid.items() if v is not None}
    if len(finite) < 3:
        return {"status": "insufficient_data", "grid": grid, "plateau": None, "best": None,
                "trial_count": len(values)}
    best = max(finite, key=lambda k: finite[k])
    neighbors = [v for k, v in finite.items() if k != best and finite[k] >= finite[best] * 0.8]
    plateau = len(neighbors) >= 2  # 至少两个邻域参数仍表现良好 → 高原而非尖峰
    return {"status": "evaluated", "grid": grid, "best": best, "plateau": plateau,
            "trial_count": len(values)}


def ablation(date_from, date_to, data, cfg, rule_names, metric="total_return") -> dict:
    """卖出规则消融：逐条关闭，度量指标变化（边际贡献）。baseline 为全开，marginal[rule] 为关掉该规则后的指标。"""
    base = _run_metric(date_from, date_to, data, cfg, metric)
    out = {"baseline": base, "marginal": {}, "trial_count": 1 + len(rule_names)}
    for rule in rule_names:
        toggles = {**cfg.sell_rule_toggles, rule: False}
        out["marginal"][rule] = _run_metric(date_from, date_to, data,
                                            replace(cfg, sell_rule_toggles=toggles), metric)
    return out


def _split_windows(days: list[str], n: int) -> list[tuple[str, str]]:
    """把交易日序列均分为 n 段连续窗口，返回 (起, 止) 列表。"""
    n = max(2, min(n, len(days)))
    k = len(days) // n
    out = []
    for i in range(n):
        a = i * k
        b = (i + 1) * k - 1 if i < n - 1 else len(days) - 1
        out.append((days[a], days[b]))
    return out


def walk_forward(date_from, date_to, data, cfg, n_windows=4, metric="total_return") -> dict:
    """滚动 walk-forward（固定 cfg）：区间均分为 n_windows 段逐段样本外评估，报告 OOS/IS 效率比。

    简化说明：本版本对**固定参数**做滚动样本外评估（参数寻优由 plateau_scan/ablation 承担），
    以相邻窗口指标比近似 OOS/IS 效率比；负值或 <0.5 视为「样本外衰减」红旗（调研 §6.1）。
    """
    try:
        days = data.calendar(date_from, date_to)
    except DataCoverageError as exc:
        return {"status": "insufficient_data", "reason": str(exc)}
    if len(days) < n_windows * 5:
        return {"status": "insufficient_data", "reason": "交易日不足", "available_days": len(days)}
    segs = _split_windows(days, n_windows)
    per_window = [_run_metric(a, b, data, cfg, metric) for a, b in segs]
    ratios = []
    for i in range(1, len(per_window)):
        if per_window[i - 1] not in (None, 0) and per_window[i] is not None:
            ratios.append(per_window[i] / per_window[i - 1])
    oos_is = sum(ratios) / len(ratios) if ratios else None
    red_flag = (oos_is is not None and oos_is < 0.5) or any(
        w is not None and w < 0 for w in per_window)
    return {"status": "evaluated", "windows": per_window, "oos_is_ratio": oos_is,
            "red_flag": red_flag, "trial_count": n_windows}
