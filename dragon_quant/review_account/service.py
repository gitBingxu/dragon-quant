"""账户级回测用例实现（公共入口见 :mod:`dragon_quant.review_account.main`）。

包含两条用例：全量回测 :func:`run_review_account` 与增量续算
:func:`run_incremental_review_account`。全量走 ``_persist_result`` 新建 run；
增量走 ``storage.append_review_account_results`` 把新快照追加到原 run 并更新
汇总列。两条薄管线共用 ``storage._insert_children``，避免子表 SQL 漂移。
买卖/成交规则仍全部由 ``AccountSimulator`` / ``TradingEngine`` 复用，本模块只
做编排与状态恢复。
"""

import copy
import json
from dataclasses import fields
from typing import Optional

from dragon_quant.infra import clock
from . import storage as account_store
from dragon_quant.review_account.models import AccountState, Position, StrategyConfig
# 导入顺序：strategy.priority 先于 market/data，避免既有
# ``market ↔ strategy.buy`` 的循环导入在部分初始化阶段取不到 ``bar_times``。
from dragon_quant.review_account.strategy.priority import RANKING_MODE_LABELS
from dragon_quant.review_account.data import MarketData
from dragon_quant.review_account.market import SHANGHAI, calendar_start
from dragon_quant.review_account.simulator import AccountSimulator, annotate_post_exit

# 恢复基准持仓时的字段白名单（= Position 的全部字段）。
_POSITION_FIELDS = frozenset(f.name for f in fields(Position))

_ERR_POSITIONS_UNPARSABLE = "基准记录持仓明细无法解析，请重新生成基准记录"
_ERR_POSITIONS_LEGACY = "基准记录快照缺少持仓明细（旧数据），请重新生成基准记录"
_ERR_BASE_MISSING = "基准记录不存在，无法增量续算"
_ERR_STRATEGY_PARAMS = "基准记录缺少可用的策略参数快照，无法增量续算"
_ERR_INITIAL_CASH = "基准记录初始资金无效，无法增量续算"
_ERR_SNAPSHOTS_MISSING = "基准记录缺少每日快照，无法增量续算"
_ERR_NO_SNAPSHOTS = "回测没有生成任何每日快照，拒绝落库"
_ERR_SNAPSHOT_MISMATCH = "基准记录快照与结束日期不一致，数据不完整"
_ERR_EQUITY_INVALID = "基准记录最终权益无效，无法增量续算"
_ERR_SOURCE_NOT_V2 = "仅支持对 v2 回测记录做增量"
_ERR_LEGACY_INCREMENTAL = ("旧版增量记录不支持继续增量，"
                           "请在对应的全量记录上操作")
_ERR_SNAPSHOT_END = "增量日历与行情快照不一致（终点 {expected}，最后快照 {actual}），请重试"
_ERR_RESULT_END = "回测快照终点（{actual}）晚于请求结束日（{expected}），拒绝落库"
_ERR_PERSIST_SNAPSHOT_END = "回测快照终点（{actual}）与结束日期（{expected}）不一致，拒绝落库"


def _xueqiu_from_services(services):
    """可选 M2 装配注入：取出 bundle 的雪球 provider；无注入返回 None。

    None 表示沿用 ``AccountSimulator`` → ``MarketData`` 的既有默认构造，
    旧调用与 ``MarketData(provider=...)`` / provider patch 行为不变。
    """
    if services is None:
        return None
    bundle = getattr(services, "providers", None)
    if bundle is None:
        return None
    return bundle.xueqiu


