from __future__ import annotations

from datetime import datetime

from dragon_quant.live_trade.shadow import build_shadow_state
from dragon_quant.live_trade.signal_engine import SignalEngine
from dragon_quant.review_account.data import MarketData, historical_events
from dragon_quant.review_account.market import (
    SHANGHAI, at, bar_times, calendar_start, candidate_loader, collect_candidates,
    collect_candidates_ex, load_market_sentiment,
)
from dragon_quant.review_account.models import UNAVAILABLE_SUSPENDED
from dragon_quant.storage import db


class LiveTrader:
    """buy/sell 命令的信号执行器：回放当日事件流，复用 SignalEngine，纯信号记账。"""

    def __init__(self, cfg, source: str = "v2", data=None):
        from dragon_quant.review_account.models import StrategyConfig
        self.cfg = StrategyConfig.from_dict(cfg.to_json_dict())
        self.source = source
        self.data = data or MarketData()

    def buy(self, trade_date: str, as_of: str | None = None, force: bool = False) -> dict:
        return self._run(trade_date, as_of, "buy", force=force)

    def sell(self, trade_date: str, as_of: str | None = None, force: bool = False) -> dict:
        return self._run(trade_date, as_of, "sell", force=force)

    def _run(self, day, clock, command, force=False):
        forced = False
        datetime.strptime(day, "%Y-%m-%d")
        if clock:
            datetime.strptime(clock, "%H:%M")
        now = datetime.now(SHANGHAI)
        today = now.strftime("%Y-%m-%d")
        calendar = self.data.calendar(calendar_start(day, self.cfg.candidate_lookback_days), day, include_today=True)
        if day not in calendar:
            raise ValueError(f"{day} 不是已确认的交易日")

        if day < today or (day == today and clock is not None):
            # 历史（或今日回放）：必须指定 --at，只取对应时点及之前的行情。
            # 该路径 --force 静默无效（forced 恒为 False），--at 优先。
            if not clock:
                raise ValueError("历史日期必须提供 --at HH:MM，且仅使用对应历史行情")
            target = at(day, clock)
            until = None if day < today else target
        else:
            # 实时今日：回放截至当前已完成的5分钟K。
            time = now.strftime("%H:%M:%S")
            in_session = ("09:30:00" <= time < "11:30:01"
                          or "13:00:00" <= time < "15:06:00")
            if not in_session:
                if time < "09:30:00":
                    # 无完整5分钟K，强制无意义：--force 也不放行。
                    raise ValueError("当前尚未开盘（09:30 开始），请开盘后执行")
                if not force:
                    if "11:30:01" <= time < "13:00:00":
                        raise ValueError("当前午间休市（11:30–13:00），请 13:00 后执行")
                    raise ValueError("当前已收盘，请在交易时段执行，或用 --date/--at 回放历史")
            forced = force and not in_session
            target = int(now.timestamp() * 1000)
            completed = [t for t in bar_times(day) if t <= target]
            if not completed:
                raise ValueError("尚无完整5分钟K（09:35 首根），请稍后执行")
            until = completed[-1]

        # cutoff 只截断事件流（强制模式下钳到最后一根已完成K，避免日K兜底路径
        # 以当日收盘价补成交）；target 仍作影子状态 as-of，保持真实当前时点，
        # 使同日在 cutoff 之后的既有平仓腿（如 15:00:00.001）仍参与 sold_today/风控。
        cutoff = min(target, until) if forced else target

        if command == "buy":
            result = self._run_buy(day, target, until, calendar, cutoff, forced)
        else:
            result = self._run_sell(day, target, until, calendar, cutoff, forced)
        result["forced"] = forced
        return result

    # ── 数据分级：候选缺日K是买入前提缺失（抛错），持仓缺失则降级剔除 ──

    def _usable(self, day: str, codes: set[str], degraded: list[str], label: str) -> set[str]:
        usable = set()
        for code in sorted(codes):
            try:
                self.data.daily(code, day)
            except Exception:
                degraded.append(f"{label} {code} 缺少日K，已从当日回放剔除")
                continue
            usable.add(code)
        return usable

    @staticmethod
    def _quality(events_rows: dict, current: str) -> str:
        if not events_rows:
            return current
        return next(iter(events_rows.values())).get("data_quality", current)

    @staticmethod
    def _unavailable_notes(suspended: set[str], unavailable: dict[str, str]) -> list[str]:
        notes = []
        if suspended:
            notes.append(f"停牌跳过 {len(suspended)} 只：{'、'.join(sorted(suspended))}"
                         "（当日不评估，持仓冻结）")
        others = sorted(c for c, r in unavailable.items() if r != UNAVAILABLE_SUSPENDED)
        if others:
            notes.append(f"日K不可用跳过 {len(others)} 只：{'、'.join(others)}")
        return notes

    def _run_buy(self, day, target, until, calendar, cutoff, forced):
        signals = db.list_all_signals(source=self.source)
        shadow = build_shadow_state(signals, self.cfg, day, as_of=target)
        sentiment = load_market_sentiment(calendar, day, self.cfg)
        shadow.sentiment_pct = sentiment
        positions = shadow.positions
        held_codes = {p.code for p in positions}
        candidates, ctx = collect_candidates_ex(
            calendar, day, self.cfg, candidate_loader(self.cfg.source))
        candidate_codes = {c["code"] for c in candidates}
        degraded: list[str] = []
        usable_positions = self._usable(day, held_codes - candidate_codes, degraded, "持仓")
        codes = candidate_codes | usable_positions
        engine = SignalEngine(self.cfg, positions=positions, held_codes=held_codes,
                              sentiment_pct=sentiment, account=shadow, evaluate_sells=False)
        buys, details = [], []
        quality = "strict_5min"
        last_rows = {}
        suspended, unavailable = set(), {}
        for event in historical_events(day, candidates, codes, self.data, until=until):
            suspended |= event.suspended
            unavailable.update(event.unavailable)
            if event.timestamp > cutoff:
                break
            event.allow_buy = True
            quality = self._quality(event.rows, quality)
            last_rows = event.rows
            result = engine.step(event)
            buys.extend(result["buys"])
            details.extend(result["details"])
        degraded.extend(self._unavailable_notes(suspended, unavailable))
        # 回放结束时仍未成交的 pending BUY：保留「命中但未成交」诊断（--force 截断常见）。
        for order in engine.pending:
            if order["side"] != "BUY":
                continue
            row = last_rows.get(order["stock_code"])
            if row:
                details.append(SignalEngine._unfilled_detail(
                    order, row, "buy_pending_unfilled", "买点命中但回放结束时仍未成交"))
        if forced:
            # 强制模式落库可追溯：复用既有 entry_signal_json，无 schema 变更。
            for b in buys:
                if isinstance(b.get("signal"), dict):
                    b["signal"]["forced"] = True
        for b in buys:
            _, created = db.insert_signal(b["code"], b["name"], b["entry_date"], b["entry_price"],
                                          b["reason_code"], b["reason_text"], b["signal"],
                                          self.source, qty=b["qty"])
            b["persisted"] = created
        # 未落库的买入（命中同 code 的另一条 open 行）不回写峰值，避免写到无关信号上。
        skipped_peaks = {b["code"] for b in buys if not b["persisted"]}
        for p in engine.positions:
            if p.code not in skipped_peaks:
                db.update_signal_peaks(p.code, p.highest_return, p.highest_price)
        return {"ok": True, "mode": "buy",
                "action": "buy" if buys else "idle", "buys": buys, "details": details,
                "sentiment_pct": sentiment, "data_quality": quality,
                "degraded": degraded + list(ctx.get("degraded") or []),
                "suspended_codes": sorted(suspended),
                "unavailable_codes": {c: r for c, r in sorted(unavailable.items())
                                      if r != UNAVAILABLE_SUSPENDED},
                "fired_gate": engine.fired_gate,
                "buy_eval_skipped": list(engine.buy_eval_skipped),
                "ranking_mode": ctx["ranking_mode"],
                "pool_source": ctx["pool_source"],
                "fallback_reason": ctx["fallback_reason"],
                "pool_empty": ctx["pool_empty"],
                "prep_snapshot_date": ctx["prep_snapshot_date"],
                "auction_rows": ctx["auction_rows"],
                "priority_degraded": list(ctx.get("degraded") or []),
                "reason_text": f"买入 {len(buys)} 只" if buys else "候选池暂无触发买点的标的"}

    def _run_sell(self, day, target, until, calendar, cutoff, forced):
        signals = db.list_all_signals(source=self.source)
        shadow = build_shadow_state(signals, self.cfg, day, as_of=target)
        positions = shadow.positions
        degraded: list[str] = []
        try:
            candidates = collect_candidates(
                calendar, day, self.cfg, candidate_loader(self.cfg.source))
        except Exception as exc:  # 卖出是风控路径，不因候选池问题整体失败
            candidates = []
            degraded.append(f"candidate_pool_unavailable: {exc}（仅按持仓判定）")
        position_codes = {p.code for p in positions}
        candidate_codes = self._usable(day, {c["code"] for c in candidates} - position_codes,
                                       degraded, "候选")
        codes = position_codes | candidate_codes
        engine = SignalEngine(self.cfg, positions=positions)
        sells = []
        quality = "strict_5min"
        suspended, unavailable = set(), {}
        for event in historical_events(day, candidates, codes, self.data, until=until):
            suspended |= event.suspended
            unavailable.update(event.unavailable)
            if event.timestamp > cutoff:
                break
            event.allow_buy = False
            quality = self._quality(event.rows, quality)
            result = engine.step(event)
            sells.extend(result["sells"])
        degraded.extend(self._unavailable_notes(suspended, unavailable))
        if forced:
            # 强制模式落库可追溯：复用既有 exit/half_exit_signal_json，无 schema 变更。
            for s in sells:
                if isinstance(s.get("signal"), dict):
                    s["signal"]["forced"] = True
        for s in sells:
            if s["partial"]:
                s["persisted"] = db.half_close_signal(
                    s["code"], s["exit_date"], s["exit_price"], s["qty"],
                    s["reason_code"], s["reason_text"], s["signal"])
            else:
                s["persisted"] = db.close_signal(
                    s["code"], s["exit_date"], s["exit_price"],
                    s["reason_code"], s["reason_text"], s["signal"], s["hold_days"])
        for p in engine.positions:
            db.update_signal_peaks(p.code, p.highest_return, p.highest_price)
        held = [{"code": p.code, "name": p.name, "highest_return": p.highest_return,
                 "highest_price": p.highest_price, "qty": p.qty,
                 "took_profit_half": p.took_profit_half} for p in engine.positions]
        return {"action": "sell" if sells else "idle", "sells": sells, "held": held,
                "data_quality": quality, "degraded": degraded,
                "suspended_codes": sorted(suspended),
                "unavailable_codes": {c: r for c, r in sorted(unavailable.items())
                                      if r != UNAVAILABLE_SUSPENDED},
                "reason_text": f"卖出 {len(sells)} 只" if sells else "持仓暂无卖出信号"}
