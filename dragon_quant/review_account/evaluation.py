from __future__ import annotations

import math
from dataclasses import replace
from typing import Mapping, Optional

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


def entry_reason_stats(positions: list[dict], initial_cash: float, *,
                       start_equity: Optional[float] = None,
                       ledger: Optional[dict] = None) -> dict:
    """买点统计：**生命周期列**与**本段归因列**严格分离（main D4）。

    - ``trades / wins / win_rate / avg_return`` 只来自已平仓持仓（closed），
      与 ``AccountSimulator.win_rate`` 同源；未平仓浮盈亏绝不参与“盈利笔数”。
    - ``period_pnl / contribution / realized_part / unrealized_part`` 来自
      ``period_ledger`` 的持仓批次（含未平仓、半卖），按 ``entry_reason_code``
      聚合；分母 = 本段起点权益。
    - 账本 unknown（期初缺 mark/数据不一致）时期间列全部 None，
      ``contribution_basis="unknown"``，不退回旧近似、不显示伪精确合计。
    """
    closed = [p for p in positions if (p.get("status") or "closed") == "closed"]
    denominator = initial_cash if start_equity is None else start_equity

    def realized(p: dict) -> float:
        return _num(p.get("realized_return"))

    def lifecycle_row(code: str, label: str, items: list[dict]) -> dict:
        trades = len(items)
        wins = sum(1 for p in items if realized(p) > 0)
        return {
            "reason_code": code, "label": label,
            "trades": trades, "wins": wins,
            "win_rate": wins / trades * 100 if trades else None,
            "avg_return": (sum(realized(p) for p in items) / trades
                           if trades else None),
            # 期间列默认 None，由 ledger 覆盖。
            "period_pnl": None, "period_codes": 0,
            "contribution": None, "realized_part": None,
            "unrealized_part": None, "has_open": False,
        }

    rows = [lifecycle_row(code, BUY_CHECK_LABELS.get(code, code),
                          [p for p in closed
                           if (p.get("entry_reason_code") or "") == code])
            for code in BUY_SIGNAL_CODES]
    unknown: dict[str, list[dict]] = {}
    for p in closed:
        code = p.get("entry_reason_code") or ""
        if code in BUY_SIGNAL_CODES:
            continue
        unknown.setdefault(code, []).append(p)
    rows.extend(lifecycle_row(code, code or "未知", items)
                for code, items in unknown.items())
    total = lifecycle_row(TOTAL_ROW_CODE, "合计", closed)
    rows.append(total)

    period_basis = None
    period_complete = False
    reconciliation = None
    unknown_rows = 0
    if ledger is not None:
        period_basis = ledger.get("quality") or "unknown"
        period_complete = period_basis != "unknown"
        reconciliation = ledger.get("reconciliation")
        unknown_rows = int(ledger.get("unknown_rows") or 0)
        buckets: dict[str, list[dict]] = {}
        for lot in ledger.get("rows") or []:
            code = lot.get("entry_reason_code") or "未知"
            buckets.setdefault(code, []).append(lot)

        # 先填充已有买点行的期间列，再追加仅期间出现的买点。
        by_code = {row["reason_code"]: row for row in rows}
        period_total = {
            "period_pnl": None, "contribution": None,
            "realized_part": None, "unrealized_part": None,
            "period_codes": 0, "has_open": False,
        }
        for code, items in buckets.items():
            known = [item for item in items
                     if item.get("period_pnl") is not None]
            target = by_code.get(code)
            if target is None:
                target = lifecycle_row(code, BUY_CHECK_LABELS.get(code, code), [])
                rows.insert(len(rows) - 1, target)
                by_code[code] = target
            if not period_complete or len(known) != len(items):
                target["has_open"] = any(item.get("status") == "open"
                                         for item in items)
                continue
            amounts = [float(item["period_pnl"]) for item in known]
            target.update({
                "period_pnl": sum(amounts),
                "period_codes": len(items),
                "realized_part": sum(float(item.get("realized_part") or 0.0)
                                     for item in known),
                "unrealized_part": sum(float(item.get("unrealized_part") or 0.0)
                                       for item in known),
                "contribution": (sum(amounts) / float(denominator) * 100
                                 if denominator else None),
                "has_open": any(item.get("status") == "open" for item in items),
            })
        if period_complete:
            totals = ledger.get("totals") or {}
            period_total.update({
                "period_pnl": totals.get("ledger_period_pnl"),
                "realized_part": totals.get("realized_part"),
                "unrealized_part": totals.get("unrealized_part"),
                "period_codes": sum(1 for lot in ledger.get("rows") or []
                                    if lot.get("period_pnl") is not None),
                "contribution": (totals["ledger_period_pnl"]
                                 / float(denominator) * 100
                                 if totals.get("ledger_period_pnl") is not None
                                 and denominator else None),
            })
        total.update(period_total)

    try:
        cash = float(initial_cash)
    except (TypeError, ValueError):
        cash = 0.0
    return {
        "initial_cash": cash,
        "start_equity": denominator,
        "total_trades": len(closed),
        "contribution_basis": period_basis or "not_provided",
        "precise_attribution_count": (sum(
            1 for lot in (ledger or {}).get("rows") or []
            if lot.get("period_pnl") is not None) if ledger else 0),
        "reconciliation": reconciliation,
        "period_complete": period_complete,
        "unknown_rows": unknown_rows,
        "rows": rows,
    }


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


