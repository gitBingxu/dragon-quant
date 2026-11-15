"""Unified release use case: one plan, one persistent state machine.

The orchestrator is the only place that owns Git mutations, version writes,
credentials, upload ordering and the GitHub Release.  PyPI and npm are two
adapters behind it.  Resume never trusts a ``done`` boolean: before acting it
re-reads the real Git / registry / Release facts recorded as intent.

Plan shape (stored verbatim in the batch state and hashed for tamper checks)::

    {
      "packages": ["pypi", "npm"],   # canonical order, pypi before npm
      "pypi_version": "0.8.2" | None,
      "npm_version": "1.0.3" | None,
      "min_cli_version": "0.8.2" | None,
      "npm_tag": "latest",
      "npm_access": "public",
      "dry_run": False,
      "tags": ["v0.8.2", "skills-v1.0.3"],
      "release_tag": "v0.8.2",
    }
"""

from __future__ import annotations

import hashlib
import shutil
from contextlib import contextmanager
from pathlib import Path

from release import credentials, git_ops, publish_npm, publish_pypi
from release.common import ReleaseError, Runner, SCHEMA_VERSION, sha256_file
from release.lock import RepoLock
from release.state import (
    ReleaseState,
    batch_state_path,
    clear_active_pointer,
    iter_batches,
    new_batch_id,
    read_active_pointer,
    write_active_pointer,
)


class _FailClosed(ReleaseError):
    """A recorded success no longer matches the remote: never soften to pending."""


# ─── helpers ──────────────────────────────────────────────────────────────


def canonical_packages(packages) -> list[str]:
    if isinstance(packages, str):
        packages = packages.split(",")
    values: set[str] = set()
    for item in packages:
        value = str(item).strip().lower()
        if value in {"both", "两个", "pypi+npm"}:
            values.update({"pypi", "npm"})
        else:
            values.add(value)
    unknown = values - {"pypi", "npm"}
    if unknown:
        raise ReleaseError("arguments", f"未知的包选择：{sorted(unknown)}")
    order = [name for name in ("pypi", "npm") if name in values]
    if not order:
        raise ReleaseError("arguments", "至少选择一个包（pypi / npm / both）")
    return order


def plan_tags(plan: dict) -> list[str]:
    tags: list[str] = []
    if "pypi" in plan["packages"]:
        tags.append(f"v{plan['pypi_version']}")
    if "npm" in plan["packages"]:
        tags.append(f"skills-v{plan['npm_version']}")
    return tags


def release_tag(plan: dict) -> str:
    if "pypi" in plan["packages"]:
        return f"v{plan['pypi_version']}"
    return f"skills-v{plan['npm_version']}"


def release_subject(plan: dict) -> str:
    parts = []
    if "pypi" in plan["packages"]:
        parts.append(f"PyPI {plan['pypi_version']}")
    if "npm" in plan["packages"]:
        parts.append(f"npm {plan['npm_version']}")
    return " + ".join(parts)


def release_message(plan: dict) -> str:
    return (
        f"🔖 发布：{release_subject(plan)}\n\n"
        "Co-Authored-By: Codex <codex@openai.com>"
    )


def copy_source_tree(repo: Path, dest: Path, runner: Runner) -> None:
    """Copy the tracked tree (worktree proven clean) without VCS/venv junk."""
    for relative in git_ops.tracked_files(repo, runner):
        source = repo / relative
        if not source.is_file():
            continue
        target = dest / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    modules = repo / "web_ui" / "frontend" / "node_modules"
    link = dest / "web_ui" / "frontend" / "node_modules"
    if modules.is_dir() and not link.exists():
        try:
            link.symlink_to(modules, target_is_directory=True)
        except OSError:
            shutil.copytree(modules, link, symlinks=True)


@contextmanager
def _step(state: ReleaseState, name: str, **intent):
    state.begin(name, **intent)
    try:
        yield
    except ReleaseError as exc:
        state.fail(name, str(exc))
        raise
    except BaseException as exc:  # noqa: BLE001 — persist a clean failure record
        state.fail(name, f"{type(exc).__name__}: {exc}")
        raise


# ─── batch discovery / creation ───────────────────────────────────────────


def unfinished_batches(root: Path) -> list[tuple[Path, dict]]:
    found = []
    for path, data in iter_batches(root):
        if data.get("schema") != SCHEMA_VERSION:
            continue
        if not data.get("complete"):
            found.append((path, data))
    return found


def _legacy_state_candidates(root: Path) -> list[Path]:
    return [Path(root) / "state.json", Path(root) / "publish" / "state.json"]


def _pointer_state(common_dir: Path) -> tuple[ReleaseState, dict] | None:
    """Load the active batch named by the repository pointer, if any."""
    pointer = read_active_pointer(common_dir)
    if pointer is None:
        return None
    state_path = Path(pointer["state_path"])
    if not state_path.is_file():
        raise ReleaseError(
            "resume",
            f"仓库活动批次指针指向的状态不存在：{state_path}；拒绝猜测，请人工核对",
        )
    state = ReleaseState.load(state_path)
    recorded_repo = state.data.get("repo")
    if pointer.get("repo") and recorded_repo and (
            Path(pointer["repo"]).resolve() != Path(recorded_repo).resolve()):
        raise ReleaseError(
            "resume",
            f"活动批次指针仓库 {pointer['repo']} 与状态记录 {recorded_repo} 不一致；"
            "拒绝继续",
        )
    return state, pointer


