# M10 Web Testability and Cookie Entrypoint Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Web server cleanly testable with ephemeral ports and shutdown support, and move the independent cookie CLI behind a thin, injectable entrypoint without changing user-facing behavior.

**Architecture:** `start_server` returns `HTTPServer` and defaults to `block=True`, preserving the existing blocking CLI path; tests use `block=False` and call `shutdown()`/`server_close()` themselves. `port=0` uses the OS-assigned port in logs and responses. `providers/cookie.py` keeps library functions and delegates its `__main__` path to `cookie_cli.main`, which accepts an optional fetch implementation for focused tests.

**Tech Stack:** Python stdlib `http.server` and threading, existing Web UI handlers, fake Playwright E2E injection, offline Node/Python test suites.

**Spec:** User-approved B2 and `/tmp/dragon-quant-architecture-solution/migration-plan.md` M10 plus `solution.md` §6.6.

## Global Constraints

- Existing `python -m web_ui.server`, `review --ui*`, and `review-account --ui*` remain blocking until interrupted and keep their output/API behavior.
- `start_server` returns the actual `HTTPServer`; `block=True` preserves current behavior, `block=False` enables test-controlled serve/shutdown. `port=0` prints and serves the assigned port, never port 0 in the URL.
- No real browser, external network, real Cookie, or production DB access. Web E2E uses localhost only and temporary databases.
- Cookie status/set/fetch output, exit codes, file locations, and independent `python -m dragon_quant.providers.cookie` behavior remain unchanged.
- Do not edit behavioral golden, run real network/smoke, execute git, or create process artifacts. All application evidence runs through pytest/harness.

## Review Focus

- Web startup failure and test failure must both close the server and release the port.
- Browser opening, if ever enabled, must happen only after the real assigned URL is known; E2E always uses `--no-browser`.
- Existing tests that patch `web_ui.server.start_server` must still observe compatible arguments and blocking behavior.
- The cookie package must not gain a second implementation of status/set/fetch; `cookie_cli` is the single CLI orchestration point and `cookie.py` remains the library.
- Fake Playwright success and failure must continue through the real independent entrypoint after the move.

---

### Task 1: HTTPServer lifecycle, ephemeral ports, and Web E2E migration

**Files:**
- Modify: `web_ui/server.py`
- Modify: `web_ui/__init__.py`
- Create: `tests/test_web_server_lifecycle.py`
- Modify: `tests/e2e/harness/web.py`
- Modify: `tests/e2e/test_web_api.py` only for the controlled port=0 migration and graceful-shutdown assertions
- Modify: `AGENTS.md` and `README.md` only for the approved B2 interface

**Interfaces:**
- Produces: `start_server(..., block=True) -> HTTPServer`
- Produces: real assigned URL when `port=0`
- Produces: `web_ui.start_server` as the package-level public entry

- [ ] **Step 1: Add failing lifecycle tests**

Cover default blocking semantics, `block=False` thread serving and shutdown, `port=0` real URL, startup failure cleanup, existing start_server patches, and no side effects on actual data outside the sandbox.

- [ ] **Step 2: Implement the server lifecycle**

Construct and return the server, derive the real URL from `server.server_address`, preserve blocking by default, and close on interruption. Keep request handlers and DB behavior unchanged. Export the public entry at package level.

- [ ] **Step 3: Migrate Web tests to ephemeral ports**

Replace preselected-port E2E with `port=0` where the command supports it. Parse the real URL from stdout, retain process-group cleanup, and add in-process graceful shutdown coverage. Do not remove failure-cleanup assertions.

- [ ] **Step 4: Run Web-focused and complete behavior gates**

Run lifecycle/Web E2E, full E2E and full unit suite. Expected: all PASS; no orphan process or occupied port; all API golden and blocking CLI behavior unchanged. Main accepts Task 1 before Task 2.

### Task 2: Cookie CLI extraction and final M10 gate

**Files:**
- Create: `dragon_quant/providers/cookie_cli.py`
- Modify: `dragon_quant/providers/cookie.py`
- Create or modify: `tests/test_providers_cookie.py` and cookie CLI tests

**Interfaces:**
- Produces: `cookie_cli.main(argv: Sequence[str] | None = None, *, fetch=fetch_xq) -> int | None`
- Produces: a thin `providers/cookie.py` `__main__` delegate while preserving library functions and existing import/patch compatibility

- [ ] **Step 1: Add failing cookie CLI tests**

Cover status/set/fetch success/empty/error/unknown action, injectable fetch, output/exit behavior, and the independent `__main__` path. Assert only one CLI orchestration implementation exists.

- [ ] **Step 2: Move CLI orchestration to cookie_cli**

Keep cookie storage/fetch library functions in `cookie.py`. Delegate the module’s main block to `cookie_cli.main`, use the optional fetch parameter only for tests, and preserve any old `main` import or monkeypatch behavior needed by current consumers.

- [ ] **Step 3: Run complete M10 verification**

Run cookie-focused tests, Web/API tests, full E2E, full unit suite, offline `npm test`, ruff on changed files, and M0 self-check. Expected: all PASS; real independent cookie entry works through fake Playwright; no real browser/network; Web ports are always released.

- [ ] **Step 4: Run code-self-review and hand off**

Report B2 lifecycle behavior, cookie entry responsibilities, full test evidence, and any remaining compatibility aliases reserved for M11. M11 must not begin until main accepts M10.