def _position_field(position, name: str) -> float | int | str | None:
    if isinstance(position, dict):
        return position.get(name)
    return getattr(position, name, None)


def _position_weight(position, weight: str) -> float:
    raw = _position_field(position, weight)
    try:
        if raw is None:      # None 原本就走 float(None) 的 TypeError 兜底
            raise ValueError("missing weight")
        value = float(raw)
        if math.isfinite(value) and value >= 0:
            return value
    except (TypeError, ValueError):
        pass
    entry_raw = _position_field(position, "entry_price")
    qty_raw = _position_field(position, "qty")
    try:
        if entry_raw is None or qty_raw is None:
            raise ValueError("missing entry")
        return max(0.0, float(entry_raw) * float(qty_raw))
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
        def _lazy_sector_loader(entry_date: str) -> dict:   # 函数内惰性 import（分层约束）
            from dragon_quant.infra.factor import storage as fstore
            return fstore.get_sector_map(entry_date)

        sector_loader = _lazy_sector_loader

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
    loss = -sum(min(0, v) for v in sell_pnl)
    equities = [snapshot.total_equity for snapshot in result.get("snapshots") or []]
    closed_returns = [p.realized_return for p in closed]
    period_amounts = [
        getattr(p, "period_pnl", None)
        if getattr(p, "period_pnl", None) is not None
        else getattr(p, "realized_pnl", None)
        for p in closed]
    period_amounts = [float(v) for v in period_amounts if v is not None]
    wins = [v for v in closed_returns if v > 0]
    losses_only = [v for v in closed_returns if v < 0]
    # 期末未实现：持仓盯市价值 − 剩余成本（不再用 0 冒充）。
    open_rows = result["account_state"].get("positions") or []
    marks = result["account_state"].get("marks") or {}
    unrealized_known = all(row.get("code") in marks for row in open_rows)
    unrealized = sum(
        row.get("qty", 0) * marks.get(row.get("code"), row.get("entry_price") or 0)
        - (row.get("cost") or 0)
        for row in open_rows)
    realized_amount = sum(t.realized_pnl for t in result["trades"] if t.side == "SELL")
    return {"total_return": result["total_return"], "max_drawdown": result["max_drawdown"],
            "sharpe": sharpe_ratio(equities),
            "trade_count": result["trade_count"], "closed_count": len(closed),
            "win_rate": result["win_rate"], "profit_factor_returns": gains / losses if losses else None,
            "fees": sum(t.fee for t in result["trades"]),
            "turnover": sum(t.amount for t in result["trades"]) / result["initial_cash"],
            "largest_win_share": max(sell_pnl, default=0) / profit if profit else None,
            "open_positions": len(open_rows),
            # ── A8：胜率之外的收益/风险证据（0/全赢/全亏 → None，不除 0）──
            "wins": len(wins),
            "losses": len(losses_only),
            "avg_win_pct": (sum(wins) / len(wins)) if wins else None,
            "avg_loss_pct": (sum(losses_only) / len(losses_only)) if losses_only else None,
            "net_profit_ratio": (profit / loss) if loss > 0 else None,
            "expectancy_amount": (sum(period_amounts) / len(period_amounts)
                                  if period_amounts else None),
            "largest_loss_amount": min(period_amounts) if period_amounts else None,
            "realized_amount": realized_amount,
            "unrealized_amount": unrealized if unrealized_known else None,
            "unrealized_basis": "mark" if unrealized_known else "unknown",
            # 本段 vs 生命周期：两者口径不同，页面必须分区展示。
            "contribution_basis": ("period_pnl"
                                   if all(getattr(p, "period_pnl", None) is not None
                                          for p in closed) and closed
                                   else "legacy_approximation")}


