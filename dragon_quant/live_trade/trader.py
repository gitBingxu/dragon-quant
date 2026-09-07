"""实盘辅助交易器：buy/sell 命令背后的策略执行，完全复用 review_account 策略。

- buy（每交易日 9:25）：取近 N 个有龙头记录的交易日票池并集，用实时行情判定开盘买点，
  择优后按开盘价整手买入并记账（分歧买龙依赖盘中 5 分钟 K，9:25 不评估）。
- sell（每交易日 14:55）：对已持仓（严格 T+1，跳过当日买入）用实时行情判定卖出信号，
  复用 evaluate_sell 与同款成交价/半仓逻辑并记账。
"""

import json
from typing import Optional

from dragon_quant.live_trade.row_builder import build_buy_row, build_sell_row
from dragon_quant.providers.tencent import TencentProvider
from dragon_quant.providers.xueqiu import XueqiuProvider
from dragon_quant.review_account.models import StrategyConfig
from dragon_quant.review_account.strategy import (
    evaluate_buy,
    evaluate_sell,
    explain_buy_candidate,
)
from dragon_quant.storage import db


def _candidate_sort_key(cand: dict) -> tuple:
    rank = cand.get("rank")
    rank = rank if rank is not None else 999999
    score = cand.get("composite_score") or 0.0
    return (rank, -score)


