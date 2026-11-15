#!/usr/bin/env python3
"""Unified, interactive release entry for dragon-quant.

``scripts/publish.sh`` is a thin shell around this module.  It is the single
daily entry for releasing PyPI and/or npm artifacts: one plan, one persistent
state, one set of Git/tag/Release operations and one password prompt.

Exit codes: ``0`` success (or a cancelled / dry-run invocation), ``1`` release
failure or pending Release, ``2`` invalid invocation.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
from pathlib import Path

if __package__ in (None, ""):  # executed as a script, not imported as a package
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from release import git_ops, orchestrator, publish_npm, publish_pypi  # noqa: E402
from release.common import (  # noqa: E402
    DEFAULT_HEARTBEAT_SECONDS,
    ProgressReporter,
    ReleaseError,
    Runner,
    compare_versions,
    redact,
    suggest_patch,
    validate_version,
)
from release.state import ReleaseState  # noqa: E402


_INPUT = input


class InteractionCancelled(Exception):
    """EOF/Ctrl-C at an interactive prompt: cancel with zero side effects."""


def _is_tty() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def _ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        answer = _INPUT(f"{prompt}{suffix}: ").strip()
    except EOFError as exc:
        raise InteractionCancelled("输入结束（EOF）") from exc
    except KeyboardInterrupt as exc:
        raise InteractionCancelled("用户中断（Ctrl-C）") from exc
    return answer or default


def _ask_yes(prompt: str, *, default: bool) -> bool:
    hint = "Y/n" if default else "y/N"
    answer = _ask(f"{prompt} ({hint})").lower()
    if not answer:
        return default
    return answer in {"y", "yes", "是"}


def _ask_choice(prompt: str) -> str:
    while True:
        answer = _ask(prompt).lower()
        if answer in {"1", "pypi", "python"}:
            return "pypi"
        if answer in {"2", "npm", "skill", "skills"}:
            return "npm"
        if answer in {"3", "both", "pypi+npm", "两个", "都"}:
            return "both"
        print("请输入 1 (PyPI) / 2 (npm) / 3 (两者)")


# ─── argument parsing ─────────────────────────────────────────────────────


def build_parser(repo_default: Path) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="publish.sh",
        description="dragon-quant 统一发布：分支/bump/tag/GitHub Release 一次编排；"
                    "无参数进入交互，所有外部写入前先隔离构建与校验",
    )
    parser.add_argument("--packages", default=None,
                        help="选择发布包：pypi / npm / both")
    parser.add_argument("--pypi-version", default=None, help="PyPI 目标版本 x.y.z")
    parser.add_argument("--npm-version", default=None,
                        help="npm（dragon-quant-skills）目标版本 x.y.z")
    parser.add_argument("--min-cli-version", default=None,
                        help="npm 包声明的最低 CLI 版本")
    parser.add_argument("--npm-tag", default=None, help="npm dist-tag（默认 latest）")
    parser.add_argument("--npm-access", default=None,
                        help="npm access（public / restricted，默认 public）")
    parser.add_argument("--v", "--version", dest="legacy_pypi_version", default=None,
                        help="PyPI 目标版本（旧参数别名）")
    parser.add_argument("--dry-run", action="store_true",
                        help="只做预检/准备/构建/校验：零凭据、零注册表、零源码/Git 变更")
    parser.add_argument("--resume", action="store_true",
                        help="按固定计划恢复未完成批次，不重新 bump、不改计划")
    parser.add_argument("--yes", action="store_true",
                        help="非交互环境确认执行（非 dry-run 必需）")
    parser.add_argument("--status-dir", default=None,
                        help="发布状态根目录（默认 <repo>/.release）")
    parser.add_argument("--repo-root", default=str(repo_default),
                        help=argparse.SUPPRESS)
    # Reject secrets in argv regardless of value.
    for flag in ("--passwd", "--password", "--token", "--npm-token",
                 "--twine-password"):
        parser.add_argument(flag, dest="legacy_secret", default=None,
                            help=argparse.SUPPRESS)
    parser.add_argument("--otp", dest="legacy_otp", default=None,
                        help=argparse.SUPPRESS)
    return parser


def parse_args(argv: list[str], repo_default: Path) -> argparse.Namespace:
    parser = build_parser(repo_default)
    args = parser.parse_args(argv)
    if args.legacy_secret is not None:
        parser.error(
            "--passwd/--token 等凭据参数已移除（会暴露在进程参数与 shell 历史中）；"
            "请用 DQ_PUBLISH_PASSWORD / TWINE_PASSWORD / NPM_TOKEN 环境变量或隐藏输入。")
    if args.legacy_otp is not None:
        parser.error("--otp 从 argv 移除；请用 NPM_OTP 环境变量传入 2FA 验证码。")
    return args


# ─── plan construction ────────────────────────────────────────────────────


def status_root(args: argparse.Namespace, repo: Path) -> Path:
    if args.status_dir:
        return Path(args.status_dir)
    env = os.environ.get("DQ_RELEASE_STATUS_DIR")
    if env:
        return Path(env)
    if args.dry_run:
        return repo / ".release" / "dry-run"
    return repo / ".release"


def _explicit_packages(args: argparse.Namespace) -> list[str] | None:
    if args.packages:
        return orchestrator.canonical_packages(args.packages.split(","))
    if args.legacy_pypi_version:
        return ["pypi"]
    return None


def _version_for(package: str, args: argparse.Namespace, repo: Path,
                 *, tty: bool) -> str:
    explicit = (args.pypi_version or args.legacy_pypi_version
                if package == "pypi" else args.npm_version)
    if explicit:
        return validate_version(explicit)
    if package == "pypi":
        current = publish_pypi.current_version(repo)
    else:
        current = publish_npm.current_version(repo)
    suggested = suggest_patch(current)
    if not tty:
        raise ReleaseError(
            "arguments",
            f"非交互环境必须显式提供 {'--pypi-version' if package == 'pypi' else '--npm-version'}",
        )
    return validate_version(_ask(
        f"{package} 目标版本（当前 {current}，建议 {suggested}）", suggested))


def _validate_plan(plan: dict, repo: Path, *, require_min_cli: bool) -> None:
    packages = plan["packages"]
    if "pypi" in packages:
        version = validate_version(plan["pypi_version"])
        current = publish_pypi.current_version(repo)
        if compare_versions(version, current) < 0:
            raise ReleaseError(
                "arguments", f"PyPI 版本不能倒退：当前 {current}，目标 {version}")
    if "npm" in packages:
        version = validate_version(plan["npm_version"])
        current = publish_npm.current_version(repo)
        if compare_versions(version, current) < 0:
            raise ReleaseError(
                "arguments", f"npm 版本不能倒退：当前 {current}，目标 {version}")
        if require_min_cli and not plan.get("min_cli_version"):
            raise ReleaseError("arguments", "npm 发布必须提供 --min-cli-version")
        min_cli = validate_version(plan["min_cli_version"])
        cli_version = (plan["pypi_version"] if "pypi" in packages
                       else publish_pypi.current_version(repo))
        if compare_versions(min_cli, cli_version) > 0:
            raise ReleaseError(
                "arguments",
                f"最低 CLI {min_cli} 不能高于用于构建验证的 CLI {cli_version}")


def _resolve_npm_options(args: argparse.Namespace) -> tuple[str, str]:
    tag = args.npm_tag or "latest"
    access = args.npm_access or "public"
    if not tag or tag.startswith("-") or any(
            char not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-"
            for char in tag):
        raise ReleaseError("arguments", f"非法 npm dist-tag：{tag!r}")
    if access not in {"public", "restricted"}:
        raise ReleaseError("arguments", "--npm-access 只接受 public / restricted")
    return tag, access


def build_new_plan(args: argparse.Namespace, repo: Path) -> dict:
    tty = _is_tty()
    packages = _explicit_packages(args)
    if packages is None:
        if not tty:
            raise ReleaseError(
                "arguments", "非交互环境必须显式选择 --packages pypi|npm|both")
        packages = orchestrator.canonical_packages([_ask_choice(
            "发布哪个包？1=PyPI 2=npm 3=两者")])

    plan: dict = {
        "packages": packages,
        "pypi_version": None,
        "npm_version": None,
        "min_cli_version": None,
        "npm_tag": None,
        "npm_access": None,
        "dry_run": bool(args.dry_run),
        "tags": [],
        "release_tag": "",
    }
    if "pypi" in packages:
        plan["pypi_version"] = _version_for("pypi", args, repo, tty=tty)
    if "npm" in packages:
        plan["npm_version"] = _version_for("npm", args, repo, tty=tty)
        default_min = (plan["pypi_version"] if "pypi" in packages
                       else publish_npm.current_min_cli(repo))
        if args.min_cli_version:
            plan["min_cli_version"] = validate_version(args.min_cli_version)
        elif tty:
            plan["min_cli_version"] = validate_version(_ask(
                "npm 包最低 CLI 版本", default_min))
        elif args.dry_run:
            plan["min_cli_version"] = default_min
        else:
            raise ReleaseError(
                "arguments", "非交互真实发布必须显式提供 --min-cli-version")
        tag, access = _resolve_npm_options(args)
        plan["npm_tag"] = tag
        plan["npm_access"] = access

    _validate_plan(plan, repo, require_min_cli=not args.dry_run)
    plan["tags"] = orchestrator.plan_tags(plan)
    plan["release_tag"] = orchestrator.release_tag(plan)
    return plan


def _overrides_from_args(args: argparse.Namespace) -> dict:
    overrides: dict = {}
    packages = _explicit_packages(args)
    if packages:
        overrides["packages"] = packages
    for key, value in (
        ("pypi_version", args.pypi_version or args.legacy_pypi_version),
        ("npm_version", args.npm_version),
        ("min_cli_version", args.min_cli_version),
        ("npm_tag", args.npm_tag),
        ("npm_access", args.npm_access),
    ):
        if value:
            overrides[key] = value
    if args.dry_run:
        overrides["dry_run"] = True
    return overrides


# ─── presentation ─────────────────────────────────────────────────────────


def print_plan(plan: dict, identity: dict | None = None) -> None:
    names = {"pypi": "PyPI", "npm": "npm"}
    print("发布计划：")
    print("  包：" + " + ".join(names[item] for item in plan["packages"]))
    if "pypi" in plan["packages"]:
        print(f"  PyPI 版本：{plan['pypi_version']}（tag v{plan['pypi_version']}）")
    if "npm" in plan["packages"]:
        print(f"  npm 版本：{plan['npm_version']}"
              f"（tag skills-v{plan['npm_version']}）")
        print(f"  最低 CLI：{plan['min_cli_version']}")
        print(f"  npm dist-tag / access：{plan['npm_tag']} / {plan['npm_access']}")
    if identity:
        print(f"  分支：{identity['branch']} -> {identity['upstream']}")
    else:
        print("  分支：按当前 upstream 发布到 origin")
    actions: list[str] = []
    if plan.get("dry_run"):
        actions.append("预检/构建/校验（零 Git、零 registry、零凭据）")
    else:
        actions.append("精确提交版本文件（中文 emoji 主题）")
        actions.append("打 tag：" + "、".join(plan["tags"]))
        actions.append(f"原子 push 到 {identity['upstream'] if identity else 'origin'}"
                       "（含全部 tag）")
        if "pypi" in plan["packages"]:
            actions.append(f"上传 PyPI {plan['pypi_version']}")
        if "npm" in plan["packages"]:
            actions.append(
                f"上传 npm {plan['npm_version']}（dist-tag {plan['npm_tag']}）")
    print("  Git/registry 动作：" + "、".join(actions))
    print(f"  GitHub Release：{plan['release_tag']}（所有上传成功后创建）")


def print_resume_status(state: ReleaseState) -> None:
    plan = state.data.get("plan") or {}
    print(f"恢复批次 {state.data.get('batch_id')}（"
          f"{plan.get('dry_run') and 'dry-run' or '真实发布'}）")
    identity = None
    if state.data.get("branch") and state.data.get("upstream"):
        identity = {"branch": state.data["branch"],
                    "upstream": state.data["upstream"]}
    print_plan(plan, identity)
    order = ["preflight", "prepare", "verify", "notes", "credentials", "commit",
             "tag", "push", "upload_pypi", "min_cli", "upload_npm", "release"]
    print("步骤：")
    for name in order:
        entry = state.entry(name)
        if not entry:
            continue
        if entry.get("done"):
            status = "已完成"
        elif entry.get("state") == "skipped":
            status = "pending/skipped"
        elif entry.get("state") == "failed":
            status = "失败，将重试"
        else:
            status = "进行中，将对账"
        print(f"  - {name}: {status}")


def _print_summary(summary: dict) -> int:
    if summary.get("dry_run"):
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    if summary.get("already_complete"):
        packages = summary["packages"]
        parts = []
        if "pypi" in packages:
            parts.append(f"PyPI {summary['pypi_version']}")
        if "npm" in packages:
            parts.append(f"npm {summary['npm_version']}")
        print(f"批次 {summary['batch']} 已完成：{' + '.join(parts)}；"
              f"Release {summary['release_tag']}。未执行任何新步骤。")
        return 0
    if summary.get("pending"):
        print(f"⚠️ 发布未整体完成：{summary['pending_reason']}", file=sys.stderr)
        print(f"批次 {summary['batch']}；可用 --resume 仅补建 GitHub Release",
              file=sys.stderr)
        return 1
    packages = summary["packages"]
    parts = []
    if "pypi" in packages:
        parts.append(f"PyPI {summary['pypi_version']}")
    if "npm" in packages:
        parts.append(f"npm {summary['npm_version']}")
    print(f"✅ 发布完成：{' + '.join(parts)}；Release {summary['release_tag']}")
    print(f"批次 {summary['batch']} 已完成，历史保留在状态根目录")
    return 0


# ─── main ────────────────────────────────────────────────────────────────


def _sigterm(signum, frame):  # noqa: ANN001
    raise KeyboardInterrupt()


def _heartbeat_seconds() -> float:
    """Heartbeat cadence; ``DQ_RELEASE_HEARTBEAT_SECONDS`` tunes it for tests."""
    raw = os.environ.get("DQ_RELEASE_HEARTBEAT_SECONDS", "").strip()
    if not raw:
        return DEFAULT_HEARTBEAT_SECONDS
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_HEARTBEAT_SECONDS
    return value if value > 0 else DEFAULT_HEARTBEAT_SECONDS


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    repo_default = Path(__file__).resolve().parents[2]
    try:
        args = parse_args(argv, repo_default)
    except SystemExit as exc:
        return int(exc.code or 0)
    repo = Path(args.repo_root).resolve()
    root = status_root(args, repo)
    # Progress goes to stderr only, so stdout stays the machine-readable result.
    runner = Runner(cwd=repo,
                    progress=ProgressReporter(heartbeat_interval=_heartbeat_seconds()))
    signal.signal(signal.SIGTERM, _sigterm)

    try:
        if args.resume:
            common_dir = git_ops.git_common_dir(repo, runner)
            state = orchestrator.locate_resume_state(
                root, common_dir, use_pointer=not args.dry_run)
            state.verify_repo(repo)
            plan = dict(state.data["plan"])
            print_resume_status(state)
            if state.data.get("complete"):
                return _print_summary(orchestrator.completed_summary(state))
            if _is_tty():
                # Resume defaults to No: an accidental Enter must not act.
                if not _ask_yes("继续恢复该批次？", default=False):
                    print("已取消，未做任何修改")
                    return 0
            elif not args.yes:
                raise ReleaseError(
                    "arguments", "非交互恢复需要 --yes 确认")
            summary = orchestrator.execute(
                repo, runner, plan, root=root, resume=True,
                overrides=_overrides_from_args(args))
            return _print_summary(summary)

        plan = build_new_plan(args, repo)
        if not args.dry_run and not _is_tty() and not args.yes:
            raise ReleaseError(
                "arguments", "非交互真实发布需要 --yes 确认（零副作用退出）")
        identity = git_ops.resolve_identity(repo, runner)
        print_plan(plan, identity)
        if _is_tty():
            prompt = ("执行 dry-run 预演？" if args.dry_run
                      else "确认发布？该操作会提交/打标签/推送并上传注册表")
            if not _ask_yes(prompt, default=False):
                print("已取消，未做任何修改")
                return 0
        summary = orchestrator.execute(
            repo, runner, plan, root=root, resume=False)
        return _print_summary(summary)
    except InteractionCancelled as exc:
        print(f"\n已取消（{exc}）；未做任何修改", file=sys.stderr)
        return 130
    except ReleaseError as exc:
        print(f"错误（{exc.stage}）：{redact(str(exc), runner.secrets)}",
              file=sys.stderr)
        print(f"状态根目录：{root}（可用 --resume 继续，未完成步骤会对账真实状态）",
              file=sys.stderr)
        return 2 if exc.stage == "arguments" else 1
    except KeyboardInterrupt:
        print("\n已中断；未完成的批次可用 --resume 对账后继续", file=sys.stderr)
        return 130
    except Exception as exc:  # noqa: BLE001 — never bubble a bare traceback
        print(f"错误：未预期异常 {type(exc).__name__}: "
              f"{redact(str(exc), runner.secrets)}", file=sys.stderr)
        print(f"状态根目录：{root}（可用 --resume 继续）", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
