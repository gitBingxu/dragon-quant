"""纯信号引擎：与 review_account.TradingEngine 共享策略与成交函数，但不维护账户。

`buy/sell` 命令只输出每日买卖参考，不映射到模拟账户，因此本引擎去掉资金/数量/
仓位/次数限制，仅复刻「决策 → 下一可执行事件成交」的两段式事件流与峰值跟踪，
保证买点/卖点/成交价与回测完全一致。

- 买入：buy-all —— 候选池内所有触发买点的标的都会记录买入（不择优只取第一只）。
- 卖出：对注入的未平仓信号（notional Position）逐根 K 判定；半仓规则只减半不平仓。
- 峰值：同 TradingEngine，成交后按同一根 K 内不能反向激活保护的口径跟踪最高价。
- 风控：可注入影子 `AccountState`（由 `live_trade.shadow` 从信号表重建），逐事件调用
  同一个 `risk_governor`；`equity <= 0` 时额外触发破产闸（修补 `risk_governor` 的
  `equity > 0` 守卫在 buy-all 杠杆下会静默跳过日级熔断）。
"""

from __future__ import annotations

from typing import Optional

from dragon_quant.review_account.execution import (
    apply_sell_fill, daily_fallback_sell_price, fee, fill_price, notional_quantity, track_peak,
)
from dragon_quant.review_account.market import at, event_date
from dragon_quant.review_account.models import (
    AccountState, MarketEvent, Position, StrategyConfig, unavailable_text,
)
from dragon_quant.review_account.priority import rank_key
from dragon_quant.review_account.risk import risk_governor
from dragon_quant.review_account.strategy import (
    BUY_CHECK_SKIPPED, buy_check_placeholders, evaluate_sell, explain_buy_candidate,
    explain_buy_checks,
)

# pending BUY 从决策到成交的最大等待（严格大于即丢弃，与 TradingEngine 一致）。
PENDING_BUY_TTL_MS = 300_000


