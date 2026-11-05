"""研究入口：scan 候选质量（只读）与账户对照实验（用户显式触发）。

边界：
  - scan-quality 只读 ``scans`` / ``scan_stocks`` 与本地 ``stock_daily``，
    不改 dragons 去重与生产候选池；
  - experiments 只在用户点击并 ``confirm=true`` 后运行，冻结指定 run 的
    期间/资金/策略/上游母集；行情只用本地快照，失败返回证据不足；
  - GET 只看已保存报告，任何查看都不隐式重算。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Body, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .. import params
from ..deps import (
    account_repository,
    assets_repository,
    bar_calendar_loader,
    candidate_loader_factory,
    clock_module,
    factor_storage,
    local_bars_loader,
    prep_snapshot_reader,
    scan_repository,
)
from ..errors import ApiError
from ..locks import ACCOUNT_HEAVY_LOCK
from ..presenters import scan_quality as quality_view
from ..presenters.bars_loader import BarsTruncated, load_bars_resilient
from ..presenters.local_account_market import LocalAccountMarket
from ..presenters.local_market import shift_day

router = APIRouter(tags=["research"])

class ExperimentRequest(BaseModel):
    run_id: int
    sell_rules: list[str] = []
    confirm: bool = False


def _iso(raw: str | None, field: str) -> str | None:
    if raw is None or str(raw).strip() == "":
        return None
    try:
        return params.normalize_date(raw)
    except ValueError as exc:
        raise ApiError(400, "invalid_request", f"{field}: {exc}") from exc


def _default_range(clock) -> tuple[str, str]:
    today = clock.now().strftime("%Y-%m-%d")
    start = (datetime.strptime(today, "%Y-%m-%d")
             - timedelta(days=60)).strftime("%Y-%m-%d")
    return start, today


@router.get("/api/research/scan-quality", response_model=None)
def scan_quality(date_from: str | None = None, date_to: str | None = None,
                 scans=Depends(scan_repository),
                 load_bars=Depends(local_bars_loader),
                 load_calendar=Depends(bar_calendar_loader),
                 clock=Depends(clock_module)):
    default_from, default_to = _default_range(clock)
    date_from = _iso(date_from, "date_from") or default_from
    date_to = _iso(date_to, "date_to") or default_to
    if date_from > date_to:
        raise ApiError(400, "invalid_request", "date_from 不能晚于 date_to")

    try:
        dates = scans.list_scan_dates(date_from, date_to)
    except Exception:  # noqa: BLE001 - 空库按无数据返回，不 500
        dates = []
    contributions = {}
    read_errors: list[str] = []
    for day in dates:
        try:
            contributions[day] = scans.list_scan_stock_contributions_by_date(day)
        except Exception as exc:  # noqa: BLE001 - 单日失败不得吞成「无扫描」
            contributions[day] = []
            read_errors.append(f"{day}: {type(exc).__name__}: {exc}")
    # 历史首入参考：读取区间前 60 个自然日内已出现过的 code。
    prior_codes: set[str] = set()
    try:
        prior_dates = scans.list_scan_dates(
            shift_day(date_from, -60), shift_day(date_from, -1))
        for day in prior_dates:
            canonical, _ = quality_view.canonical_candidates(
                scans.list_scan_stock_contributions_by_date(day))
            prior_codes |= {str(row.get("code")) for row in canonical}
    except Exception as exc:  # noqa: BLE001
        read_errors.append(f"prior_history: {type(exc).__name__}: {exc}")
    codes = sorted({
        str(row.get("code"))
        for rows in contributions.values()
        for row in rows if row.get("code")
    } | prior_codes)
    calendar = load_calendar(shift_day(date_from, -10), shift_day(date_to, 40)) or []
    bars = {}
    try:
        loaded = load_bars_resilient(
            load_bars, codes, shift_day(date_from, -10),
            shift_day(date_to, 40)) if codes else {}
        bars = (loaded or {}).get("rows") or {}
    except BarsTruncated:
        return quality_view.build_scan_quality(
            date_from=date_from, date_to=date_to,
            contributions_by_date={}, calendar=calendar, bars={},
            read_errors=read_errors + ["local_bars_truncated"])
    except Exception as exc:  # noqa: BLE001 - 行情缺失按证据不足返回
        read_errors.append(f"local_bars_error: {type(exc).__name__}: {exc}")
    payload = quality_view.build_scan_quality(
        date_from=date_from,
        date_to=date_to,
        contributions_by_date=contributions,
        calendar=calendar,
        bars=bars,
        read_errors=read_errors,
        prior_codes=prior_codes,
    )
    return payload


@router.post("/api/account/experiments", response_model=None)
def create_account_experiments(payload: dict | None = Body(default=None),
                               account=Depends(account_repository),
                               assets=Depends(assets_repository),
                               factor_store=Depends(factor_storage),
                               load_bars=Depends(local_bars_loader),
                               load_calendar=Depends(bar_calendar_loader),
                               loader_factory=Depends(candidate_loader_factory),
                               snapshot_reader=Depends(prep_snapshot_reader)):
    from dragon_quant.review_account import experiments
    from dragon_quant.review_account.models import StrategyConfig

    body = ExperimentRequest(**(payload or {}))
    if not body.confirm:
        raise ApiError(400, "confirmation_required",
                       "对照实验会消耗本地算力，需显式 confirm=true")
    run = account.get_review_account_run(body.run_id)
    if not run:
        return JSONResponse(status_code=404, content={"error": "run not found"})
    if not ACCOUNT_HEAVY_LOCK.acquire(blocking=False):
        return JSONResponse(status_code=409, content={
            "error": "experiment_running",
            "detail": "已有对照实验在运行，请稍后再试",
        })
    try:
        try:
            cfg = StrategyConfig.from_dict({
                **(run.get("strategy_params") or {}),
                "source": run.get("source") or "v2",
                "strategy_name": run.get("strategy_name") or "dragon_pullback_daily",
                "initial_cash": run.get("initial_cash") or 100000.0,
            })
        except (TypeError, ValueError) as exc:
            raise ApiError(400, "invalid_run", f"基准 run 策略参数不可用: {exc}")

        # 增量 run 必须继承冻结期初状态，不得用空账户 initial_cash 冒充续算。
        # 只从本批次自身冻结的期初字段构造 seed；**禁止**读取 run.date_to
        # 的期末快照倒放过去（会构成前视，main D1）。
        seed = None
        if (run.get("start_positions") or run.get("base_run_id")
                or run.get("start_equity") is not None
                and run.get("base_equity") is not None):
            try:
                seed = experiments.seed_from_run(run)
            except ValueError as exc:
                raise ApiError(
                    400, "incremental_seed_unavailable",
                    f"无法从批次期初字段恢复状态，不能冒充续算实验: {exc}")
            if seed is None:
                raise ApiError(
                    400, "incremental_seed_unavailable",
                    "增量批次缺少可验证的期初状态")

        warmup = max(60, (cfg.candidate_lookback_days + 20) * 2)
        start = shift_day(run["date_from"], -warmup)
        end = shift_day(run["date_to"], 30)
        calendar = load_calendar(start, end) or []
        if not calendar:
            return JSONResponse(status_code=422, content={
                "error": "local_calendar_missing",
                "detail": "本地确认交易日历为空，无法建立冻结实验基线",
            })

        model_snapshot = None
        try:
            model_snapshot = factor_store.get_model_snapshot(
                cfg.factor_model_name, cfg.factor_model_version or None)
        except Exception:  # noqa: BLE001 - 模型读取失败 → factor variant 证据不足
            model_snapshot = None

        def _bars_getter(codes: list[str]):
            if not codes:
                return {}
            loaded = load_bars(codes, start, end) or {}
            if loaded.get("truncated"):
                raise ValueError("local_bars_truncated")
            return loaded.get("rows") or {}

        def _factor_reader(dates, codes, names, versions):
            # 一次性冻结：所有 split / stress 复用；之后仓储变化不影响结果。
            payload: dict[str, dict] = {}
            for day in dates:
                result = factor_store.get_day_values(
                    day, names=list(names), codes=list(codes),
                    versions=dict(versions))
                payload[day] = dict((result or {}).get("values") or {})
            return payload

        frozen = experiments.freeze_upstream(
            cfg=cfg,
            calendar=calendar,
            date_from=run["date_from"],
            date_to=run["date_to"],
            snapshot_reader=lambda day: snapshot_reader(
                day, cfg.source, calendar),
            dragon_loader=loader_factory(cfg.source),
            auction_reader=lambda day: assets.list_auction_snapshots(
                day, source=cfg.source),
            bars_getter=_bars_getter,
            model_snapshot=model_snapshot,
            factor_reader=_factor_reader,
            extra_codes=[p.get("code") for p in (run.get("start_positions") or [])],
        )
        data = LocalAccountMarket(frozen["bars"], frozen["data_calendar"])
        report = experiments.run_experiments(
            run=run, cfg=cfg, data=data, frozen=frozen, seed=seed,
            sell_rules=body.sell_rules)
        experiment_id = account.save_review_account_experiment(
            body.run_id,
            experiment_version=experiments.EXPERIMENT_VERSION,
            status=report.get("status"),
            request={"run_id": body.run_id, "sell_rules": body.sell_rules,
                     "confirm": True,
                     "frozen_sha256": frozen.get("content_sha256")},
            report=report,
        )
        report["experiment_id"] = experiment_id
        return JSONResponse(status_code=201, content={"data": report})
    except ValueError as exc:
        # 本地行情截断/冻结失败：明确证据不足，不返回部分统计。
        raise ApiError(422, "experiment_inputs_unavailable", str(exc))
    finally:
        ACCOUNT_HEAVY_LOCK.release()


@router.get("/api/account/experiments", response_model=None)
def list_account_experiments(run_id: str | None = None,
                             account=Depends(account_repository)):
    if not run_id:
        raise ApiError(400, "invalid_request", "missing run_id")
    try:
        value = int(run_id)
    except ValueError as exc:
        raise ApiError(400, "invalid_request", "invalid run_id") from exc
    return {"data": account.query_review_account_experiments(value)}


__all__: list[Any] = ["router"]
