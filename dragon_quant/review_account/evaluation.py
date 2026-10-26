from __future__ import annotations

import math
from dataclasses import replace
from typing import Optional

from dragon_quant.review_account.market import DataCoverageError
from dragon_quant.review_account.models import BUY_SIGNAL_CODES, StrategyConfig
from dragon_quant.review_account.simulator import AccountSimulator
from dragon_quant.review_account.strategy.main import BUY_CHECK_LABELS

TOTAL_ROW_CODE = "__total__"

# A/B 因子 variant 的前置门禁：区间内有因子值的交易日占比门槛（方案 §11 P2）。
FACTOR_BUILD_COMPLETION_MIN = 0.95


def _num(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def entry_reason_stats(positions: list[dict], initial_cash: float) -> dict:
    """按买点类型聚合已平仓记录（纯函数，供 API / 测试复用）。

    - 固定按 ``BUY_SIGNAL_CODES`` 顺序输出四类（0 笔也输出），未知 reason_code 追加在后，
      最后一行合计（``reason_code="__total__"``）；
    - 胜率口径与 ``AccountSimulator._result()["win_rate"]`` 同源：同一批已平仓记录、
      ``realized_return > 0`` 计为盈利；
    - 累计贡献为近似值：``Σ(realized_return/100 × entry_price × qty) / initial_cash × 100``，
      用 ``entry_price × qty`` 近似初始成本（忽略买入佣金），``realized_return`` 已含半仓与买卖费用；
    - 0 笔且 ``initial_cash>0`` 时 contribution 为 0.0（可计算的「无贡献」）；
      ``initial_cash<=0`` 时 contribution 为 None（不可计算）。
    """
    closed = [p for p in positions if (p.get("status") or "closed") == "closed"]

    def realized(p: dict) -> float:
        return _num(p.get("realized_return"))

    def contribution(items: list[dict]):
        try:
            cash = float(initial_cash)
        except (TypeError, ValueError):
            return None
        if not (cash > 0):
            return None
        pnl = sum(realized(p) / 100 * _num(p.get("entry_price")) * _num(p.get("qty"))
                  for p in items)
        return pnl / cash * 100

    def row(code: str, label: str, items: list[dict]) -> dict:
        trades = len(items)
        wins = sum(1 for p in items if realized(p) > 0)
        return {
            "reason_code": code, "label": label, "trades": trades, "wins": wins,
            "win_rate": wins / trades * 100 if trades else None,
            "avg_return": sum(realized(p) for p in items) / trades if trades else None,
            "contribution": contribution(items),
        }

    rows = [row(code, BUY_CHECK_LABELS.get(code, code),
                [p for p in closed if (p.get("entry_reason_code") or "") == code])
            for code in BUY_SIGNAL_CODES]
    unknown: dict[str, list[dict]] = {}
    for p in closed:
        code = p.get("entry_reason_code") or ""
        if code in BUY_SIGNAL_CODES:
            continue
        unknown.setdefault(code, []).append(p)
    rows.extend(row(code, code or "未知", items) for code, items in unknown.items())
    rows.append(row(TOTAL_ROW_CODE, "合计", closed))
    try:
        cash = float(initial_cash)
    except (TypeError, ValueError):
        cash = 0.0
    return {"initial_cash": cash, "total_trades": len(closed), "rows": rows}


def sharpe_ratio(equities, *, periods_per_year: int = 252,
                 risk_free: float = 0.0) -> Optional[float]:
    """日频权益曲线 → 年化 Sharpe（样本 < 2 / std=0 / 非有限 → None，绝不返回 0）。

    ``returns[t] = equities[t] / equities[t-1] - 1``；
    ``sharpe = (mean(returns) - risk_free / periods) / std(returns, ddof=1) * sqrt(periods)``。
    纯 Python 实现（零 numpy/pandas/scipy），与项目既有回测口径一致（默认无风险利率 0）。
    """
    try:
        series = [float(value) for value in (equities or ())]
        periods = int(periods_per_year)
        annual_rf = float(risk_free)
    except (TypeError, ValueError):
        return None
    if len(series) < 2 or periods <= 0:
        return None
    if any(not math.isfinite(value) for value in series) or not math.isfinite(annual_rf):
        return None
    returns = []
    for previous, current in zip(series, series[1:]):
        if previous == 0:
            return None
        returns.append(current / previous - 1.0)
    if len(returns) < 2:
        return None
    mean = sum(returns) / len(returns)
    variance = sum((value - mean) ** 2 for value in returns) / (len(returns) - 1)
    if not math.isfinite(variance) or variance <= 0:
        return None
    std = math.sqrt(variance)
    daily_rf = annual_rf / periods
    value = (mean - daily_rf) / std * math.sqrt(periods)
    return value if math.isfinite(value) else None


def _position_field(position, name: str):
    if isinstance(position, dict):
        return position.get(name)
    return getattr(position, name, None)


def _position_weight(position, weight: str) -> float:
    try:
        value = float(_position_field(position, weight))
        if math.isfinite(value) and value >= 0:
            return value
    except (TypeError, ValueError):
        pass
    try:
        return max(0.0, float(_position_field(position, "entry_price"))
                   * float(_position_field(position, "qty")))
    except (TypeError, ValueError):
        return 0.0


def industry_exposure(positions, sector_map: Optional[dict] = None, *,
                      weight: str = "market_value",
                      sector_loader=None) -> dict:
    """行业暴露（方案 §4.8）：只统计 ``category == "hy"``，其余计入 unattributed。

    - 归属日**必须用 ``entry_date`` 的 ``factor_sector_snapshots``**；未注入
      ``sector_loader`` / ``sector_map`` 时按 entry_date 惰性读取（禁止用最新板块
      回填历史，避免前视）；
    - ``unattributed_ratio`` 是结构性指标（``primary_sector`` 可能是概念/地区），
      首次全量 build 后应把实测基线写回方案文档；
    - ``weight`` 缺省取 ``market_value``，缺失时回退 ``entry_price × qty``；
      空池返回 ``coverage/unattributed_ratio/hhi = None``（不伪造 0 覆盖率）。
    """
    if sector_loader is None and sector_map is None:
        def sector_loader(entry_date: str) -> dict:   # 函数内惰性 import（分层约束）
            from dragon_quant.infra.factor import storage as fstore
            return fstore.get_sector_map(entry_date)

    fallback_map = sector_map or {}
    cache: dict[str, dict] = {}
    by_industry: dict[str, dict] = {}
    reasons = {"non_hy_primary": 0, "no_snapshot": 0}
    total_weight = 0.0
    attributed_weight = 0.0
    for position in positions or []:
        value = _position_weight(position, weight)
        total_weight += value
        code = str(_position_field(position, "code") or "")
        entry_date = str(_position_field(position, "entry_date") or "")
        current = fallback_map
        if sector_loader is not None and entry_date:
            if entry_date not in cache:
                cache[entry_date] = sector_loader(entry_date) or {}
            current = cache[entry_date]
        info = current.get(code)
        if not isinstance(info, dict):
            reasons["no_snapshot"] += 1
            continue
        if str(info.get("category")) != "hy":
            reasons["non_hy_primary"] += 1
            continue
        name = str(info.get("name") or info.get("code") or code)
        row = by_industry.setdefault(name, {"weight": 0.0, "count": 0})
        row["weight"] += value
        row["count"] += 1
        attributed_weight += value

    industries = [
        {"name": name,
         "weight": (row["weight"] / total_weight) if total_weight > 0 else 0.0,
         "count": row["count"]}
        for name, row in by_industry.items()
    ]
    industries.sort(key=lambda item: (-item["weight"], item["name"]))
    has_weight = total_weight > 0
    coverage = (attributed_weight / total_weight) if has_weight else None
    return {
        "by_industry": industries,
        "unattributed_ratio": (1.0 - coverage) if coverage is not None else None,
        "coverage": coverage,
        "hhi": (sum(item["weight"] ** 2 for item in industries)
                if industries else None),
        "basis": "entry_date_snapshot",
        "unattributed_reasons": reasons,
    }


def metrics(result: dict) -> dict:
    closed = [p for p in result["positions"] if p.status == "closed"]
    gains = sum(max(0, p.realized_return) for p in closed)
    losses = -sum(min(0, p.realized_return) for p in closed)
    sell_pnl = [t.realized_pnl for t in result["trades"] if t.side == "SELL"]
    profit = sum(max(0, v) for v in sell_pnl)
    equities = [snapshot.total_equity for snapshot in result.get("snapshots") or []]
    return {"total_return": result["total_return"], "max_drawdown": result["max_drawdown"],
            "sharpe": sharpe_ratio(equities),
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
    # 因子 variant 前置门禁：区间内 build 完成率 < 95% → 不评估（结论标 insufficient_data）。
    # 只在该 variant 真正开启 factor_ranking_enabled 时检查；纯基线不读因子表。
    factor_variants = [name for name, variant in variants.items()
                       if getattr(variant, "factor_ranking_enabled", False)]
    if factor_variants:
        from dragon_quant.infra.factor.decision import build_completion_rate   # 分层：函数内惰性 import
        completion = build_completion_rate(days)
        report["factor_build_completion"] = completion
        if completion is None or completion < FACTOR_BUILD_COMPLETION_MIN:
            return {**report, "status": "insufficient_data",
                    "reason": (f"factor_build_completion: 区间内因子 build 完成率 "
                               f"{completion if completion is not None else 0.0:.0%} "
                               f"< {FACTOR_BUILD_COMPLETION_MIN:.0%}，"
                               "不评估因子 variant")}
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
    stop = {"first_day_stop_loss", "hard_stop_loss", "soft_time_stop", "hard_time_stop",
            "first_day_stop_half", "first_day_disaster_stop"}
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
