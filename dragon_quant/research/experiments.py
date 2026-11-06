"""共享账户对照实验用例（Web 路由与 CLI 共用冻结/单飞/保存逻辑）。

边界：核心算法仍在 ``review_account.experiments``；本模块只做输入校验、期初
状态恢复、上游冻结、单飞（进程内 + 数据目录进程级）与显式保存。``list`` /
``show`` 走只读仓储，任何查看都不重算。
"""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Callable, Iterable, Optional

from .envelope import InvalidRequest, envelope, normalize_int


class ConfirmationRequired(InvalidRequest):
    code = "confirmation_required"

try:  # POSIX
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - Windows
    _fcntl = None
try:  # Windows
    import msvcrt as _msvcrt
except ImportError:  # pragma: no cover - POSIX
    _msvcrt = None


class ExperimentBusy(RuntimeError):
    def __init__(self, kind: str = "process"):
        super().__init__("已有对照实验在运行，请稍后再试")
        self.kind = kind


class ExperimentInputError(ValueError):
    """输入/冻结/行情不可用；携带稳定的错误码与 HTTP 状态供两个消费者映射。"""

    def __init__(self, message: str, *, code: str = "experiment_inputs_unavailable",
                 status: int = 422):
        super().__init__(message)
        self.code = code
        self.status = status


