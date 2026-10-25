"""Deterministic JSON/text canonicalisation for E2E assertions.

Standard library only, no application imports.  The helpers are intentionally
small: later milestones use them to normalise sandbox paths and compare pinned
golden payloads without widening tolerances.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Iterable, Mapping


def dumps(payload: Any) -> str:
    """Compact, sorted, NaN-rejecting JSON (stable across runs)."""

    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def load_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def normalize_paths(text: str, root: str | Path, token: str = "<ROOT>") -> str:
    """Replace the sandbox root (and its resolved form) with ``token``."""

    output = normalize_newlines(str(text))
    candidates = {str(root), str(Path(root).resolve()), str(Path(root))}
    for candidate in sorted(candidates, key=len, reverse=True):
        if candidate:
            output = output.replace(candidate, token)
    return output


def normalize_text(
    text: str, replacements: Iterable[tuple[str, str]] = ()
) -> str:
    output = normalize_newlines(str(text))
    for old, new in replacements:
        if old:
            output = output.replace(old, new)
    return output


def floats_close(
    actual: float,
    expected: float,
    *,
    rel: float = 1e-9,
    abs_tol: float = 1e-12,
) -> bool:
    return math.isclose(actual, expected, rel_tol=rel, abs_tol=abs_tol)


def _fail(path: str, message: str) -> None:
    raise AssertionError(f"JSON 差异 @ {path}: {message}")


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def assert_json_equal(
    actual: Any,
    expected: Any,
    *,
    path: str = "$",
    rel: float = 1e-9,
    abs_tol: float = 1e-12,
) -> None:
    """Structural equality with float tolerance and strict bool/int separation."""

    if isinstance(expected, Mapping):
        if not isinstance(actual, Mapping):
            _fail(path, f"期望对象，实际 {type(actual).__name__}")
        missing = [key for key in expected if key not in actual]
        if missing:
            _fail(path, f"缺失键 {missing!r}")
        extra = [key for key in actual if key not in expected]
        if extra:
            _fail(path, f"多余键 {extra!r}")
        for key in expected:
            assert_json_equal(
                actual[key],
                expected[key],
                path=f"{path}.{key}",
                rel=rel,
                abs_tol=abs_tol,
            )
        return

    if isinstance(expected, list):
        if not isinstance(actual, list):
            _fail(path, f"期望数组，实际 {type(actual).__name__}")
        if len(actual) != len(expected):
            _fail(path, f"长度 {len(actual)} != {len(expected)}")
        for index, (actual_item, expected_item) in enumerate(zip(actual, expected)):
            assert_json_equal(
                actual_item,
                expected_item,
                path=f"{path}[{index}]",
                rel=rel,
                abs_tol=abs_tol,
            )
        return

    if isinstance(expected, bool):
        if not isinstance(actual, bool):
            _fail(path, f"期望 bool，实际 {type(actual).__name__}")
        if actual != expected:
            _fail(path, f"{actual!r} != {expected!r}")
        return

    if _is_number(expected):
        if not _is_number(actual):
            _fail(path, f"期望数值，实际 {type(actual).__name__}")
        if not floats_close(float(actual), float(expected), rel=rel, abs_tol=abs_tol):
            _fail(path, f"{actual!r} != {expected!r}")
        return

    if actual != expected:
        _fail(path, f"{actual!r} != {expected!r}")


def key_order(mapping: Mapping) -> list[str]:
    return list(mapping.keys())


def strip_keys(mapping: Mapping, keys: Iterable[str]) -> dict:
    dropped = set(keys)
    return {key: value for key, value in mapping.items() if key not in dropped}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()
