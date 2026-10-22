from dragon_quant.review_account.execution import (
    apply_sell_fill, buy_quantity, daily_fallback_sell_price, fee, fill_price, track_peak,
)
from dragon_quant.review_account.market import at, event_date
from dragon_quant.review_account.models import (
    AccountState, ClosedPosition, MarketEvent, Position, StrategyConfig, Trade,
)
from dragon_quant.review_account.priority import TRI_LABELS, key_components, rank_key
from dragon_quant.review_account.risk import risk_governor
from dragon_quant.review_account.strategy import (
    evaluate_sell, explain_buy_candidate, explain_buy_checks,
)


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
            state.realized_today = 0.0
        for code, row in event.rows.items():
            state.marks[code] = row.get("execution_price") or row["bar_close"]
        equity = self._equity()
        state.peak_equity = max(state.peak_equity, equity)
        _, risk_allow_buy, fired = risk_governor(cfg, state, equity)
        if fired:
            result.setdefault("fired_gate", []).extend(fired)
        for order in sorted(list(state.pending), key=lambda o: o["side"] != "SELL"):
            if order["created_at"] >= event.timestamp:
                continue
            row = event.rows.get(order["stock_code"])
            if not row:
                continue
            if row.get("execution_timestamp", event.timestamp) <= order["created_at"]:
                continue
            if order["side"] == "BUY" and (not event.allow_buy or not risk_allow_buy):
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
                if event.phase != "close" and not any(
                        o["stock_code"] == p.code and o["side"] == "SELL"
                        for o in state.pending):
                    sell_row = row
                    if row["bar_timestamp"] <= entry_ts:
                        sell_row = {**row, "bar_low": row["bar_close"], "bar_high": row["bar_close"]}
                    signals = evaluate_sell(p, sell_row, hold_days, cfg, row["intraday_bars"])
                    if signals:
                        state.pending.append(self._order(p.code, p.name, signals[0], event.timestamp))
                # The current bar cannot activate protection retroactively within that same bar.
                track_peak(p, row, entry_ts)
            buy_window = (event.phase != "close" and event.allow_buy and risk_allow_buy
                          and not state.sold_today)
            if (buy_window and state.buys_today < cfg.max_daily_buys
                    and len(state.positions) < cfg.max_positions
                    and not any(o["side"] == "BUY" for o in state.pending)):
                signals = []
                held = {p.code for p in state.positions}
                for cand in event.candidates:
                    row = event.rows.get(cand["code"])
                    if cand["code"] in held:
                        checks = (explain_buy_checks(
                            cand, row, cfg, row.get("prev_row"), row.get("hist_rows"),
                            row.get("intraday_bars")) if row else [])
                        result["details"].append({
                            "code": cand["code"], "name": cand.get("name", ""),
                            "passed": False, "rank": cand.get("rank"),
                            "reason_code": "already_holding", "reason_text": "已持仓",
                            "signal": {}, "full_signal": None,
                            "observed_at": row.get("observed_at") if row else None,
                            "phase": row.get("phase") if row else None,
                            "checks": checks})
                        continue
                    key = f"buy:{cand['code']}"
                    if row and state.evaluated.get(key, 0) >= row["bar_timestamp"]:
                        continue
                    if row:
                        state.evaluated[key] = row["bar_timestamp"]
                    detail = explain_buy_candidate(cand, row, cfg, row.get("prev_row") if row else None,
                                                   row.get("hist_rows") if row else None,
                                                   row.get("intraday_bars") if row else None,
                                                   state.sentiment_pct)
                    result["details"].append(detail)
                    if detail["passed"] and detail.get("full_signal"):
                        signals.append((detail["full_signal"], cand))
                # 择优：P/A 三值 tier → 五维综合分 → 买点得分 → 代码（两引擎共用 key）。
                signals.sort(key=lambda x: rank_key(x[0], x[1], cfg.auction_ranking_enabled))
                if signals:
                    sig, cand = signals[0]
                    state.pending.append(self._order(cand["code"], cand.get("name", ""), sig, event.timestamp))
                    for position, (other_sig, other) in enumerate(signals[1:], start=2):
                        result["details"].append(
                            self._ranked_out_detail(
                                other, other_sig, position, cfg,
                                row=event.rows.get(other["code"])))
            elif (buy_window and state.buys_today >= cfg.max_daily_buys
                    and len(state.positions) < cfg.max_positions):
                # 当日买入次数已满：只记一次，避免在每个后续事件重复追加。
                limit_key = f"limit:{day}"
                if limit_key not in state.evaluated:
                    state.evaluated[limit_key] = 1
                    held = {p.code for p in state.positions}
                    for cand in event.candidates:
                        if cand["code"] in held:
                            continue
                        row = event.rows.get(cand["code"])
                        checks = (explain_buy_checks(
                            cand, row, cfg, row.get("prev_row"), row.get("hist_rows"),
                            row.get("intraday_bars")) if row else [])
                        result["details"].append(
                            self._unsignaled_detail(
                                cand, "daily_buy_limit",
                                f"当日买入次数已达上限 {cfg.max_daily_buys}", cfg,
                                row=row, checks=checks))
        state.last_event = event.timestamp
        result["pending"] = list(state.pending)
        if state.pending:
            result["reason_code"] = "pending_execution"
        return result

    @staticmethod
    def _order(code, name, signal, timestamp):
        return {"stock_code": code, "name": name, "side": signal["action"], "code": signal["code"],
                "reason_text": signal["reason_text"], "signal": signal["signal"], "created_at": timestamp}

    @staticmethod
    def _detail_base(candidate, cfg, reason_code, reason_text, signal=None,
                     row=None, checks=None):
        detail = {
            "code": candidate.get("code"), "name": candidate.get("name", ""),
            "rank": candidate.get("rank"), "passed": False,
            "reason_code": reason_code, "reason_text": reason_text,
            "signal": {}, "full_signal": None,
            "observed_at": row.get("observed_at") if row else None,
            "phase": row.get("phase") if row else None,
            "checks": checks or [],
            "entry_priority": candidate.get("entry_priority") or {},
            "priority_key": key_components(signal or {}, candidate,
                                           cfg.auction_ranking_enabled),
        }
        block = detail["entry_priority"].get("auction")
        if isinstance(block, dict) and block.get("loaded"):
            detail["would_veto"] = block.get("state") is False
        return detail

    @classmethod
    def _ranked_out_detail(cls, candidate, signal, position, cfg, row=None):
        entry = candidate.get("entry_priority") or {}
        a_state = (entry.get("auction") or {}).get("state")
        p_state = (entry.get("prep") or {}).get("state")
        checks = (explain_buy_checks(
            candidate, row, cfg, row.get("prev_row"), row.get("hist_rows"),
            row.get("intraday_bars"), selected_rule=signal.get("code")) if row else [])
        return cls._detail_base(
            candidate, cfg, "ranked_out",
            f"排序第 {position}（A={TRI_LABELS.get(a_state, '未知')}, "
            f"P={TRI_LABELS.get(p_state, '未知')}），当日最多买入 {cfg.max_daily_buys} 只，未入选",
            signal=signal, row=row, checks=checks)

    @classmethod
    def _unsignaled_detail(cls, candidate, reason_code, reason_text, cfg,
                           row=None, checks=None):
        return cls._detail_base(candidate, cfg, reason_code, reason_text,
                                row=row, checks=checks)

    def _equity(self) -> float:
        return self.state.cash + sum(p.qty * self.state.marks.get(p.code, p.entry_price)
                                     for p in self.state.positions)

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
            delevel, _, _ = risk_governor(cfg, state, equity)
            budget_scale = delevel
            if cfg.sentiment_sizing_enabled and state.sentiment_pct is not None:
                budget_scale *= state.sentiment_pct
            qty = buy_quantity(state.cash, equity, price, cfg, budget_scale)
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
        fill = apply_sell_fill(p, price, order["code"], cfg)
        state.cash += fill["amount"] - fill["fee"]
        state.sold_today = True
        state.realized_today += fill["pnl"]
        closed = None
        if not fill["partial"]:
            days = sum(p.entry_date < r["date"] < day for r in row["hist_rows"]) + 1
            closed = ClosedPosition(p.code, p.name, p.entry_date, p.entry_price, p.initial_qty,
                                    p.entry_reason_code, p.entry_signal, day, price, order["code"], signal,
                                    p.realized_pnl / p.initial_cost * 100, days)
            state.positions.remove(p)
            state.consecutive_losses = state.consecutive_losses + 1 if closed.realized_return < 0 else 0
        return Trade(day, code, p.name, side, price, fill["qty"], fill["amount"], fill["fee"],
                     fill["pnl"], state.cash, p.qty, order["code"], order["reason_text"], signal), closed
