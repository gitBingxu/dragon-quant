"""纯信号引擎：与 review_account.TradingEngine 共享策略与成交函数，但不维护账户。

`buy/sell` 命令只输出每日买卖参考，不映射到模拟账户，因此本引擎去掉资金/数量/
仓位/次数限制，仅复刻「决策 → 下一可执行事件成交」的两段式事件流与峰值跟踪，
保证买点/卖点/成交价与回测完全一致。

- 买入：buy-all —— 候选池内所有触发买点的标的都会记录买入（不择优只取第一只）。
- 卖出：对注入的未平仓信号（notional Position，qty=100）逐根 K 判定，触发即平。
- 峰值：同 TradingEngine，成交后按同一根 K 内不能反向激活保护的口径跟踪最高价。
"""

from __future__ import annotations

from dragon_quant.review_account.execution import daily_fallback_sell_price, fee, fill_price
from dragon_quant.review_account.market import at, event_date
from dragon_quant.review_account.models import MarketEvent, Position, StrategyConfig
from dragon_quant.review_account.strategy import evaluate_buy, evaluate_sell, explain_buy_candidate


class SignalEngine:
    def __init__(self, cfg: StrategyConfig, positions: list[Position] | None = None,
                 held_codes: set[str] | None = None):
        self.cfg = StrategyConfig.from_dict(cfg.to_json_dict())
        # 未平仓信号以 notional Position（qty=100）注入，供卖出评估与峰值跟踪。
        self.positions = positions or []
        # 已买入未卖出的 code（buy 命令据此跳过重复买入）。
        self.held_codes = held_codes or set()
        self.pending: list[dict] = []
        self.last_event = 0
        self.trade_date = ""
        self.evaluated: dict[str, int] = {}

    def step(self, event: MarketEvent) -> dict:
        cfg = self.cfg
        result = {"buys": [], "sells": [], "details": [], "reason_code": "no_signal"}
        if event.timestamp <= self.last_event:
            return {**result, "reason_code": "already_processed"}
        day = event_date(event.timestamp)
        if self.trade_date != day:
            self.pending = [o for o in self.pending if o["side"] == "SELL"]
            self.trade_date = day
        # ── 成交：pending 单在下一可执行事件按 execution_price 撮合 ──
        for order in sorted(list(self.pending), key=lambda o: o["side"] != "SELL"):
            if order["created_at"] >= event.timestamp:
                continue
            row = event.rows.get(order["stock_code"])
            if not row:
                continue
            if row.get("execution_timestamp", event.timestamp) <= order["created_at"]:
                continue
            if order["side"] == "BUY" and not event.allow_buy:
                continue
            price = fill_price(row, order["side"], cfg, raw=self._raw_fill_price(order, row))
            if price is None:
                if row.get("execution_price") is None:
                    continue
                result["reason_code"] = "untradable_price"
                if order["side"] == "BUY":
                    self.pending.remove(order)
                continue
            if order["side"] == "BUY":
                result["buys"].append(self._fill_buy(order, row, price, day))
            else:
                result["sells"].append(self._fill_sell(order, row, price, day))
            self.pending.remove(order)
        # ── 卖出评估 + 峰值跟踪 ──
        if event.phase in {"open", "bar", "late", "close"}:
            for p in list(self.positions):
                row = event.rows.get(p.code)
                if not row:
                    continue
                entry_ts = p.entry_signal.get("fill_timestamp", at(p.entry_date, "15:00"))
                observation = row["bar_timestamp"]
                key = f"sell:{p.code}"
                if event.timestamp <= entry_ts or self.evaluated.get(key, 0) >= observation:
                    continue
                self.evaluated[key] = observation
                hold_days = sum(p.entry_date < r["date"] < day for r in row["hist_rows"]) + (day > p.entry_date)
                if event.phase != "close" and not any(o["stock_code"] == p.code and o["side"] == "SELL" for o in self.pending):
                    sell_row = row
                    if row["bar_timestamp"] <= entry_ts:
                        sell_row = {**row, "bar_low": row["bar_close"], "bar_high": row["bar_close"]}
                    signals = evaluate_sell(p, sell_row, hold_days, cfg, row["intraday_bars"])
                    if signals:
                        self.pending.append(self._order(p.code, p.name, signals[0], event.timestamp))
                # 同一根 K 的高点不能反向激活该根 K 内的保护。
                if row["bar_timestamp"] > entry_ts:
                    observed_high = (row["bar_high"] if row["bar_timestamp"] - 300_000 >= entry_ts
                                     else row["bar_close"])
                    p.highest_price = max(p.highest_price, observed_high)
                    p.highest_return = max(p.highest_return, (p.highest_price / p.entry_price - 1) * 100)
        # ── 买入评估（buy-all，无仓位/数量/次数限制） ──
        if event.phase != "close" and event.allow_buy and not any(o["side"] == "BUY" for o in self.pending):
            held = {p.code for p in self.positions} | self.held_codes
            signals = []
            for cand in event.candidates:
                if cand["code"] in held:
                    result["details"].append({"code": cand["code"], "name": cand.get("name", ""),
                                              "passed": False, "rank": cand.get("rank"),
                                              "reason_code": "already_holding", "reason_text": "已买入"})
                    continue
                row = event.rows.get(cand["code"])
                key = f"buy:{cand['code']}"
                if row and self.evaluated.get(key, 0) >= row["bar_timestamp"]:
                    continue
                if row:
                    self.evaluated[key] = row["bar_timestamp"]
                detail = explain_buy_candidate(cand, row, cfg, row.get("prev_row") if row else None,
                                               row.get("hist_rows") if row else None,
                                               row.get("intraday_bars") if row else None)
                result["details"].append(detail)
                if detail["passed"]:
                    sig = evaluate_buy(cand, row, cfg, row["prev_row"], row["hist_rows"], row["intraday_bars"])
                    signals.append((sig, cand))
            signals.sort(key=lambda x: (-x[0]["priority"], -(x[1].get("composite_score") or 0), x[1]["code"]))
            for sig, cand in signals:
                self.pending.append(self._order(cand["code"], cand.get("name", ""), sig, event.timestamp))
        self.last_event = event.timestamp
        result["pending"] = list(self.pending)
        if self.pending:
            result["reason_code"] = "pending_execution"
        return result

    @staticmethod
    def _order(code: str, name: str, signal: dict, timestamp: int) -> dict:
        return {"stock_code": code, "name": name, "side": signal["action"], "code": signal["code"],
                "reason_text": signal["reason_text"], "signal": signal["signal"], "created_at": timestamp}

    def _fill_signal(self, order: dict, row: dict, price: float, day: str) -> dict:
        return {**order["signal"], "decision_timestamp": order["created_at"],
                "fill_timestamp": row["observed_at"], "execution_price": row["execution_price"],
                "delay_seconds": (row["observed_at"] - order["created_at"]) / 1000}

    def _fill_buy(self, order: dict, row: dict, price: float, day: str) -> dict:
        signal = self._fill_signal(order, row, price, day)
        cost = price * 100 + fee(price * 100, "BUY", self.cfg)
        self.positions.append(Position(order["stock_code"], order["name"], 100, day, price, cost,
                                       order["code"], order["reason_text"], signal, highest_price=price))
        return {"code": order["stock_code"], "name": order["name"], "entry_date": day, "entry_price": price,
                "reason_code": order["code"], "reason_text": order["reason_text"], "signal": signal}

    def _fill_sell(self, order: dict, row: dict, price: float, day: str) -> dict:
        signal = self._fill_signal(order, row, price, day)
        p = next((p for p in self.positions if p.code == order["stock_code"]), None)
        hold_days = sum(p.entry_date < r["date"] < day for r in row["hist_rows"]) + 1 if p else None
        if p is not None:
            self.positions.remove(p)
        return {"code": order["stock_code"], "name": order["name"], "exit_date": day, "exit_price": price,
                "reason_code": order["code"], "reason_text": order["reason_text"],
                "signal": signal, "hold_days": hold_days}

    def _raw_fill_price(self, order: dict, row: dict) -> float | None:
        """日K兜底日的卖出按原因近似定价（与 TradingEngine 一致）。"""
        if order["side"] == "SELL" and row.get("data_quality") == "daily_fallback":
            p = next((p for p in self.positions if p.code == order["stock_code"]), None)
            if p is not None:
                return daily_fallback_sell_price(p, row, order["code"], self.cfg)
        return None
