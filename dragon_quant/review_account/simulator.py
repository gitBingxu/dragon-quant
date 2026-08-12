"""账户级交易模拟器。"""

from typing import Optional

from dragon_quant.providers.xueqiu import XueqiuProvider
from dragon_quant.review_account.indicators import enrich_daily_klines, row_by_date
from dragon_quant.review_account.models import (
    ClosedPosition,
    Position,
    Snapshot,
    StrategyConfig,
    TimelineEvent,
    Trade,
)
from dragon_quant.review_account.strategy import (
    evaluate_buy,
    evaluate_sell,
    explain_buy_candidate,
)
from dragon_quant.storage import db


class AccountSimulator:
    """按交易日推进的账户级 review。"""

    def __init__(self, cfg: StrategyConfig, provider: Optional[XueqiuProvider] = None):
        self.cfg = cfg
        self.provider = provider or XueqiuProvider()
        self.cash = float(cfg.initial_cash)
        self.positions: list[Position] = []
        self.trades: list[Trade] = []
        self.snapshots: list[Snapshot] = []
        self.closed_positions: list[ClosedPosition] = []
        self.events: list[TimelineEvent] = []
        self._kline_cache: dict[str, list[dict]] = {}
        self._peak_equity = float(cfg.initial_cash)
        self._prev_equity = float(cfg.initial_cash)
        self._candidate_dates: list[str] = []

    @property
    def position(self) -> Optional[Position]:
        """兼容旧单持仓测试/调用：返回当前第一只持仓。"""
        return self.positions[0] if self.positions else None

    @position.setter
    def position(self, value: Optional[Position]):
        self.positions = [value] if value else []

    def run(self, date_from: str, date_to: str) -> dict:
        trading_days = db.list_dragon_trade_dates(date_from, date_to, source=self.cfg.source)
        if not trading_days:
            return self._result(date_from, date_to)
        self._candidate_dates = trading_days

        for day in trading_days:
            self._process_day(day)

        return self._result(date_from, date_to)

    def _process_day(self, day: str):
        day_events: list[TimelineEvent] = []
        sold_today = False
        for position in list(self.positions):
            if position.entry_date >= day:
                continue
            row = self._row_for(position.code, day)
            if row:
                hold_days = self._hold_days(position, day)
                sell_signals = evaluate_sell(position, row, hold_days, self.cfg)
                if sell_signals:
                    for sell_signal in sell_signals:
                        if position not in self.positions:
                            break
                        price = self._sell_execution_price(position, row, sell_signal["code"])
                        trade = self._sell(
                            position, day, price, sell_signal["code"],
                            sell_signal["reason_text"], sell_signal["signal"],
                        )
                        if trade:
                            sold_today = True
                            day_events.append(self._event_from_trade(trade))
                elif position in self.positions:
                    day_events.append(self._hold_event(position, day, row, hold_days))

        if self._can_open_position(day, sold_today):
            buy_result = self._try_buy(day)
            if buy_result.get("trade"):
                day_events.append(self._event_from_trade(buy_result["trade"]))
            elif not day_events:
                day_events.append(self._idle_event(day, buy_result))
        elif not self.positions and not day_events:
            day_events.append(self._idle_event(day, {
                "reason_code": "cash_too_low",
                "reason_text": "账户现金不足一手，无法开仓",
                "candidate_date": self._previous_candidate_date(day),
                "details": [],
            }))

        snapshot = self._snapshot(day)
        self._finalize_events(day_events, snapshot)

    def _try_buy(self, day: str):
        candidate_date = self._previous_candidate_date(day)
        if not candidate_date:
            return {
                "reason_code": "no_previous_pool",
                "reason_text": "没有上一交易日真龙池，按规则不开仓",
                "candidate_date": None,
                "details": [],
            }
        candidates = db.get_dragons_by_date(
            candidate_date, top_n=self.cfg.candidate_top_n, source=self.cfg.source
        )
        if not candidates:
            return {
                "reason_code": "empty_previous_pool",
                "reason_text": f"{candidate_date} 真龙池为空，按规则不开仓",
                "candidate_date": candidate_date,
                "details": [],
            }
        signals = []
        details = []
        held_codes = {p.code for p in self.positions}
        for cand in candidates:
            if cand["code"] in held_codes:
                details.append({
                    "code": cand.get("code", ""),
                    "name": cand.get("name", ""),
                    "rank": cand.get("rank"),
                    "passed": False,
                    "reason_code": "already_holding",
                    "reason_text": f"{cand.get('name') or cand.get('code')} 当前已持仓，不重复买入",
                    "signal": {},
                })
                continue
            row = self._row_for(cand["code"], day)
            prev_row = self._previous_row_for(cand["code"], day) if row else None
            details.append(explain_buy_candidate(cand, row, self.cfg, prev_row=prev_row))
            if not row:
                continue
            signal = evaluate_buy(cand, row, self.cfg, prev_row=prev_row)
            if signal:
                signals.append((signal, cand, row))

        if not signals:
            return {
                "reason_code": "no_candidate_passed",
                "reason_text": f"{candidate_date} 真龙池没有候选触发开盘买入条件",
                "candidate_date": candidate_date,
                "details": details,
            }

        signals.sort(
            key=lambda item: (
                item[0]["priority"],
                -(item[1].get("rank") or 999999),
                item[1].get("composite_score") or 0,
            ),
            reverse=True,
        )
        signal, cand, row = signals[0]
        price = row["open"] * (1 + self.cfg.buy_slippage)
        qty = int(self.cash / (price * self.cfg.lot_size)) * self.cfg.lot_size
        if qty <= 0:
            return {
                "reason_code": "cash_too_low",
                "reason_text": "账户现金不足以买入一手",
                "candidate_date": candidate_date,
                "details": details,
            }
        amount = price * qty
        fee = self._buy_fee(amount)
        if amount + fee > self.cash:
            qty = int((self.cash / (1 + self.cfg.commission_rate)) / (price * self.cfg.lot_size)) * self.cfg.lot_size
            amount = price * qty
            fee = self._buy_fee(amount)
        if qty <= 0 or amount + fee > self.cash:
            return {
                "reason_code": "cash_too_low",
                "reason_text": "扣除费用后现金不足以买入一手",
                "candidate_date": candidate_date,
                "details": details,
            }

        self.cash -= amount + fee
        position = Position(
            code=cand["code"],
            name=cand.get("name", ""),
            qty=qty,
            entry_date=day,
            entry_price=price,
            cost=amount + fee,
            entry_reason_code=signal["code"],
            entry_reason_text=signal["reason_text"],
            entry_signal=signal["signal"],
            highest_return=0.0,
            highest_price=price,
            entry_day_low=row.get("low"),
        )
        self.positions.append(position)
        trade = Trade(
            trade_date=day,
            code=cand["code"],
            name=cand.get("name", ""),
            side="BUY",
            price=price,
            qty=qty,
            amount=amount,
            fee=fee,
            realized_pnl=-fee,
            cash_after=self.cash,
            position_after=qty,
            reason_code=signal["code"],
            reason_text=signal["reason_text"],
            signal=signal["signal"],
        )
        self.trades.append(trade)
        return {
            "trade": trade,
            "reason_code": signal["code"],
            "reason_text": signal["reason_text"],
            "candidate_date": candidate_date,
            "details": details,
        }

    def _sell(self, position: Position, day: str, price: float, reason_code: str, reason_text: str, signal: dict):
        if position not in self.positions:
            return None
        p = position
        qty = self._sell_qty(p, reason_code)
        if qty <= 0:
            return None
        exec_price = price * (1 - self.cfg.sell_slippage)
        amount = exec_price * qty
        fee = self._sell_fee(amount)
        cost_portion = p.cost * qty / p.qty if p.qty > 0 else 0.0
        realized_pnl = amount - fee - cost_portion
        self.cash += amount - fee
        p.realized_pnl += realized_pnl
        hold_days = self._hold_days(p, day)
        remaining_qty = p.qty - qty
        trade = Trade(
            trade_date=day,
            code=p.code,
            name=p.name,
            side="SELL",
            price=exec_price,
            qty=qty,
            amount=amount,
            fee=fee,
            realized_pnl=realized_pnl,
            cash_after=self.cash,
            position_after=remaining_qty,
            reason_code=reason_code,
            reason_text=reason_text,
            signal=signal,
        )
        self.trades.append(trade)
        if remaining_qty > 0:
            p.qty = remaining_qty
            p.cost -= cost_portion
            if reason_code in {"take_profit_half", "next_day_limit_up_half"}:
                p.took_profit_half = True
            return trade

        realized_return = (
            p.realized_pnl / p.initial_cost * 100
            if p.initial_cost > 0 else 0.0
        )
        self.closed_positions.append(ClosedPosition(
            code=p.code,
            name=p.name,
            entry_date=p.entry_date,
            entry_price=p.entry_price,
            qty=p.initial_qty,
            entry_reason_code=p.entry_reason_code,
            entry_signal=p.entry_signal,
            exit_date=day,
            exit_price=exec_price,
            exit_reason_code=reason_code,
            exit_signal=signal,
            realized_return=realized_return,
            hold_days=hold_days,
        ))
        if p in self.positions:
            self.positions.remove(p)
        return trade

    def _snapshot(self, day: str):
        market_value = 0.0
        market_price = None
        unrealized = None
        code = name = ""
        qty = 0
        cost = None
        total_cost = 0.0
        for p in self.positions:
            row = self._row_for(p.code, day)
            px = row["close"] if row else p.entry_price
            market_value += px * p.qty
            total_cost += p.cost
        if len(self.positions) == 1:
            p = self.positions[0]
            row = self._row_for(p.code, day)
            market_price = row["close"] if row else p.entry_price
            unrealized = (market_price * p.qty - p.cost) / p.cost * 100 if p.cost > 0 else 0.0
            code = p.code
            name = p.name
            qty = p.qty
            cost = p.entry_price
        elif len(self.positions) > 1:
            code = "MULTI"
            name = f"{len(self.positions)}只持仓"
            qty = sum(p.qty for p in self.positions)
            unrealized = (market_value - total_cost) / total_cost * 100 if total_cost > 0 else 0.0
        equity = self.cash + market_value
        self._peak_equity = max(self._peak_equity, equity)
        daily_return = (equity / self._prev_equity - 1) * 100 if self._prev_equity > 0 else 0.0
        cumulative_return = (equity / self.cfg.initial_cash - 1) * 100 if self.cfg.initial_cash > 0 else 0.0
        drawdown = (equity / self._peak_equity - 1) * 100 if self._peak_equity > 0 else 0.0
        snapshot = Snapshot(
            trade_date=day,
            cash=self.cash,
            market_value=market_value,
            total_equity=equity,
            daily_return=daily_return,
            cumulative_return=cumulative_return,
            drawdown=drawdown,
            position_code=code,
            position_name=name,
            position_qty=qty,
            position_cost=cost,
            position_market_price=market_price,
            position_unrealized_return=unrealized,
        )
        self.snapshots.append(snapshot)
        self._prev_equity = equity
        return snapshot

    def _event_from_trade(self, trade: Trade) -> TimelineEvent:
        side_text = "买入" if trade.side == "BUY" else "卖出"
        return TimelineEvent(
            event_date=trade.trade_date,
            event_type=trade.side,
            code=trade.code,
            name=trade.name,
            title=f"{side_text} {trade.name or trade.code}",
            detail=trade.reason_text or trade.reason_code,
            reason_code=trade.reason_code,
            cash=trade.cash_after,
            signal=trade.signal,
        )

    def _hold_event(self, position: Position, day: str, row: dict, hold_days: int) -> TimelineEvent:
        p = position
        unrealized = (row["close"] / p.entry_price - 1) * 100 if p.entry_price > 0 else 0.0
        return TimelineEvent(
            event_date=day,
            event_type="HOLD",
            code=p.code,
            name=p.name,
            title=f"继续持有 {p.name or p.code}",
            detail=f"持有第{hold_days}个交易日，收盘浮盈亏{unrealized:+.1f}%，未触发止盈止损",
            reason_code="hold_no_signal",
            cash=self.cash,
            signal={
                "close": row.get("close"),
                "ma5": row.get("ma5"),
                "hold_days": hold_days,
                "unrealized_return": unrealized,
            },
        )

    def _idle_event(self, day: str, result: dict) -> TimelineEvent:
        details = result.get("details") or []
        top_details = details[:3]
        detail_text = result.get("reason_text") or "当日空仓，未触发买入条件"
        if top_details:
            reason_lines = "；".join(d.get("reason_text", "") for d in top_details if d.get("reason_text"))
            if reason_lines:
                detail_text = f"{detail_text}：{reason_lines}"
        return TimelineEvent(
            event_date=day,
            event_type="IDLE",
            title="空仓",
            detail=detail_text,
            reason_code=result.get("reason_code", "idle"),
            cash=self.cash,
            signal={
                "candidate_date": result.get("candidate_date"),
                "details": details,
            },
        )

    def _finalize_events(self, events: list[TimelineEvent], snapshot: Snapshot):
        for event in events:
            event.total_equity = snapshot.total_equity
            if event.cash is None:
                event.cash = snapshot.cash
            self.events.append(event)

    def _row_for(self, code: str, day: str) -> Optional[dict]:
        rows = self._klines(code)
        return row_by_date(rows, day)

    def _previous_row_for(self, code: str, day: str) -> Optional[dict]:
        prev = None
        for row in self._klines(code):
            if row["date"] >= day:
                break
            prev = row
        return prev

    def _previous_candidate_date(self, day: str) -> Optional[str]:
        prev = [d for d in self._candidate_dates if d < day]
        return prev[-1] if prev else None

    def _klines(self, code: str) -> list[dict]:
        if code not in self._kline_cache:
            self._kline_cache[code] = enrich_daily_klines(
                self.provider.get_kline(code, days=260, fq_type="normal") or []
            )
        return self._kline_cache[code]

    def _hold_days(self, position: Position, day: str) -> int:
        return sum(
            1 for r in self._klines(position.code)
            if position.entry_date < r["date"] <= day
        )

    def _sell_execution_price(self, position: Position, row: dict, reason_code: str) -> float:
        if reason_code == "hard_stop_loss":
            stop_price = position.entry_price * (1 + self.cfg.stop_loss_pct / 100)
            return row["open"] if row["open"] <= stop_price else stop_price
        if reason_code in {"breakeven_stop", "profit_back_to_cost_take_profit"}:
            return position.entry_price
        if reason_code == "next_day_limit_up_half":
            return row["close"]
        if reason_code in {"take_profit", "take_profit_half"}:
            target_price = position.entry_price * (1 + self.cfg.take_profit_pct / 100)
            return row["open"] if row["open"] >= target_price else target_price
        return row["close"]

    def _can_open_position(self, day: str, sold_today: bool) -> bool:
        if sold_today:
            return False
        if self.cash < self.cfg.lot_size:
            return False
        return len(self.positions) < self.cfg.max_positions

    def _sell_qty(self, position: Position, reason_code: str) -> int:
        if reason_code not in {"take_profit_half", "next_day_limit_up_half"}:
            return position.qty
        half = position.qty // 2
        qty = (half // self.cfg.lot_size) * self.cfg.lot_size
        if qty <= 0:
            return position.qty
        return min(qty, position.qty)

    def _buy_fee(self, amount: float) -> float:
        return amount * self.cfg.commission_rate

    def _sell_fee(self, amount: float) -> float:
        return amount * (self.cfg.commission_rate + self.cfg.stamp_tax_rate)

    def _result(self, date_from: str, date_to: str) -> dict:
        final_equity = self.snapshots[-1].total_equity if self.snapshots else self.cfg.initial_cash
        wins = sum(1 for p in self.closed_positions if p.realized_return > 0)
        total_closed = len(self.closed_positions)
        max_drawdown = min([s.drawdown for s in self.snapshots], default=0.0)
        return {
            "date_from": date_from,
            "date_to": date_to,
            "initial_cash": self.cfg.initial_cash,
            "final_equity": final_equity,
            "total_return": (final_equity / self.cfg.initial_cash - 1) * 100 if self.cfg.initial_cash > 0 else 0.0,
            "max_drawdown": max_drawdown,
            "trade_count": len(self.trades),
            "win_rate": wins / total_closed * 100 if total_closed else None,
            "snapshots": self.snapshots,
            "trades": self.trades,
            "positions": self.closed_positions,
            "events": self.events,
        }
