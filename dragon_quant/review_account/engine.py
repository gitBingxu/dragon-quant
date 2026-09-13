from dragon_quant.review_account.execution import buy_quantity, daily_fallback_sell_price, fee, fill_price, sell_quantity
from dragon_quant.review_account.market import at, event_date
from dragon_quant.review_account.models import AccountState, ClosedPosition, MarketEvent, Position, StrategyConfig, Trade
from dragon_quant.review_account.strategy import evaluate_buy, evaluate_sell, explain_buy_candidate


class TradingEngine:
    def __init__(self, cfg: StrategyConfig, state: AccountState):
        self.cfg, self.state = cfg, state

    def step(self, event: MarketEvent) -> dict:
        state, cfg = self.state, self.cfg
        result = {"trades": [], "positions": [], "details": [], "reason_code": "no_signal"}
        if event.timestamp <= state.last_event:
            return {**result, "reason_code": "already_processed"}
        day = event_date(event.timestamp)
        if state.trade_date != day:
            state.pending = [o for o in state.pending if o["side"] == "SELL"]
            state.trade_date, state.sold_today, state.buys_today = day, False, 0
        for code, row in event.rows.items():
            state.marks[code] = row.get("execution_price") or row["bar_close"]
        for order in sorted(list(state.pending), key=lambda o: o["side"] != "SELL"):
            if order["created_at"] >= event.timestamp:
                continue
            row = event.rows.get(order["stock_code"])
            if not row:
                continue
            if row.get("execution_timestamp", event.timestamp) <= order["created_at"]:
                continue
            if order["side"] == "BUY" and not event.allow_buy:
                continue
            if order["side"] == "BUY" and (state.sold_today or event.timestamp - order["created_at"] > 300_000
                    or state.buys_today >= cfg.max_daily_buys):
                state.pending.remove(order)
                continue
            price = fill_price(row, order["side"], cfg, raw=self._raw_fill_price(order, row))
            if price is None:
                if row.get("execution_price") is None:
                    continue
                result["reason_code"] = "untradable_price"
                if order["side"] == "BUY":
                    state.pending.remove(order)
                continue
            trade, closed = self._fill(order, row, price, day)
            state.pending.remove(order)
            if trade:
                result["trades"].append(trade)
            if closed:
                result["positions"].append(closed)
        if event.phase in {"open", "bar", "late", "close"}:
            for p in list(state.positions):
                row = event.rows.get(p.code)
                if not row:
                    continue
                entry_ts = p.entry_signal.get("fill_timestamp", at(p.entry_date, "15:00"))
                observation = row["bar_timestamp"]
                key = f"sell:{p.code}"
                if event.timestamp <= entry_ts or state.evaluated.get(key, 0) >= observation:
                    continue
                state.evaluated[key] = observation
                hold_days = sum(p.entry_date < r["date"] < day for r in row["hist_rows"]) + (day > p.entry_date)
                if event.phase != "close" and not any(o["stock_code"] == p.code and o["side"] == "SELL" for o in state.pending):
                    sell_row = row
                    if row["bar_timestamp"] <= entry_ts:
                        sell_row = {**row, "bar_low": row["bar_close"], "bar_high": row["bar_close"]}
                    signals = evaluate_sell(p, sell_row, hold_days, cfg, row["intraday_bars"])
                    if signals:
                        state.pending.append(self._order(p.code, p.name, signals[0], event.timestamp))
                # The current bar cannot activate protection retroactively within that same bar.
                if row["bar_timestamp"] > entry_ts:
                    observed_high = (row["bar_high"] if row["bar_timestamp"] - 300_000 >= entry_ts
                                     else row["bar_close"])
                    p.highest_price = max(p.highest_price, observed_high)
                    p.highest_return = max(p.highest_return, (p.highest_price / p.entry_price - 1) * 100)
            if (event.phase != "close" and event.allow_buy and not state.sold_today and state.buys_today < cfg.max_daily_buys
                    and len(state.positions) < cfg.max_positions
                    and not any(o["side"] == "BUY" for o in state.pending)):
                signals = []
                held = {p.code for p in state.positions}
                for cand in event.candidates:
                    if cand["code"] in held:
                        result["details"].append({"code": cand["code"], "name": cand.get("name", ""),
                                                  "passed": False, "rank": cand.get("rank"),
                                                  "reason_code": "already_holding", "reason_text": "已持仓"})
                        continue
                    row = event.rows.get(cand["code"])
                    key = f"buy:{cand['code']}"
                    if row and state.evaluated.get(key, 0) >= row["bar_timestamp"]:
                        continue
                    if row:
                        state.evaluated[key] = row["bar_timestamp"]
                    detail = explain_buy_candidate(cand, row, cfg, row.get("prev_row") if row else None,
                                                   row.get("hist_rows") if row else None,
                                                   row.get("intraday_bars") if row else None)
                    result["details"].append(detail)
                    if detail["passed"]:
                        sig = evaluate_buy(cand, row, cfg, row["prev_row"], row["hist_rows"], row["intraday_bars"])
                        signals.append((sig, cand))
                signals.sort(key=lambda x: (-x[0]["priority"], x[1].get("rank") or 999999,
                                           -(x[1].get("composite_score") or 0), x[1]["code"]))
                if signals:
                    sig, cand = signals[0]
                    state.pending.append(self._order(cand["code"], cand.get("name", ""), sig, event.timestamp))
        state.last_event = event.timestamp
        result["pending"] = list(state.pending)
        if state.pending:
            result["reason_code"] = "pending_execution"
        return result

    @staticmethod
    def _order(code, name, signal, timestamp):
        return {"stock_code": code, "name": name, "side": signal["action"], "code": signal["code"],
                "reason_text": signal["reason_text"], "signal": signal["signal"], "created_at": timestamp}

    def _raw_fill_price(self, order, row):
        """日K兜底日的卖出按原因近似定价；盘中及买入仍用 execution_price。"""
        if order["side"] == "SELL" and row.get("data_quality") == "daily_fallback":
            p = next((p for p in self.state.positions if p.code == order["stock_code"]), None)
            if p is not None:
                return daily_fallback_sell_price(p, row, order["code"], self.cfg)
        return None

    def _fill(self, order, row, price, day):
        state, cfg = self.state, self.cfg
        signal = {**order["signal"], "decision_timestamp": order["created_at"],
                  "fill_timestamp": row["observed_at"], "execution_price": row["execution_price"],
                  "delay_seconds": (row["observed_at"] - order["created_at"]) / 1000}
        code, side = order["stock_code"], order["side"]
        if side == "BUY":
            if any(p.code == code for p in state.positions) or len(state.positions) >= cfg.max_positions:
                return None, None
            equity = state.cash + sum(p.qty * state.marks.get(p.code, p.entry_price) for p in state.positions)
            qty = buy_quantity(state.cash, equity, price, cfg)
            if not qty:
                return None, None
            amount, cost_fee = price * qty, fee(price * qty, side, cfg)
            state.cash -= amount + cost_fee
            p = Position(code, order["name"], qty, day, price, amount + cost_fee,
                         order["code"], order["reason_text"], signal, highest_price=price)
            state.positions.append(p)
            state.buys_today += 1
            return Trade(day, code, p.name, side, price, qty, amount, cost_fee, -cost_fee,
                         state.cash, qty, order["code"], order["reason_text"], signal), None
        p = next((p for p in state.positions if p.code == code), None)
        if p is None or p.entry_date >= day:
            return None, None
        qty = sell_quantity(p, order["code"], cfg)
        amount, cost_fee = price * qty, fee(price * qty, side, cfg)
        cost = p.cost * qty / p.qty
        pnl = amount - cost_fee - cost
        state.cash += amount - cost_fee
        state.sold_today = True
        p.realized_pnl += pnl
        p.qty -= qty
        p.cost -= cost
        closed = None
        if not p.qty:
            days = sum(p.entry_date < r["date"] < day for r in row["hist_rows"]) + 1
            closed = ClosedPosition(p.code, p.name, p.entry_date, p.entry_price, p.initial_qty,
                                    p.entry_reason_code, p.entry_signal, day, price, order["code"], signal,
                                    p.realized_pnl / p.initial_cost * 100, days)
            state.positions.remove(p)
        elif order["code"] == "next_day_limit_up_half":
            p.took_profit_half = True
        return Trade(day, code, p.name, side, price, qty, amount, cost_fee, pnl, state.cash,
                     p.qty, order["code"], order["reason_text"], signal), closed