def _num_or_none(value) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def period_ledger(run: dict, snapshots: list[dict],
                  trades: list[dict]) -> dict:
    """本段**按持仓批次/买点**的现金/盯市归因账本（main D4）。

    使用期初批次 + 有序成交做 FIFO 分摊，逐批次保留 ``entry_reason_code``：

    ``period_pnl = 该批次卖出净额 + 期末盯市 − 本段买入成本 − 期初盯市价值``

    * 继承批次期初价值 = qty × 冻结 mark；本段新仓期初价值 = 0；
    * 卖出按成交顺序 FIFO 消耗批次，半卖/全清/期末未平仓都落到对应买点；
    * 期末估值只用期末快照 mark；缺 mark / 期初缺 mark 的批次标 unknown，
      **不跳过、不用成本价冒充**，合计相应返回 None；
    * ``reconciliation.diff = 期末权益 − 期初权益 − Σperiod_pnl``。
    """
    start_equity = _num_or_none(run.get("start_equity"))
    if start_equity is None:
        start_equity = _num_or_none(run.get("base_equity"))
    if start_equity is None:
        start_equity = _num_or_none(run.get("initial_cash")) or 0.0
    end_equity = (_num_or_none(snapshots[-1].get("total_equity"))
                  if snapshots else None)
    last_positions = snapshots[-1].get("positions") if snapshots else []
    end_marks: dict[str, float | None] = {}
    for row in last_positions or []:
        code = str(row.get("code") or "")
        if code:
            end_marks[code] = _num_or_none(row.get("mark"))

    lots: list[dict] = []
    unknown = False
    start_positions = run.get("start_positions") or []
    if (run.get("base_run_id") and not run.get("start_state_recorded")
            and not start_positions):
        # 旧增量 run 没有冻结期初持仓：整段归因 unknown，不用空期初伪算。
        unknown = True
    for row in start_positions if isinstance(start_positions, list) else []:
        if not isinstance(row, Mapping):
            continue
        code = str(row.get("code") or "")
        if not code:
            continue
        qty = int(row.get("qty") or 0)
        if qty <= 0:
            continue
        mark = _num_or_none(row.get("mark"))
        if mark is None:
            unknown = True
            basis = None
        else:
            basis = qty * mark
        lots.append({
            "code": code,
            "entry_reason_code": row.get("entry_reason_code") or "",
            "entry_date": row.get("entry_date"),
            "qty": qty,
            "initial_qty": qty,
            "basis": basis,
            "start_value": basis,
            "buy_cost": 0.0,
            "sell_net": 0.0,
            "realized_part": 0.0,
            "inherited": True,
        })

    ordered_trades = sorted(
        enumerate(trades or []),
        key=lambda item: (str(item[1].get("trade_date") or ""), item[0]))
    for _index, trade in ordered_trades:
        code = str(trade.get("code") or "")
        if not code:
            continue
        qty = int(trade.get("qty") or 0)
        amount = _num_or_none(trade.get("amount")) or 0.0
        fee = _num_or_none(trade.get("fee")) or 0.0
        if trade.get("side") == "BUY":
            cost = amount + fee
            lots.append({
                "code": code,
                "entry_reason_code": trade.get("reason_code") or "",
                "entry_date": trade.get("trade_date"),
                "qty": qty,
                "initial_qty": qty,
                "basis": cost,
                "start_value": 0.0,
                "buy_cost": cost,
                "sell_net": 0.0,
                "realized_part": 0.0,
                "inherited": False,
            })
            continue
        if trade.get("side") != "SELL" or qty <= 0:
            continue
        proceeds = amount - fee
        per_share = proceeds / qty
        remaining = qty
        for lot in [item for item in lots
                    if item["code"] == code and item["qty"] > 0]:
            take = min(remaining, lot["qty"])
            if take <= 0:
                continue
            lot_proceeds = per_share * take
            if lot["basis"] is None:
                unknown = True
                lot_basis = None
            else:
                lot_basis = lot["basis"] * take / lot["qty"]
            lot["qty"] -= take
            lot["sell_net"] += lot_proceeds
            if lot_basis is not None:
                lot["realized_part"] += lot_proceeds - lot_basis
                lot["basis"] -= lot_basis
            else:
                lot["basis"] = None
            remaining -= take
            if remaining <= 0:
                break
        if remaining > 0:
            # 卖出数量大于已知批次（数据不一致）：单独 unknown 批次，不伪造成本。
            unknown = True
            lots.append({
                "code": code,
                "entry_reason_code": "unknown",
                "entry_date": trade.get("trade_date"),
                "qty": 0,
                "initial_qty": 0,
                "basis": None,
                "start_value": 0.0,
                "buy_cost": 0.0,
                "sell_net": per_share * remaining,
                "realized_part": None,
                "inherited": False,
            })

    rows: list[dict] = []
    for lot in lots:
        qty = lot["qty"]
        mark = end_marks.get(lot["code"])
        if qty > 0 and mark is None:
            unknown = True
            end_value = None
        else:
            end_value = qty * float(mark) if qty > 0 else 0.0
        if lot["basis"] is None:
            unrealized = None
        elif qty > 0:
            unrealized = end_value - lot["basis"]
        else:
            unrealized = None if lot["realized_part"] is None else 0.0
        period_pnl = None
        if lot["realized_part"] is not None and unrealized is not None:
            period_pnl = lot["realized_part"] + unrealized
        elif qty == 0 and lot["realized_part"] is not None:
            period_pnl = lot["realized_part"]
        rows.append({
            "code": lot["code"],
            "entry_reason_code": lot["entry_reason_code"],
            "entry_date": lot["entry_date"],
            "start_qty": lot["initial_qty"] if lot["inherited"] else 0,
            "start_value": lot["start_value"],
            "buy_qty": 0 if lot["inherited"] else lot["initial_qty"],
            "buy_cost": lot["buy_cost"],
            "sell_qty": lot["initial_qty"] - qty,
            "sell_net": lot["sell_net"],
            "end_qty": qty,
            "end_value": end_value,
            "period_pnl": period_pnl,
            "realized_part": lot["realized_part"],
            "unrealized_part": unrealized,
            "inherited": lot["inherited"],
            "status": "open" if qty > 0 else "closed",
        })

    known_rows = [row for row in rows if row["period_pnl"] is not None]
    complete = len(known_rows) == len(rows) and not unknown
    ledger_sum = sum(row["period_pnl"] for row in known_rows) if complete else None
    realized_total = (sum(row["realized_part"] for row in known_rows)
                      if complete else None)
    unrealized_total = (sum(row["unrealized_part"] for row in known_rows)
                        if complete else None)
    fees = sum(_num_or_none(t.get("fee")) or 0.0 for t in trades or [])
    diff = None
    if ledger_sum is not None and end_equity is not None:
        diff = round(end_equity - start_equity - ledger_sum, 6)
    return {
        "rows": rows,
        "totals": {
            "start_equity": start_equity,
            "end_equity": end_equity,
            "ledger_period_pnl": ledger_sum,
            "realized_part": realized_total,
            "unrealized_part": unrealized_total,
            "fees": fees,
            "equity_change": (round(end_equity - start_equity, 6)
                              if end_equity is not None else None),
        },
        "reconciliation": {
            "diff": diff,
            "within_tolerance": (diff is not None and abs(diff) <= 0.05),
            "tolerance": 0.05,
        },
        "quality": ("unknown" if (unknown or not complete)
                    else "legacy_approximation"
                    if (run.get("attribution_quality")
                        or (run.get("run_meta") or {}).get(
                            "attribution_quality")) == "legacy_approximation"
                    else "precise"),
        "unknown_rows": sum(1 for row in rows if row["period_pnl"] is None),
    }


