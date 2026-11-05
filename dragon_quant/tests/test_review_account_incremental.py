"""增量回测：状态恢复、峰值/连亏口径、区间解析、落库与「链式 = 全量」oracle。

全部在临时 SQLite + FakeData 上运行，不触网、不写真实库。核心 oracle：
``full(09-04→09-11)`` 与 ``full(09-04→09-08) + incremental(→09-11)`` 在重叠
日期上的快照、交割单、已平仓记录必须逐值一致——它是「绝不复制买卖规则」的
可验证形式。
"""

import json
import sqlite3
import tempfile
import unittest
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from dragon_quant.infra import clock as runtime_clock
from dragon_quant.infra.clock import FixedClock
from dragon_quant.infra.models import KBar
from dragon_quant.infra.storage.connection import init_db
from dragon_quant.review_account import storage as account_store
from dragon_quant.review_account.market import SHANGHAI, bar_times, validate_bars
from dragon_quant.review_account.models import AccountState, Position, StrategyConfig
from dragon_quant.review_account.service import (
    _consecutive_losses,
    restore_incremental_seed,
    resolve_incremental_window,
    run_incremental_review_account,
    run_review_account,
)
from dragon_quant.review_account.simulator import AccountSimulator
from dragon_quant.tests.test_review_account import CAND, history

DAYS = ["2026-09-04", "2026-09-07", "2026-09-08",
        "2026-09-09", "2026-09-10", "2026-09-11"]
BASE_TO = "2026-09-08"
PRICES = {
    "2026-09-04": 10.0,   # 买入
    "2026-09-07": 9.0,    # -10% 触发硬止损
    "2026-09-08": 10.0,   # 再次买入（基准收盘持仓）
    "2026-09-09": 10.9,   # 续算首日：跟踪峰值
    "2026-09-10": 10.4,   # 移动止盈触发卖出
    "2026-09-11": 10.0,   # 再次买入
}


class ChainData:
    """无状态行情：日历 + 固定日K + 按日价格的分时K。"""

    def __init__(self, days=None, prices=None):
        self.days = list(days or DAYS)
        self.prices = dict(prices or PRICES)

    def calendar(self, start, end, include_today=False):
        return [d for d in self.days if start <= d <= end]

    def daily(self, code, start, refresh=False):
        return history("2026-09-12")

    def intraday(self, code, day, until=None):
        price = self.prices.get(day, 10.0)
        bars = [KBar(t, 10000, price, price, price, price, 0, 0, 1.0, 100000)
                for t in bar_times(day)]
        return validate_bars(bars, day, until)

    def try_intraday(self, code, day, until=None):
        return self.intraday(code, day, until)


class ClockCalendar(ChainData):
    """按 ``infra.clock`` 实现「当日 15:05 前不含当日」的日历（镜像 MarketData）。"""

    def __init__(self):
        super().__init__()
        self.calls = []

    def calendar(self, start, end, include_today=False):
        self.calls.append(end)
        now = runtime_clock.now(SHANGHAI)
        today = now.strftime("%Y-%m-%d")
        closed_today = now.strftime("%H:%M") >= "15:05"
        return [d for d in self.days
                if start <= d <= end
                and (d < today or (d == today and closed_today))]


def _snapshot(trade_date, cash, market_value, positions, *, drawdown=0.0):
    return {
        "trade_date": trade_date,
        "cash": cash,
        "market_value": market_value,
        "total_equity": cash + market_value,
        "daily_return": 0.0,
        "cumulative_return": 0.0,
        "drawdown": drawdown,
        "positions": positions,
        "positions_json": json.dumps(positions, ensure_ascii=False),
    }


def _position(**overrides):
    values = dict(
        code="600001", name="样本", qty=500, entry_date="2026-09-04",
        entry_price=10.0, cost=5005.0, entry_reason_code="buy_open_ma5_pullback",
        entry_reason_text="买", entry_signal={"a": 1}, highest_return=9.0,
        highest_price=10.9, entry_day_low=9.8, initial_qty=1000,
        initial_cost=10010.0, realized_pnl=250.0, took_profit_half=True,
    )
    values.update(overrides)
    return Position(**values)