def locate_resume_state(root: Path, common_dir: Path,
                        *, use_pointer: bool = True) -> ReleaseState:
    """Find the batch to resume: canonical pointer first, then the status root.

    A completed batch is returned too, so callers can report its result without
    replaying any step.  ``use_pointer=False`` is used for dry-run resumes,
    which must only see the isolated preview root.
    """
    pointed = _pointer_state(common_dir) if use_pointer else None
    if pointed is not None:
        return pointed[0]
    unfinished = unfinished_batches(root)
    if not unfinished:
        for candidate in _legacy_state_candidates(root):
            if candidate.is_file():
                # Delegates to schema validation, which refuses to guess/convert.
                ReleaseState.load(candidate)
        completed = [
            (path, data) for path, data in iter_batches(root)
            if data.get("schema") == SCHEMA_VERSION and data.get("complete")
        ]
        if completed:
            # Reporting an already-finished batch is side-effect free and is
            # preferred over forcing the user to delete state files.
            return ReleaseState.load(completed[-1][0])
        raise ReleaseError(
            "resume",
            f"在 {root} 没有可恢复的未完成发布批次；不重放初始任务"
            "（真实发布与 --dry-run 预演使用不同状态根，预演请加 --dry-run）",
        )
    if len(unfinished) > 1:
        names = [path.parent.name for path, _ in unfinished]
        raise ReleaseError(
            "resume", f"存在多个未完成批次 {names}；请人工核对后再恢复")
    return ReleaseState.load(unfinished[0][0])


def ensure_no_unfinished(root: Path, common_dir: Path) -> None:
    """Refuse a new real release while any repository batch is unfinished.

    The git-common-dir pointer makes this independent of ``--status-dir`` and
    shared across linked worktrees.  A stale pointer to a completed batch is
    released here; anything else blocks.
    """
    pointed = _pointer_state(common_dir)
    if pointed is not None:
        state, pointer = pointed
        if state.data.get("complete"):
            clear_active_pointer(common_dir, state.data["batch_id"])
        else:
            raise ReleaseError(
                "preflight",
                f"存在未完成发布批次 {state.data.get('batch_id')}"
                f"（状态 {pointer.get('status_root')}）；请先用 --resume 完成，"
                "或人工核对后清理",
            )
    unfinished = unfinished_batches(root)
    if unfinished:
        names = [path.parent.name for path, _ in unfinished]
        raise ReleaseError(
            "preflight",
            f"存在未完成发布批次 {names}；请先用 --resume 完成，或人工核对后清理",
        )


def abandon_dry_run_batches(root: Path) -> list[str]:
    """Retire unfinished dry-run batches so a fresh preview can start.

    Dry runs have zero external side effects, so a failed preview must never
    block later previews.  History is kept and marked ``abandoned``; nothing is
    deleted and no Git/registry state was ever touched by those batches.
    """
    names: list[str] = []
    for path, data in unfinished_batches(root):
        state = ReleaseState.load(path)
        if not (state.data.get("plan") or {}).get("dry_run"):
            raise ReleaseError(
                "preflight",
                f"状态根 {root} 含未完成的真实发布批次 {path.parent.name}；"
                "请用 --resume 完成或人工核对",
            )
        state.data["complete"] = True
        state.data["abandoned"] = True
        state.data["abandoned_reason"] = "新的 dry-run 预演开始，旧预演历史保留"
        state.save()
        names.append(path.parent.name)
    return names


def compare_overrides(state: ReleaseState, overrides: dict) -> None:
    plan = state.data.get("plan") or {}
    checks = {
        "packages": canonical_packages(overrides["packages"])
        if overrides.get("packages") else None,
        "pypi_version": overrides.get("pypi_version"),
        "npm_version": overrides.get("npm_version"),
        "min_cli_version": overrides.get("min_cli_version"),
        "npm_tag": overrides.get("npm_tag"),
        "npm_access": overrides.get("npm_access"),
    }
    for key, value in checks.items():
        if value is None:
            continue
        if plan.get(key) != value:
            raise ReleaseError(
                "resume",
                f"--resume 不允许改变发布计划：{key} 记录 {plan.get(key)!r}，"
                f"本次 {value!r}",
            )
    if overrides.get("dry_run") and not plan.get("dry_run"):
        raise ReleaseError(
            "resume", "真实发布批次不能用 --dry-run 恢复；请去掉 --dry-run")


# ─── notes ────────────────────────────────────────────────────────────────


def git_change_list(repo: Path, base: str, runner: Runner,
                    match: str = "") -> str:
    """Deterministic list of commits reachable from the frozen base."""
    describe = ["describe", "--tags", "--abbrev=0"]
    if match:
        describe.extend(["--match", match])
    previous = git_ops.git(repo, runner, *describe, f"{base}^",
                           stage="preflight", allow_failure=True)
    if previous.returncode == 0 and previous.stdout.strip():
        rev_range = [f"{previous.stdout.strip()}..{base}"]
    else:
        rev_range = [base]
    log = git_ops.git(repo, runner, "log", "--oneline", "--no-decorate",
                      "-n", "50", *rev_range, stage="preflight",
                      allow_failure=True)
    lines = [line for line in log.stdout.splitlines() if line.strip()]
    body = "\n".join(f"- {line}" for line in lines) or "- （无可列出的变更）"
    return f"### 变更\n{body}\n"


