from dataclasses import asdict

from dragon_quant.review_account.data import MarketData, historical_events
from dragon_quant.review_account.engine import TradingEngine
from dragon_quant.review_account.market import calendar_start, collect_candidates
from dragon_quant.review_account.models import AccountState, Snapshot, StrategyConfig, TimelineEvent
from dragon_quant.storage import db


class AccountSimulator:
    def __init__(self, cfg: StrategyConfig, provider=None, data=None):
        self.cfg = StrategyConfig.from_dict(cfg.to_json_dict())
        self.data = data or MarketData(provider=provider)
        self.state = AccountState(cfg.initial_cash)
        self.engine = TradingEngine(cfg, self.state)
        self.trades, self.snapshots, self.closed_positions, self.events = [], [], [], []
        self._peak = self._previous = cfg.initial_cash
        self._calendar = []
        self._day_quality = {}

    def run(self, date_from: str, date_to: str) -> dict:
        if date_from > date_to:
            raise ValueError("开始日期不能晚于结束日期")
        self._calendar = self.data.calendar(calendar_start(date_from, self.cfg.candidate_lookback_days), date_to)
        days = [d for d in self._calendar if date_from <= d <= date_to]
        from dragon_quant.review_account.market import DataCoverageError
        if not days:
            raise DataCoverageError("回测区间没有完整交易日")
        for day in days:
            candidates = collect_candidates(self._calendar, day, self.cfg, db.get_dragons_by_date)
            codes = {c["code"] for c in candidates} | {p.code for p in self.state.positions}
            day_trades = []
            details = {}
            for event in historical_events(day, candidates, codes, self.data):
                if event.rows:
                    self._day_quality[day] = next(iter(event.rows.values())).get("data_quality", "strict_5min")
                result = self.engine.step(event)
                self.trades.extend(result["trades"])
                day_trades.extend(result["trades"])
                self.closed_positions.extend(result["positions"])
                for detail in result["details"]:
                    details[(detail["code"], detail["reason_code"])] = detail
                for trade in result["trades"]:
                    self.events.append(TimelineEvent(day, trade.side, f"{trade.side} {trade.name or trade.code}",
                                                     trade.reason_text, trade.reason_code, trade.code, trade.name,
                                                     self.state.cash, signal=trade.signal))
            snapshot = self._snapshot(day)
            if not day_trades:
                self.events.append(TimelineEvent(day, "HOLD" if self.state.positions else "IDLE",
                    "继续持有" if self.state.positions else "空仓", "未出现可成交的新信号", "no_execution",
                    cash=self.state.cash, total_equity=snapshot.total_equity, signal={"details": list(details.values())}))
            for event in self.events:
                if event.event_date == day:
                    event.total_equity = snapshot.total_equity
        return self._result(date_from, date_to)

    def _snapshot(self, day):
        state = self.state
        market_value = sum(p.qty * state.marks.get(p.code, p.entry_price) for p in state.positions)
        equity = state.cash + market_value
        self._peak = max(self._peak, equity)
        p = state.positions[0] if len(state.positions) == 1 else None
        cost = sum(p.cost for p in state.positions)
        snapshot = Snapshot(day, state.cash, market_value, equity,
                            (equity / self._previous - 1) * 100,
                            (equity / self.cfg.initial_cash - 1) * 100,
                            (equity / self._peak - 1) * 100,
                            p.code if p else "MULTI" if state.positions else "",
                            p.name if p else f"{len(state.positions)}只持仓" if state.positions else "",
                            sum(p.qty for p in state.positions), p.entry_price if p else None,
                            state.marks.get(p.code, p.entry_price) if p else None,
                            (market_value / cost - 1) * 100 if cost else None)
        import json
        snapshot.positions_json = json.dumps([asdict(p) for p in state.positions], ensure_ascii=False)
        self.snapshots.append(snapshot)
        self._previous = equity
        return snapshot

    def _result(self, start, end):
        final = self.snapshots[-1].total_equity
        count = len(self.closed_positions)
        positions = list(self.closed_positions)
        fallback_days = sum(1 for q in self._day_quality.values() if q == "daily_fallback")
        quality = ("strict_5min" if not fallback_days
                   else "daily_fallback" if fallback_days == len(self._day_quality) else "mixed")
        return {"date_from": start, "date_to": end, "initial_cash": self.cfg.initial_cash,
                "final_equity": final, "total_return": (final / self.cfg.initial_cash - 1) * 100,
                "max_drawdown": min(s.drawdown for s in self.snapshots), "trade_count": len(self.trades),
                "win_rate": sum(p.realized_return > 0 for p in self.closed_positions) / count * 100 if count else None,
                "trades": self.trades, "snapshots": self.snapshots, "positions": positions, "events": self.events,
                "account_state": self.state.to_dict(), "data_quality": quality,
                "fallback_days": fallback_days, "total_days": len(self._day_quality),
                "strategy_params": self.cfg.to_json_dict()}