class _IncrementalBase(unittest.TestCase):
    """临时 SQLite 隔离持久化 + 固定 15:30 时钟。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._db_path = str(Path(self._tmpdir.name) / "test.db")
        self._conn = sqlite3.connect(self._db_path)
        self._patch = patch("dragon_quant.infra.storage.connection._connect",
                            side_effect=lambda: sqlite3.connect(self._db_path))
        self._patch.start()
        init_db()
        self._previous_clock = runtime_clock.set_clock(
            FixedClock(datetime(2026, 9, 11, 15, 30, tzinfo=SHANGHAI)))

    def tearDown(self):
        runtime_clock.set_clock(self._previous_clock)
        self._patch.stop()
        self._conn.close()
        self._tmpdir.cleanup()

    def _seed_run(self, *, source="v2", initial_cash=100_000.0, params=None,
                  date_from="2026-09-04", date_to=BASE_TO, display_name="基准",
                  snapshots=None, closed=None):
        cfg_cash = initial_cash if initial_cash else 100_000.0
        cfg = StrategyConfig(initial_cash=cfg_cash)
        run_id = account_store.create_review_account_run(
            source=source,
            strategy_name="dragon_pullback_daily",
            strategy_params_json=json.dumps(
                cfg.to_json_dict() if params is None else params, ensure_ascii=False),
            date_from=date_from,
            date_to=date_to,
            initial_cash=initial_cash,
            final_equity=initial_cash,
            total_return=0.0,
            max_drawdown=0.0,
            trade_count=0,
            win_rate=None,
            display_name=display_name,
        )
        account_store.save_review_account_results(
            run_id,
            snapshots=snapshots or [
                _snapshot(date_to, initial_cash, 0.0, [])],
            trades=[],
            positions=closed or [],
            events=[],
        )
        return run_id


# ─── 状态恢复 ───────────────────────────────────────────────────────────────


class TestRestoreSeed(unittest.TestCase):
    def test_position_roundtrip_including_half_leg_fields(self):
        position = _position()
        seed = restore_incremental_seed(
            {"date_to": BASE_TO},
            [_snapshot(BASE_TO, 1000.0, 5000.0, [asdict(position)])],
            [],
        )
        self.assertEqual(seed["base_equity"], 6000.0)
        self.assertEqual(len(seed["state"].positions), 1)
        restored = seed["state"].positions[0]
        # 基础字段逐位还原；期初归因字段由恢复入口冻结（M3）。
        frozen = {"mark", "inherited", "period_start_qty", "period_start_mark",
                  "period_start_realized_pnl", "period_start_cost"}
        self.assertEqual(
            {k: v for k, v in asdict(restored).items() if k not in frozen},
            {k: v for k, v in asdict(position).items() if k not in frozen})
        self.assertTrue(restored.inherited)
        self.assertEqual(restored.period_start_qty, restored.qty)
        self.assertIsNotNone(restored.period_start_mark)
        self.assertEqual(seed["state"].cash, 1000.0)
        self.assertIn(seed["attribution_quality"],
                      {"precise", "legacy_approximation"})

    def test_consecutive_losses_tail_only(self):
        def rows(*returns):
            return [{"realized_return": value} for value in returns]

        self.assertEqual(_consecutive_losses(rows(1, -1, -1, 0.5, -1)), 1)
        self.assertEqual(_consecutive_losses(rows(-1, -1)), 2)
        self.assertEqual(_consecutive_losses([]), 0)
        # NULL 停止计数：不把缺失收益臆造成亏损。
        self.assertEqual(_consecutive_losses(rows(-1, None, -1)), 1)

    def test_peak_prefers_drawdown_reverse_for_chained_runs(self):
        # 基准期曾到 1300，但快照权益最大只有 1200；drawdown=-7.6923% 反推 1300。
        snapshots = [
            _snapshot("2026-09-04", 1300.0, 0.0, [], drawdown=0.0),
            _snapshot("2026-09-08", 1200.0, 0.0, [], drawdown=(1200 / 1300 - 1) * 100),
        ]
        seed = restore_incremental_seed({"date_to": BASE_TO}, snapshots, [])
        self.assertAlmostEqual(seed["peak_equity"], 1300.0)
        self.assertAlmostEqual(seed["state"].peak_equity, 1300.0)

    def test_peak_without_drawdown_falls_back_to_equity_max(self):
        snapshots = [_snapshot(BASE_TO, 1100.0, 0.0, [])]
        snapshots[0]["drawdown"] = None
        seed = restore_incremental_seed({"date_to": BASE_TO}, snapshots, [])
        self.assertEqual(seed["peak_equity"], 1100.0)

    def test_integrity_failures_raise_domain_errors(self):
        cases = [
            ([], {}, "基准记录缺少每日快照，无法增量续算"),
            ([_snapshot("2026-09-07", 100.0, 0.0, [])], {},
             "基准记录快照与结束日期不一致，数据不完整"),
        ]
        for snapshots, extra, message in cases:
            base = {"date_to": BASE_TO, **extra}
            with self.assertRaises(ValueError) as caught:
                restore_incremental_seed(base, snapshots, [])
            self.assertEqual(str(caught.exception), message)

        bad = _snapshot(BASE_TO, 100.0, 0.0, [])
        bad["cash"] = None
        with self.assertRaisesRegex(ValueError, "最终权益无效"):
            restore_incremental_seed({"date_to": BASE_TO}, [bad], [])

        bad = _snapshot(BASE_TO, 100.0, 0.0, [])
        bad["total_equity"] = 0.0
        with self.assertRaisesRegex(ValueError, "最终权益无效"):
            restore_incremental_seed({"date_to": BASE_TO}, [bad], [])

        legacy = _snapshot(BASE_TO, 100.0, 5000.0, [])
        with self.assertRaisesRegex(ValueError, "缺少持仓明细（旧数据）"):
            restore_incremental_seed({"date_to": BASE_TO}, [legacy], [])

    def test_unparsable_positions_raise_domain_error(self):
        snapshots = [_snapshot(BASE_TO, 100.0, 0.0, [])]
        snapshots[0]["positions"] = [{"code": "600001"}]  # 缺必填字段
        with self.assertRaisesRegex(ValueError, "持仓明细无法解析"):
            restore_incremental_seed({"date_to": BASE_TO}, snapshots, [])

        snapshots[0]["positions"] = [{"code": "600001", "unknown": 1}]
        with self.assertRaisesRegex(ValueError, "持仓明细无法解析"):
            restore_incremental_seed({"date_to": BASE_TO}, snapshots, [])


# ─── 增量区间 ───────────────────────────────────────────────────────────────


class TestIncrementalWindow(unittest.TestCase):
    def setUp(self):
        self._previous = runtime_clock.set_clock(
            FixedClock(datetime(2026, 9, 11, 14, 0, tzinfo=SHANGHAI)))
        self.addCleanup(runtime_clock.set_clock, self._previous)

    def test_before_close_excludes_today_and_passes_today_as_end(self):
        data = ClockCalendar()
        start, end = resolve_incremental_window(data, StrategyConfig(), BASE_TO)
        self.assertEqual((start, end), ("2026-09-09", "2026-09-10"))
        self.assertEqual(data.calls, ["2026-09-11"])

    def test_after_close_includes_today(self):
        runtime_clock.set_clock(FixedClock(datetime(2026, 9, 11, 15, 30, tzinfo=SHANGHAI)))
        start, end = resolve_incremental_window(ClockCalendar(), StrategyConfig(), BASE_TO)
        self.assertEqual((start, end), ("2026-09-09", "2026-09-11"))

    def test_no_new_trading_day_raises_chinese_error(self):
        runtime_clock.set_clock(FixedClock(datetime(2026, 9, 11, 15, 30, tzinfo=SHANGHAI)))
        with self.assertRaises(ValueError) as caught:
            resolve_incremental_window(ClockCalendar(), StrategyConfig(), "2026-09-11")
        self.assertEqual(
            str(caught.exception),
            "基准记录已覆盖最近已收盘交易日（2026-09-11），无新增数据",
        )


# ─── 模拟器注入 ─────────────────────────────────────────────────────────────


class TestSimulatorInjection(unittest.TestCase):
    def _run(self, **kwargs):
        with patch("dragon_quant.infra.assets.dragons_mirror.get_dragons_by_date",
                   return_value=[]), \
                patch("dragon_quant.review_account.market.prep_snapshot_for",
                      return_value=(None, None, None)):
            sim = AccountSimulator(StrategyConfig(initial_cash=100_000.0),
                                   data=ChainData(days=["2026-09-09"],
                                                  prices={"2026-09-09": 10.0}),
                                   **kwargs)
            return sim.run("2026-09-09", "2026-09-09")

    def test_injected_base_and_peak_drive_return_and_drawdown(self):
        state = AccountState(cash=6000.0)
        result = self._run(initial_state=state, base_equity=5000.0, peak_equity=8000.0)
        snapshot = result["snapshots"][0]
        self.assertAlmostEqual(snapshot.cumulative_return, (6000 / 5000 - 1) * 100)
        self.assertAlmostEqual(snapshot.daily_return, (6000 / 5000 - 1) * 100)
        self.assertAlmostEqual(snapshot.drawdown, (6000 / 8000 - 1) * 100)
        self.assertAlmostEqual(result["total_return"], (6000 / 5000 - 1) * 100)
        self.assertEqual(result["base_equity"], 5000.0)
        self.assertEqual(result["initial_cash"], 100_000.0)

    def test_defaults_are_bit_for_bit_legacy(self):
        result = self._run()
        snapshot = result["snapshots"][0]
        self.assertEqual(result["base_equity"], 100_000.0)
        self.assertAlmostEqual(snapshot.daily_return, 0.0)
        self.assertAlmostEqual(snapshot.cumulative_return, 0.0)
        self.assertAlmostEqual(snapshot.drawdown, 0.0)
        self.assertAlmostEqual(result["total_return"], 0.0)


# ─── 服务层校验 / 落库 ──────────────────────────────────────────────────────


class TestIncrementalService(_IncrementalBase):
    def test_rejects_non_v2_base(self):
        run_id = self._seed_run(source="v1")
        with self.assertRaises(ValueError) as caught:
            run_incremental_review_account(run_id, verbose=False)
        self.assertEqual(str(caught.exception), "仅支持对 v2 回测记录做增量")

    def test_rejects_incomplete_base_rows(self):
        run_id = self._seed_run(params={})
        with self.assertRaisesRegex(ValueError, "缺少可用的策略参数快照"):
            run_incremental_review_account(run_id, verbose=False)

        run_id = self._seed_run(params=[])  # 非对象快照同样拒绝，绝不回落默认配置
        with self.assertRaisesRegex(ValueError, "缺少可用的策略参数快照"):
            run_incremental_review_account(run_id, verbose=False)

        run_id = self._seed_run(
            initial_cash=None,
            snapshots=[_snapshot(BASE_TO, 100_000.0, 0.0, [])],
        )
        with self.assertRaisesRegex(ValueError, "初始资金无效"):
            run_incremental_review_account(run_id, verbose=False)

    def test_missing_base_is_domain_error(self):
        with self.assertRaisesRegex(ValueError, "基准记录不存在"):
            run_incremental_review_account(999_999, verbose=False)

    def test_persists_incremental_run_and_inherits_strategy(self):
        base_equity = 101_000.0
        run_id = self._seed_run(
            initial_cash=100_000.0,
            snapshots=[_snapshot(BASE_TO, base_equity, 0.0, [])],
        )
        with patch("dragon_quant.review_account.service.MarketData",
                   side_effect=lambda provider=None: ChainData()), \
                patch("dragon_quant.infra.assets.dragons_mirror.get_dragons_by_date",
                      return_value=[CAND]), \
                patch("dragon_quant.review_account.market.prep_snapshot_for",
                      return_value=(None, None, None)):
            result = run_incremental_review_account(run_id, verbose=False)

        run = account_store.get_review_account_run(result["run_id"])
        base = account_store.get_review_account_run(run_id)
        self.assertIsNone(base["base_equity"])
        self.assertEqual(run["base_equity"], base_equity)
        self.assertEqual(run["source"], "v2")
        self.assertEqual(run["strategy_name"], "dragon_pullback_daily")
        self.assertEqual(run["strategy_params"], base["strategy_params"])
        self.assertEqual(run["initial_cash"], 100_000.0)
        self.assertEqual((run["date_from"], run["date_to"]),
                         ("2026-09-09", "2026-09-11"))
        self.assertEqual(run["display_name"], "基准 · 增量 2026-09-09")

        runs = account_store.query_review_account_runs()
        self.assertEqual(runs[0]["id"], run["id"])  # additive 键：列表行也返回 base_equity
        self.assertEqual(runs[0]["base_equity"], base_equity)

        snapshots = account_store.query_review_account_snapshots(result["run_id"])
        self.assertEqual([s["trade_date"] for s in snapshots],
                         ["2026-09-09", "2026-09-10", "2026-09-11"])

    def test_snapshot_end_mismatch_aborts_before_persist(self):
        run_id = self._seed_run()

        class StubSimulator:
            def __init__(self, *args, **kwargs):
                self.data = ChainData()

            def run(self, date_from, date_to):
                # 模拟第二次日历取数变短：只生成到 09-09，终点应为 09-11。
                return {"snapshots": [SimpleNamespace(trade_date=date_from)]}

        with patch("dragon_quant.review_account.service.MarketData",
                   side_effect=lambda provider=None: ClockCalendar()), \
                patch("dragon_quant.review_account.service.AccountSimulator",
                      StubSimulator):
            with self.assertRaises(ValueError) as caught:
                run_incremental_review_account(run_id, verbose=False)
        self.assertEqual(
            str(caught.exception),
            "增量日历与行情快照不一致（终点 2026-09-11，最后快照 2026-09-09），请重试",
        )
        # 中止发生在落库前：只保留基准 run，没有半条增量记录。
        self.assertEqual(len(account_store.query_review_account_runs()), 1)

    def test_second_incremental_reports_no_new_data(self):
        run_id = self._seed_run()
        with patch("dragon_quant.review_account.service.MarketData",
                   side_effect=lambda provider=None: ClockCalendar()), \
                patch("dragon_quant.infra.assets.dragons_mirror.get_dragons_by_date",
                      return_value=[CAND]), \
                patch("dragon_quant.review_account.market.prep_snapshot_for",
                      return_value=(None, None, None)):
            result = run_incremental_review_account(run_id, verbose=False)
            with self.assertRaises(ValueError) as caught:
                run_incremental_review_account(result["run_id"], verbose=False)
        self.assertIn("无新增数据", str(caught.exception))


# ─── 核心 oracle：链式增量 = 一次性全量 ─────────────────────────────────────


class TestChainEqualsFull(_IncrementalBase):
    def test_incremental_matches_full_replay_on_overlap(self):
        patches = [
            patch("dragon_quant.review_account.simulator.MarketData",
                  side_effect=lambda provider=None: ChainData()),
            patch("dragon_quant.review_account.service.MarketData",
                  side_effect=lambda provider=None: ChainData()),
            patch("dragon_quant.infra.assets.dragons_mirror.get_dragons_by_date",
                  return_value=[CAND]),
            patch("dragon_quant.review_account.market.prep_snapshot_for",
                  return_value=(None, None, None)),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

        full = run_review_account("2026-09-04", "2026-09-11", verbose=False,
                                  display_name="全量")
        base = run_review_account("2026-09-04", BASE_TO, verbose=False,
                                  display_name="基准")
        incremental = run_incremental_review_account(base["run_id"], verbose=False)

        inc_snapshots = account_store.query_review_account_snapshots(incremental["run_id"])
        full_snapshots = [s for s in account_store.query_review_account_snapshots(full["run_id"])
                          if s["trade_date"] >= "2026-09-09"]
        self.assertEqual([s["trade_date"] for s in inc_snapshots],
                         ["2026-09-09", "2026-09-10", "2026-09-11"])
        for inc, ref in zip(inc_snapshots, full_snapshots):
            for key in ("cash", "market_value", "total_equity"):
                self.assertAlmostEqual(inc[key], ref[key], places=6,
                                       msg=f"{inc['trade_date']} {key}")

        inc_trades = account_store.query_review_account_trades(incremental["run_id"])
        full_trades = [t for t in account_store.query_review_account_trades(full["run_id"])
                       if t["trade_date"] >= "2026-09-09"]
        self.assertTrue(inc_trades, "增量期应至少有一笔成交，否则 oracle 不成立")
        self.assertEqual(len(inc_trades), len(full_trades))
        for inc, ref in zip(inc_trades, full_trades):
            for key in ("trade_date", "code", "side", "price", "qty", "amount",
                        "fee", "realized_pnl", "cash_after", "position_after",
                        "reason_code"):
                self.assertEqual(inc[key], ref[key], msg=key)

        inc_positions = account_store.query_review_account_positions(incremental["run_id"])
        full_positions = [p for p in account_store.query_review_account_positions(full["run_id"])
                          if (p["exit_date"] or "") >= "2026-09-09"]
        self.assertEqual(len(inc_positions), len(full_positions))
        for inc, ref in zip(inc_positions, full_positions):
            for key in ("code", "entry_date", "entry_price", "qty", "exit_date",
                        "exit_price", "realized_return", "hold_days", "status"):
                self.assertEqual(inc[key], ref[key], msg=key)

        # 增量 run 的收益率分母是基准最后权益，不是 initial_cash。
        self.assertEqual(incremental["base_equity"], base["final_equity"])
        self.assertAlmostEqual(
            incremental["total_return"],
            (incremental["final_equity"] / base["final_equity"] - 1) * 100,
        )


if __name__ == "__main__":
    unittest.main()