def run_review_account(date_from: str,
                       date_to: str,
                       *,
                       initial_cash: float = 100_000.0,
                       source: str = "v2",
                       strategy_name: str = "dragon_pullback_daily",
                       verbose: bool = True,
                       display_name: Optional[str] = None,
                       strategy_params: Optional[dict] = None,
                       services=None) -> dict:
    """运行账户级交易模拟并持久化结果。

    ``services`` 为可选 M2 装配注入口：传入且其 ``providers`` 非空时，K 线
    加载使用 ``services.providers.xueqiu``；不传时保持原有 ``XueqiuProvider()``
    构造路径。策略、成交、候选池、信号与输出不变。
    """
    if strategy_params is not None and not isinstance(strategy_params, dict):
        raise ValueError("strategy_params 必须是 JSON 对象")
    cfg = StrategyConfig.from_dict({**(strategy_params or {}), "source": source,
                                    "strategy_name": strategy_name, "initial_cash": initial_cash})
    sim = AccountSimulator(cfg, provider=_xueqiu_from_services(services))
    result = sim.run(date_from, date_to)
    # 日历在 15:05 前 / 非交易日不含当日，模拟器会少跑这些日期；落库终点
    # 必须收敛到实际最后快照，否则增量回测会拒绝续算（见 resolve_* 注释）。
    requested_date_to = date_to
    effective_date_to = resolve_persisted_date_to(result, requested_date_to)
    if effective_date_to != requested_date_to:
        result["date_to"] = effective_date_to
        result["date_to_clamped"] = True
        # 前端/兼容层默认名是「起点 ~ 请求终点」；钳制后同步为真实终点，
        # 避免列表里的名称继续宣称一个没有被回测的日期。
        if display_name == f"{date_from} ~ {requested_date_to}":
            display_name = f"{date_from} ~ {effective_date_to}"
        if verbose:
            print(f"⚠️ 结束日 {requested_date_to} 尚无已收盘数据，"
                  f"已回测至 {effective_date_to}（原始请求日已记录在 run_meta）")
    # 事后评估：清仓后窗口指标。放在 run() 之外，回测核心保持纯 as-of；失败逐条降级。
    annotate_post_exit(result["positions"], sim.data)

    run_id = _persist_result(
        result,
        source=source,
        strategy_name=strategy_name,
        strategy_params_json=json.dumps(cfg.to_json_dict(), ensure_ascii=False),
        date_from=date_from,
        date_to=effective_date_to,
        requested_date_to=requested_date_to,
        display_name=display_name,
        start_positions=[],
        start_realized_pnl=0.0,
        start_cash=initial_cash,
        start_peak_equity=initial_cash,
        start_consecutive_losses=0,
        start_marks={},
        attribution_quality="full_run",
    )
    result["run_id"] = run_id
    result["display_name"] = display_name

    if verbose:
        _print_summary(result, source, strategy_name)
    return result