def build_notes(repo: Path, plan: dict, base: str, runner: Runner) -> str:
    parts: list[str] = []
    if "pypi" in plan["packages"]:
        changelog = publish_pypi.changelog_notes(repo, plan["pypi_version"])
        if changelog:
            parts.append("### Python 包\n" + changelog.rstrip() + "\n")
        else:
            # The plan mandates a deterministic fallback instead of blocking on
            # (or rewriting) the user's CHANGELOG.
            parts.append(
                "### Python 包\n（CHANGELOG 无对应段落，以下为 git 变更列表）\n"
                + git_change_list(repo, base, runner, match="v*"))
    else:
        parts.append("### Agent Skill 包\n"
                     + git_change_list(repo, base, runner, match="skills-v*"))

    lines = ["### 包版本"]
    if "pypi" in plan["packages"]:
        lines.append(f"- PyPI `dragon-quant`：{plan['pypi_version']}（tag `v{plan['pypi_version']}`）")
    if "npm" in plan["packages"]:
        lines.append(
            f"- npm `dragon-quant-skills`：{plan['npm_version']}"
            f"（tag `skills-v{plan['npm_version']}`）")
    if plan.get("min_cli_version"):
        lines.append(f"- 最低 CLI：`{plan['min_cli_version']}`")
    lines.append("")
    lines.append("### 安装")
    if "pypi" in plan["packages"]:
        lines.append(f"- `pip install dragon-quant=={plan['pypi_version']}`")
    if "npm" in plan["packages"]:
        lines.append(f"- `npx dragon-quant-skills@{plan['npm_version']} install`")
    parts.append("\n".join(lines) + "\n")
    return "\n".join(parts).strip() + "\n"


def ensure_notes(state: ReleaseState, repo: Path, plan: dict, base: str,
                 runner: Runner) -> dict:
    """Return the frozen Release notes; verify their recorded hash on resume."""
    stored = state.data.get("release_notes")
    if stored and stored.get("text"):
        actual = hashlib.sha256(stored["text"].strip().encode("utf-8")).hexdigest()
        if actual != stored.get("sha256"):
            raise ReleaseError(
                "notes",
                "发布 notes 内容哈希与记录不一致（状态被修改）；拒绝继续",
            )
        return stored
    text = build_notes(repo, plan, base, runner)
    stored = {
        "text": text,
        "sha256": hashlib.sha256(text.strip().encode("utf-8")).hexdigest(),
        "tag": plan["release_tag"],
    }
    state.data["release_notes"] = stored
    state.save()
    return stored


# ─── execution ────────────────────────────────────────────────────────────


def _precheck_new(repo: Path, runner: Runner, plan: dict) -> tuple[dict, str]:
    """Read-only checks that must pass before a batch directory is created."""
    identity = git_ops.resolve_identity(repo, runner)
    git_ops.check_clean_worktree(repo, runner)
    base = git_ops.head_commit(repo, runner, stage="preflight")
    python = _resolve_python(repo)
    if not plan.get("dry_run"):
        git_ops.check_new_tags(repo, runner, plan["tags"])
        _check_remote_versions_free(plan, runner)
        _check_min_cli_available(plan, runner)
        _check_tools(repo, python, runner, plan["packages"])
    return identity, base


def completed_summary(state: ReleaseState) -> dict:
    """Report an already-complete batch without replaying any step."""
    plan = dict(state.data.get("plan") or {})
    packages = plan.get("packages") or []
    if plan.get("dry_run"):
        return {
            "dry_run": True,
            "already_complete": True,
            "batch": state.data.get("batch_id"),
            "packages": packages,
            "pypi_version": plan.get("pypi_version"),
            "npm_version": plan.get("npm_version"),
            "min_cli_version": plan.get("min_cli_version"),
            "tags": plan.get("tags"),
            "release_tag": plan.get("release_tag"),
            "artifacts": state.data.get("artifacts") or {},
            "notes": "该预演批次已完成；没有新的外部写入",
        }
    return {
        "dry_run": False,
        "already_complete": True,
        "batch": state.data.get("batch_id"),
        "packages": packages,
        "pypi_version": plan.get("pypi_version"),
        "npm_version": plan.get("npm_version"),
        "min_cli_version": plan.get("min_cli_version"),
        "tags": plan.get("tags"),
        "release_tag": plan.get("release_tag"),
        "commit": state.result("commit").get("commit"),
        "uploaded": {
            "pypi": state.result("upload_pypi").get("uploaded")
            if "pypi" in packages else None,
            "npm": state.result("upload_npm").get("uploaded")
            if "npm" in packages else None,
        },
        "release_created": state.done("release"),
    }


