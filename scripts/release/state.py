"""Persistent release state with an explicit, verifiable step machine.

Every side-effecting step records an *intent* (with the identity it expects to
produce) before acting and a *result* afterwards.  A resume never trusts a
``done`` boolean alone: it re-reads the real Git / registry / Release state and
only adopts a step when the observed facts match the recorded identity.
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass, field
from pathlib import Path

from release.common import (
    ReleaseError,
    SCHEMA_VERSION,
    atomic_write_json,
    atomic_write_text,
    now,
    plan_hash,
    read_json,
)

ACTIVE_POINTER_NAME = "dq-release-active.json"


def new_batch_id() -> str:
    from datetime import datetime, timezone

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{secrets.token_hex(3)}"


def batch_root(root: Path, batch_id: str) -> Path:
    return Path(root) / "batches" / batch_id


def batch_state_path(root: Path, batch_id: str) -> Path:
    return batch_root(root, batch_id) / "state.json"


def iter_batches(root: Path) -> list[tuple[Path, dict]]:
    """Return ``(state_path, data)`` for readable batches, oldest first."""
    batches = Path(root) / "batches"
    if not batches.is_dir():
        return []
    found: list[tuple[Path, dict]] = []
    for state_path in sorted(batches.glob("*/state.json")):
        try:
            data = read_json(state_path)
        except (OSError, json.JSONDecodeError) as exc:
            raise ReleaseError(
                "resume", f"无法读取发布状态 {state_path}: {exc}；请人工核对") from exc
        if isinstance(data, dict):
            found.append((state_path, data))
    return found


# ─── canonical active-batch pointer (git common dir) ──────────────────────


def active_pointer_path(common_dir: Path) -> Path:
    return Path(common_dir) / ACTIVE_POINTER_NAME


def read_active_pointer(common_dir: Path) -> dict | None:
    """Read the repository-wide active-batch pointer, or ``None``.

    The pointer lives in the git *common* dir, so linked worktrees of the same
    repository share it and a different ``--status-dir`` cannot hide an
    unfinished release.  A malformed pointer fails closed instead of guessing.
    """
    path = active_pointer_path(common_dir)
    if not path.is_file():
        return None
    try:
        data = read_json(path)
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseError(
            "resume",
            f"仓库活动批次指针无法读取：{path}: {exc}；拒绝猜测，请人工核对",
        ) from exc
    if not isinstance(data, dict) or not data.get("state_path"):
        raise ReleaseError(
            "resume", f"仓库活动批次指针内容不完整：{path}；请人工核对")
    return data


def write_active_pointer(common_dir: Path, *, state_path: Path, root: Path,
                         repo: Path, batch_id: str) -> None:
    atomic_write_json(
        active_pointer_path(common_dir),
        {
            "batch_id": batch_id,
            "state_path": str(Path(state_path).resolve()),
            "status_root": str(Path(root).resolve()),
            "repo": str(Path(repo).resolve()),
            "created_at": now(),
        },
        mode=0o600,
    )


def clear_active_pointer(common_dir: Path, batch_id: str) -> None:
    """Release the pointer only when it still names ``batch_id``."""
    path = active_pointer_path(common_dir)
    if not path.is_file():
        return
    data = read_active_pointer(common_dir) or {}
    if data.get("batch_id") != batch_id:
        raise ReleaseError(
            "resume",
            f"活动批次指针指向 {data.get('batch_id')}，与完成批次 {batch_id} 不一致；"
            "拒绝删除，请人工核对",
        )
    path.unlink(missing_ok=True)


@dataclass
class ReleaseState:
    path: Path
    data: dict = field(default_factory=dict)

    # ─── construction / loading ────────────────────────────────────────────
    @classmethod
    def new(cls, path: Path, *, batch_id: str, plan: dict, repo: Path) -> "ReleaseState":
        data = {
            "schema": SCHEMA_VERSION,
            "batch_id": batch_id,
            "created_at": now(),
            "repo": str(Path(repo).resolve()),
            "plan": plan,
            "plan_hash": plan_hash(plan),
            "base_commit": None,
            "branch": None,
            "upstream": None,
            "push_target_fingerprint": None,
            "staging": None,
            "artifacts": {},
            "release_notes": None,
            "steps": {},
            "notes": [],
            "complete": False,
        }
        return cls(path=Path(path), data=data)

    @classmethod
    def load(cls, path: Path) -> "ReleaseState":
        path = Path(path)
        if not path.is_file():
            raise ReleaseError("resume", f"发布状态不存在：{path}")
        try:
            data = read_json(path)
        except (OSError, json.JSONDecodeError) as exc:
            raise ReleaseError("resume", f"无法读取发布状态 {path}: {exc}") from exc
        if not isinstance(data, dict) or "schema" not in data:
            raise ReleaseError(
                "resume",
                f"{path} 不是统一发布状态（缺少 schema）；旧 PyPI 状态不能冒充新状态，"
                "请人工核对或使用新的状态目录",
            )
        if data.get("schema") != SCHEMA_VERSION:
            raise ReleaseError(
                "resume",
                f"旧发布状态 schema={data.get('schema')!r}，当前统一流程要求 "
                f"schema={SCHEMA_VERSION}；不会自动迁移或覆盖，请人工核对",
            )
        state = cls(path=path, data=data)
        state.verify_plan_integrity()
        return state

    def verify_plan_integrity(self) -> None:
        stored = self.data.get("plan_hash")
        if not stored or stored != plan_hash(self.data.get("plan") or {}):
            raise ReleaseError(
                "resume",
                "发布计划与记录的哈希不一致（状态文件被修改）；拒绝继续",
            )

    def verify_repo(self, repo: Path) -> None:
        recorded = self.data.get("repo")
        if recorded and Path(recorded).resolve() != Path(repo).resolve():
            raise ReleaseError(
                "resume",
                f"状态目录属于仓库 {recorded}，与当前 {Path(repo).resolve()} 不一致",
            )

    # ─── step machine ──────────────────────────────────────────────────────
    @property
    def steps(self) -> dict:
        return self.data.setdefault("steps", {})

    def entry(self, name: str) -> dict:
        return self.steps.get(name) or {}

    def done(self, name: str) -> bool:
        return bool(self.entry(name).get("done"))

    def intent(self, name: str) -> dict:
        return dict(self.entry(name).get("intent") or {})

    def result(self, name: str) -> dict:
        return dict(self.entry(name).get("result") or {})

    def begin(self, name: str, **intent) -> None:
        """Record an attempt without discarding a previously recorded success.

        A resume re-enters a step to *verify* it.  If the step was already
        recorded as done, the success fact and its result stay authoritative:
        callers use ``done()``/``result()`` to reconcile against the real world
        and must fail closed when a completed side effect has vanished.  Intent
        fields recorded by an earlier attempt are never silently overwritten —
        a conflicting identity is rejected instead.
        """
        entry = dict(self.entry(name))
        if entry.get("done"):
            entry["attempts"] = int(entry.get("attempts") or 0) + 1
            entry["rechecked_at"] = now()
            self.steps[name] = entry
            self.save()
            return
        previous = dict(entry.get("intent") or {})
        conflicts = {
            key: (previous[key], value)
            for key, value in intent.items()
            if key in previous and previous[key] != value
        }
        if conflicts:
            raise ReleaseError(
                "resume",
                f"步骤 {name} 的预期身份与已记录 intent 冲突：{conflicts}；拒绝覆盖",
            )
        entry.update(
            {
                "state": "intent",
                "done": False,
                "intent": {**previous, **intent},
                "attempts": int(entry.get("attempts") or 0) + 1,
                "started_at": now(),
            }
        )
        self.steps[name] = entry
        self.save()

    def finish(self, name: str, **result) -> None:
        entry = dict(self.entry(name))
        merged = {**(entry.get("result") or {}), **result}
        entry.update(
            {
                "state": "done",
                "done": True,
                "result": merged,
                "finished_at": now(),
            }
        )
        entry.pop("error", None)
        self.steps[name] = entry
        self.save()

    def skip(self, name: str, message: str, **result) -> None:
        entry = dict(self.entry(name))
        entry.update(
            {
                "state": "skipped",
                "skipped": True,
                "note": message,
                "result": {**(entry.get("result") or {}), **result},
                "updated_at": now(),
            }
        )
        # A graceful pending must not erase a previously recorded success.
        if not entry.get("done"):
            entry["done"] = False
        self.steps[name] = entry
        self.save()

    def fail(self, name: str, message: str) -> None:
        entry = dict(self.entry(name))
        entry.update(
            {
                "state": "failed",
                "error": message,
                "failed_at": now(),
            }
        )
        # A failure while re-verifying a recorded success keeps that fact:
        # resume must compare against it instead of forgetting it.
        if not entry.get("done"):
            entry["done"] = False
        self.steps[name] = entry
        self.save()

    def note(self, message: str) -> None:
        self.data.setdefault("notes", []).append({"at": now(), "message": message})
        self.save()

    # ─── persistence ───────────────────────────────────────────────────────
    def save(self) -> None:
        self.data["updated_at"] = now()
        text = json.dumps(self.data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        atomic_write_text(self.path, text, mode=0o600)