def run_incremental_review_account(base_run_id: int, *,
                                   verbose: bool = True,
                                   services=None) -> dict:
    """在基准 run 的最后收盘状态上继续交易，并把新快照**追加到同一条 run**。

    语义：A 截止到 10-10，增量就在 A 的权益曲线上续写 10-13 起的快照，更新
    A 的 ``date_to`` / 汇总列，而不是新建一条从 10-13 开始的记录。基准记录
    只在「追加前」被读取（``restore_incremental_seed`` 是纯函数）；写入阶段
    快照、交割单、持仓、事件与 run 汇总列在同一事务内完成，失败整体回滚。
    任何状态口径不满足完整性校验都抛 ``ValueError``（Web 层映射 400），绝不
    回落到「当前默认配置」或用未来数据。
    """
    try:
        base = account_store.get_review_account_run(base_run_id)
    except ValueError as exc:  # strategy_params_json 坏行 → 域错误而非 500
        raise ValueError(_ERR_STRATEGY_PARAMS) from exc
    if base is None:
        raise ValueError(_ERR_BASE_MISSING)

    source = base.get("source") or "v2"
    if source != "v2":
        raise ValueError(_ERR_SOURCE_NOT_V2)
    if base.get("base_equity") is not None:
        # 旧版增量 run 的 initial_cash 是原全量起点，快照却从续算段开始；
        # 在它上面再追加会让整条 run 的 total_return 口径失真。
        raise ValueError(_ERR_LEGACY_INCREMENTAL)

    initial_cash = base.get("initial_cash")
    if initial_cash is None or initial_cash <= 0:
        raise ValueError(_ERR_INITIAL_CASH)

    params = base.get("strategy_params")
    if not isinstance(params, dict) or not params:
        raise ValueError(_ERR_STRATEGY_PARAMS)
    try:
        # 冻结基准策略快照：不使用当前默认配置，也不允许缺字段静默回落。
        cfg = StrategyConfig.from_dict({
            **params,
            "source": source,
            "strategy_name": base.get("strategy_name") or "",
            "initial_cash": initial_cash,
        })
    except (TypeError, ValueError) as exc:
        raise ValueError(_ERR_STRATEGY_PARAMS) from exc

    try:
        snapshots = account_store.query_review_account_snapshots(base_run_id)
    except ValueError as exc:  # positions_json 解析失败：旧数据坏行 → 400 而非 500
        raise ValueError(_ERR_POSITIONS_UNPARSABLE) from exc
    closed_rows = account_store.query_review_account_closed_positions(base_run_id)
    seed = restore_incremental_seed(base, snapshots, closed_rows)

    data = MarketData(provider=_xueqiu_from_services(services))
    date_from, date_to = resolve_incremental_window(data, cfg, base["date_to"])

    sim = AccountSimulator(cfg, data=data,
                           initial_state=copy.deepcopy(seed["state"]),
                           base_equity=seed["base_equity"],
                           peak_equity=seed["peak_equity"],
                           start_realized_pnl=seed["start_realized_pnl"])
    result = sim.run(date_from, date_to)
    _assert_snapshot_end(result, date_to)
    annotate_post_exit(result["positions"], sim.data)

    stats = _aggregate_appended_stats(base, snapshots, closed_rows, result)
    segment_from, segment_to = date_from, date_to
    # 返回值描述「追加后的整条 run」：区间、收益、回撤、成交与胜率都用 run 口径，
    # 本段区间另存 segment_*，供调用方展示本次续算了哪几天。
    result.update(stats)
    result["date_from"] = base["date_from"]
    result["date_to"] = date_to
    result["segment_date_from"] = segment_from
    result["segment_date_to"] = segment_to
    run_meta_json = json.dumps(
        _merged_run_meta(
            base, result,
            total_days=len(snapshots) + len(result["snapshots"]),
            date_from=date_from, date_to=date_to),
        ensure_ascii=False)
    account_store.append_review_account_results(
        base_run_id,
        date_to=date_to,
        final_equity=stats["final_equity"],
        total_return=stats["total_return"],
        max_drawdown=stats["max_drawdown"],
        trade_count=stats["trade_count"],
        win_rate=stats["win_rate"],
        run_meta_json=run_meta_json,
        snapshots=[s.__dict__ for s in result["snapshots"]],
        trades=[
            {**t.__dict__, "signal_json": json.dumps(t.signal, ensure_ascii=False)}
            for t in result["trades"]
        ],
        positions=[
            {
                **p.__dict__,
                "entry_signal_json": json.dumps(p.entry_signal, ensure_ascii=False),
                "exit_signal_json": json.dumps(p.exit_signal, ensure_ascii=False),
            }
            for p in result["positions"]
        ],
        events=[
            {**e.__dict__, "signal_json": json.dumps(e.signal, ensure_ascii=False)}
            for e in result.get("events", [])
        ],
    )
    result["run_id"] = base_run_id
    result["display_name"] = base.get("display_name")
    result["base_equity"] = seed["base_equity"]
    result["appended_to_run"] = base_run_id

    if verbose:
        base_label = base.get("display_name") or f"{base['date_from']} ~ {base['date_to']}"
        print(f"增量已追加到 run #{base_run_id}（{base_label}）："
              f"本段 {segment_from} ~ {segment_to}")
        _print_summary(result, source, cfg.strategy_name)
    return result


