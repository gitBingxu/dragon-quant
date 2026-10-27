"""Real-data-directory metadata sentinel.

Only ``os.stat`` is used: this module never opens, reads, queries, migrates or
deletes a real database.  Any metadata difference fails closed; it cannot prove
*who* changed the directory, so E2E requires an exclusive environment.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping

try:  # package import (pytest) and script import (runner --self-check)
    from .sandbox import E2EHarnessError, default_real_paths
except ImportError:  # pragma: no cover - script-mode bootstrap
    from sandbox import E2EHarnessError, default_real_paths  # type: ignore

FILES = (
    "dragon.db",
    "dragon.db-wal",
    "dragon.db-shm",
    "assets.db",
    "assets.db-wal",
    "assets.db-shm",
    "prep.db",
)


class RealDataSentinel:
    """Snapshot real data directory metadata and compare two snapshots."""

    def __init__(
        self,
        data_dir: str | os.PathLike[str] | None = None,
        *,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        if data_dir is None:
            self.data_dir = default_real_paths(environ)["data"]
        else:
            self.data_dir = Path(data_dir).resolve()

    def snapshot(self) -> dict[str, tuple]:
        """Return ``{name: metadata tuple}``; ``__dir__`` covers the root."""

        result: dict[str, tuple] = {}
        try:
            stat = os.stat(self.data_dir)
        except FileNotFoundError:
            result["__dir__"] = (False, 0, 0)
        except OSError as exc:
            result["__dir__"] = (True, -1, type(exc).__name__)
        else:
            result["__dir__"] = (True, stat.st_mtime_ns, stat.st_mode)

        for name in FILES:
            path = self.data_dir / name
            try:
                stat = os.stat(path)
            except FileNotFoundError:
                result[name] = (False, 0, 0, 0)
            except OSError as exc:
                result[name] = (True, -1, -1, type(exc).__name__)
            else:
                result[name] = (True, stat.st_size, stat.st_mtime_ns, stat.st_mode)
        return result

    def assert_unchanged(
        self, before: Mapping[str, tuple], after: Mapping[str, tuple]
    ) -> None:
        if os.environ.get("DQ_E2E_SKIP_SENTINEL") == "1":
            return
        differences = []
        for name in ("__dir__", *FILES):
            old = before.get(name)
            new = after.get(name)
            if old != new:
                differences.append(f"  {name}: {old!r} -> {new!r}")
        if differences:
            raise E2EHarnessError(
                "真实数据目录元数据发生变化（E2E 需要独占环境，禁止自动重试）:\n"
                f"  dir: {self.data_dir}\n" + "\n".join(differences)
            )