def execute(repo: Path, runner: Runner, plan: dict, *, root: Path,
            resume: bool, overrides: dict | None = None) -> dict:
    repo = Path(repo).resolve()
    root = Path(root)
    common_dir = git_ops.git_common_dir(repo, runner)
    with RepoLock(common_dir):
        if resume:
            state = locate_resume_state(
                root, common_dir, use_pointer=not (overrides or {}).get("dry_run"))
            state.verify_repo(repo)
            compare_overrides(state, overrides or {})
            plan = dict(state.data["plan"])
            if plan.get("dry_run") and not (overrides or {}).get("dry_run"):
                raise ReleaseError(
                    "resume", "预演批次请用 --dry-run --resume 恢复")
            if state.data.get("complete"):
                if not plan.get("dry_run"):
                    clear_active_pointer(common_dir, state.data["batch_id"])
                return completed_summary(state)
        else:
            plan = dict(plan)
            plan["tags"] = plan_tags(plan)
            plan["release_tag"] = release_tag(plan)
            abandoned: list[str] = []
            if plan.get("dry_run"):
                # A failed preview must never block the next preview.
                abandoned = abandon_dry_run_batches(root)
            else:
                # Canonical pointer + status root; --status-dir cannot hide an
                # unfinished release of the same repository.
                ensure_no_unfinished(root, common_dir)
            # Prove the release can start before creating any batch directory:
            # a rejected preflight must leave zero new state behind.
            identity, base = _precheck_new(repo, runner, plan)
            batch_id = new_batch_id()
            state = ReleaseState.new(
                batch_state_path(root, batch_id), batch_id=batch_id,
                plan=plan, repo=repo)
            state.data["branch"] = identity["branch"]
            state.data["upstream"] = identity["upstream"]
            state.data["push_target_fingerprint"] = identity[
                "push_target_fingerprint"]
            state.data["base_commit"] = base
            if abandoned:
                state.data["abandoned_dry_batches"] = abandoned
            state.save()
            if not plan.get("dry_run"):
                write_active_pointer(common_dir, state_path=state.path,
                                     root=root, repo=repo, batch_id=batch_id)
        plan = dict(plan)
        if not plan.get("tags"):
            plan["tags"] = plan_tags(plan)
        if not plan.get("release_tag"):
            plan["release_tag"] = release_tag(plan)
        if not plan.get("dry_run"):
            # Also adopt an older batch (no pointer yet) into the canonical
            # pointer so linked worktrees see the same active release.
            write_active_pointer(common_dir, state_path=state.path, root=root,
                                 repo=repo, batch_id=state.data["batch_id"])
        return _run(repo, runner, state, plan, resume=resume,
                    common_dir=common_dir)