def restore_incremental_seed(base_run: dict,
                             snapshots: list[dict],
                             closed_rows: list[dict]) -> dict:
    """从基准 run 的已落库状态恢复增量续算种子（纯函数，不读写数据库）。

    返回 ``{"state": AccountState, "base_equity": float, "peak_equity": float}``。
    任一完整性校验失败抛 ``ValueError``（中文域错误，Web 层直接展示）。
    """
    if not snapshots:
        raise ValueError(_ERR_SNAPSHOTS_MISSING)
    last = snapshots[-1]
    if last.get("trade_date") != base_run.get("date_to"):
        raise ValueError(_ERR_SNAPSHOT_MISMATCH)

    cash = last.get("cash")
    total_equity = last.get("total_equity")
    if cash is None or total_equity is None or total_equity <= 0:
        raise ValueError(_ERR_EQUITY_INVALID)

    market_value = last.get("market_value") or 0
    positions = _restore_positions(last.get("positions"))
    if not positions and market_value > 0:
        # 旧数据缺持仓明细时静默续算会丢掉真实持仓，必须拒绝。
        raise ValueError(_ERR_POSITIONS_LEGACY)

    peak_equity = _restore_peak(snapshots, total_equity)
    # 冻结本段期初状态：继承持仓以期初盯市价值为归因成本基准。
    single_mark = last.get("position_market_price")
    legacy_mark = 0
    for p in positions:
        p.inherited = True
        p.period_start_qty = p.qty
        mark = p.mark
        if mark is None and len(positions) == 1 and single_mark is not None:
            mark = single_mark
        if mark is None:
            legacy_mark += 1
            mark = p.entry_price
        p.period_start_mark = mark
        p.period_start_realized_pnl = p.realized_pnl
        p.period_start_cost = p.cost

    state = AccountState(
        cash=cash,
        positions=positions,
        consecutive_losses=_consecutive_losses(closed_rows),
        peak_equity=peak_equity,
    )
    return {
        "state": state,
        "base_equity": total_equity,
        "peak_equity": peak_equity,
        "start_realized_pnl": _start_realized_pnl(base_run),
        # 旧快照无盯市价 → 期初价值只能是近似（明示，不冒充精确）。
        "attribution_quality": ("legacy_approximation"
                                if legacy_mark else "precise"),
    }


def _start_realized_pnl(base_run: dict) -> float:
    """基准 run 的累计已实现盈亏（新元数据）；旧 run 缺失按 0 起算并标记。"""
    meta = base_run.get("run_meta")
    if isinstance(meta, dict):
        value = meta.get("realized_pnl_total")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    return 0.0


def resolve_incremental_window(data, cfg: StrategyConfig,
                               base_date_to: str) -> tuple[str, str]:
    """增量区间 = 基准结束日之后、最近已收盘交易日（含）的交易日序列首尾。

    ``MarketData.calendar`` 已实现「当日 15:05 前不含当日」，与 BS 回放同口径；
    区间为空时抛中文域错误（400）。
    """
    today = clock.now(SHANGHAI).strftime("%Y-%m-%d")
    days = data.calendar(calendar_start(base_date_to, cfg.candidate_lookback_days), today)
    future = [d for d in days if d > base_date_to]
    if not future:
        raise ValueError(f"基准记录已覆盖最近已收盘交易日（{base_date_to}），无新增数据")
    return future[0], future[-1]


def resolve_persisted_date_to(result: dict, requested_date_to: str) -> str:
    """全量回测落库终点：收敛到实际最后快照日（策略决定，区别于落库断言）。

    ``MarketData.calendar`` 在「当日 15:05 前」与「非交易日」不含当日，
    ``sim.run`` 会静默少跑这些日期。若仍按请求日落库，增量回测就会以
    「基准记录快照与结束日期不一致」拒绝续算。这里以实际快照为准，
    原始请求日由 ``run_meta.requested_date_to`` 留痕，不静默丢失。
    """
    snapshots = result.get("snapshots") or []
    if not snapshots:
        raise ValueError(_ERR_NO_SNAPSHOTS)
    actual = snapshots[-1].trade_date
    if actual > requested_date_to:
        raise ValueError(_ERR_RESULT_END.format(expected=requested_date_to,
                                                actual=actual))
    return actual


