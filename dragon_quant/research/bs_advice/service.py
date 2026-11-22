"""共享 BS 运行用例（C1）：确认 → 锁 → 冻结/持久化 → 执行 → 生命周期。

CLI 与 API 只做参数解析与输出投影；锁在建立 running 前取得，冻结输入先落库/校验
再执行；同 digest 幂等查询/续跑，显式新 attempt 才新增记录。
"""

from __future__ import annotations


from dragon_quant.research.experiments import (
    ConfirmationRequired, ExperimentBusy, process_lock,
)

from .experiment import (
    BsExperimentError, build_experiment_pack, find_bs_experiment_by_digest,
    finish_experiment_run, resume_bs_experiment,
    run_bs_experiment, save_experiment_run, show_bs_experiment,
)
from .review import BsReviewError, run_bs_review


def run_experiment_flow(*, account, policy, date_from: str, date_to: str,
                        confirm: bool, lock_path, db_path=None,
                        cohort_mode: str = "rolling",
                        dragons_reader=None, prep_reader=None,
                        calendar_reader=None, plan_reader=None,
                        calendar=None, persist: bool = True,
                        new_attempt: bool = False,
                        study=None, enhanced=None) -> dict:
    if not confirm:
        raise ConfirmationRequired("对照实验会消耗本地算力，需显式 confirm")
    if date_from > date_to:
        raise BsExperimentError("date_from 不能晚于 date_to",
                                code="invalid_request", status=400)
    if cohort_mode not in ("rolling", "fixed"):
        raise BsExperimentError("cohort_mode 非法", code="invalid_request",
                                status=400)
    if not persist:
        pack = build_experiment_pack(
            account=account, policy=policy, date_from=date_from,
            date_to=date_to, db_path=db_path, cohort_mode=cohort_mode,
            dragons_reader=dragons_reader, prep_reader=prep_reader,
            calendar_reader=calendar_reader, plan_reader=plan_reader,
            calendar=calendar, study=study, enhanced=enhanced)
        return run_bs_experiment(
            account=account, policy=policy, date_from=date_from,
            date_to=date_to, db_path=db_path, cohort_mode=cohort_mode,
            pack=pack, study=study, enhanced=enhanced)
    with process_lock(lock_path):
        pack = build_experiment_pack(
            account=account, policy=policy, date_from=date_from,
            date_to=date_to, db_path=db_path, cohort_mode=cohort_mode,
            dragons_reader=dragons_reader, prep_reader=prep_reader,
            calendar=calendar, calendar_reader=calendar_reader,
            plan_reader=plan_reader, study=study, enhanced=enhanced)
        digest = pack["digest"]
        existing = None if new_attempt else find_bs_experiment_by_digest(digest)
        if existing is not None:
            if existing.get("status") == "completed":
                return {"id": existing["id"],
                        **dict(existing.get("report") or {})}
            # 同输入续跑：读已落盘 frozen，无需源，完整恢复所有 variant/指标。
            return resume_bs_experiment(existing["id"])
        experiment_id = save_experiment_run(
            request={"date_from": date_from, "date_to": date_to,
                     "policy_hash": policy.content_hash,
                     "cohort_mode": cohort_mode},
            expected_events=len(pack["events"]), frozen=pack["freeze"],
            run_digest=digest)
        try:
            report = run_bs_experiment(
                account=account, policy=policy, date_from=date_from,
                date_to=date_to, db_path=db_path, cohort_mode=cohort_mode,
                pack=pack)
        except Exception as exc:  # noqa: BLE001 - 生命周期 failed
            finish_experiment_run(
                experiment_id, status="failed",
                report={"status": "failed", "reason": type(exc).__name__,
                        "message": str(exc),
                        "expected_events": len(pack["events"])})
            raise
        finish_experiment_run(experiment_id,
                              status=report.get("status") or "failed",
                              report=report)
        return {"id": experiment_id, **report}


def resume_experiment_flow(*, experiment_id: int, lock_path) -> dict:
    with process_lock(lock_path):
        return resume_bs_experiment(int(experiment_id))


def enhanced_freeze_for_experiment(*, experiment_id: int) -> dict:
    """把已存实验的冻结输入作为增强证据来源（只认本地已存 ID，不存路径）。"""
    row = show_bs_experiment(int(experiment_id), include_frozen=True)
    if row is None:
        raise BsExperimentError("增强实验不存在", code="experiment_not_found",
                                status=404)
    frozen = row.get("frozen")
    if not frozen:
        raise BsExperimentError(
            "增强实验缺少冻结输入，legacy_non_replayable",
            code="legacy_non_replayable", status=422)
    return dict(frozen)


def run_review_flow(*, account, policy, date_from: str, date_to: str,
                    lock_path, db_path=None, persist: bool = True,
                    cohort_mode: str = "rolling",
                    dragons_reader=None, prep_reader=None,
                    calendar_reader=None, plan_reader=None) -> dict:
    kwargs = dict(account=account, policy=policy, date_from=date_from,
                  date_to=date_to, db_path=db_path, persist=persist,
                  cohort_mode=cohort_mode, dragons_reader=dragons_reader,
                  prep_reader=prep_reader, calendar_reader=calendar_reader,
                  plan_reader=plan_reader)
    if not persist:
        return run_bs_review(**kwargs)
    with process_lock(lock_path):
        return run_bs_review(**kwargs)


__all__ = ["run_experiment_flow", "resume_experiment_flow",
           "enhanced_freeze_for_experiment", "run_review_flow",
           "ExperimentBusy", "ConfirmationRequired", "BsReviewError"]