class LiveTrader:
    """基于持久化纸上账户的实盘辅助交易器。"""

    def __init__(self, account: dict, cfg: StrategyConfig,
                 quote_provider: Optional[TencentProvider] = None,
                 kline_provider: Optional[XueqiuProvider] = None):
        self.account = account
        self.account_id = account["id"]
        self.cfg = cfg
        self.quotes = quote_provider or TencentProvider()
        self.klines = kline_provider or XueqiuProvider()
        self._kline_cache: dict[str, list] = {}
        self._quote_cache: dict[str, object] = {}

    # ─── 数据获取（带进程内缓存） ───

    def _get_klines(self, code: str) -> list:
        if code not in self._kline_cache:
            self._kline_cache[code] = self.klines.get_kline(
                code, days=260, fq_type="normal"
            ) or []
        return self._kline_cache[code]

    def _get_quote(self, code: str):
        if code not in self._quote_cache:
            self._quote_cache[code] = self.quotes.get_quote(code)
        return self._quote_cache[code]

    # ─── 候选票池（近 N 个有记录交易日并集去重） ───

    def _lookback_pool_dates(self, trade_date: str) -> list[str]:
        dates = db.list_dragon_trade_dates(date_to=trade_date, source=self.cfg.source)
        prior = [d for d in dates if d < trade_date]
        n = max(1, self.cfg.candidate_lookback_days)
        return prior[-n:]

    def _collect_candidates(self, trade_date: str) -> list[dict]:
        merged: dict[str, dict] = {}
        for date in self._lookback_pool_dates(trade_date):
            for cand in db.get_dragons_by_date(
                date, top_n=self.cfg.candidate_top_n, source=self.cfg.source
            ):
                code = cand.get("code")
                if not code:
                    continue
                existing = merged.get(code)
                if existing is None or _candidate_sort_key(cand) < _candidate_sort_key(existing):
                    merged[code] = cand
        return sorted(merged.values(), key=_candidate_sort_key)[: self.cfg.candidate_top_n]

    # ─── 费用 ───

    def _buy_fee(self, amount: float) -> float:
        return amount * self.cfg.commission_rate

    def _sell_fee(self, amount: float) -> float:
        return amount * (self.cfg.commission_rate + self.cfg.stamp_tax_rate)

    # ─── buy ───

    def buy(self, trade_date: str) -> dict:
        cash = self.account["cash"]
        positions = db.list_live_positions(self.account_id, status="open")
        held_codes = {p["code"] for p in positions}

        if len(positions) >= self.cfg.max_positions:
            return {"action": "idle", "reason_code": "max_positions",
                    "reason_text": f"已达最大持仓数 {self.cfg.max_positions}，不再开仓",
                    "details": []}
        if cash < self.cfg.lot_size:
            return {"action": "idle", "reason_code": "cash_too_low",
                    "reason_text": "账户现金不足，无法开仓", "details": []}

        candidates = self._collect_candidates(trade_date)
        if not candidates:
            return {"action": "idle", "reason_code": "empty_pool",
                    "reason_text": f"近{self.cfg.candidate_lookback_days}个交易日无龙头票池，不开仓",
                    "details": []}

        signals = []
        details = []
        for cand in candidates:
            if cand["code"] in held_codes:
                details.append({"code": cand["code"], "name": cand.get("name", ""),
                                "rank": cand.get("rank"), "passed": False,
                                "reason_code": "already_holding",
                                "reason_text": f"{cand.get('name') or cand['code']} 已持仓，不重复买入"})
                continue
            quote = self._get_quote(cand["code"])
            if not quote:
                details.append({"code": cand["code"], "name": cand.get("name", ""),
                                "rank": cand.get("rank"), "passed": False,
                                "reason_code": "no_quote",
                                "reason_text": f"{cand.get('name') or cand['code']} 无实时行情"})
                continue
            built = build_buy_row(self._get_klines(cand["code"]), quote, trade_date)
            if not built:
                details.append({"code": cand["code"], "name": cand.get("name", ""),
                                "rank": cand.get("rank"), "passed": False,
                                "reason_code": "missing_kline",
                                "reason_text": f"{cand.get('name') or cand['code']} 历史日K不足"})
                continue
            row, prev_row, hist_rows = built["row"], built["prev_row"], built["hist_rows"]
            # 9:25 无当日 5 分钟 K，分歧买龙不评估，仅走开盘买点
            details.append(explain_buy_candidate(
                cand, row, self.cfg, prev_row=prev_row, hist_rows=hist_rows, intraday_bars=None))
            sig = evaluate_buy(cand, row, self.cfg, prev_row=prev_row,
                               hist_rows=hist_rows, intraday_bars=None)
            if sig and sig["code"] != "buy_divergence_first_break":
                signals.append((sig, cand, row))

        if not signals:
            return {"action": "idle", "reason_code": "no_candidate_passed",
                    "reason_text": "票池内无候选触发开盘买点", "details": details}

        signals.sort(key=lambda it: (it[0]["priority"], -(it[1].get("rank") or 999999),
                                     it[1].get("composite_score") or 0), reverse=True)
        sig, cand, row = signals[0]
        return self._execute_buy(trade_date, sig, cand, row, cash, details)

    def _execute_buy(self, trade_date, sig, cand, row, cash, details) -> dict:
        exec_px = sig["signal"].get("execution_price") or row["open"]
        price = exec_px * (1 + self.cfg.buy_slippage)
        qty = int(cash / (price * self.cfg.lot_size)) * self.cfg.lot_size
        if qty > 0:
            amount = price * qty
            fee = self._buy_fee(amount)
            if amount + fee > cash:
                qty = int((cash / (1 + self.cfg.commission_rate)) / (price * self.cfg.lot_size)) * self.cfg.lot_size
                amount = price * qty
                fee = self._buy_fee(amount)
        if qty <= 0 or price * qty + self._buy_fee(price * qty) > cash:
            return {"action": "idle", "reason_code": "cash_too_low",
                    "reason_text": "扣除费用后现金不足以买入一手", "details": details}

        amount = price * qty
        fee = self._buy_fee(amount)
        new_cash = cash - amount - fee
        signal_json = json.dumps(sig["signal"], ensure_ascii=False)
        position = {
            "code": cand["code"], "name": cand.get("name", ""), "qty": qty,
            "entry_date": trade_date, "entry_price": price, "cost": amount + fee,
            "entry_reason_code": sig["code"], "entry_reason_text": sig["reason_text"],
            "entry_signal_json": signal_json, "highest_return": 0.0,
            "highest_price": price, "initial_qty": qty, "initial_cost": amount + fee,
        }
        pos_id = db.add_live_position(self.account_id, position)
        db.update_live_cash(self.account_id, new_cash)
        trade = {
            "trade_date": trade_date, "command": "buy", "code": cand["code"],
            "name": cand.get("name", ""), "side": "BUY", "price": price, "qty": qty,
            "amount": amount, "fee": fee, "realized_pnl": -fee, "cash_after": new_cash,
            "position_after": qty, "reason_code": sig["code"],
            "reason_text": sig["reason_text"], "signal_json": signal_json,
        }
        db.add_live_trade(self.account_id, trade)
        self.account["cash"] = new_cash
        return {"action": "buy", "position_id": pos_id, "trade": trade,
                "reason_code": sig["code"], "reason_text": sig["reason_text"],
                "details": details}

    # ─── sell ───

    def _hold_days(self, code: str, entry_date: str, trade_date: str) -> int:
        """持有交易日数：历史 K 中 (entry_date, trade_date) 的交易日 + 今日。"""
        from dragon_quant.review_account.indicators import kbar_date
        elapsed = sum(
            1 for k in self._get_klines(code)
            if entry_date < kbar_date(k) < trade_date
        )
        return elapsed + 1

    def sell(self, trade_date: str) -> dict:
        positions = db.list_live_positions(self.account_id, status="open")
        results = []
        for pos in positions:
            if pos["entry_date"] >= trade_date:
                results.append({"code": pos["code"], "name": pos["name"],
                                "action": "hold", "reason_code": "t_plus_1",
                                "reason_text": f"{pos['name'] or pos['code']} 当日买入，T+1 次日才可卖出"})
                continue
            quote = self._get_quote(pos["code"])
            if not quote:
                results.append({"code": pos["code"], "name": pos["name"],
                                "action": "hold", "reason_code": "no_quote",
                                "reason_text": f"{pos['name'] or pos['code']} 无实时行情，暂不操作"})
                continue
            built = build_sell_row(self._get_klines(pos["code"]), quote, trade_date)
            if not built:
                results.append({"code": pos["code"], "name": pos["name"],
                                "action": "hold", "reason_code": "missing_kline",
                                "reason_text": f"{pos['name'] or pos['code']} 历史日K不足，暂不操作"})
                continue
            row = built["row"]
            hold_days = self._hold_days(pos["code"], pos["entry_date"], trade_date)
            results.extend(self._eval_and_sell(pos, row, hold_days, trade_date, quote))
        return {"results": results, "positions_before": len(positions)}

    def _eval_and_sell(self, pos, row, hold_days, trade_date, quote) -> list[dict]:
        """对单只持仓评估卖出并落库；未触发返回持有说明。"""
        # 用可变镜像承载 evaluate_sell 对 highest_* 的更新
        from dragon_quant.review_account.models import Position
        p = Position(
            code=pos["code"], name=pos["name"], qty=pos["qty"],
            entry_date=pos["entry_date"], entry_price=pos["entry_price"],
            cost=pos["cost"], entry_reason_code=pos["entry_reason_code"],
            entry_reason_text=pos["entry_reason_text"], entry_signal=pos["entry_signal"],
            highest_return=pos["highest_return"], highest_price=pos["highest_price"],
            initial_qty=pos["initial_qty"], initial_cost=pos["initial_cost"],
            realized_pnl=pos["realized_pnl"], took_profit_half=pos["took_profit_half"],
        )
        sells = evaluate_sell(p, row, hold_days, self.cfg, intraday_bars=None)
        # 无论是否卖出，持仓的最高浮盈/最高价都会被 evaluate_sell 更新，需回写
        db.update_live_position(pos["id"], {
            "highest_return": p.highest_return, "highest_price": p.highest_price,
        })
        if not sells:
            unrealized = (row["close"] / p.entry_price - 1) * 100 if p.entry_price > 0 else 0.0
            return [{"code": p.code, "name": p.name, "action": "hold",
                     "reason_code": "hold_no_signal",
                     "reason_text": f"{p.name or p.code} 持有第{hold_days}日，浮盈亏{unrealized:+.1f}%，未触发卖出"}]

        out = []
        for s in sells:
            merged_row = {**row, **s.get("signal", {})}
            price = self._sell_execution_price(pos, p, merged_row, s["code"])
            trade = self._execute_sell(pos, p, trade_date, price, s["code"],
                                       s["reason_text"], s["signal"], hold_days)
            if trade:
                out.append({"code": p.code, "name": p.name, "action": "sell",
                            "reason_code": s["code"], "reason_text": s["reason_text"],
                            "trade": trade})
        return out

    def _sell_execution_price(self, pos, p, row, reason_code) -> float:
        signal_price = row.get("execution_price")
        if signal_price and signal_price > 0:
            return signal_price
        if reason_code in {"hard_stop_loss", "first_day_stop_loss"}:
            applied = row.get("applied_stop_pct")
            if applied is None:
                applied = (self.cfg.first_day_stop_loss_pct
                           if reason_code == "first_day_stop_loss" else self.cfg.stop_loss_pct)
            stop_price = p.entry_price * (1 + applied / 100)
            return row["open"] if row["open"] <= stop_price else stop_price
        if reason_code in {"breakeven_stop", "profit_back_to_cost_take_profit"}:
            return p.entry_price
        if reason_code == "next_day_limit_up_half":
            return row["close"]
        if reason_code in {"take_profit", "take_profit_half"}:
            target = p.entry_price * (1 + self.cfg.take_profit_pct / 100)
            return row["open"] if row["open"] >= target else target
        return row["close"]

    def _sell_qty(self, qty: int, reason_code: str) -> int:
        if reason_code not in {"take_profit_half", "next_day_limit_up_half"}:
            return qty
        half = (qty // 2 // self.cfg.lot_size) * self.cfg.lot_size
        return half if half > 0 else qty

    def _execute_sell(self, pos, p, trade_date, price, reason_code, reason_text,
                      signal, hold_days) -> Optional[dict]:
        qty = self._sell_qty(pos["qty"], reason_code)
        if qty <= 0:
            return None
        exec_price = price * (1 - self.cfg.sell_slippage)
        amount = exec_price * qty
        fee = self._sell_fee(amount)
        cost_portion = pos["cost"] * qty / pos["qty"] if pos["qty"] > 0 else 0.0
        realized_pnl = amount - fee - cost_portion
        new_cash = self.account["cash"] + amount - fee
        remaining_qty = pos["qty"] - qty
        remaining_cost = pos["cost"] - cost_portion
        total_realized = pos["realized_pnl"] + realized_pnl

        db.update_live_cash(self.account_id, new_cash)
        self.account["cash"] = new_cash
        signal_json = json.dumps(signal, ensure_ascii=False)
        trade = {
            "trade_date": trade_date, "command": "sell", "code": pos["code"],
            "name": pos["name"], "side": "SELL", "price": exec_price, "qty": qty,
            "amount": amount, "fee": fee, "realized_pnl": realized_pnl,
            "cash_after": new_cash, "position_after": remaining_qty,
            "reason_code": reason_code, "reason_text": reason_text, "signal_json": signal_json,
        }
        db.add_live_trade(self.account_id, trade)

        if remaining_qty > 0:
            fields = {"qty": remaining_qty, "cost": remaining_cost,
                      "realized_pnl": total_realized}
            if reason_code in {"take_profit_half", "next_day_limit_up_half"}:
                fields["took_profit_half"] = True
            db.update_live_position(pos["id"], fields)
            # 同步内存镜像，供同日多笔卖出链
            pos["qty"] = remaining_qty
            pos["cost"] = remaining_cost
            pos["realized_pnl"] = total_realized
        else:
            realized_return = (total_realized / pos["initial_cost"] * 100
                               if pos["initial_cost"] else 0.0)
            db.update_live_position(pos["id"], {
                "qty": 0, "cost": 0.0, "realized_pnl": total_realized,
                "status": "closed", "exit_date": trade_date, "exit_price": exec_price,
                "exit_reason_code": reason_code, "exit_signal_json": signal_json,
                "realized_return": realized_return, "hold_days": hold_days,
            })
            pos["qty"] = 0
        return trade