def _aggregate_appended_stats(base_run: dict, snapshots: list[dict],
                              closed_rows: list[dict], result: dict) -> dict:
    """追加后的 run 级汇总：收益率回到 ``initial_cash`` 口径，回撤/胜率覆盖全周期。

    增量段结果只描述本次续算；run 行必须继续表达整条记录。收益率分母用 run 的
    ``initial_cash``（与全量一致），而不是本段的 ``base_equity``。
    """
    drawdowns = [s.get("drawdown") for s in snapshots
                 if s.get("drawdown") is not None]
    drawdowns += [s.drawdown for s in result["snapshots"]
                  if s.drawdown is not None]
    final_equity = result["snapshots"][-1].total_equity
    initial_cash = base_run.get("initial_cash") or 0
    total_closed = len(closed_rows) + len(result["positions"])
    wins = sum(1 for row in closed_rows
               if (row.get("realized_return") or 0) > 0)
    wins += sum(1 for p in result["positions"] if p.realized_return > 0)
    return {
        "final_equity": final_equity,
        "total_return": ((final_equity / initial_cash - 1) * 100
                         if initial_cash else 0.0),
        "max_drawdown": min(drawdowns) if drawdowns else 0.0,
        "trade_count": (base_run.get("trade_count") or 0) + result["trade_count"],
        "win_rate": (wins / total_closed * 100) if total_closed else None,
    }


def _merged_run_meta(base_run: dict, result: dict, *,
                     total_days: int, date_from: str, date_to: str) -> dict:
    """把增量段并入 run_meta：可累加字段求和，枚举冲突记 ``mixed``。

    ``incremental_segments`` 记录每次续算的区间，供排障与 UI 追溯；旧 run
    没有该键时从空列表开始。
    """
    meta = dict(base_run.get("run_meta") or {})
    meta["data_quality"] = _merge_enum(meta.get("data_quality"),
                                       result.get("data_quality"))
    meta["ranking_mode"] = _merge_enum(meta.get("ranking_mode"),
                                       result.get("ranking_mode"))
    meta["fallback_days"] = ((meta.get("fallback_days") or 0)
                             + (result.get("fallback_days") or 0))
    meta["total_days"] = total_days
    meta["suspended_days_count"] = ((meta.get("suspended_days_count") or 0)
                                    + (result.get("suspended_days_count") or 0))
    meta["suspended_codes"] = sorted(
        set(meta.get("suspended_codes") or [])
        | set(result.get("suspended_codes") or []))
    meta["pool_sources"] = {**(meta.get("pool_sources") or {}),
                            **(result.get("pool_sources") or {})}
    meta["fallback_reasons"] = {**(meta.get("fallback_reasons") or {}),
                                **(result.get("fallback_reasons") or {})}
    meta["priority_degraded"] = _dedup(meta.get("priority_degraded"),
                                       result.get("priority_degraded"))
    meta["data_degraded"] = _dedup(meta.get("data_degraded"),
                                   result.get("data_degraded"))
    meta["strategy_name"] = base_run.get("strategy_name") or ""
    meta["initial_cash"] = base_run.get("initial_cash")
    meta.setdefault("base_equity", meta["initial_cash"])
    meta["realized_pnl_total"] = ((meta.get("realized_pnl_total") or 0)
                                  + (result.get("realized_pnl") or 0))
    meta.setdefault("attribution_quality", "full_run")
    segments = list(meta.get("incremental_segments") or [])
    segments.append({"date_from": date_from, "date_to": date_to})
    meta["incremental_segments"] = segments
    return meta


def _merge_enum(old, new):
    """枚举型元数据合并：缺失取另一侧，冲突记 ``mixed``。"""
    if old is None:
        return new
    if new is None:
        return old
    return old if old == new else "mixed"


def _dedup(first, second):
    return list(dict.fromkeys((first or []) + (second or [])))