def _run(repo: Path, runner: Runner, state: ReleaseState, plan: dict,
         *, resume: bool, common_dir: Path) -> dict:
    dry_run = bool(plan.get("dry_run"))
    packages = plan["packages"]
    files: list[str] = []
    if "pypi" in packages:
        files.extend(publish_pypi.RELEASE_FILES)
    if "npm" in packages:
        files.extend(publish_npm.RELEASE_FILES)
    include_dist = "pypi" in packages
    current = "preflight"

    try:
        # ── preflight ────────────────────────────────────────────────────
        current = "preflight"
        with _step(state, "preflight", dry_run=dry_run):
            if resume and not state.data.get("base_commit"):
                raise ReleaseError("resume", "状态缺少 base_commit，无法核验发布身份")
            if resume:
                identity = git_ops.verify_recorded_identity(repo, runner, state)
            else:
                identity = git_ops.resolve_identity(repo, runner)
                state.data["branch"] = identity["branch"]
                state.data["upstream"] = identity["upstream"]
                state.data["push_target_fingerprint"] = identity[
                    "push_target_fingerprint"]
                state.save()
            if resume and state.done("commit"):
                # After the release commit landed, no release file may be dirty.
                git_ops.check_clean_worktree(repo, runner)
            elif resume:
                git_ops.check_resume_worktree(repo, runner, files, include_dist)
            else:
                git_ops.check_clean_worktree(repo, runner)
            base = git_ops.head_commit(repo, runner, stage="preflight")
            if not state.data.get("base_commit"):
                state.data["base_commit"] = base
                state.save()
            base = state.data["base_commit"]
            python = _resolve_python(repo)
            if not dry_run:
                if not resume:
                    git_ops.check_new_tags(repo, runner, plan["tags"])
                    _check_remote_versions_free(plan, runner)
                _check_tools(repo, python, runner, packages)
                _check_min_cli_available(plan, runner, state)
            state.finish("preflight", branch=identity["branch"],
                         base_commit=base, dry_run=dry_run)

        # ── prepare / build ──────────────────────────────────────────────
        current = "prepare"
        artifacts = state.data.get("artifacts") or {}
        staging = state.data.get("staging")
        if resume and artifacts and staging:
            with _step(state, "prepare", reused=True):
                _verify_local(Path(staging), state, packages)
                state.finish("prepare", staging=staging, artifacts=artifacts,
                             reused=True)
        else:
            with _step(state, "prepare"):
                # Persistent staging inside this batch's state dir: a resume
                # must survive TMPDIR cleanup.  Never auto-deleted.
                staging_path = state.path.parent / "staging"
                if staging_path.exists():
                    # A crash during an incomplete prepare leaves only scratch;
                    # nothing externally visible exists yet, so rebuild cleanly.
                    shutil.rmtree(staging_path)
                staging_path.mkdir(parents=True)
                state.data["staging"] = str(staging_path)
                state.save()
                copy_source_tree(repo, staging_path, runner)
                artifacts = {}
                if "pypi" in packages:
                    built = publish_pypi.prepare(
                        repo, staging_path, plan["pypi_version"], python, runner)
                    artifacts["pypi"] = publish_pypi.digests(built)
                if "npm" in packages:
                    tarball = publish_npm.prepare(
                        repo, staging_path, plan["npm_version"],
                        plan["min_cli_version"], python, runner)
                    artifacts["npm"] = publish_npm.artifact(staging_path, tarball)
                state.data["artifacts"] = artifacts
                state.data["staging_identity"] = staging_identity(
                    staging_path, release_paths(packages), include_dist)
                state.save()
                state.finish("prepare", staging=str(staging_path),
                             artifacts=artifacts)
            staging = state.data["staging"]

        current = "verify"
        with _step(state, "verify"):
            _verify_local(Path(staging), state, packages)
            if "pypi" in packages:
                names = state.data["artifacts"]["pypi"]
                publish_pypi.verify_package_boundary(
                    [Path(staging) / "dist" / name for name in names])
            state.finish("verify", artifacts=state.data["artifacts"])

        # ── Release notes: generated and frozen before any external write ──
        current = "notes"
        with _step(state, "notes", release_tag=plan["release_tag"]):
            notes = ensure_notes(state, repo, plan,
                                 state.data["base_commit"], runner)
            state.finish("notes", tag=notes["tag"], sha256=notes["sha256"])

        if dry_run:
            summary = {
                "dry_run": True,
                "batch": state.data["batch_id"],
                "packages": packages,
                "pypi_version": plan["pypi_version"],
                "npm_version": plan["npm_version"],
                "min_cli_version": plan["min_cli_version"],
                "tags": plan["tags"],
                "release_tag": plan["release_tag"],
                "artifacts": state.data["artifacts"],
                "release_notes_sha256": (state.data.get("release_notes") or {}).get("sha256"),
                "abandoned_dry_batches": state.data.get("abandoned_dry_batches") or [],
                "remote_writes": 0,
                "network_calls": 0,
                "notes": "dry-run 不读凭据、不连接注册表、不改源码/Git",
            }
            state.data["complete"] = True
            state.save()
            return summary

        # ── upload need + credentials (before any Git write) ─────────────
        if resume:
            _reconcile_git_before_credentials(repo, runner, state)
        current = "credentials"
        with _step(state, "credentials"):
            need = _upload_need(plan, state.data["artifacts"], runner)
            tokens: dict[str, str] = {}
            creds = credentials.Credentials(repo, runner)
            if "pypi" in need:
                tokens["pypi"] = creds.pypi_token()
            if "npm" in need:
                tokens["npm"] = creds.npm_token()
            state.finish("credentials", needed=sorted(need))

        # ── exact release commit / tags / atomic push ───────────────────
        message = release_message(plan)
        subject = message.splitlines()[0]
        base = state.data["base_commit"]

        current = "commit"
        if state.done("commit"):
            commit = state.result("commit")["commit"]
            if git_ops.head_commit(repo, runner) != commit:
                raise ReleaseError("commit", "HEAD 已变化，与记录的发布提交不一致")
        else:
            recorded_intent = state.intent("commit")
            try:
                with _step(state, "commit", base_commit=base, files=files,
                           include_dist=include_dist, subject=subject):
                    current_head = git_ops.head_commit(repo, runner)
                    frozen_tree = recorded_intent.get("expected_tree")
                    if current_head != base:
                        # A previous attempt may have committed but crashed
                        # before recording it.  Reconcile only against the tree
                        # frozen before that attempt, never a freshly computed
                        # tree (which could adopt unknown content).
                        tree = frozen_tree or git_ops.expected_tree(
                            repo, runner, Path(staging), files, include_dist,
                            parent=base)
                        adopted = git_ops.detect_release_commit(
                            repo, runner, base, tree, subject)
                        if adopted is None:
                            raise ReleaseError(
                                "commit", "HEAD 与发布基准不一致，拒绝继续")
                        commit = adopted
                        state.finish("commit", commit=commit, tree=tree,
                                     adopted=True)
                    else:
                        git_ops.check_release_files(
                            repo, runner, Path(staging), files)
                        tree = frozen_tree or git_ops.expected_tree(
                            repo, runner, Path(staging), files, include_dist,
                            parent=base)
                        state.begin("commit", base_commit=base, files=files,
                                    include_dist=include_dist, subject=subject,
                                    expected_tree=tree)
                        writeback = git_ops.apply_release_files(
                            repo, Path(staging), files, include_dist)
                        try:
                            commit, changed = git_ops.commit_release(
                                repo, runner, files, include_dist, message)
                            landed = git_ops.commit_tree(repo, runner, commit)
                            if landed != tree:
                                raise ReleaseError(
                                    "commit",
                                    "落地的发布提交 tree 与冻结的预期内容不一致；"
                                    "拒绝上传，请人工核对")
                            state.finish("commit", commit=commit, tree=tree,
                                         changed=changed)
                        except BaseException:
                            git_ops.unstage(repo, runner, files, include_dist)
                            # If the commit itself succeeded (e.g. a post-commit
                            # hook failed), keep the new worktree; a later resume
                            # adopts HEAD.  Only roll back a commit that never
                            # landed.
                            if git_ops.head_commit(repo, runner) == base:
                                writeback.restore()
                            raise
                        finally:
                            writeback.cleanup()
            except ReleaseError as exc:
                state.fail("commit", str(exc))
                raise

        current = "tag"
        with _step(state, "tag", tags=plan["tags"], commit=commit):
            if state.done("tag"):
                for tag in plan["tags"]:
                    target = git_ops.local_tag_commit(repo, runner, tag)
                    if target != commit:
                        raise ReleaseError(
                            "tag", f"状态记录已打标签，但本地 {tag} 指向 {target}；拒绝改写")
            else:
                git_ops.ensure_tags(repo, runner, plan["tags"], commit)
            state.finish("tag", tags=plan["tags"], commit=commit)

        current = "push"
        with _step(state, "push", branch=state.data["branch"], commit=commit,
                   tags=plan["tags"]):
            matched = git_ops.reconcile_push(
                repo, runner, state.data["branch"], commit, plan["tags"])
            if state.done("push"):
                if not matched:
                    raise ReleaseError(
                        "push", "状态记录推送已完成，但远端引用与记录不一致；拒绝改写")
                state.finish("push", commit=commit, tags=plan["tags"], pushed=False)
            elif matched:
                state.finish("push", commit=commit, tags=plan["tags"], pushed=False)
            else:
                git_ops.atomic_push(repo, runner, state.data["branch"], commit,
                                    plan["tags"])
                if not git_ops.reconcile_push(
                        repo, runner, state.data["branch"], commit, plan["tags"]):
                    raise ReleaseError("push", "推送后远端引用仍与记录不一致")
                state.finish("push", commit=commit, tags=plan["tags"], pushed=True)

        # ── uploads (PyPI first, then npm) ──────────────────────────────
        if "pypi" in packages:
            current = "upload_pypi"
            with _step(state, "upload_pypi", version=plan["pypi_version"]):
                expected = state.data["artifacts"]["pypi"]
                missing = publish_pypi.reconcile(plan["pypi_version"], expected)
                if state.done("upload_pypi") and missing:
                    raise ReleaseError(
                        "upload_pypi",
                        f"状态记录上传已完成，但远端缺少工件：{missing}；拒绝重传")
                if missing:
                    token = tokens.get("pypi") or creds.pypi_token()
                    paths = [Path(staging) / "dist" / name for name in missing]
                    publish_pypi.upload(paths, python, runner, token)
                    remaining = publish_pypi.reconcile(
                        plan["pypi_version"], expected)
                    if remaining:
                        raise ReleaseError(
                            "upload_pypi",
                            f"上传后核验仍有缺失：{remaining}；请人工核对")
                state.finish(
                    "upload_pypi",
                    uploaded=sorted(set(state.result("upload_pypi").get("uploaded")
                                        or []) | set(missing)),
                    version=plan["pypi_version"])

        if "npm" in packages:
            current = "min_cli"
            with _step(state, "min_cli", version=plan["min_cli_version"]):
                min_cli = plan["min_cli_version"]
                fresh = (min_cli == plan.get("pypi_version")
                         and "pypi" in packages)
                if state.done("upload_npm"):
                    # Nothing left to upload: the dependency gate is moot and a
                    # Release-only repair must not depend on PyPI reachability.
                    pass
                elif fresh:
                    # Preflight only validated the plan/build version; confirm
                    # the resulting PyPI upload actually landed before npm.
                    if not state.done("upload_pypi"):
                        raise ReleaseError(
                            "min_cli",
                            "最低 CLI 为本批次 PyPI 目标，但 PyPI 上传尚未成功；停止 npm 上传")
                    if not publish_pypi.version_exists(min_cli, stage="min_cli"):
                        raise ReleaseError(
                            "min_cli",
                            f"最低 CLI 版本 {min_cli} 上传后仍不在 PyPI；停止 npm 上传")
                elif not publish_pypi.version_exists(min_cli, stage="min_cli"):
                    raise ReleaseError(
                        "min_cli",
                        f"最低 CLI 版本 {min_cli} 不在 PyPI（或已全部撤回）；"
                        "npm 包依赖不可用，停止上传")
                state.finish("min_cli", version=min_cli, available=True)

            current = "upload_npm"
            with _step(state, "upload_npm", version=plan["npm_version"]):
                npm_artifact = state.data["artifacts"]["npm"]
                tarball = Path(staging) / "npm-dist" / npm_artifact["filename"]
                present = publish_npm.reconcile(
                    plan["npm_version"], npm_artifact["integrity"], runner)
                if state.done("upload_npm") and not present:
                    raise ReleaseError(
                        "upload_npm",
                        "状态记录 npm 上传已完成，但远端版本缺失；拒绝重传")
                uploaded = bool(state.result("upload_npm").get("uploaded"))
                if not present:
                    # npm publish reassigns the dist-tag itself: guard before
                    # the irreversible upload, not after.
                    publish_npm.guard_dist_tag(
                        plan["npm_version"], plan["npm_tag"], runner)
                    token = tokens.get("npm") or creds.npm_token()
                    otp = runner.env_for().get("NPM_OTP") or None
                    publish_npm.upload(
                        tarball, runner, token, plan["npm_tag"],
                        plan["npm_access"], otp)
                    if not publish_npm.reconcile(
                            plan["npm_version"], npm_artifact["integrity"], runner):
                        raise ReleaseError(
                            "upload_npm", "上传后核验失败：远端 integrity 不一致或缺失")
                    uploaded = True
                tagged = publish_npm.ensure_dist_tag(
                    plan["npm_version"], plan["npm_tag"], runner,
                    lambda: tokens.get("npm") or creds.npm_token(),
                    runner.env_for().get("NPM_OTP") or None)
                state.finish(
                    "upload_npm", uploaded=uploaded, dist_tag=plan["npm_tag"],
                    dist_tag_updated=bool(
                        tagged or state.result("upload_npm").get("dist_tag_updated")))

        # ── GitHub Release (last, fixed notes) ──────────────────────────
        current = "release"
        notes = ensure_notes(state, repo, plan, base, runner)
        state.begin("release", tag=notes["tag"], notes_sha256=notes["sha256"])
        try:
            if not git_ops.gh_available(runner):
                raise ReleaseError(
                    "release",
                    "gh CLI 缺失，GitHub Release 保持 pending；"
                    "安装 gh 后可用 --resume 只补建 Release",
                )
            view = git_ops.gh_release_view(repo, runner, notes["tag"])
            if view is not None:
                if view.get("isDraft"):
                    raise ReleaseError(
                        "release", "远端存在 draft Release；拒绝自动处理")
                if _body_hash(view.get("body", "")) != notes["sha256"]:
                    raise ReleaseError(
                        "release",
                        "远端 Release 正文与记录不一致；拒绝覆盖，请人工核对")
                state.finish("release", tag=notes["tag"], adopted=True)
            elif state.done("release"):
                # A recorded success must not be re-created when the remote
                # object vanished: fail closed instead of side-effecting again.
                raise _FailClosed(
                    "release",
                    "状态记录 Release 已完成，但远端缺失；拒绝重建，请人工核对")
            else:
                notes_path = state.path.parent / "release-notes.md"
                notes_path.write_text(notes["text"], encoding="utf-8")
                git_ops.gh_release_create(
                    repo, runner, notes["tag"], notes["tag"], notes_path)
                check = git_ops.gh_release_view(repo, runner, notes["tag"])
                if check is None or _body_hash(
                        check.get("body", "")) != notes["sha256"]:
                    raise ReleaseError(
                        "release",
                        "创建后无法核验 Release 正文；保持未完成，可 --resume 对账")
                state.finish("release", tag=notes["tag"], created=True)
        except _FailClosed as exc:
            state.fail("release", str(exc))
            raise
        except ReleaseError as exc:
            # Release is the only graceful-pending step: every selected package
            # is already published and resume only needs to retry this step.
            state.skip("release", str(exc), pending=True)
            state.note(str(exc))
            return {
                "dry_run": False,
                "batch": state.data["batch_id"],
                "packages": packages,
                "pypi_version": plan["pypi_version"],
                "npm_version": plan["npm_version"],
                "min_cli_version": plan["min_cli_version"],
                "tags": plan["tags"],
                "release_tag": plan["release_tag"],
                "commit": state.result("commit").get("commit"),
                "pending": True,
                "pending_reason": str(exc),
            }

        state.data["complete"] = True
        state.save()
        if not dry_run:
            clear_active_pointer(common_dir, state.data["batch_id"])
        return {
            "dry_run": False,
            "batch": state.data["batch_id"],
            "packages": packages,
            "pypi_version": plan["pypi_version"],
            "npm_version": plan["npm_version"],
            "min_cli_version": plan["min_cli_version"],
            "tags": plan["tags"],
            "release_tag": plan["release_tag"],
            "commit": state.result("commit").get("commit"),
            "uploaded": {
                "pypi": state.result("upload_pypi").get("uploaded", [])
                if "pypi" in packages else None,
                "npm": state.result("upload_npm").get("uploaded")
                if "npm" in packages else None,
            },
            "release_created": state.done("release"),
        }
    except ReleaseError as exc:
        if "state" in locals():
            try:
                state.fail(current, str(exc))
            except OSError:
                pass
        raise


