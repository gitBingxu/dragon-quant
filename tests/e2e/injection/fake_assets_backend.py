"""Adapt ``S3Backend`` to ``LocalDirBackend(<root>/remote)`` in E2E children.

The real assets-sync code path is exercised; only the object-storage transport is
swapped.  Every operation is appended to ``DQ_E2E_CALL_LOG`` so tests can prove
which remote calls (if any) a command made.

``DQ_E2E_BACKEND_MODE=error`` makes every operation raise the real
``StorageError``, letting later milestones cover failure branches without any
network fallback.  Application imports stay function-local so this module is
stdlib-only at import time.
"""

from __future__ import annotations

import json
import os
from pathlib import Path


def _log(operation: str, key: str, **fields: object) -> None:
    path = os.environ.get("DQ_E2E_CALL_LOG")
    if not path:
        return
    record = {
        "provider": "assets_backend",
        "method": operation,
        "key": key,
        "pid": os.getpid(),
    }
    record.update(fields)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def _root() -> Path:
    root = os.environ.get("DQ_E2E_ROOT")
    if not root:
        raise RuntimeError("DQ_E2E_ROOT 缺失，拒绝初始化 fake assets backend")
    sandbox = Path(root).resolve()
    remote = (sandbox / "remote").resolve()
    if remote != sandbox and sandbox not in remote.parents:
        raise RuntimeError(f"assets backend 目录越界: {remote}")
    return remote


def _check_mode() -> None:
    if os.environ.get("DQ_E2E_BACKEND_MODE", "ok").strip().lower() != "error":
        return
    from dragon_quant.assets_sync.errors import StorageError

    raise StorageError("E2E injected assets backend failure")


class LocalBackendAdapter:
    """Drop-in surface of ``S3Backend`` backed by the sandbox ``remote/`` dir."""

    def __init__(
        self,
        config,
        *,
        timeout: float = 60.0,
        max_attempts: int = 5,
        sleep=None,
    ) -> None:
        from dragon_quant.assets_sync.backends.local import LocalDirBackend

        self._impl = LocalDirBackend(_root())
        self.config = config
        self.timeout = timeout
        self.max_attempts = max_attempts
        self._sleep = sleep

    def put(self, key, data, metadata=None, content_md5=None):  # type: ignore[no-untyped-def]
        _check_mode()
        _log("put", key, size=len(data) if data is not None else None)
        return self._impl.put(
            key, data, metadata=metadata, content_md5=content_md5
        )

    def get(self, key):  # type: ignore[no-untyped-def]
        _check_mode()
        _log("get", key)
        return self._impl.get(key)

    def head(self, key):  # type: ignore[no-untyped-def]
        _check_mode()
        _log("head", key)
        return self._impl.head(key)

    def list(self, prefix):  # type: ignore[no-untyped-def]
        _check_mode()
        _log("list", prefix)
        return self._impl.list(prefix)

    def delete(self, key):  # type: ignore[no-untyped-def]
        _check_mode()
        _log("delete", key)
        return self._impl.delete(key)


def patch_s3_module(module) -> None:  # type: ignore[no-untyped-def]
    if getattr(module, "E2E_PATCHED", False):
        return
    module.S3Backend = LocalBackendAdapter
    module.E2E_PATCHED = True