class SignalEngine:
    def __init__(self, cfg: StrategyConfig, positions: list[Position] | None = None,
                 held_codes: set[str] | None = None, sentiment_pct: Optional[float] = None,
                 account: Optional[AccountState] = None, evaluate_sells: bool = True):
        self.cfg = StrategyConfig.from_dict(cfg.to_json_dict())
        # 未平仓信号以 notional Position 注入，供卖出评估与峰值跟踪。
        # account 存在时与 account.positions 共用同一 list，净值恒等式 run 内持续成立。
        self.positions = positions if positions is not None else (account.positions if account else [])
        # 已买入未卖出的 code（buy 命令据此跳过重复买入）。
        self.held_codes = held_codes or set()
        # 情绪建议仓位（由 trader 在回放前注入，供禁买判定）。
        self.sentiment_pct = sentiment_pct
        # 影子账户：仅 buy 命令注入，供 risk_governor 与 equity 使用。
        self.account = account
        # buy 命令不做卖出评估，但仍跟踪既有持仓峰值（否则当日峰值丢失）。
        self.evaluate_sells = evaluate_sells
        self.pending: list[dict] = []
        self.last_event = 0
        self.trade_date = ""
        self.evaluated: dict[str, int] = {}
        self.fired_gate: list[str] = []
        # 事件级闸门（风控/已卖出）导致买点未评估时，记录原因供输出/JSON 使用。
        self.buy_eval_skipped: list[str] = []

    def step(self, event: MarketEvent) -> dict:
        cfg = self.cfg
        result = {"buys": [], "sells": [], "details": [], "reason_code": "no_signal"}
        if event.timestamp <= self.last_event:
            return {**result, "reason_code": "already_processed"}
        day = event_date(event.timestamp)
        if self.trade_date != day:
            self.pending = [o for o in self.pending if o["side"] == "SELL"]
            self.trade_date = day
        for detail in self._unavailable_details(event):
            result["details"].append(detail)
        risk_allow_buy = True
        if self.account is not None:
            for code, row in event.rows.items():
                self.account.marks[code] = row.get("execution_price") or row["bar_close"]
            # 停牌/不可用持仓：不评估、不更新峰值，但按最近可得价格估值。
            for code, price in event.last_closes.items():
                if code not in event.rows:
                    self.account.marks[code] = price
            equity = self.account.cash + sum(
                p.qty * self.account.marks.get(p.code, p.entry_price) for p in self.positions)
            _, risk_allow_buy, fired = risk_governor(cfg, self.account, equity)
            if equity <= 0:
                risk_allow_buy = False
                fired = [*fired, "shadow_equity_floor"]
            for gate in fired:
                if gate not in self.fired_gate:
                    self.fired_gate.append(gate)
            result["fired_gate"] = list(fired)
        # ── 成交：pending 单在下一可执行事件按 execution_price 撮合 ──
        for order in sorted(list(self.pending), key=lambda o: o["side"] != "SELL"):
            if order["created_at"] >= event.timestamp:
                continue
            row = event.rows.get(order["stock_code"])
            if not row:
                continue
            if row.get("execution_timestamp", event.timestamp) <= order["created_at"]:
                continue
            if order["side"] == "BUY":
                # 风控不通过：保留 pending（与 TradingEngine 的 continue 同位）。
                if not event.allow_buy or not risk_allow_buy:
                    continue
                # 当日已卖出 / 超过 300s 未成交：丢弃（与 TradingEngine 的 remove 同位）。
                if self._sold_today():
                    result["details"].append(self._unfilled_detail(
                        order, row, "buy_pending_expired", "买点命中但当日已卖出，未成交"))
                    self.pending.remove(order)
                    continue
                if event.timestamp - order["created_at"] > PENDING_BUY_TTL_MS:
                    result["details"].append(self._unfilled_detail(
                        order, row, "buy_pending_expired", "买点命中但超过300秒未成交"))
                    self.pending.remove(order)
                    continue
            price = fill_price(row, order["side"], cfg, raw=self._raw_fill_price(order, row))
            if price is None:
                if row.get("execution_price") is None:
                    continue
                result["reason_code"] = "untradable_price"
                if order["side"] == "BUY":
                    result["details"].append(self._unfilled_detail(
                        order, row, "untradable_price", "买点命中但价格不可成交（涨跌停/无效价）"))
                    self.pending.remove(order)
                continue
            if order["side"] == "BUY":
                filled = self._fill_buy(order, row, price, day)
                if filled is None:
                    result["details"].append(self._unfilled_detail(
                        order, row, "budget_insufficient",
                        "预算不足（不足一手），回测同样不会建仓"))
                else:
                    result["buys"].append(filled)
            else:
                filled = self._fill_sell(order, row, price, day)
                if filled is not None:
                    result["sells"].append(filled)
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
                if (self.evaluate_sells and event.phase != "close"
                        and not any(o["stock_code"] == p.code and o["side"] == "SELL"
                                    for o in self.pending)):
                    sell_row = row
                    if row["bar_timestamp"] <= entry_ts:
                        sell_row = {**row, "bar_low": row["bar_close"], "bar_high": row["bar_close"]}
                    signals = evaluate_sell(p, sell_row, hold_days, cfg, row["intraday_bars"])
                    if signals:
                        self.pending.append(self._order(p.code, p.name, signals[0], event.timestamp))
                # 同一根 K 的高点不能反向激活该根 K 内的保护。
                track_peak(p, row, entry_ts)
        # ── 买入评估（buy-all，无仓位/数量/次数限制） ──
        base_window = event.phase != "close" and event.allow_buy
        buy_window = base_window and risk_allow_buy and not self._sold_today()
        has_pending_buy = any(o["side"] == "BUY" for o in self.pending)
        if buy_window and not has_pending_buy:
            held = {p.code for p in self.positions} | self.held_codes
            signals = []
            for cand in event.candidates:
                if cand["code"] in event.unavailable:
                    continue  # 已由 _unavailable_details 在闸门外输出
                row = event.rows.get(cand["code"])
                if cand["code"] in held:
                    result["details"].append(self._already_holding_detail(cand, row, cfg))
                    continue
                key = f"buy:{cand['code']}"
                if row and self.evaluated.get(key, 0) >= row["bar_timestamp"]:
                    continue
                if row:
                    self.evaluated[key] = row["bar_timestamp"]
                detail = explain_buy_candidate(cand, row, cfg, row.get("prev_row") if row else None,
                                               row.get("hist_rows") if row else None,
                                               row.get("intraday_bars") if row else None,
                                               self.sentiment_pct)
                result["details"].append(detail)
                if detail["passed"] and detail.get("full_signal"):
                    signals.append((detail["full_signal"], cand))
            # buy-all 语义不变；排序只决定 pending 顺序（两引擎共用同一 key）。
            signals.sort(key=lambda x: rank_key(x[0], x[1], cfg.auction_ranking_enabled))
            for sig, cand in signals:
                order = self._order(cand["code"], cand.get("name", ""), sig, event.timestamp)
                order["checks"] = next(
                    (d.get("checks") for d in result["details"]
                     if d.get("code") == cand["code"] and d.get("passed")), [])
                # 成交失败时展示的诊断必须与决策帧一致（而不是成交失败帧）。
                decision_row = event.rows.get(cand["code"])
                order["decision_observed_at"] = (decision_row.get("observed_at")
                                                 if decision_row else None)
                order["decision_phase"] = decision_row.get("phase") if decision_row else None
                self.pending.append(order)
        elif base_window and not has_pending_buy:
            # 事件级闸门：买点整段未评估。只对 buy 命令输出诊断，pending 单存在时不追加。
            if not risk_allow_buy:
                gate = "组合风控闸门（当日暂停开新仓）"
            else:
                gate = "当日已卖出，暂停开新仓"
            if gate not in self.buy_eval_skipped:
                self.buy_eval_skipped.append(gate)
            held = {p.code for p in self.positions} | self.held_codes
            for cand in event.candidates:
                if cand["code"] in event.unavailable:
                    continue  # 已由 _unavailable_details 在闸门外输出
                row = event.rows.get(cand["code"])
                key = f"buy_gate:{cand['code']}"
                observation = row["bar_timestamp"] if row else event.timestamp
                if self.evaluated.get(key, 0) >= observation:
                    continue
                self.evaluated[key] = observation
                if cand["code"] in held:
                    result["details"].append(self._already_holding_detail(cand, row, cfg))
                else:
                    result["details"].append({
                        "code": cand["code"], "name": cand.get("name", ""),
                        "rank": cand.get("rank"), "passed": False,
                        "reason_code": "buy_eval_skipped",
                        "reason_text": f"本轮买点未评估（{gate}）",
                        "signal": {}, "full_signal": None,
                        "observed_at": row.get("observed_at") if row else None,
                        "phase": row.get("phase") if row else None,
                        "checks": buy_check_placeholders(
                            cfg, BUY_CHECK_SKIPPED, f"前置闸门：{gate}",
                            date=row.get("date") if row else None),
                    })
        self.last_event = event.timestamp
        result["pending"] = list(self.pending)
        result["buy_eval_skipped"] = list(self.buy_eval_skipped)
        if self.pending:
            result["reason_code"] = "pending_execution"
        return result

    def _unavailable_details(self, event) -> list[dict]:
        """停牌/不可用候选的 detail：买窗闸门外产出，每 code 每日一条。"""
        details = []
        mark = at(event_date(event.timestamp), "00:00")
        for cand in event.candidates:
            reason = event.unavailable.get(cand["code"])
            if reason is None:
                continue
            key = f"unavailable:{cand['code']}"
            if self.evaluated.get(key, 0) >= mark:
                continue
            self.evaluated[key] = mark
            details.append({
                "code": cand["code"], "name": cand.get("name", ""),
                "rank": cand.get("rank"), "passed": False,
                "reason_code": reason, "reason_text": unavailable_text(reason),
                "signal": {}, "full_signal": None,
                "observed_at": None, "phase": None, "checks": [],
            })
        return details

    def _already_holding_detail(self, cand: dict, row: Optional[dict], cfg) -> dict:
        checks = (explain_buy_checks(cand, row, cfg, row.get("prev_row"), row.get("hist_rows"),
                                     row.get("intraday_bars"))
                  if row else [])
        return {"code": cand["code"], "name": cand.get("name", ""),
                "passed": False, "rank": cand.get("rank"),
                "reason_code": "already_holding", "reason_text": "已买入",
                "signal": {}, "full_signal": None,
                "observed_at": row.get("observed_at") if row else None,
                "phase": row.get("phase") if row else None,
                "checks": checks}

    @staticmethod
    def _unfilled_detail(order: dict, row: dict, reason_code: str, reason_text: str) -> dict:
        return {"code": order["stock_code"], "name": order.get("name", ""),
                "passed": False, "rank": None,
                "reason_code": reason_code, "reason_text": reason_text,
                "signal": order.get("signal") or {}, "full_signal": None,
                # checks 来自决策帧，observed_at/phase 必须同帧，避免 pending 帧被误读为终态。
                "observed_at": order.get("decision_observed_at", row.get("observed_at")),
                "phase": order.get("decision_phase", row.get("phase")),
                "checks": order.get("checks") or []}

    def _sold_today(self) -> bool:
        """实时读影子状态（与 TradingEngine 在 pending/买点两处实时读 state.sold_today 同构）。"""
        return bool(self.account and self.account.sold_today)

    @staticmethod
    def _order(code: str, name: str, signal: dict, timestamp: int) -> dict:
        return {"stock_code": code, "name": name, "side": signal["action"], "code": signal["code"],
                "reason_text": signal["reason_text"], "signal": signal["signal"], "created_at": timestamp}

    def _fill_signal(self, order: dict, row: dict, price: float, day: str) -> dict:
        return {**order["signal"], "decision_timestamp": order["created_at"],
                "fill_timestamp": row["observed_at"], "execution_price": row["execution_price"],
                "delay_seconds": (row["observed_at"] - order["created_at"]) / 1000}

    def _fill_buy(self, order: dict, row: dict, price: float, day: str) -> dict | None:
        """成交买入；预算不足（qty=0）返回 None，与回测 `_fill` 的不建仓语义一致。"""
        qty = notional_quantity(self.cfg, price)
        if not qty:
            return None
        signal = self._fill_signal(order, row, price, day)
        amount, cost_fee = price * qty, fee(price * qty, "BUY", self.cfg)
        self.positions.append(Position(order["stock_code"], order["name"], qty, day, price,
                                       amount + cost_fee, order["code"], order["reason_text"],
                                       signal, highest_price=price))
        if self.account is not None:
            self.account.cash -= amount + cost_fee
        return {"code": order["stock_code"], "name": order["name"], "entry_date": day,
                "entry_price": price, "qty": qty, "reason_code": order["code"],
                "reason_text": order["reason_text"], "signal": signal}

    def _fill_sell(self, order: dict, row: dict, price: float, day: str) -> dict | None:
        """成交卖出；半仓规则只减半仓（position 保留），与 TradingEngine 共用 apply_sell_fill。"""
        signal = self._fill_signal(order, row, price, day)
        p = next((p for p in self.positions if p.code == order["stock_code"]), None)
        if p is None or p.entry_date >= day:
            return None
        fill = apply_sell_fill(p, price, order["code"], self.cfg)
        if self.account is not None:
            self.account.cash += fill["amount"] - fill["fee"]
            self.account.realized_today += fill["pnl"]
            self.account.sold_today = True
        if not fill["partial"]:
            self.positions.remove(p)
        hold_days = sum(p.entry_date < r["date"] < day for r in row["hist_rows"]) + 1
        return {"code": order["stock_code"], "name": order["name"], "exit_date": day,
                "exit_price": price, "qty": fill["qty"], "partial": fill["partial"],
                "reason_code": order["code"], "reason_text": order["reason_text"],
                "signal": signal, "hold_days": hold_days}

    def _raw_fill_price(self, order: dict, row: dict) -> float | None:
        """日K兜底日的卖出按原因近似定价（与 TradingEngine 一致）。"""
        if order["side"] == "SELL" and row.get("data_quality") == "daily_fallback":
            p = next((p for p in self.positions if p.code == order["stock_code"]), None)
            if p is not None:
                return daily_fallback_sell_price(p, row, order["code"], self.cfg)
        return None