def _assert_snapshot_end(result: dict, expected: str) -> None:
    """落库前校验行情覆盖：最后一条快照必须落在解析出的增量终点。

    区间解析与 ``sim.run`` 各自取一次日历；若两次取数之间数据源异常变短，
    落库后 run.date_to 会大于实际快照日期，导致下一次增量被完整性校验拒绝。
    这里在写任何 run/快照/交割单之前直接以中文域错误中止。
    """
    snapshots = result.get("snapshots") or []
    actual = snapshots[-1].trade_date if snapshots else "无"
    if actual != expected:
        raise ValueError(_ERR_SNAPSHOT_END.format(expected=expected, actual=actual))


def _restore_positions(raw_positions) -> list[Position]:
    """``positions_json`` → ``Position`` 全字段还原；坏行/字段漂移 → 400。"""
    if raw_positions is None:
        return []
    if not isinstance(raw_positions, list):
        raise ValueError(_ERR_POSITIONS_UNPARSABLE)
    positions = []
    for item in raw_positions:
        if not isinstance(item, dict) or set(item) - _POSITION_FIELDS:
            raise ValueError(_ERR_POSITIONS_UNPARSABLE)
        try:
            positions.append(Position(**item))
        except TypeError as exc:
            raise ValueError(_ERR_POSITIONS_UNPARSABLE) from exc
    return positions


def _restore_peak(snapshots: list[dict], fallback: float) -> float:
    """失败峰值恢复：快照权益最大值 ∪ 由 ``drawdown`` 反推的峰值。

    对首代全量基准二者逐值相同；链式增量时基准 run 的峰值由更早 run 继承，
    只取快照权益最大值会「洗掉」历史峰值，用 ``drawdown = equity / peak - 1``
    反推可精确还原且不新增列。
    """
    peaks: list[float] = []
    for snap in snapshots:
        equity, drawdown = snap.get("total_equity"), snap.get("drawdown")
        if isinstance(equity, (int, float)) and not isinstance(equity, bool):
            peaks.append(float(equity))
        if (isinstance(equity, (int, float)) and not isinstance(equity, bool)
                and isinstance(drawdown, (int, float)) and not isinstance(drawdown, bool)):
            denominator = 1 + drawdown / 100
            if denominator > 0:
                peaks.append(equity / denominator)
    return max(peaks) if peaks else fallback


def _consecutive_losses(closed_rows: list[dict]) -> int:
    """已平仓记录尾部连续亏损笔数（``None`` 停止计数，不臆造亏损）。"""
    streak = 0
    for row in reversed(list(closed_rows or [])):
        realized = row.get("realized_return")
        if realized is None or realized >= 0:
            break
        streak += 1
    return streak


