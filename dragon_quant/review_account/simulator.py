from dataclasses import asdict

from dragon_quant.review_account.data import MarketData, historical_events
from dragon_quant.review_account.engine import TradingEngine
from dragon_quant.review_account.market import (
    calendar_start, collect_candidates_ex, load_market_sentiment,
)
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
        self._ranking_modes = {}
        self._pool_sources = {}
        self._fallback_reasons = {}
        self._prep_snapshot_dates = {}
        self._auction_rows = {}
        self._priority_degraded = []

    def run(self, date_from: str, date_to: str) -> dict:
        if date_from > date_to:
            raise ValueError("开始日期不能晚于结束日期")
        self._calendar = self.data.calendar(calendar_start(date_from, self.cfg.candidate_lookback_days), date_to)
        days = [d for d in self._calendar if date_from <= d <= date_to]
        from dragon_quant.review_account.market import DataCoverageError
        if not days:
            raise DataCoverageError("回测区间没有完整交易日")
        for day in days:
            candidates, ctx = collect_candidates_ex(
                self._calendar, day, self.cfg, db.get_dragons_by_date)
            self._ranking_modes[day] = ctx["ranking_mode"]
            self._pool_sources[day] = ctx["pool_source"]
            self._fallback_reasons[day] = ctx["fallback_reason"]
            self._prep_snapshot_dates[day] = ctx["prep_snapshot_date"]
            self._auction_rows[day] = ctx["auction_rows"]
            for note in ctx.get("degraded") or []:
                if note not in self._priority_degraded:
                    self._priority_degraded.append(note)
            self.state.sentiment_pct = load_market_sentiment(self._calendar, day, self.cfg)
            codes = {c["code"] for c in candidates} | {p.code for p in self.state.positions}
            day_trades = []
            details = {}
            day_fired = []
            for event in historical_events(day, candidates, codes, self.data):
                if event.rows:
                    self._day_quality[day] = next(iter(event.rows.values())).get("data_quality", "strict_5min")
                result = self.engine.step(event)
                self.trades.extend(result["trades"])
                day_trades.extend(result["trades"])
                self.closed_positions.extend(result["positions"])
                for detail in result["details"]:
                    details[(detail["code"], detail["reason_code"])] = detail
                if result.get("fired_gate"):
                    day_fired.extend(result["fired_gate"])
                for trade in result["trades"]:
                    self.events.append(TimelineEvent(day, trade.side, f"{trade.side} {trade.name or trade.code}",
                                                     trade.reason_text, trade.reason_code, trade.code, trade.name,
                                                     self.state.cash, signal=trade.signal))
            day_fired = list(dict.fromkeys(day_fired))  # 同一闸门逐事件去重
            snapshot = self._snapshot(day)
            if not day_trades:
                self.events.append(TimelineEvent(day, "HOLD" if self.state.positions else "IDLE",
                    "继续持有" if self.state.positions else "空仓", "未出现可成交的新信号", "no_execution",
                    cash=self.state.cash, total_equity=snapshot.total_equity, signal={}))
            # 决策记录：无论当日是否有成交，details 统一合并到该日第一条事件（M4）。
            first = next((e for e in self.events if e.event_date == day), None)
            if first is not None:
                first.signal = {
                    **(first.signal or {}),
                    "ranking_mode": ctx["ranking_mode"],
                    "pool_source": ctx["pool_source"],
                    "fallback_reason": ctx["fallback_reason"],
                    "details": list(details.values()),
                }
                if day_fired:
                    first.signal["fired_gate"] = day_fired
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
        modes = set(self._ranking_modes.values())
        ranking_mode = next(iter(modes)) if len(modes) == 1 else ("mixed" if modes else "legacy")
        return {"date_from": start, "date_to": end, "initial_cash": self.cfg.initial_cash,
                "ranking_mode": ranking_mode,
                "ranking_modes": dict(self._ranking_modes),
                "pool_sources": dict(self._pool_sources),
                "fallback_reasons": dict(self._fallback_reasons),
                "prep_snapshot_dates": dict(self._prep_snapshot_dates),
                "auction_rows": dict(self._auction_rows),
                "priority_degraded": list(self._priority_degraded),
                "final_equity": final, "total_return": (final / self.cfg.initial_cash - 1) * 100,
                "max_drawdown": min(s.drawdown for s in self.snapshots), "trade_count": len(self.trades),
                "win_rate": sum(p.realized_return > 0 for p in self.closed_positions) / count * 100 if count else None,
                "trades": self.trades, "snapshots": self.snapshots, "positions": positions, "events": self.events,
                "account_state": self.state.to_dict(), "data_quality": quality,
                "fallback_days": fallback_days, "total_days": len(self._day_quality),
                "strategy_params": self.cfg.to_json_dict()}
