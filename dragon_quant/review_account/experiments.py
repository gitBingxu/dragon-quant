"""账户对照实验编排（A9/A10 的用户可用入口，main R1/R2 返工）。

设计约束：
  - **不复制**买卖/撮合算法：全部 variant 复用 ``AccountSimulator``；
  - **真正冻结上游输入**：实验开始前把 prep 快照、龙头池、竞价行、情绪、因子模型
    版本与本地行情内容读入内存并计算内容指纹；variant 只在这些冻结输入上运行；
  - 增量 run 继承冻结的期初状态 / base_equity / 峰值 / 累计已实现盈亏；
  - 只允许下游 eligible 集合随 variant 变化，上游候选母集与行情 codes 同源；
  - 训练/验证选型，test 只对 baseline 与选中项运行；压力测试同样只对二者；
  - 缺模型/因子覆盖/本地行情不足的 variant 返回 ``insufficient_data``，不回退；
  - 参数与 baseline 相同的 variant 标 ``no_change``，不伪造消融差异；
  - 结果描述性、不自动推广。
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import replace
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence

from dragon_quant.review_account.evaluation import metrics
from dragon_quant.review_account.market import DataCoverageError
from dragon_quant.review_account.models import (
    SELL_RULE_NAMES, AccountState, Position, StrategyConfig,
)
from dragon_quant.review_account.simulator import AccountSimulator

EXPERIMENT_VERSION = "account_experiment_v3"
MIN_CLOSED_TRADES = 10
FACTOR_BUILD_COMPLETION_MIN = 0.95
HOLDOUT_MAX_DRAWDOWN = -15.0

__all__ = [
    "EXPERIMENT_VERSION",
    "build_variants",
    "freeze_upstream",
    "run_experiments",
    "seed_from_run",
]

VARIANT_LABELS = {
    "baseline": "基线（冻结参数）",
    "drop_prep_filter": "去掉 prep 资格过滤",
    "drop_auction_ranking": "去掉 auction 排序",
    "drop_auction_veto": "去掉 auction 风险否决",
    "drop_divergence_buy": "关闭分歧买点",
    "factor_model_on": "开启因子排序模型",
    "stress_double_slippage": "基线 + 双倍滑点",
}


def build_variants(cfg: StrategyConfig,
                   sell_rules: Optional[list[str]] = None) -> dict[str, StrategyConfig]:
    """按冻结 cfg 构造消融 variant（不改变任何生产默认阈值）。"""
    variants: dict[str, StrategyConfig] = {"baseline": cfg}
    variants["drop_prep_filter"] = replace(
        cfg, prep_verdict_filter_enabled=False,
        research_prep_bypass=True)
    variants["drop_auction_ranking"] = replace(cfg, auction_ranking_enabled=False)
    variants["drop_auction_veto"] = replace(cfg, auction_veto_enabled=False)
    variants["drop_divergence_buy"] = replace(cfg, divergence_enabled=False)
    variants["factor_model_on"] = replace(
        cfg, factor_ranking_enabled=True,
        factor_model_name=cfg.factor_model_name or "equal_rank_v1")
    variants["stress_double_slippage"] = replace(
        cfg, buy_slippage=cfg.buy_slippage * 2,
        sell_slippage=cfg.sell_slippage * 2)
    for name in sell_rules or []:
        if name not in SELL_RULE_NAMES:
            continue
        toggles = {**cfg.sell_rule_toggles, name: False}
        variants[f"sell_rule_{name}_off"] = replace(
            cfg, sell_rule_toggles=toggles)
    return variants


def seed_from_run(run: Mapping[str, Any]) -> Optional[dict]:
    """从**批次自身冻结的期初字段**构造实验 seed（禁止用期末快照倒放）。

    使用 ``start_positions`` / ``start_cash``（或 start_equity−盯市价值）/
    ``start_peak_equity`` / ``start_consecutive_losses`` / ``start_realized_pnl``。
    任一必需信息缺失时抛 ``ValueError``（调用方按不可复现处理），绝不读取
    run.date_to 的期末快照，也不用前段之后的已平仓记录推断期初风险。
    """
    if not run:
        return None
    start_equity = run.get("start_equity")
    if start_equity is None:
        start_equity = run.get("base_equity")
    start_positions = run.get("start_positions") or []
    if (run.get("base_run_id") and not run.get("start_state_recorded")
            and not start_positions):
        # 旧增量 run 只存 base_equity、没冻结 start_positions：无法区分“空仓”
        # 与“历史持仓缺失”，按不可复现处理，绝不按空期初伪造。
        raise ValueError("旧批次缺少 start_positions 冻结记录，不可复现")
    if not start_positions and start_equity is None:
        # 全量 run：从 initial_cash 空账户起跑。
        return None
    if start_equity is None:
        raise ValueError("缺少期初权益（start_equity/base_equity），不可复现")
    marks = run.get("start_marks") or {}
    positions: list[Position] = []
    missing_marks = False
    for raw in start_positions if isinstance(start_positions, list) else []:
        if not isinstance(raw, Mapping):
            continue
        data = {k: v for k, v in raw.items()
                if k in Position.__dataclass_fields__}
        pos = Position(**data)
        mark = pos.mark
        if mark is None:
            mark = marks.get(pos.code)
            pos.mark = mark
        if mark is None:
            missing_marks = True
        pos.inherited = True
        # 本实验的期初状态就是该 run 的 period start：覆盖为当前 qty/mark/cost，
        # 不能沿用 base run 自己的 period_start（那是更早一层的期初）。
        pos.period_start_qty = pos.qty
        pos.period_start_mark = mark
        pos.period_start_cost = pos.cost
        pos.period_start_realized_pnl = pos.realized_pnl
        positions.append(pos)

    cash = run.get("start_cash")
    if cash is None:
        if missing_marks:
            raise ValueError("期初现金未冻结且持仓缺 mark，不可复现")
        cash = float(start_equity) - sum(
            p.qty * float(p.mark) for p in positions)
    peak = run.get("start_peak_equity")
    quality = "precise"
    if peak is None:
        peak = float(start_equity)
        quality = "legacy_approximation"
    if missing_marks:
        quality = "unknown"
    consecutive = run.get("start_consecutive_losses")
    state = AccountState(
        cash=float(cash),
        positions=positions,
        consecutive_losses=int(consecutive or 0),
        peak_equity=float(peak),
    )
    return {
        "state": state,
        "base_equity": float(start_equity),
        "peak_equity": float(peak),
        "start_realized_pnl": float(run.get("start_realized_pnl") or 0.0),
        "attribution_quality": quality,
        "seed_source": "run_start_state",
    }


def _canonical(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)


def _clamp_sentiment(snapshot: Mapping | None) -> Optional[float]:
    if not isinstance(snapshot, Mapping):
        return None
    sentiment = snapshot.get("sentiment")
    if not isinstance(sentiment, Mapping):
        return None
    value = sentiment.get("position_pct")
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, min(1.0, number))


def freeze_upstream(*, cfg: StrategyConfig, calendar: Sequence[str],
                    date_from: str, date_to: str,
                    snapshot_reader: Callable[[str], tuple],
                    dragon_loader: Callable[..., list],
                    auction_reader: Callable[[str], list],
                    bars: Optional[Mapping[str, Sequence[Mapping]]] = None,
                    bars_getter: Optional[Callable[[list[str]], Mapping]] = None,
                    model_snapshot: Optional[Mapping] = None,
                    factor_reader: Optional[Callable] = None,
                    extra_codes: Optional[Iterable[str]] = None,
                    lookback_days: int = 0) -> dict:
    """把一次实验需要的上游输入全部读入内存并冻结。

    ``snapshot_reader(day) -> (snapshot, snapshot_date, reason)`` 由调用方注入
    （通常是真实 ``prep_snapshot_for`` 的**一次性**读取结果）；variant 运行期
    只消费本结构，不再读活动仓储。
    """
    days = sorted({str(day) for day in calendar if day})
    window_start = days[0] if days else date_from
    if lookback_days:
        # candidate lookback 由调用方裁剪后的 calendar 已覆盖；这里只保证覆盖区间。
        window_start = min(window_start, date_from)
    dates = [day for day in days if window_start <= day <= date_to]
    snapshots: dict[str, dict] = {}
    snapshot_meta: dict[str, Any] = {}
    sentiments: dict[str, Optional[float]] = {}
    dragons: dict[str, list] = {}
    auctions: dict[str, list] = {}
    for day in dates:
        snap, snapshot_date, reason = snapshot_reader(day)
        snapshots[day] = snap
        snapshot_meta[day] = {"snapshot_date": snapshot_date,
                              "reason": reason}
        sentiments[day] = _clamp_sentiment(snap)
        try:
            dragons[day] = list(dragon_loader(day, top_n=None,
                                              source=cfg.source) or [])
        except Exception as exc:  # noqa: BLE001 - 冻结失败显式记录
            dragons[day] = []
            snapshot_meta[day]["dragon_error"] = f"{type(exc).__name__}: {exc}"
        try:
            auctions[day] = list(auction_reader(day) or [])
        except Exception as exc:  # noqa: BLE001
            auctions[day] = []
            snapshot_meta[day]["auction_error"] = f"{type(exc).__name__}: {exc}"

    codes = sorted({
        str(item.get("code"))
        for pool in dragons.values() for item in pool if item.get("code")
    } | {str(code) for code in (extra_codes or []) if code} | {
        str(item.get("code"))
        for snap in snapshots.values()
        if isinstance(snap, Mapping)
        for item in (snap.get("review_candidates") or [])
        if isinstance(item, Mapping) and item.get("code")
    } | {
        str(item.get("code")) for rows in auctions.values()
        for item in rows if isinstance(item, Mapping) and item.get("code")
    })
    if bars_getter is not None:
        loaded_bars = bars_getter(codes) or {}
    else:
        loaded_bars = bars or {}
    frozen_bars = {code: list(loaded_bars.get(code) or []) for code in codes}

    factor_values: dict = {}
    factor_completion = None
    factor_names: list[str] = []
    factor_versions: dict[str, str] = {}
    if isinstance(model_snapshot, Mapping):
        raw_versions = model_snapshot.get("factor_versions")
        if isinstance(raw_versions, Mapping):
            factor_versions = {str(k): str(v) for k, v in raw_versions.items()
                               if isinstance(k, str) and isinstance(v, str)}
            factor_names = sorted(factor_versions)
    if factor_reader is not None and factor_names:
        # 只读一次：所有 split / stress 复用同一批因子值；运行期仓储变动不影响。
        factor_values = factor_reader(tuple(dates), tuple(codes),
                                      tuple(factor_names), dict(factor_versions))
        period_dates = [day for day in dates if date_from <= day <= date_to]
        if period_dates:
            factor_completion = (
                sum(1 for day in period_dates if factor_values.get(day))
                / len(period_dates))
    versions = sorted({
        str(snap.get("version")) for snap in snapshots.values()
        if isinstance(snap, Mapping) and snap.get("version") is not None
    })
    fingerprint = hashlib.sha256(_canonical({
        "snapshots": snapshots,
        "snapshot_meta": snapshot_meta,
        "dragons": dragons,
        "auctions": auctions,
        "sentiments": sentiments,
        "bars": frozen_bars,
        "versions": versions,
        "model": model_snapshot,
        "factor_values": factor_values,
        "factor_completion": factor_completion,
        "config_upstream": {
            "source": cfg.source,
            "candidate_top_n": cfg.candidate_top_n,
            "candidate_lookback_days": cfg.candidate_lookback_days,
            "min_score": cfg.min_score,
            "min_amount": cfg.min_amount,
            "min_turnover": cfg.min_turnover,
        },
    }).encode("utf-8")).hexdigest()
    return {
        # 实验区间（train/validation/test 只看这段）。
        "calendar": [day for day in days if date_from <= day <= date_to],
        # 数据日历保留 lookback 日（candidate_dates / 预热 / T-1 解析需要）。
        "data_calendar": [day for day in days if day <= date_to],
        "snapshots": snapshots,
        "snapshot_meta": snapshot_meta,
        "sentiments": sentiments,
        "dragons": dragons,
        "auctions": auctions,
        "bars": frozen_bars,
        "codes": codes,
        "versions": versions,
        "model_snapshot": dict(model_snapshot) if model_snapshot else None,
        "factor_values": factor_values,
        "factor_names": factor_names,
        "factor_versions": factor_versions,
        "factor_completion": factor_completion,
        "content_sha256": fingerprint,
    }


def _frozen_snapshot_loader(frozen: Mapping[str, Any]):
    def load(day: str) -> tuple:
        meta = frozen["snapshot_meta"].get(day) or {}
        return (frozen["snapshots"].get(day),
                meta.get("snapshot_date"), meta.get("reason"))
    return load


def _frozen_dragon_loader(frozen: Mapping[str, Any]):
    def load(date: str, top_n=None, source: str = "v2") -> list:
        return list(frozen["dragons"].get(date) or [])
    return load


def _frozen_auction_loader(frozen: Mapping[str, Any]):
    def load(day: str, source: str = "v2") -> list:
        return list(frozen["auctions"].get(day) or [])
    return load


def _frozen_sentiment_loader(frozen: Mapping[str, Any]):
    def load(day: str) -> Optional[float]:
        return frozen["sentiments"].get(day)
    return load


def _frozen_factor_loader(frozen: Mapping[str, Any]):
    values = frozen.get("factor_values") or {}

    def load(trade_date: str, names, codes, versions, *, db_path=None) -> dict:
        day_values = values.get(trade_date) or {}
        wanted = set(codes)
        return {code: dict(day_values.get(code) or {})
                for code in wanted if day_values.get(code)}

    return load


def _split(days: list[str]) -> dict[str, tuple[str, str]]:
    if len(days) < 10:
        return {"all": (days[0], days[-1])}
    a = max(1, int(len(days) * 0.6))
    b = max(a + 1, int(len(days) * 0.8))
    splits = {
        "train": (days[0], days[a - 1]),
        "validation": (days[a], days[b - 1]),
    }
    if b < len(days):
        splits["test"] = (days[b], days[-1])
    else:
        splits["test"] = (days[b - 1], days[-1])
    return splits


def _run_variant(cfg: StrategyConfig, data, date_from: str, date_to: str,
                 frozen: Mapping[str, Any], seed: Optional[Mapping],
                 factor_model=None, runtime=None, runtime_resolver=None,
                 runtime_factory=None) -> tuple[Optional[dict], Optional[str]]:
    created = None
    try:
        # 独立模拟必须使用独立策略实例：worker/有状态策略不能跨 split 复用。
        if runtime_factory is not None:
            created = runtime_factory()
            runtime = created
        # 每个 variant 必须从同一冻结期初状态独立起跑：深拷贝，绝不共享可变对象。
        initial_state = copy.deepcopy(seed.get("state")) if seed else None
        simulator = AccountSimulator(
            cfg, data=data,
            initial_state=initial_state,
            base_equity=seed.get("base_equity") if seed else None,
            peak_equity=seed.get("peak_equity") if seed else None,
            start_realized_pnl=seed.get("start_realized_pnl", 0.0) if seed else 0.0,
            candidate_source=_frozen_dragon_loader(frozen),
            auction_source=_frozen_auction_loader(frozen),
            snapshot_loader=_frozen_snapshot_loader(frozen),
            sentiment_loader=_frozen_sentiment_loader(frozen),
            factor_loader=_frozen_factor_loader(frozen),
            factor_model=factor_model,
            runtime=runtime,
            runtime_resolver=runtime_resolver,
        )
        result = simulator.run(date_from, date_to)
    except DataCoverageError as exc:
        return None, f"data_coverage: {exc}"
    except ValueError as exc:
        return None, f"invalid_variant: {exc}"
    finally:
        if created is not None:
            closer = getattr(created, "close", None)
            if callable(closer):
                closer()
    if (cfg.factor_ranking_enabled
            and result.get("factor_degraded_days")):
        return None, ("factor_degraded_in_run: "
                      f"{len(result['factor_degraded_days'])} 日排序降级")
    return metrics(result), None


def _factor_gate(cfg: StrategyConfig, days: Sequence[str],
                 frozen: Mapping[str, Any]):
    if not getattr(cfg, "factor_ranking_enabled", False):
        return cfg, None, None
    snapshot = frozen.get("model_snapshot")
    if not snapshot:
        return None, "factor_model_missing: 无冻结模型快照，不降级假评估", None
    version = str(snapshot.get("model_version") or "")
    if not version:
        return None, "factor_model_missing: 模型快照缺 model_version", None
    completion = frozen.get("factor_completion")
    if completion is None or completion < FACTOR_BUILD_COMPLETION_MIN:
        return None, (f"factor_build_completion: "
                      f"{(completion if completion is not None else 0.0):.0%} "
                      f"< {FACTOR_BUILD_COMPLETION_MIN:.0%}"), None
    try:
        from dragon_quant.infra.factor.decision import EqualWeightRankModel
        model = EqualWeightRankModel.from_snapshot(snapshot)
    except Exception as exc:  # noqa: BLE001
        return None, f"factor_model_invalid: {type(exc).__name__}: {exc}", None
    return replace(cfg, factor_model_version=version), None, model


def _same_params(left: StrategyConfig, right: StrategyConfig) -> bool:
    return _canonical(left.to_json_dict()) == _canonical(right.to_json_dict())


def _holds(metric: Optional[Mapping], key: str = "total_return") -> Optional[float]:
    if not metric:
        return None
    value = metric.get(key)
    return float(value) if isinstance(value, (int, float)) else None


def run_experiments(*, run: Mapping[str, Any], cfg: StrategyConfig, data,
                    frozen: Mapping[str, Any],
                    seed: Optional[Mapping] = None,
                    sell_rules: Optional[list[str]] = None,
                    runtime=None, runtime_resolver=None,
                    strategy_ref: Optional[Mapping] = None,
                    parameter_variants: Optional[list[Mapping]] = None,
                    runtime_factory=None, variant_runtime_factory=None) -> dict:
    """对一条冻结 run 运行 variants，返回可审计报告（无 I/O 写）。"""
    if not run:
        raise ValueError("基准 run 不存在")
    days = list(frozen.get("calendar") or [])
    if not days:
        return {"status": "insufficient_data", "reason": "no_trading_days",
                "experiment_version": EXPERIMENT_VERSION, "promoted": False}
    variants = build_variants(cfg, sell_rules)
    splits = _split(days)
    report: dict = {
        "status": "evaluated",
        "experiment_version": EXPERIMENT_VERSION,
        "base_run_id": run.get("id"),
        "period": {"date_from": days[0], "date_to": days[-1],
                   "trading_days": len(days),
                   "source_run_from": run.get("date_from"),
                   "source_run_to": run.get("date_to")},
        "frozen": {
            "strategy_name": cfg.strategy_name,
            "initial_cash": cfg.initial_cash,
            "start_equity": (seed.get("base_equity") if seed else None)
                            or run.get("start_equity") or run.get("initial_cash"),
            "strategy_params": cfg.to_json_dict(),
            "content_sha256": frozen.get("content_sha256"),
            "snapshot_versions": list(frozen.get("versions") or []),
            "codes_count": len(frozen.get("codes") or []),
            "seed_inherited": bool(seed),
            "attribution_quality": (seed or {}).get("attribution_quality"),
            "model_snapshot": (frozen.get("model_snapshot") or {}).get("model_version"),
            "factor_completion": frozen.get("factor_completion"),
            "factor_names": list(frozen.get("factor_names") or []),
            "market_basis": "local_daily_bars_no_intraday",
            "strategy_ref": dict(strategy_ref or {}),
        },
        "upstream": {
            "candidate_universe_sha256": frozen.get("content_sha256"),
            "basis": "frozen prep snapshots + dragon pools + auction rows + sentiment + bars",
        },
        "splits": {name: list(window) for name, window in splits.items()},
        "split_seed_policy": "independent_restart_from_period_start",
        "split_historical_continuity": False,
        "selection_rule": "max validation total_return; closed>=10; drawdown>=-15",
        "minimum_closed_trades": MIN_CLOSED_TRADES,
        "trial_count": len(variants),
        "promoted": False,
        "comparability": "same_frozen_upstream_universe",
        "variants": {},
    }
    if strategy_ref and str(strategy_ref.get("strategy_id", "")).startswith("user:"):
        report["user_strategy_note"] = (
            "该基准 run 使用用户 workspace 策略；variants 的 sell-rule/cfg 消融不改变用户策略"
            "决策逻辑，报告主要用于时间切分与成本压力评估；用户策略版本/参数见 frozen.strategy_ref。")
    if not seed and run.get("base_run_id"):
        # 增量基准但缺期初状态：不得用空账户 initial_cash 冒充续算。
        return {
            **report,
            "status": "insufficient_data",
            "reason": "incremental_seed_missing",
            "comparability": "historical_not_reproducible",
        }

    for name, variant in variants.items():
        entry: dict = {"label": VARIANT_LABELS.get(name, name)}
        if name != "baseline" and _same_params(variant, cfg):
            entry.update({"status": "no_change",
                          "reason": "与冻结 baseline 参数相同，无可消融差异"})
            report["variants"][name] = entry
            continue
        gated, reason, factor_model = _factor_gate(variant, days, frozen)
        if reason or gated is None:
            entry.update({"status": "insufficient_data", "reason": reason})
            report["variants"][name] = entry
            continue
        block: dict = {"status": "evaluated"}
        for split_name in ("train", "validation"):
            if split_name not in splits:
                continue
            start, end = splits[split_name]
            result, error = _run_variant(gated, data, start, end, frozen, seed,
                                         factor_model=factor_model,
                                         runtime=runtime if runtime_factory is None else None,
                                         runtime_resolver=runtime_resolver,
                                         runtime_factory=runtime_factory)
            if error:
                block = {"status": "insufficient_data", "reason": error}
                break
            block[split_name] = result
        if block.get("status") == "evaluated":
            validation = block.get("validation") or block.get("train") or {}
            block["eligible"] = (
                name == "baseline" or
                ((validation.get("closed_count") or 0) >= MIN_CLOSED_TRADES
                 and (validation.get("max_drawdown") or 0) >= HOLDOUT_MAX_DRAWDOWN))
            if name != "baseline" and not block["eligible"]:
                block["status"] = "insufficient_evidence"
                block["reason"] = (
                    f"validation closed {validation.get('closed_count') or 0}"
                    f" < {MIN_CLOSED_TRADES} 或回撤未过门槛")
        report["variants"][name] = {**entry, **block}

    eligible = [name for name, entry in report["variants"].items()
                if name != "baseline" and entry.get("status") == "evaluated"
                and entry.get("eligible")]
    selected = None
    if eligible:
        selected = max(
            eligible,
            key=lambda name: _holds(
                report["variants"][name].get("validation")) or float("-inf"))
    report["selected"] = selected
    report["selected_params"] = (variants[selected].to_json_dict()
                                 if selected else None)

    # holdout 只给 baseline 与选中项；压力只给二者。
    holdout = [name for name in ("baseline", selected)
               if name and name in variants]
    test_split = splits.get("test")
    for name in holdout:
        variant = variants[name]
        if not test_split:
            report["variants"][name]["test"] = {"status": "no_holdout"}
            continue
        _, _, factor_model = _factor_gate(variant, days, frozen)
        result, error = _run_variant(variant, data, *test_split, frozen, seed,
                                     factor_model=factor_model,
                                     runtime=runtime if runtime_factory is None else None,
                                     runtime_resolver=runtime_resolver,
                                     runtime_factory=runtime_factory)
        report["variants"][name]["test"] = (
            result if not error
            else {"status": "insufficient_data", "reason": error})
        stress = replace(variant, buy_slippage=variant.buy_slippage * 2,
                         sell_slippage=variant.sell_slippage * 2)
        stress_result, stress_error = _run_variant(
            stress, data, *test_split, frozen, seed,
            factor_model=factor_model,
            runtime=runtime if runtime_factory is None else None,
            runtime_resolver=runtime_resolver,
            runtime_factory=runtime_factory)
        report["variants"][name]["stress_test"] = (
            stress_result if not stress_error
            else {"status": "insufficient_data", "reason": stress_error})

    # 相对基线差值（同 test 区间；缺失不伪造）。
    base_ret = _holds(report["variants"]["baseline"].get("test"))
    baseline_val = _holds(report["variants"]["baseline"].get("validation"))
    for name, entry in report["variants"].items():
        if name == "baseline" or entry.get("status") != "evaluated":
            continue
        value = _holds(entry.get("validation"))
        entry["diff_vs_baseline_validation"] = (
            round(value - baseline_val, 4)
            if value is not None and baseline_val is not None else None)
        test_value = _holds(entry.get("test"))
        entry["diff_vs_baseline_test"] = (
            round(test_value - base_ret, 4)
            if test_value is not None and base_ret is not None else None)

    # 用户策略声明参数变体：真正以不同参数构造运行时并跑 validation/test。
    if parameter_variants and variant_runtime_factory is not None:
        for index, params in enumerate(parameter_variants):
            name = f"param_variant_{index}"
            entry: dict = {"label": f"参数变体 {index + 1}", "parameters": dict(params)}
            try:
                probe = variant_runtime_factory(dict(params))
            except ValueError as exc:
                entry.update({"status": "insufficient_data", "reason": str(exc)})
                report["variants"][name] = entry
                report["trial_count"] += 1
                continue
            closer = getattr(probe, "close", None)
            if callable(closer):
                closer()  # 只用于校验参数可构造；实际模拟每次新建实例
            for split_name in ("validation", "test"):
                if split_name not in splits:
                    continue
                start, end = splits[split_name]
                result, error = _run_variant(
                    cfg, data, start, end, frozen, seed,
                    runtime_resolver=runtime_resolver,
                    runtime_factory=lambda p=dict(params): variant_runtime_factory(p))
                entry[split_name] = (result if not error
                                     else {"status": "insufficient_data", "reason": error})
            entry["status"] = ("evaluated"
                               if isinstance(entry.get("validation"), dict)
                               and "status" not in entry["validation"]
                               else "insufficient_data")
            report["variants"][name] = entry
            report["trial_count"] += 1

    evaluated = [name for name, entry in report["variants"].items()
                 if entry.get("status") in ("evaluated", "no_change")]
    report["status"] = "evaluated" if eligible or selected else (
        "insufficient_evidence" if evaluated else "insufficient_data")
    if report["status"] == "insufficient_evidence":
        report["reason"] = "no_variant_passed_selection"
    report["caveats"] = [
        "对照实验只做描述性比较，不自动推广参数、不写回生产策略",
        "所有 variant 在同一冻结上游母集/行情/情绪/模型版本上运行；仅下游 eligible 可不同",
        "行情的本地快照为日 K 兜底（无分时），与实时盘中撮合口径不同",
        "test 区间不参与选型；仅 baseline 与选中项运行 holdout 与双倍滑点压力",
        "旧 run 若缺少期初状态/冻结输入，不作为历史复现，而是基于当前冻结数据的实验基线",
        "train/validation/test 均从本批次期初状态独立重启（非跨区间连续持仓）；"
        "因此是各 sub-period 的对照，不宣称历史连续复现",
    ]
    return report