def _persist_result(result: dict, *,
                    source: str,
                    strategy_name: str,
                    strategy_params_json: str,
                    date_from: str,
                    date_to: str,
                    display_name: Optional[str],
                    requested_date_to: Optional[str] = None,
                    base_equity: Optional[float] = None,
                    base_run_id: Optional[int] = None,
                    start_positions: Optional[list[dict]] = None,
                    start_realized_pnl: float = 0.0,
                    start_cash: Optional[float] = None,
                    start_peak_equity: Optional[float] = None,
                    start_consecutive_losses: Optional[int] = None,
                    start_marks: Optional[dict] = None,
                    attribution_quality: str = "full_run") -> int:
    """全量回测落库管线：新建 run 并原子写入全部子表。

    落库前校验不变量 ``date_to == 最后快照 trade_date``：调用方负责决定终点
    （全量钳制），这里拒绝任何不一致的结果进入数据库。增量不走本函数，而是
    ``storage.append_review_account_results`` 原地追加。
    """
    snapshots = result.get("snapshots") or []
    if not snapshots:
        raise ValueError(_ERR_NO_SNAPSHOTS)
    actual_end = snapshots[-1].trade_date
    if actual_end != date_to:
        raise ValueError(_ERR_PERSIST_SNAPSHOT_END.format(expected=date_to,
                                                          actual=actual_end))
    run_meta = {
        "data_quality": result.get("data_quality"),
        "fallback_days": result.get("fallback_days"),
        "total_days": result.get("total_days"),
        "ranking_mode": result.get("ranking_mode"),
        "pool_sources": result.get("pool_sources") or {},
        "fallback_reasons": result.get("fallback_reasons") or {},
        "priority_degraded": result.get("priority_degraded") or [],
        "suspended_days_count": result.get("suspended_days_count"),
        "suspended_codes": result.get("suspended_codes") or [],
        "data_degraded": result.get("data_degraded") or [],
        "strategy_name": strategy_name,
        "initial_cash": result["initial_cash"],
        "base_equity": base_equity if base_equity is not None else result["initial_cash"],
        "realized_pnl_total": result.get("realized_pnl_total"),
        "attribution_quality": attribution_quality,
        "requested_date_to": requested_date_to or date_to,
        "date_to_clamped": bool(requested_date_to and requested_date_to != date_to),
    }
    run_id = account_store.create_review_account_run(
        source=source,
        strategy_name=strategy_name,
        strategy_params_json=strategy_params_json,
        date_from=date_from,
        date_to=date_to,
        initial_cash=result["initial_cash"],
        final_equity=result["final_equity"],
        total_return=result["total_return"],
        max_drawdown=result["max_drawdown"],
        trade_count=result["trade_count"],
        win_rate=result["win_rate"],
        display_name=display_name,
        base_equity=base_equity,
        base_run_id=base_run_id,
        start_equity=(base_equity if base_equity is not None
                      else result["initial_cash"]),
        start_positions_json=json.dumps(start_positions or [], ensure_ascii=False),
        start_realized_pnl=start_realized_pnl,
        start_cash=start_cash,
        start_peak_equity=start_peak_equity,
        start_consecutive_losses=start_consecutive_losses,
        start_marks_json=json.dumps(start_marks or {}, ensure_ascii=False),
        run_meta_json=json.dumps(run_meta, ensure_ascii=False),
        # run 行与全部子行同一事务提交：子表失败时不留下空 run。
        children={
            "snapshots": [s.__dict__ for s in result["snapshots"]],
            "trades": [
                {**t.__dict__, "signal_json": json.dumps(t.signal, ensure_ascii=False)}
                for t in result["trades"]
            ],
            "positions": [
                {
                    **p.__dict__,
                    "entry_signal_json": json.dumps(p.entry_signal, ensure_ascii=False),
                    "exit_signal_json": json.dumps(p.exit_signal, ensure_ascii=False),
                }
                for p in result["positions"]
            ],
            "events": [
                {
                    **e.__dict__,
                    "signal_json": json.dumps(e.signal, ensure_ascii=False),
                }
                for e in result.get("events", [])
            ],
        },
    )
    return run_id


def _print_summary(result: dict, source: str, strategy_name: str):
    print(f"账户级回测 [{source}] {strategy_name}")
    print(f"区间: {result['date_from']} ~ {result['date_to']}")
    print(f"初始资金: {result['initial_cash']:.2f}")
    print(f"最终权益: {result['final_equity']:.2f}")
    print(f"累计收益: {result['total_return']:+.2f}%")
    print(f"最大回撤: {result['max_drawdown']:+.2f}%")
    if result["win_rate"] is None:
        print("胜率: —")
    else:
        print(f"胜率: {result['win_rate']:.1f}%")
    print(f"交割单: {result['trade_count']} 笔")
    mode = result.get("ranking_mode")
    if mode:
        print(f"排序模式: {mode}（{RANKING_MODE_LABELS.get(mode, mode)}）")
    fallbacks = {day: reason for day, reason in (result.get("fallback_reasons") or {}).items()
                 if reason}
    if fallbacks:
        reasons = "、".join(sorted(set(fallbacks.values())))
        print(f"候选池回退: {len(fallbacks)} 天（{reasons}）")
    for note in result.get("priority_degraded") or []:
        print(f"⚠️ {note}")
    suspended = result.get("suspended_codes") or []
    if suspended:
        print(f"停牌跳过: {len(suspended)} 只 / {result.get('suspended_days_count', 0)} 天"
              f"（{'、'.join(suspended)}）")
    for note in result.get("data_degraded") or []:
        print(f"⚠️ {note}")
    print(f"run_id: {result['run_id']}")