def _body_hash(body: str) -> str:
    return hashlib.sha256((body or "").strip().encode("utf-8")).hexdigest()


def _resolve_python(repo: Path) -> str:
    import os

    explicit = os.environ.get("DQ_RELEASE_PYTHON", "").strip()
    if explicit:
        return explicit
    venv = repo / ".venv" / "bin" / "python"
    if venv.exists():
        return str(venv)
    raise ReleaseError(
        "preflight",
        "未找到发布解释器：请设置 DQ_RELEASE_PYTHON，或在仓库创建 .venv 并安装 build/twine。",
    )


def _check_tools(repo: Path, python: str, runner: Runner, packages: list[str]) -> None:
    if not Path(python).exists():
        raise ReleaseError("preflight", f"发布解释器不存在：{python}")
    runner.run([python, "-c", "import sys; assert sys.version_info >= (3, 10)"],
               stage="preflight", scrub=True)
    if "pypi" in packages:
        for module in ("build", "twine"):
            result = runner.run([python, "-c", f"import {module}"],
                                stage="preflight", scrub=True, allow_failure=True)
            if result.returncode != 0:
                raise ReleaseError(
                    "preflight",
                    f"发布解释器缺少 {module}；请在该虚拟环境中安装（脚本不会自动安装）："
                    f" {python} -m pip install build twine",
                )
    if shutil.which("npm", path=runner.env_for().get("PATH")) is None:
        raise ReleaseError("preflight", "缺少 npm")
    if "pypi" in packages:
        frontend = repo / "web_ui" / "frontend"
        if not (frontend / "node_modules").is_dir():
            raise ReleaseError(
                "preflight",
                "web_ui/frontend/node_modules 缺失；请先在该目录 npm install"
                "（脚本不会自动安装）",
            )


