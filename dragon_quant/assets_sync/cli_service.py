"""assets 命令的应用服务：装配、动作分派与日期/错误协调。

职责边界：

* **本模块**：解析/校验 ``--from``/``--to``，装配资产源（``build_sources``）、
  同步路径（``default_paths``）与后端；默认用凭据构造 ``S3Backend``，调用方
  （测试 / E2E）可注入 ``backend`` 覆盖 **全部远程动作**
  （doctor/push/pull/status/verify）；把异常映射成稳定的退出码契约。
* **``assets_sync.service``**：真正的 push/pull/status/verify 编排与行级合并算法；
  本模块只转发，不复制、不拆分。
* **``dragon_quant.commands.assets``**：参数映射、人读/JSON 输出与退出码；
  它不装配后端、不调用同步函数。

``backfill-dragons`` 是纯本地动作（读 ``dragons_v2``、写 ``assets.db.dragons``），
因此从不构造后端，注入的 ``backend`` 对它无效。

依赖方向：``cli_service`` → assets_sync（service/sources/state/doctor/backends/
credentials）与 ``storage.dragons_mirror``；**禁止** import ``dragon_quant.commands``、
``dragon_quant.cli``、``dragon_quant.app``。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dragon_quant.runtime import clock

SYNC_ACTIONS = ("push", "pull", "status", "verify")


@dataclass(frozen=True)
class AssetsOutcome:
    """一次 assets 动作的结果：JSON 载荷 + 退出码。

    ``command`` 是输出契约里的命令名（``assets <action>``）；``payload`` 是
    写到 stdout 的 JSON 对象（同步/doctor/未知动作）或 ``backfill-dragons`` 的
    原始结果；``exit_code`` 由命令层直接 ``sys.exit``。输出通道（stdout JSON /
    stderr 人读摘要）仍由命令层决定。
    """

    command: str
    payload: dict
    exit_code: int
    #: ``True`` when the sync service actually ran (it may still report ok=False);
    #: ``False`` for argument/config/transport errors raised before a result.
    completed: bool = True


def parse_asset_date(raw, field: str):
    """资产同步日期：支持 YYYYMMDD / YYYY-MM-DD / YYYY/MM/DD，拒绝未来日期。"""
    if raw is None:
        return None
    text = str(raw).strip()
    if len(text) == 8 and text.isdigit():
        text = f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    elif len(text) == 10 and text[4] == "/" and text[7] == "/":
        text = f"{text[:4]}-{text[5:7]}-{text[8:10]}"
    try:
        moment = datetime.strptime(text, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError(
            f"{field} 日期非法: {raw}（支持 YYYYMMDD / YYYY-MM-DD / YYYY/MM/DD）"
        ) from exc
    today = clock.now(timezone(timedelta(hours=8))).date()
    if moment.date() > today:
        raise ValueError(f"{field} 不能是未来日期: {raw}")
    return moment.strftime("%Y-%m-%d")


def _failure(command: str, reason: str, kind: str | None = None) -> dict:
    item = {"reason": reason}
    if kind is not None:
        item["kind"] = kind
    return {"ok": False, "command": command, "failed": [item], "summary": {}}


def _build_backend(args, backend):
    """Return the injected backend, or construct the production S3 backend."""
    if backend is not None:
        return backend
    from dragon_quant.assets_sync.backends.s3 import S3Backend
    from dragon_quant.assets_sync.credentials import load_config

    config = load_config(profile=getattr(args, "profile", None))
    return S3Backend(config, timeout=getattr(args, "timeout", 60.0))


def _parse_dates(args):
    date_from = parse_asset_date(getattr(args, "from_date", None), "--from")
    date_to = parse_asset_date(getattr(args, "to_date", None), "--to")
    return date_from, date_to


def _run_sync(action: str, args, backend) -> AssetsOutcome:
    from dragon_quant.assets_sync.errors import AssetsSyncError, LockBusyError
    from dragon_quant.assets_sync.sources import build_sources
    from dragon_quant.assets_sync.state import default_paths

    command = f"assets {action}"
    try:
        date_from, date_to = _parse_dates(args)
        if date_from and date_to and date_from > date_to:
            raise ValueError("--from 不能晚于 --to")
        if action == "pull" and (not date_from or not date_to):
            raise ValueError("pull 必须同时指定 --from 与 --to")
        samples = getattr(args, "samples", None)
        if action == "verify":
            if samples is not None and samples < 1:
                raise ValueError("--samples 必须 >= 1")
            if bool(date_from) != bool(date_to):
                raise ValueError("--from 与 --to 必须成对指定")
            if getattr(args, "deep", False) and not (date_from and date_to) \
                    and samples is None:
                raise ValueError("--deep 需配合 --from/--to 或 --samples")

        target = getattr(args, "target_dir", None)
        data_dir = Path(target).expanduser() if target else None
        if data_dir is not None:
            data_dir.mkdir(parents=True, exist_ok=True)
        sources = build_sources(db_path=(data_dir / "assets.db") if data_dir else None)
        paths = default_paths(data_dir)
        backend = _build_backend(args, backend)

        from dragon_quant.assets_sync import service as sync_service

        if action == "push":
            result = sync_service.push(
                backend, sources, date_from=date_from, date_to=date_to,
                all_dates=getattr(args, "all_dates", False),
                dry_run=getattr(args, "dry_run", False),
                allow_shrink=getattr(args, "allow_shrink", False), paths=paths)
        elif action == "pull":
            result = sync_service.pull(
                backend, sources, date_from=date_from, date_to=date_to,
                apply=getattr(args, "apply", False),
                force=getattr(args, "force", False),
                pick_latest=(getattr(args, "pick", None) == "latest"),
                paths=paths)
        elif action == "status":
            result = sync_service.status(
                backend, sources, all_dates=getattr(args, "all_dates", False),
                orphans=getattr(args, "orphans", False), paths=paths)
        else:
            result = sync_service.verify(
                backend, sources, date_from=date_from, date_to=date_to,
                samples=samples, deep=getattr(args, "deep", False),
                show_md5=getattr(args, "show_md5", False), paths=paths)
    except ValueError as exc:
        return AssetsOutcome(
            command, _failure(command, str(exc)), 2, completed=False)
    except LockBusyError as exc:
        return AssetsOutcome(
            command, _failure(command, str(exc), "lock"), 1, completed=False)
    except AssetsSyncError as exc:
        return AssetsOutcome(
            command, _failure(command, str(exc), type(exc).__name__), 2,
            completed=False)
    except Exception as exc:  # 配置/网络之外的意外错误也按配置错误退出
        return AssetsOutcome(
            command, _failure(command, f"{type(exc).__name__}: {exc}"), 2,
            completed=False)

    exit_code = 2 if result.get("fatal") else (0 if result.get("ok") else 1)
    payload = {
        "ok": result.get("ok", False),
        "command": command,
        "failed": result.get("failed") or [],
        "summary": result.get("summary") or {},
    }
    return AssetsOutcome(command, payload, exit_code)


def _run_doctor(args, backend) -> AssetsOutcome:
    from dragon_quant.assets_sync.errors import AssetsSyncError

    command = "assets doctor"
    samples = getattr(args, "samples", 50)
    if samples is None or samples < 1:
        return AssetsOutcome(
            command, _failure(command, "--samples 必须 >= 1"), 2)
    try:
        from dragon_quant.assets_sync.doctor import run_doctor

        backend = _build_backend(args, backend)
        summary = run_doctor(backend, samples=samples)
    except AssetsSyncError as exc:
        return AssetsOutcome(
            command, _failure(command, str(exc)), 2, completed=False)
    except Exception as exc:  # 配置/网络之外的意外错误也按配置错误退出
        return AssetsOutcome(
            command, _failure(command, f"{type(exc).__name__}: {exc}"), 2,
            completed=False)

    failed = list(summary.get("errors") or [])
    failed += [{"op": "cleanup", "error": reason}
               for reason in (summary.get("cleanup_errors") or [])]
    payload = {"ok": summary["ok"], "command": command,
               "failed": failed, "summary": summary}
    return AssetsOutcome(command, payload, 0 if summary["ok"] else 1)


def _run_backfill(args) -> AssetsOutcome:
    from dragon_quant.storage import dragons_mirror

    command = "assets backfill-dragons"
    try:
        date_from, date_to = _parse_dates(args)
        result = dragons_mirror.backfill_dragons(
            date_from=date_from, date_to=date_to,
            dry_run=bool(getattr(args, "dry_run", False)),
            prune=bool(getattr(args, "prune", False)))
    except ValueError as exc:      # 参数错误 / 日期非法 / --from 晚于 --to
        return AssetsOutcome(
            command, {"ok": False, "error": str(exc)}, 2, completed=False)
    except Exception as exc:       # 库不可用（schema / IO）
        return AssetsOutcome(
            command, {"ok": False, "error": f"{type(exc).__name__}: {exc}"}, 2,
            completed=False)

    # 全局失败（如 assets.db 不可用，failed[].date is None）属「库不可用」→ exit 2；
    # 单日失败 → exit 1。两种模式都输出完整汇总（含 failed[]）。
    fatal = any(isinstance(item, dict) and item.get("date") is None
                for item in result.get("failed") or [])
    exit_code = 2 if fatal else (0 if result.get("ok") else 1)
    return AssetsOutcome(command, result, exit_code)


def run(action, args, *, backend=None, services=None) -> AssetsOutcome:
    """执行一个 assets 动作并返回 JSON 载荷 + 退出码。

    ``backend`` 为 None 时按 ``--profile``/``--timeout`` 构造 ``S3Backend``；
    注入后覆盖 doctor/push/pull/status/verify（``backfill-dragons`` 纯本地，
    不使用后端）。``services`` 目前仅保留组合根注入位，未参与装配。
    """
    if action == "backfill-dragons":
        return _run_backfill(args)
    if action in SYNC_ACTIONS:
        return _run_sync(action, args, backend)
    if action == "doctor":
        return _run_doctor(args, backend)
    return AssetsOutcome("assets", _failure("assets", "未知 assets 子命令"), 2)
