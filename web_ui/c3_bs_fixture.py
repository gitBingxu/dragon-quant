"""C3 BS 研究 UI 本地 fixture：隔离 DQ_DATA_DIR/HOME + 真实可成交样例。

用法::

    .venv/bin/python web_ui/c3_bs_fixture.py --root /tmp/dq-c3-bs
    DQ_DATA_DIR=/tmp/dq-c3-bs/data HOME=/tmp/dq-c3-bs/home \
        .venv/bin/python -m dragon_quant dashboard --port 8766

脚本只写 ``--root`` 下的临时目录：日 K + 分钟观测 + dragons/prep，
账户/策略/研究规范 JSON，以及三个已存实验（多 variant、增强来源、失败可恢复）。
不联网、不读真实业务库、不写用户生产 dashboard。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
from pathlib import Path

_MARKER = ".dq-c3-fixture"


def _temporary_root(path: Path) -> Path:
    """只允许系统临时目录下的独占 fixture 子目录；拒绝 home/repo/root/symlink 越界。"""
    bases = {Path(tempfile.gettempdir()).resolve(),
             Path("/tmp").resolve(), Path("/private/tmp").resolve()}
    target = path.expanduser().resolve()
    allowed = [base for base in bases
               if target != base and _is_relative(target, base)]
    if not allowed:
        raise SystemExit(
            "拒绝：--root 必须位于系统临时目录（/tmp 或 tempfile.gettempdir()）"
            " 下的独占子目录")
    return target


def _is_relative(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
        return True
    except ValueError:
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="build isolated BS research UI fixture")
    parser.add_argument(
        "--root", default=None,
        help="系统临时目录下的独占 fixture 目录；缺省自动 mkdtemp")
    parser.add_argument(
        "--force", action="store_true",
        help="仅删除带 .dq-c3-fixture 标记的本脚本目录")
    args = parser.parse_args()
    if args.root is None:
        root = Path(tempfile.mkdtemp(prefix="dq-c3-bs-",
                                     dir=tempfile.gettempdir()))
    else:
        root = _temporary_root(Path(args.root))
        if root.exists() and any(root.iterdir()):
            if not (root / _MARKER).is_file():
                raise SystemExit(
                    f"拒绝：{root} 非空且缺少 {_MARKER} 标记，不删除未知内容")
            if not args.force:
                raise SystemExit(f"拒绝：{root} 已存在；确认是 fixture 后加 --force")
            shutil.rmtree(root)
    data_dir = root / "data"
    home_dir = root / "home"
    data_dir.mkdir(parents=True, exist_ok=True)
    home_dir.mkdir(parents=True, exist_ok=True)
    (root / _MARKER).write_text("dq-c3 fixture; safe to --force delete\n",
                                encoding="utf-8")
    os.environ["DQ_DATA_DIR"] = str(data_dir)
    os.environ["HOME"] = str(home_dir)

    from dragon_quant.review_account.strategy.bs_policy import teaching_fixture
    from dragon_quant.tests.test_bs_advice import (
        _cash_account, _history, _minute,
    )

    assets_db = data_dir / "assets.db"
    days = _history(assets_db)
    for start, end, close, volume in (
            ("09:59:00", "10:00:00", 10.0, 1_000_000),
            ("10:00:00", "10:01:00", 10.3, 1_000_000),
            ("10:01:00", "10:02:00", 10.05, 2500)):
        _minute(assets_db, "2026-11-18", start=start, end=end, close=close,
                volume=volume)

    from dragon_quant.infra.assets import db as assets_module
    from dragon_quant.scan.storage import dragons as dragon_store

    dragon_store.save_dragons("2026-11-17", [{
        "code": "600001", "name": "龙一", "rank": 1, "composite_score": 90.0,
        "is_true_dragon": True, "scan_id": "c3-demo"}], version="v2")
    assets_module.save_prep_snapshot("2026-11-17", {
        "review_candidates": [{"code": "600001", "name": "龙一",
                               "trade_date": "2026-11-17",
                               "eligibility": "eligible", "position_pct": 5.0}],
        "market": {"state": "strong", "score": 80.0, "coeff": 1.0,
                   "partial": False, "missing_inputs": []}}, source="v2")
    conn = assets_module._connect(create=False)
    conn.execute("UPDATE prep_snapshots SET created_at=? WHERE trade_date=?",
                 ("2026-11-17T16:50:00+08:00", "2026-11-17"))
    conn.commit()
    conn.close()

    config = teaching_fixture()   # 明确标注：教学假设，非推荐参数
    account_payload = _cash_account(config)
    policy_payload = config.to_dict()
    variant = teaching_fixture(
        profile_id="c3-demo-variant", profile_version="demo-1",
        fixture=False, label="C3 本地演示 variant（非推荐）")
    study = {
        "schema_version": 1, "study_id": "c3-demo-study",
        "evaluation_cutoff": "2026-11-18", "tail_threshold_pct": 5.0,
        "variants": [
            {"name": "no_new_risk", "kind": "baseline_no_new_risk"},
            {"name": "base", "kind": "policy", "config": policy_payload},
            {"name": "variant", "kind": "policy", "config": variant.to_dict()},
        ],
    }
    inputs = root / "inputs"
    inputs.mkdir(exist_ok=True)
    for name, payload in (("account.json", account_payload),
                          ("policy.json", policy_payload),
                          ("study.json", study)):
        (inputs / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                                   encoding="utf-8")

    from dragon_quant.research.bs_advice import (
        load_policy_payload, parse_account_file, service,
    )
    from dragon_quant.review_account import storage

    account = parse_account_file(account_payload)
    policy = load_policy_payload(policy_payload)
    lock = root / "locks" / "bs_experiment.lock"
    base = dict(account=account, policy=policy, date_from="2026-11-18",
                date_to="2026-11-18", confirm=True, lock_path=lock,
                db_path=assets_db)
    # A：负例（无 T+1 日历支撑）→ 意图挂起、fills 0，仍保留可查看。
    first = service.run_experiment_flow(study=study, **base)
    print("experiment A (unfilled negative):", first["id"], first["status"])
    # 补 11-18/19 日 K，让默认 calendar 支撑 T+1 撮合（UI review/roll 也正成交）。
    from dragon_quant.infra.assets import observations as obs_store
    from dragon_quant.infra.calibration import (
        PRODUCTION_CALIBRATIONS, binding_for,
    )
    cid = PRODUCTION_CALIBRATIONS[0].calibration_id
    for day in ("2026-11-18", "2026-11-19"):
        ts = f"{day}T15:05:00+08:00"
        obs_store.append_bar(
            trade_date=day, market="SH", code="600001", period="daily",
            interval_start=f"{day}T09:30:00+08:00",
            interval_end=f"{day}T15:00:00+08:00", open=10.05, high=10.05,
            low=10.05, close=10.05, volume=1_000_000, amount=10_050_000,
            source="tdx", observed_at=ts, available_at=ts,
            completeness="final", time_semantics="verified",
            unit_semantics="verified", adjustment="none", db_path=assets_db,
            calibration=binding_for(cid, calibrated_at=ts, source="tdx",
                                    period="daily"))
    # B：补充 T+1 日历 + 有限/充足容量分钟序列 → partial 后完整成交。
    calendar = list(days) + ["2026-11-18", "2026-11-19"]
    _minute(assets_db, "2026-11-18", start="10:02:00", end="10:03:00",
            close=10.05, volume=2500)
    _minute(assets_db, "2026-11-18", start="10:03:00", end="10:04:00",
            close=10.05, volume=2500)
    _minute(assets_db, "2026-11-18", start="10:04:00", end="10:05:00",
            close=10.05, volume=1_000_000)
    common = {**base, "calendar": calendar}
    second = service.run_experiment_flow(study=study, **common)
    print("experiment B (positive fills):", second["id"], second["status"])
    # C：同一研究窗口下用 B 的冻结包作增强来源（证据不同、资源一致）。
    enhanced = service.enhanced_freeze_for_experiment(
        experiment_id=int(second["id"]))
    third = service.run_experiment_flow(
        study=study, enhanced=enhanced, **common)
    print("experiment C (enhanced source):", third["id"], third["status"])
    # D：失败/部分运行，保留冻结输入供 UI 显式 resume。
    fourth = service.run_experiment_flow(study=study, new_attempt=True,
                                         **common)
    storage.update_review_account_experiment(
        int(fourth["id"]), status="failed",
        report={"status": "failed", "expected_events": 99,
                "completed_events": 0})
    print("experiment D (failed, resumable):", fourth["id"])

    print(json.dumps({
        "root": str(root),
        "data_dir": str(data_dir),
        "home_dir": str(home_dir),
        "inputs": str(inputs),
        "experiments": [first["id"], second["id"], third["id"],
                        fourth["id"]],
        "start": (f"DQ_DATA_DIR={data_dir} HOME={home_dir} "
                  ".venv/bin/python -m dragon_quant dashboard --port 8766 "
                  "--no-browser --no-bs-autogen"),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