def _check_remote_versions_free(plan: dict, runner: Runner) -> None:
    if "pypi" in plan["packages"]:
        if publish_pypi.remote_artifacts(plan["pypi_version"]) is not None:
            raise ReleaseError("preflight", f"PyPI 已存在版本 {plan['pypi_version']}")
    if "npm" in plan["packages"]:
        facts = publish_npm.remote_facts(plan["npm_version"], runner)
        if facts is not None:
            raise ReleaseError("preflight", f"npm 已存在版本 {plan['npm_version']}")


def _check_min_cli_available(plan: dict, runner: Runner,
                             state: ReleaseState | None = None) -> None:
    """Verify an already-published min-CLI before any irreversible write.

    When the minimum CLI is this batch's own new PyPI target, only the frozen
    plan/build version is validated here; the upload is confirmed right before
    npm.  An obviously missing dependency must not cost a pushed tag.  A batch
    whose npm upload already succeeded only needs a Release repair, so it does
    not re-check the dependency.
    """
    if "npm" not in plan["packages"]:
        return
    if state is not None and state.done("upload_npm"):
        return
    min_cli = plan["min_cli_version"]
    if "pypi" in plan["packages"] and min_cli == plan.get("pypi_version"):
        return
    if not publish_pypi.version_exists(min_cli, stage="preflight"):
        raise ReleaseError(
            "preflight",
            f"最低 CLI 版本 {min_cli} 不在 PyPI（或已全部撤回）；"
            "npm 包依赖不可用，未做任何外部写入",
        )


