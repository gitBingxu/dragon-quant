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


def compare_strategies(date_from: str, date_to: str, data, cfg=None) -> dict:
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
    report = {"status": "evaluated", "splits": splits, "variants": {}, "promoted": False,
              "method": "chronological_60_20_20_fresh_accounts_mark_open_positions",
              "minimum_closed_trades_per_split": 10}
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
    return report
