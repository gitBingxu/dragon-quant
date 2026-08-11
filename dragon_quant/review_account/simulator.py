"""账户级交易模拟器。"""

from typing import Optional

from dragon_quant.providers.xueqiu import XueqiuProvider
from dragon_quant.review_account.indicators import enrich_daily_klines, row_by_date
from dragon_quant.review_account.models import (
    ClosedPosition,
    Position,
    Snapshot,
    StrategyConfig,
    Trade,
)
from dragon_quant.review_account.strategy import evaluate_buy, evaluate_sell
from dragon_quant.storage import db


class AccountSimulator:
    """按交易日推进的账户级 review。"""

    def __init__(self, cfg: StrategyConfig, provider: Optional[XueqiuProvider] = None):
        self.cfg = cfg
        self.provider = provider or XueqiuProvider()
        self.cash = float(cfg.initial_cash)
        self.position: Optional[Position] = None
        self.trades: list[Trade] = []
        self.snapshots: list[Snapshot] = []
        self.closed_positions: list[ClosedPosition] = []
        self._kline_cache: dict[str, list[dict]] = {}
        self._peak_equity = float(cfg.initial_cash)
        self._prev_equity = float(cfg.initial_cash)
        self._candidate_dates: list[str] = []

    def run(self, date_from: str, date_to: str) -> dict:
        trading_days = db.list_dragon_trade_dates(date_from, date_to, source=self.cfg.source)
        if not trading_days:
            return self._result(date_from, date_to)
        self._candidate_dates = trading_days

        for day in trading_days:
            self._process_day(day)

        return self._result(date_from, date_to)

    def _process_day(self, day: str):
        if self.position and self.position.entry_date < day:
            row = self._row_for(self.position.code, day)
            if row:
                hold_days = self._hold_days(self.position.entry_date, day)
                sell_signal = evaluate_sell(self.position, row, hold_days, self.cfg)
                if sell_signal:
                    price = self._sell_execution_price(row, sell_signal["code"])
                    self._sell(day, price, sell_signal["code"], sell_signal["reason_text"], sell_signal["signal"])

        if not self.position and self.cash >= self.cfg.lot_size:
            self._try_buy(day)

        self._snapshot(day)

    def _try_buy(self, day: str):
        candidate_date = self._previous_candidate_date(day)
        if not candidate_date:
            return
        candidates = db.get_dragons_by_date(
            candidate_date, top_n=self.cfg.candidate_top_n, source=self.cfg.source
        )
        signals = []
        for cand in candidates:
            row = self._row_for(cand["code"], day)
            if not row:
                continue
            prev_row = self._previous_row_for(cand["code"], day)
            signal = evaluate_buy(cand, row, self.cfg, prev_row=prev_row)
            if signal:
                signals.append((signal, cand, row))

        if not signals:
            return

        signals.sort(
            key=lambda item: (
                item[0]["score"],
                item[1].get("composite_score") or 0,
                -(item[1].get("rank") or 999),
            ),
            reverse=True,
        )
        signal, cand, row = signals[0]
        price = row["open"] * (1 + self.cfg.buy_slippage)
        qty = int(self.cash / (price * self.cfg.lot_size)) * self.cfg.lot_size
        if qty <= 0:
            return
        amount = price * qty
        fee = self._buy_fee(amount)
        if amount + fee > self.cash:
            qty = int((self.cash / (1 + self.cfg.commission_rate)) / (price * self.cfg.lot_size)) * self.cfg.lot_size
            amount = price * qty
            fee = self._buy_fee(amount)
        if qty <= 0 or amount + fee > self.cash:
            return

        self.cash -= amount + fee
        self.position = Position(
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
        self.trades.append(Trade(
            trade_date=day,
            code=cand["code"],
            name=cand.get("name", ""),
            side="BUY",
            price=price,
            qty=qty,
            amount=amount,
            fee=fee,
            cash_after=self.cash,
            position_after=qty,
            reason_code=signal["code"],
            reason_text=signal["reason_text"],
            signal=signal["signal"],
        ))

    def _sell(self, day: str, price: float, reason_code: str, reason_text: str, signal: dict):
        if not self.position:
            return
        p = self.position
        exec_price = price * (1 - self.cfg.sell_slippage)
        amount = exec_price * p.qty
        fee = self._sell_fee(amount)
        self.cash += amount - fee
        realized_return = (amount - fee - p.cost) / p.cost * 100 if p.cost > 0 else 0.0
        hold_days = self._hold_days(p.entry_date, day)
        self.trades.append(Trade(
            trade_date=day,
            code=p.code,
            name=p.name,
            side="SELL",
            price=exec_price,
            qty=p.qty,
            amount=amount,
            fee=fee,
            cash_after=self.cash,
            position_after=0,
            reason_code=reason_code,
            reason_text=reason_text,
            signal=signal,
        ))
        self.closed_positions.append(ClosedPosition(
            code=p.code,
            name=p.name,
            entry_date=p.entry_date,
            entry_price=p.entry_price,
            qty=p.qty,
            entry_reason_code=p.entry_reason_code,
            entry_signal=p.entry_signal,
            exit_date=day,
            exit_price=exec_price,
            exit_reason_code=reason_code,
            exit_signal=signal,
            realized_return=realized_return,
            hold_days=hold_days,
        ))
        self.position = None

    def _snapshot(self, day: str):
        market_value = 0.0
        market_price = None
        unrealized = None
        code = name = ""
        qty = 0
        cost = None
        if self.position:
            row = self._row_for(self.position.code, day)
            market_price = row["close"] if row else self.position.entry_price
            market_value = market_price * self.position.qty
            unrealized = (market_value - self.position.cost) / self.position.cost * 100 if self.position.cost > 0 else 0.0
            code = self.position.code
            name = self.position.name
            qty = self.position.qty
            cost = self.position.entry_price
        equity = self.cash + market_value
        self._peak_equity = max(self._peak_equity, equity)
        daily_return = (equity / self._prev_equity - 1) * 100 if self._prev_equity > 0 else 0.0
        cumulative_return = (equity / self.cfg.initial_cash - 1) * 100 if self.cfg.initial_cash > 0 else 0.0
        drawdown = (equity / self._peak_equity - 1) * 100 if self._peak_equity > 0 else 0.0
        self.snapshots.append(Snapshot(
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
        ))
        self._prev_equity = equity

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

    def _hold_days(self, entry_date: str, day: str) -> int:
        if not self.position:
            return 0
        return sum(
            1 for r in self._klines(self.position.code)
            if entry_date < r["date"] <= day
        )

    def _sell_execution_price(self, row: dict, reason_code: str) -> float:
        if reason_code == "hard_stop_loss":
            return min(row["open"], row["close"], row["low"])
        if reason_code == "take_profit":
            return max(row["open"], row["close"])
        return row["close"]

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
        }