def metrics_from_rows(run: dict, snapshots: list[dict], trades: list[dict],
                      positions: list[dict]) -> dict:
    """已落库行 → 账户收益/风险摘要（A8，纯函数）。

    生命周期胜率与「本段贡献」分开：胜率分母是已平仓持仓，贡献分母是本段起点
    权益。0 / 全赢 / 全亏按不可计算返回 ``None``，不除 0、不冒充无风险。
    """
    closed = [p for p in positions if (p.get("status") or "closed") == "closed"]
    returns = [p.get("realized_return") for p in closed
               if p.get("realized_return") is not None]
    wins = [v for v in returns if v > 0]
    losses = [v for v in returns if v < 0]
    ledger = period_ledger(run, snapshots, trades)
    totals = ledger["totals"]
    equities = [s.get("total_equity") for s in snapshots
                if s.get("total_equity") is not None]
    start_equity = totals["start_equity"]
    # 期末持仓数必须来自期末快照（positions 表只保存已平仓行）。
    last_snapshot = snapshots[-1] if snapshots else {}
    last_positions = last_snapshot.get("positions") or []
    market_value = _num_or_none(last_snapshot.get("market_value")) or 0.0
    open_count = (len(last_positions) if last_positions
                  else (0 if market_value <= 0 else None))
    # 整笔净盈亏金额（生命周期口径），用于净盈亏比；与「本段贡献」分开。
    whole_trade = [p.get("realized_pnl") for p in closed
                   if p.get("realized_pnl") is not None]
    if not whole_trade:
        whole_trade = [p.get("period_pnl") for p in closed
                       if p.get("period_pnl") is not None]
    whole_trade = [float(v) for v in whole_trade]
    profit = sum(max(0.0, value) for value in whole_trade)
    loss = -sum(min(0.0, value) for value in whole_trade)
    period_amounts = [row["period_pnl"] for row in ledger["rows"]
                      if row.get("period_pnl") is not None]
    quality = ledger["quality"]
    known = totals["ledger_period_pnl"] is not None
    return {
        "run_id": run.get("id"),
        "start_equity": start_equity,
        "final_equity": run.get("final_equity"),
        "initial_cash": run.get("initial_cash"),
        "total_return": run.get("total_return"),
        "max_drawdown": run.get("max_drawdown"),
        "sharpe": sharpe_ratio(equities),
        "trade_count": run.get("trade_count"),
        "closed_count": len(closed),
        "open_count": open_count,
        "win_rate": (len(wins) / len(returns) * 100) if returns else None,
        "wins": len(wins),
        "losses": len(losses),
        "avg_win_pct": (sum(wins) / len(wins)) if wins else None,
        "avg_loss_pct": (sum(losses) / len(losses)) if losses else None,
        "net_profit_ratio": (profit / loss) if loss > 0 else None,
        "net_profit_ratio_basis": "whole_trade_realized_pnl_amount",
        "expectancy_amount": (sum(period_amounts) / len(period_amounts)
                              if period_amounts else None),
        "fees": totals["fees"],
        "realized_amount": totals["realized_part"] if known else None,
        "unrealized_amount": totals["unrealized_part"] if known else None,
        "unrealized_basis": ("mark" if known else "unknown"),
        "period_pnl_amount": totals["ledger_period_pnl"],
        "equity_change": totals["equity_change"],
        "reconciliation": ledger["reconciliation"],
        "largest_loss_amount": min(period_amounts) if period_amounts else None,
        "contribution_basis": ("period_pnl" if quality == "precise"
                               else quality),
        "ledger_rows": ledger["rows"],
        "data_quality": (run.get("run_meta") or {}).get("data_quality"),
        "run_meta": run.get("run_meta") or {},
    }


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


def _run_metric(date_from, date_to, data, cfg, metric) -> Optional[float]:
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
        previous, current = per_window[i - 1], per_window[i]
        if previous is not None and previous != 0 and current is not None:
            ratios.append(current / previous)
    oos_is = sum(ratios) / len(ratios) if ratios else None
    red_flag = (oos_is is not None and oos_is < 0.5) or any(
        w is not None and w < 0 for w in per_window)
    return {"status": "evaluated", "windows": per_window, "oos_is_ratio": oos_is,
            "red_flag": red_flag, "trial_count": n_windows}
