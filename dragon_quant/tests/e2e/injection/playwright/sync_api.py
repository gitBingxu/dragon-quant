"""Fake ``playwright.sync_api`` implementing only the cookie-fetch surface."""

from __future__ import annotations

import json
import os


def _log(method: str, **fields: object) -> None:
    path = os.environ.get("DQ_E2E_CALL_LOG")
    if not path:
        return
    record = {"provider": "playwright", "method": method, "pid": os.getpid()}
    record.update(fields)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def _mode() -> str:
    return os.environ.get("DQ_E2E_COOKIE_FETCH", "ok").strip().lower()


class _Page:
    def goto(self, url: str, **kwargs: object) -> None:
        _log("page.goto", url=str(url))

    def wait_for_timeout(self, milliseconds: int) -> None:
        _log("page.wait_for_timeout", milliseconds=int(milliseconds))


class _Context:
    def add_init_script(self, script: str) -> None:
        _log("context.add_init_script")

    def new_page(self) -> _Page:
        _log("context.new_page")
        return _Page()

    def cookies(self) -> list[dict[str, str]]:
        _log("context.cookies")
        if _mode() == "empty":
            return []
        return [
            {"name": "xq_a_token", "value": "e2e-synthetic-token"},
            {"name": "u", "value": "e2e-synthetic-user"},
        ]


class _Browser:
    def new_context(self, **kwargs: object) -> _Context:
        _log("browser.new_context", **{key: str(value) for key, value in kwargs.items()})
        return _Context()

    def close(self) -> None:
        _log("browser.close")


class _Chromium:
    def launch(self, **kwargs: object) -> _Browser:
        _log("chromium.launch", headless=kwargs.get("headless"))
        if _mode() == "error":
            raise RuntimeError("E2E fake playwright failure")
        return _Browser()


class _Playwright:
    chromium = _Chromium()


class _SyncPlaywright:
    def __enter__(self) -> _Playwright:
        _log("sync_playwright.enter")
        return _Playwright()

    def __exit__(self, exc_type, exc, traceback) -> bool:  # type: ignore[no-untyped-def]
        _log("sync_playwright.exit", raised=exc_type is not None)
        return False


def sync_playwright() -> _SyncPlaywright:
    return _SyncPlaywright()