@contextlib.contextmanager
def process_lock(path: Path):
    """数据目录中的进程级互斥（flock / msvcrt）；异常与退出自动释放。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "a+b")
    try:
        if _fcntl is not None:
            try:
                _fcntl.flock(handle.fileno(), _fcntl.LOCK_EX | _fcntl.LOCK_NB)
            except OSError as exc:
                raise ExperimentBusy("process") from exc
        elif _msvcrt is not None:  # pragma: no cover - Windows
            try:
                _msvcrt.locking(handle.fileno(), _msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise ExperimentBusy("process") from exc
        else:  # pragma: no cover - 无可用原语
            raise ExperimentBusy("process")
        yield
    finally:
        try:
            if _fcntl is not None:
                _fcntl.flock(handle.fileno(), _fcntl.LOCK_UN)
            elif _msvcrt is not None:  # pragma: no cover - Windows
                try:
                    _msvcrt.locking(handle.fileno(), _msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
        finally:
            handle.close()


def validate_sell_rules(names: Optional[Iterable[str]]) -> list[str]:
    from dragon_quant.review_account.models import SELL_RULE_NAMES

    result: list[str] = []
    for raw in names or []:
        name = str(raw).strip()
        if not name:
            continue
        if name not in SELL_RULE_NAMES:
            raise InvalidRequest(
                f"未知 sell-rule：{name}；可选：{', '.join(SELL_RULE_NAMES)}")
        if name not in result:
            result.append(name)
    return result


def _seed(run: dict):
    from dragon_quant.review_account import experiments

    if (run.get("start_positions") or run.get("base_run_id")
            or run.get("start_equity") is not None
            and run.get("base_equity") is not None):
        try:
            seed = experiments.seed_from_run(run)
        except ValueError as exc:
            raise ExperimentInputError(
                f"无法从批次期初字段恢复状态，不能冒充续算实验: {exc}",
                code="incremental_seed_unavailable", status=400) from exc
        if seed is None:
            raise ExperimentInputError(
                "增量批次缺少可验证的期初状态",
                code="incremental_seed_unavailable", status=400)
        return seed
    return None


def run_account_experiment(*, run_id: int, sell_rules: Optional[list[str]],
                           confirm: bool, account, factor_store, load_bars,
                           load_calendar, loader_factory, snapshot_reader, assets,
                           save_experiment: Callable[..., int],
                           heavy_lock=None, lock_path: Optional[Path] = None) -> dict:
    from dragon_quant.review_account import experiments
    from dragon_quant.review_account.models import StrategyConfig
    from .local_account_market import LocalAccountMarket

    if not confirm:
        raise ConfirmationRequired("对照实验会消耗本地算力，需显式 confirm=true")
    sell_rules = validate_sell_rules(sell_rules)
    value = normalize_int(run_id, "--run-id")
    run = account.get_review_account_run(value)
    if not run:
        raise ExperimentInputError("run not found", code="run_not_found", status=404)

    acquired = False
    if heavy_lock is not None:
        if not heavy_lock.acquire(blocking=False):
            raise ExperimentBusy("thread")
        acquired = True
    try:
        with (process_lock(lock_path) if lock_path is not None
              else contextlib.nullcontext()):
            try:
                cfg = StrategyConfig.from_dict({
                    **(run.get("strategy_params") or {}),
                    "source": run.get("source") or "v2",
                    "strategy_name": run.get("strategy_name") or "dragon_pullback_daily",
                    "initial_cash": run.get("initial_cash") or 100000.0,
                })
            except (TypeError, ValueError) as exc:
                raise ExperimentInputError(
                    f"基准 run 策略参数不可用: {exc}",
                    code="invalid_run", status=400) from exc
            seed = _seed(run)
            warmup = max(60, (cfg.candidate_lookback_days + 20) * 2)
            start = _shift(run["date_from"], -warmup)
            end = _shift(run["date_to"], 30)
            calendar = load_calendar(start, end) or []
            if not calendar:
                raise ExperimentInputError(
                    "本地确认交易日历为空，无法建立冻结实验基线",
                    code="local_calendar_missing", status=422)
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
                payload: dict[str, dict] = {}
                for day in dates:
                    result = factor_store.get_day_values(
                        day, names=list(names), codes=list(codes),
                        versions=dict(versions))
                    payload[day] = dict((result or {}).get("values") or {})
                return payload

            try:
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
                    extra_codes=[p.get("code")
                                 for p in (run.get("start_positions") or [])],
                )
                data = LocalAccountMarket(frozen["bars"], frozen["data_calendar"])
                report = experiments.run_experiments(
                    run=run, cfg=cfg, data=data, frozen=frozen, seed=seed,
                    sell_rules=sell_rules)
            except ValueError as exc:
                raise ExperimentInputError(str(exc)) from exc
            experiment_id = save_experiment(
                value,
                experiment_version=experiments.EXPERIMENT_VERSION,
                status=report.get("status"),
                request={"run_id": value, "sell_rules": sell_rules,
                         "confirm": True,
                         "frozen_sha256": frozen.get("content_sha256")},
                report=report,
            )
            report["experiment_id"] = experiment_id
            return report
    finally:
        if acquired:
            heavy_lock.release()


def _shift(day: str, days: int) -> str:
    from datetime import datetime, timedelta

    return (datetime.strptime(day, "%Y-%m-%d") + timedelta(days=days)).strftime(
        "%Y-%m-%d")


# ─── 只读用例 ──────────────────────────────────────────────────────────────


def list_experiments(run_id: int, *, limit: int | None = None,
                     cursor: str | None = None) -> dict:
    from dragon_quant.review_account import research_reads

    from .envelope import decode_cursor, encode_cursor
    from .query import read_with_status

    value = normalize_int(run_id, "--run-id")
    if value is None:
        raise InvalidRequest("缺少 --run-id")
    try:
        size = 100 if limit is None else int(limit)
    except (TypeError, ValueError) as exc:
        raise InvalidRequest(f"--limit 必须是整数：{limit!r}") from exc
    if size < 1 or size > 1000:
        raise InvalidRequest("--limit 范围 1..1000")
    filters = {"run_id": value}
    offset = decode_cursor(cursor, dataset="experiment_list",
                           filters=filters, fields=None)
    rows, reason, warnings = read_with_status(
        research_reads.experiments, run_id=value, limit=size + 1, offset=offset)
    if reason:
        return envelope(
            command="research experiments", request=filters, data=[],
            meta={"data_quality": "degraded", "empty_reason": reason,
                  "returned": 0, "truncated": False, "next_cursor": None},
            warnings=warnings)
    truncated = len(rows) > size
    page = rows[:size]
    corrupt = sum(1 for row in page if row.get("_corrupt"))
    data = [{key: value for key, value in row.items()
             if key not in ("report", "_corrupt")}
            | {"report_omitted": True} for row in page]
    next_cursor = (encode_cursor(dataset="experiment_list", filters=filters,
                                 fields=None, offset=offset + size)
                   if truncated else None)
    issue_warnings = warnings + [
        "列表默认省略大 report；用 research experiments show 读取完整报告"]
    if corrupt:
        issue_warnings.append(
            f"corrupt_json_rows={corrupt}: 损坏报告字段按默认值隐藏")
    return envelope(
        command="research experiments",
        request=filters,
        data=data,
        meta={"data_quality": ("degraded" if corrupt else
                               ("ok" if data else "empty")),
              "empty_reason": None if data else "no_experiments_for_run",
              "returned": len(data), "truncated": truncated,
              "next_cursor": next_cursor},
        warnings=issue_warnings)


def show_experiment(experiment_id: int, *, run_id: int | None = None) -> dict:
    from dragon_quant.review_account import research_reads

    value = normalize_int(experiment_id, "--experiment-id")
    if value is None:
        raise InvalidRequest("缺少 --experiment-id")
    filters = {"experiment_id": value}
    if run_id is not None:
        filters["run_id"] = normalize_int(run_id, "--run-id")
    from .query import read_with_status

    rows, reason, warnings = read_with_status(
        research_reads.experiments, run_id=filters.get("run_id"),
        experiment_id=value, limit=1, offset=0)
    if reason:
        return envelope(
            command="research experiments",
            request={"experiment_id": value, "run_id": filters.get("run_id")},
            data=None, ok=True,
            meta={"data_quality": "degraded", "empty_reason": reason,
                  "returned": 0, "truncated": False, "next_cursor": None},
            warnings=warnings)
    if not rows:
        return envelope(
            command="research experiments",
            request={"experiment_id": value, "run_id": filters.get("run_id")},
            data=None, ok=True,
            meta={"data_quality": "empty", "empty_reason": "experiment_not_found",
                  "returned": 0, "truncated": False, "next_cursor": None})
    shown = dict(rows[0])
    corrupt_fields = shown.pop("_corrupt", None)
    if corrupt_fields:
        shown["corrupt_json_fields"] = corrupt_fields
    return envelope(
        command="research experiments",
        request={"experiment_id": value, "run_id": filters.get("run_id")},
        data=shown,
        meta={"data_quality": ("degraded" if rows[0].get("_corrupt") else "ok"),
              "returned": 1, "truncated": False, "next_cursor": None},
        warnings=warnings + ["只读已存报告，不重算"]
        + (["corrupt_json_fields: 报告/请求 JSON 损坏，按默认值展示"]
           if rows[0].get("_corrupt") else []))


def experiment_run(run_id: int, *, confirm: bool, sell_rules: Optional[list[str]],
                   lock_path: Optional[Path] = None) -> dict:
    """CLI 入口：本地仓储 + 进程级单飞。"""
    from dragon_quant.infra.assets import db as assets_db
    from dragon_quant.infra.factor import storage as factor_store
    from dragon_quant.infra.storage import paths
    from dragon_quant.review_account import market, storage

    from .experiments import run_account_experiment as _run

    report = _run(
        run_id=run_id, sell_rules=sell_rules, confirm=confirm,
        account=storage, factor_store=factor_store,
        load_bars=assets_db.list_stock_daily_bars,
        load_calendar=assets_db.list_stock_daily_bar_dates,
        loader_factory=market.candidate_loader,
        snapshot_reader=market.prep_snapshot_for,
        assets=assets_db,
        save_experiment=storage.save_review_account_experiment,
        lock_path=lock_path or (paths.DATA_DIR / "locks" / "account_experiment.lock"),
    )
    return envelope(
        command="research experiments",
        request={"run_id": run_id, "sell_rules": validate_sell_rules(sell_rules),
                 "confirm": True},
        data=report,
        meta={"data_quality": "ok",
              "returned": 1, "truncated": False, "next_cursor": None,
              "experiment_id": report.get("experiment_id")},
        warnings=["仅本次显式运行；不自动推广结论"],
    )
