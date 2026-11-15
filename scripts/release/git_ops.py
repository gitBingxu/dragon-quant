"""Git-side operations for the unified release flow.

Only the orchestrator mutates Git.  Every function here is either a read-only
probe or an explicit, exactly-scoped mutation (write release files, commit,
tag, atomic push, GitHub Release).
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from release.common import (
    ReleaseError,
    Runner,
    redact,
)


# ─── probes ───────────────────────────────────────────────────────────────


def git(repo: Path, runner: Runner, *args: str, stage: str = "git",
        env: dict | None = None,
        allow_failure: bool = False) -> subprocess.CompletedProcess:
    return runner.run(
        ["git", "-C", str(repo), *args],
        stage=stage,
        env=env,
        scrub=True,
        allow_failure=allow_failure,
    )


def git_dir(repo: Path, runner: Runner) -> Path:
    return Path(git(repo, runner, "rev-parse", "--absolute-git-dir",
                    stage="preflight").stdout.strip())


def git_common_dir(repo: Path, runner: Runner) -> Path:
    """Shared git directory (the same for every linked worktree)."""
    raw = git(repo, runner, "rev-parse", "--git-common-dir",
              stage="preflight").stdout.strip()
    path = Path(raw)
    if not path.is_absolute():
        path = (Path(repo) / path).resolve()
    return path


def head_commit(repo: Path, runner: Runner, stage: str = "resume") -> str:
    return git(repo, runner, "rev-parse", "HEAD", stage=stage).stdout.strip()


def tracked_files(repo: Path, runner: Runner) -> list[str]:
    raw = git(repo, runner, "ls-files", "-z", stage="prepare").stdout
    return [item for item in raw.split("\0") if item]


def check_clean_worktree(repo: Path, runner: Runner) -> None:
    status = git(repo, runner, "status", "--porcelain", stage="preflight").stdout.strip()
    if status:
        raise ReleaseError(
            "preflight",
            "工作区不干净（含暂存/未跟踪文件），发布前请先提交或清理：\n" + status,
        )
    top = git(repo, runner, "rev-parse", "--show-toplevel",
              stage="preflight").stdout.strip()
    if Path(top).resolve() != repo.resolve():
        raise ReleaseError("preflight", f"--repo-root 不是 git 工作区根：{top}")


def check_resume_worktree(repo: Path, runner: Runner, files: list[str],
                          include_dist: bool) -> None:
    """Resume may see a half-written release; only release paths may be dirty."""
    status = git(repo, runner, "status", "--porcelain", stage="preflight").stdout
    if not status.strip():
        return
    allowed = set(files)
    offenders: list[str] = []
    for line in status.splitlines():
        path = line[3:].strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        if path in allowed:
            continue
        if include_dist and (path.startswith("web_ui/dist/")
                             or path.startswith("web_ui/dist.__release_")):
            continue
        offenders.append(path)
    if offenders:
        raise ReleaseError(
            "preflight",
            "工作区含发布范围外的改动，拒绝恢复：\n" + "\n".join(offenders[:10]),
        )
    top = git(repo, runner, "rev-parse", "--show-toplevel",
              stage="preflight").stdout.strip()
    if Path(top).resolve() != repo.resolve():
        raise ReleaseError("preflight", f"--repo-root 不是 git 工作区根：{top}")


def _current_upstream(repo: Path, runner: Runner) -> str | None:
    result = git(repo, runner, "rev-parse", "--abbrev-ref", "--symbolic-full-name",
                 "@{u}", stage="preflight", allow_failure=True)
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _normalize_push_url(url: str) -> str:
    """Strip http(s) userinfo before hashing; never persist the raw URL."""
    if "://" not in url:
        return url
    scheme, rest = url.split("://", 1)
    authority, sep, path = rest.partition("/")
    if "@" in authority:
        authority = authority.rsplit("@", 1)[1]
    return f"{scheme}://{authority}{sep}{path}"


def push_target_fingerprint(repo: Path, runner: Runner) -> str:
    result = git(repo, runner, "remote", "get-url", "--push", "origin",
                 stage="preflight", allow_failure=True)
    if result.returncode != 0 or not result.stdout.strip():
        raise ReleaseError("preflight", "未找到 origin 的 push URL；发布固定推送到 origin")
    normalized = _normalize_push_url(result.stdout.strip())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def resolve_identity(repo: Path, runner: Runner) -> dict:
    """Current immutable release identity; fixed ``origin/<branch>`` contract."""
    branch = git(repo, runner, "rev-parse", "--abbrev-ref",
                 "HEAD", stage="preflight").stdout.strip()
    if not branch or branch == "HEAD":
        raise ReleaseError(
            "preflight", "当前处于 detached HEAD；发布必须基于有 upstream 的命名分支")
    upstream = _current_upstream(repo, runner)
    if not upstream:
        raise ReleaseError(
            "preflight", f"分支 {branch} 没有 upstream；请先设置 origin/{branch} 跟踪分支")
    expected = f"origin/{branch}"
    if upstream != expected:
        raise ReleaseError(
            "preflight",
            f"当前 upstream 为 {upstream}，与固定发布目标 {expected} 不一致；"
            "请在 git 中显式调整跟踪分支或分支名后再发布（脚本不会修改 git 配置）",
        )
    return {
        "branch": branch,
        "upstream": upstream,
        "push_target_fingerprint": push_target_fingerprint(repo, runner),
    }


def verify_recorded_identity(repo: Path, runner: Runner, state) -> dict:
    """Read-only resume precheck: recorded identity must still match."""
    recorded_upstream = state.data.get("upstream")
    recorded_target = state.data.get("push_target_fingerprint")
    if not recorded_upstream or not recorded_target:
        raise ReleaseError(
            "resume",
            "旧状态缺少 upstream / push target 身份，无法证明同一发布目标；"
            "请人工核对后使用新的状态目录重新发布，脚本不会补写当前值",
        )
    current = resolve_identity(repo, runner)
    if current["branch"] != state.data.get("branch"):
        raise ReleaseError(
            "resume",
            f"分支已变化：记录 {state.data.get('branch')}，当前 {current['branch']}")
    if current["upstream"] != recorded_upstream:
        raise ReleaseError(
            "resume",
            f"upstream 已变化：记录 {recorded_upstream}，当前 {current['upstream']}；"
            "拒绝继续，也不会覆盖原始记录",
        )
    if current["push_target_fingerprint"] != recorded_target:
        raise ReleaseError("resume", "origin push URL 指纹与记录不一致；拒绝继续")
    return current


def local_tag_commit(repo: Path, runner: Runner, tag: str) -> str | None:
    exists = git(repo, runner, "tag", "--list", tag, stage="resume").stdout.strip()
    if not exists:
        return None
    result = git(repo, runner, "rev-parse", f"refs/tags/{tag}^{{}}",
                 stage="resume", allow_failure=True)
    return result.stdout.strip() or None


def check_new_tags(repo: Path, runner: Runner, tags: list[str]) -> None:
    for tag in tags:
        if local_tag_commit(repo, runner, tag):
            raise ReleaseError("preflight", f"本地已存在标签 {tag}")
        remote = git(repo, runner, "ls-remote", "--exit-code", "--tags", "origin",
                     f"refs/tags/{tag}", stage="preflight", allow_failure=True)
        if remote.returncode == 0 and remote.stdout.strip():
            raise ReleaseError("preflight", f"远端已存在标签 {tag}")
        if remote.returncode not in (0, 2):
            raise ReleaseError(
                "preflight",
                f"无法确认远端标签状态（不能当作不存在）："
                f"{redact(remote.stderr, runner.secrets)}",
            )


def ls_remote(repo: Path, runner: Runner, ref: str) -> dict[str, str]:
    result = git(repo, runner, "ls-remote", "origin", ref, stage="resume",
                 allow_failure=True)
    if result.returncode != 0:
        detail = redact((result.stderr or result.stdout or "").strip(), runner.secrets)
        raise ReleaseError("resume", f"无法核验远端引用 {ref}：{detail}")
    mapping: dict[str, str] = {}
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) == 2:
            mapping[parts[1]] = parts[0]
    return mapping


def remote_branch_commit(repo: Path, runner: Runner, branch: str) -> str | None:
    refs = ls_remote(repo, runner, f"refs/heads/{branch}")
    return refs.get(f"refs/heads/{branch}")


def remote_tag_commit(repo: Path, runner: Runner, tag: str) -> str | None:
    refs = ls_remote(repo, runner, f"refs/tags/{tag}*")
    # annotated tag: peeled ``^{}`` is the commit; lightweight tag points directly.
    return refs.get(f"refs/tags/{tag}^{{}}") or refs.get(f"refs/tags/{tag}")


# ─── exact release commit ─────────────────────────────────────────────────


class Writeback:
    """Backups for an in-progress exact-file rewrite (rollback only)."""

    def __init__(self, repo: Path, backup_dir: Path):
        self.repo = repo
        self.backup_dir = backup_dir
        self.dist_backup: Path | None = None

    def restore(self) -> None:
        for saved in sorted(self.backup_dir.rglob("*")):
            if saved.is_dir():
                continue
            relative = saved.relative_to(self.backup_dir)
            target = self.repo / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(saved, target)
        target = self.repo / "web_ui" / "dist"
        if self.dist_backup and self.dist_backup.exists():
            shutil.rmtree(target, ignore_errors=True)
            self.dist_backup.rename(target)

    def cleanup(self) -> None:
        shutil.rmtree(self.backup_dir, ignore_errors=True)
        if self.dist_backup:
            shutil.rmtree(self.dist_backup, ignore_errors=True)


def _swap_dist(repo: Path, staging: Path) -> Path | None:
    """Replace ``web_ui/dist`` with the staged build; return backup path."""
    staged = staging / "web_ui" / "dist"
    if not staged.is_dir():
        raise ReleaseError("commit", "暂存树缺少 web_ui/dist，无法提交前端产物")
    target = repo / "web_ui" / "dist"
    incoming = repo / "web_ui" / "dist.__release_new__"
    backup = repo / "web_ui" / "dist.__release_backup__"
    shutil.rmtree(incoming, ignore_errors=True)
    shutil.rmtree(backup, ignore_errors=True)
    shutil.copytree(staged, incoming)
    backup_path: Path | None = None
    if target.exists():
        target.rename(backup)
        backup_path = backup
    try:
        incoming.rename(target)
    except BaseException:
        if not target.exists() and backup.exists():
            backup.rename(target)
        raise
    return backup_path


def _blob_of_file(repo: Path, runner: Runner, path: Path) -> str:
    return git(repo, runner, "hash-object", "--", str(path),
               stage="commit").stdout.strip()


def _staged_tree_files(staging: Path, include_dist: bool) -> list[str]:
    """Every file the release commit will contain, relative to the repo root."""
    if not include_dist:
        return []
    dist = Path(staging) / "web_ui" / "dist"
    if not dist.is_dir():
        raise ReleaseError("commit", "暂存树缺少 web_ui/dist，无法计算发布树")
    return sorted(
        f"web_ui/dist/{path.relative_to(dist).as_posix()}"
        for path in dist.rglob("*") if path.is_file()
    )


def check_release_files(repo: Path, runner: Runner, staging: Path,
                        files: list[str]) -> None:
    """Only the old value or the exact target value may be present.

    A third content means somebody edited a release file outside the flow; do
    not silently overwrite it on resume.
    """
    for relative in files:
        staged = Path(staging) / relative
        target = repo / relative
        if not staged.is_file():
            raise ReleaseError("commit", f"暂存树缺少发布文件：{relative}")
        if not target.is_file():
            raise ReleaseError("commit", f"工作区缺少发布文件：{relative}")
        new_blob = _blob_of_file(repo, runner, staged)
        current_blob = _blob_of_file(repo, runner, target)
        old = git(repo, runner, "rev-parse", f"HEAD:{relative}",
                  stage="commit", allow_failure=True)
        old_blob = old.stdout.strip() if old.returncode == 0 else None
        if current_blob not in {old_blob, new_blob}:
            raise ReleaseError(
                "commit",
                f"{relative} 内容既非原版本也非目标版本；拒绝覆盖（请人工核对）",
            )


def apply_release_files(repo: Path, staging: Path, files: list[str],
                        include_dist: bool) -> Writeback:
    """Copy exactly ``files`` (and optionally dist) from staging into the repo."""
    backup_dir = Path(tempfile.mkdtemp(prefix="dq-release-backup-"))
    writeback = Writeback(repo.resolve(), backup_dir)
    try:
        for relative in files:
            source = staging / relative
            if not source.is_file():
                raise ReleaseError("commit", f"暂存树缺少发布文件：{relative}")
            target = repo / relative
            saved = backup_dir / relative
            saved.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, saved)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        if include_dist:
            writeback.dist_backup = _swap_dist(repo, staging)
    except BaseException:
        writeback.restore()
        writeback.cleanup()
        raise
    return writeback


def expected_tree(repo: Path, runner: Runner, staging: Path, files: list[str],
                  include_dist: bool, parent: str) -> str:
    """Tree hash the release commit will have.

    Computed from the *frozen parent* and the verified staging content — never
    from the current HEAD/worktree — so a resume can compare a commit that
    landed before its result was recorded without adopting unknown content.
    """
    scratch = Path(tempfile.mkdtemp(prefix="dq-release-index-"))
    env = {"GIT_INDEX_FILE": str(scratch / "index")}
    try:
        git(repo, runner, "read-tree", parent, stage="commit", env=env)
        if include_dist:
            # The build replaces web_ui/dist wholesale; drop stale base entries.
            git(repo, runner, "rm", "-q", "-r", "--cached", "--ignore-unmatch",
                "--", "web_ui/dist", stage="commit", env=env, allow_failure=True)
        targets = list(files)
        if include_dist:
            targets.extend(_staged_tree_files(staging, include_dist))
        for relative in targets:
            source = Path(staging) / relative
            if not source.is_file():
                raise ReleaseError("commit", f"暂存树缺少发布文件：{relative}")
            blob = git(repo, runner, "hash-object", "-w", "--", str(source),
                       stage="commit").stdout.strip()
            git(repo, runner, "update-index", "--add", "--cacheinfo",
                f"100644,{blob},{relative}", stage="commit", env=env)
        return git(repo, runner, "write-tree", stage="commit", env=env).stdout.strip()
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def commit_tree(repo: Path, runner: Runner, commit: str) -> str:
    return git(repo, runner, "rev-parse", f"{commit}^{{tree}}",
               stage="commit").stdout.strip()


def commit_release(repo: Path, runner: Runner, files: list[str],
                   include_dist: bool, message: str) -> tuple[str, bool]:
    """Stage exactly the release paths and commit. Returns ``(commit, changed)``."""
    subject = message.splitlines()[0]
    allowed = set(files)
    git(repo, runner, "add", "--", *files,
        *(["web_ui/dist"] if include_dist else []), stage="commit")
    staged = git(repo, runner, "diff", "--cached", "--name-only",
                 stage="commit").stdout.splitlines()
    extra = [name for name in staged
             if name not in allowed and not name.startswith("web_ui/dist/")]
    if extra:
        raise ReleaseError("commit", f"暂存区出现非发布文件，已中止：{extra[:5]}")
    if not staged:
        return head_commit(repo, runner, stage="commit"), False
    git(repo, runner, "commit", "-m", message, stage="commit")
    commit = head_commit(repo, runner, stage="commit")
    check = git(repo, runner, "log", "-1", "--pretty=%s", stage="commit").stdout.strip()
    if check != subject:
        raise ReleaseError("commit", "发布提交主题与预期不一致")
    return commit, True


def unstage(repo: Path, runner: Runner, files: list[str],
            include_dist: bool) -> None:
    git(repo, runner, "reset", "-q", "--", *files,
        *(["web_ui/dist"] if include_dist else []),
        stage="commit", allow_failure=True)


def detect_release_commit(repo: Path, runner: Runner, base: str,
                          tree: str, subject: str) -> str | None:
    """Adopt a commit created before the crash, or reject a foreign HEAD."""
    current = head_commit(repo, runner)
    if current == base:
        return None
    parents = git(repo, runner, "rev-list", "--parents", "-n", "1",
                  "HEAD", stage="resume").stdout.split()
    if len(parents) != 2 or parents[1] != base:
        raise ReleaseError(
            "resume",
            f"HEAD 已变化且不是基于 {base} 的发布提交：{current}；拒绝继续",
        )
    actual_tree = git(repo, runner, "rev-parse", "HEAD^{tree}",
                      stage="resume").stdout.strip()
    if actual_tree != tree:
        raise ReleaseError(
            "resume", "本地 HEAD 提交内容与预期发布内容不一致；拒绝继续")
    actual_subject = git(repo, runner, "log", "-1", "--pretty=%s",
                         stage="resume").stdout.strip()
    if actual_subject != subject:
        raise ReleaseError("resume", "本地 HEAD 提交主题与记录不一致；拒绝继续")
    return current


# ─── tags / push ──────────────────────────────────────────────────────────


def ensure_tags(repo: Path, runner: Runner, tags: list[str], commit: str) -> None:
    for tag in tags:
        existing = local_tag_commit(repo, runner, tag)
        if existing:
            if existing != commit:
                raise ReleaseError(
                    "tag", f"本地标签 {tag} 指向 {existing}，与发布提交 {commit} 不一致")
            continue
        git(repo, runner, "tag", tag, commit, stage="tag")


def atomic_push(repo: Path, runner: Runner, branch: str, commit: str,
                tags: list[str]) -> None:
    """Push exactly the recorded release object and tags (atomic, no fallback)."""
    result = git(
        repo, runner, "push", "--atomic", "origin",
        f"{commit}:refs/heads/{branch}",
        *[f"refs/tags/{tag}" for tag in tags],
        stage="push", allow_failure=True,
    )
    if result.returncode == 0:
        return
    detail = redact((result.stderr or result.stdout or "").strip(), runner.secrets)
    raise ReleaseError(
        "push",
        "原子推送失败（服务器不支持 --atomic 时不会退化为全标签推送）：\n" + detail,
    )


def is_ancestor(repo: Path, runner: Runner, ancestor: str, descendant: str) -> bool:
    result = git(repo, runner, "merge-base", "--is-ancestor", ancestor, descendant,
                 stage="resume", allow_failure=True)
    return result.returncode == 0


def reconcile_push(repo: Path, runner: Runner, branch: str, commit: str,
                   tags: list[str]) -> bool:
    """True when remote already matches; raise on any conflicting remote state."""
    remote_branch = remote_branch_commit(repo, runner, branch)
    if remote_branch and remote_branch != commit:
        # A normal fast-forward (remote at the release base) is not a conflict;
        # a diverged/unknown remote commit is.
        if not is_ancestor(repo, runner, remote_branch, commit):
            raise ReleaseError(
                "push",
                f"远端分支 {branch} 指向 {remote_branch}，与本次发布提交 {commit} 不一致；"
                "拒绝覆盖（不会非原子强推）",
            )
    branch_ok = remote_branch == commit
    tags_ok = True
    for tag in tags:
        remote = remote_tag_commit(repo, runner, tag)
        if remote and remote != commit:
            raise ReleaseError(
                "push", f"远端标签 {tag} 指向 {remote}，与发布提交 {commit} 不一致")
        tags_ok = tags_ok and remote == commit
    return branch_ok and tags_ok


# ─── GitHub Release ───────────────────────────────────────────────────────


def gh_available(runner: Runner) -> bool:
    return shutil.which("gh", path=runner.env_for().get("PATH")) is not None


def gh_release_view(repo: Path, runner: Runner, tag: str) -> dict | None:
    result = runner.run(
        ["gh", "release", "view", tag, "--json", "tagName,isDraft,body"],
        cwd=repo, stage="release", scrub=True, allow_failure=True,
    )
    if result.returncode != 0:
        detail = (result.stderr or "").lower()
        if "not found" in detail or "could not find" in detail or "http 404" in detail:
            return None
        raise ReleaseError(
            "release",
            "无法确认 GitHub Release 状态（网络失败不能当作不存在）："
            + redact((result.stderr or result.stdout or "").strip(), runner.secrets),
        )
    try:
        return json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise ReleaseError("release", f"无法解析 gh release view 输出：{exc}") from exc


def gh_release_create(repo: Path, runner: Runner, tag: str, title: str,
                      notes_path: Path) -> None:
    runner.run(
        ["gh", "release", "create", tag, "--title", title,
         "--notes-file", str(notes_path), "--verify-tag"],
        cwd=repo, stage="release", scrub=True,
    )