def _reconcile_git_before_credentials(repo: Path, runner: Runner,
                                      state: ReleaseState) -> None:
    """Reject Git/remote identity drift before prompting for a password."""
    if state.done("commit"):
        commit = state.result("commit")["commit"]
        if git_ops.head_commit(repo, runner) != commit:
            raise ReleaseError("commit", "HEAD 已变化，与记录的发布提交不一致")
    if state.done("tag"):
        commit = state.result("commit").get("commit")
        for tag in state.result("tag").get("tags") or []:
            if git_ops.local_tag_commit(repo, runner, tag) != commit:
                raise ReleaseError("tag", f"本地标签 {tag} 与发布提交不一致")
    if state.done("push"):
        commit = state.result("commit").get("commit")
        branch = state.data.get("branch")
        tags = state.result("push").get("tags") or []
        if not git_ops.reconcile_push(repo, runner, branch, commit, tags):
            raise ReleaseError(
                "push", "状态记录推送已完成，但远端引用与记录不一致；拒绝改写")
    # A recorded successful upload is a fact: if the remote copy is gone, stop
    # before prompting for credentials instead of uploading it again.
    artifacts = state.data.get("artifacts") or {}
    packages = (state.data.get("plan") or {}).get("packages") or []
    if "pypi" in packages and state.done("upload_pypi"):
        missing = publish_pypi.reconcile(
            state.data["plan"]["pypi_version"], artifacts.get("pypi") or {})
        if missing:
            raise ReleaseError(
                "upload_pypi",
                f"状态记录上传已完成，但远端缺少工件：{missing}；拒绝重传")
    if "npm" in packages and state.done("upload_npm"):
        npm_artifact = artifacts.get("npm") or {}
        if not publish_npm.reconcile(
                state.data["plan"]["npm_version"],
                npm_artifact.get("integrity", ""), runner):
            raise ReleaseError(
                "upload_npm",
                "状态记录 npm 上传已完成，但远端版本缺失；拒绝重传")


def release_paths(packages: list[str]) -> list[str]:
    paths: list[str] = []
    if "pypi" in packages:
        paths.extend(publish_pypi.RELEASE_FILES)
    if "npm" in packages:
        paths.extend(publish_npm.RELEASE_FILES)
    return paths


def _hash_tree(root: Path) -> dict[str, str]:
    if not root.is_dir():
        return {}
    return {
        path.relative_to(root).as_posix(): sha256_file(path)
        for path in sorted(root.rglob("*")) if path.is_file()
    }


def staging_identity(staging: Path, files: list[str],
                     include_dist: bool) -> dict:
    """Content identity of everything a release commit would take from staging."""
    staged: dict[str, str] = {}
    for relative in files:
        path = Path(staging) / relative
        if not path.is_file():
            raise ReleaseError("prepare", f"暂存树缺少发布文件：{relative}")
        staged[relative] = sha256_file(path)
    dist = _hash_tree(Path(staging) / "web_ui" / "dist") if include_dist else None
    if include_dist and not dist:
        raise ReleaseError("prepare", "暂存树缺少 web_ui/dist 构建产物")
    return {"files": staged, "dist": dist}


def _identity_diff(recorded: dict, current: dict) -> str:
    details: list[str] = []
    old_files = recorded.get("files") or {}
    new_files = current.get("files") or {}
    for name in sorted(set(old_files) | set(new_files)):
        if old_files.get(name) != new_files.get(name):
            details.append(f"文件 {name}")
    old_dist = recorded.get("dist")
    new_dist = current.get("dist")
    if old_dist is not None or new_dist is not None:
        old_dist = old_dist or {}
        new_dist = new_dist or {}
        for name in sorted(set(old_dist) | set(new_dist)):
            if old_dist.get(name) != new_dist.get(name):
                details.append(f"web_ui/dist/{name}")
                if len(details) >= 6:
                    break
    return "；".join(details[:8]) or "未知差异"


def _verify_local(staging: Path, state: ReleaseState,
                  packages: list[str]) -> None:
    """Verify frozen artifacts *and* the staged source content identity.

    A resume must not commit (or adopt) source/dist content that differs from
    what was prepared and recorded — including missing, extra or deleted files.
    """
    artifacts = state.data.get("artifacts") or {}
    if "pypi" in packages:
        publish_pypi.verify_local(staging, artifacts.get("pypi") or {})
    if "npm" in packages:
        publish_npm.verify_local(staging, artifacts.get("npm") or {})
    recorded = state.data.get("staging_identity")
    if not recorded:
        raise ReleaseError(
            "resume",
            "状态缺少暂存内容身份（staging_identity）；无法证明待提交内容未被修改，"
            "拒绝继续",
        )
    include_dist = "pypi" in packages
    try:
        current = staging_identity(staging, release_paths(packages), include_dist)
    except ReleaseError as exc:
        raise ReleaseError(
            "resume",
            f"暂存内容与冻结身份不一致：{exc}；拒绝提交或采纳未知内容",
        ) from exc
    if current != recorded:
        raise ReleaseError(
            "resume",
            "暂存源码/构建产物与冻结身份不一致："
            + _identity_diff(recorded, current)
            + "；拒绝提交或采纳未知内容",
        )


def _upload_need(plan: dict, artifacts: dict, runner: Runner) -> set[str]:
    need: set[str] = set()
    if "pypi" in plan["packages"]:
        if publish_pypi.reconcile(
                plan["pypi_version"], artifacts["pypi"]):
            need.add("pypi")
    if "npm" in plan["packages"]:
        npm_artifact = artifacts["npm"]
        if not publish_npm.reconcile(
                plan["npm_version"], npm_artifact["integrity"], runner):
            need.add("npm")
    return need
