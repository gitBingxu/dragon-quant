# M8 Import Side-Effect Removal and Lazy Storage Initialization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make importing the package and requesting CLI help free of filesystem/database side effects while preserving automatic, idempotent initialization on the first real storage access.

**Architecture:** `storage/paths.py` computes paths without creating them; `ensure_data_dirs()` performs creation on demand. `storage/db.py` removes import-time `init_db()` and uses a locked first-connection schema guard. The package root uses PEP 562 for lazy `scan`/`run_review` exports, while cache, reports, assets, manager, and web entry points ensure directories only when actually used.

**Tech Stack:** Python 3.14 sqlite3/threading, PEP 562 lazy imports, existing storage APIs, isolated subprocess E2E and temporary SQLite tests.

**Spec:** User-approved B1 and `/tmp/dragon-quant-architecture-solution/migration-plan.md` M8 plus `solution.md` §6.5.

## Global Constraints

- `import dragon_quant`, `import dragon_quant.storage.paths`, `python -m dragon_quant -h`, and `-v` must create no data directory, database, WAL/SHM, cache, report, or cookie file.
- First real storage access must still create the required directories/schema exactly once under the existing `DQ_DATA_DIR` semantics. `init_db()` remains a public idempotent API.
- `assets_db` read behavior (`create=False`) must not be changed into eager creation; write/create paths ensure directories explicitly.
- Existing `db._connect`, `assets_db._connect`, and `patch_storage_dbs` test seams remain valid. `init_db()` must also work with patched connections without recursive `_connect` calls.
- `scan` / `review` algorithms, CLI output, database schemas/table names, and all non-import golden remain unchanged.
- Only tests specifically documenting import-time directory/DB creation may change to the approved B1 expectation. No other golden or behavioral assertion may be relaxed.
- No real database content access, network, inline application-import evidence, git commit/push/PR, or process artifacts. Any real metadata sentinel change stops work immediately.

## Review Focus

- Concurrent first connections must not recursively call `_connect`, duplicate schema work, or leak a partially ready flag.
- A failed schema initialization must not leave `_schema_ready=True`; the next connection must be able to retry.
- Direct library calls to storage APIs must work without a CLI-specific initialization hook.
- `review --ui-only`, `dragons`, and other read paths must still initialize when their command semantics require an existing schema, while help remains side-effect free.
- Lazy package-root exports must preserve `from dragon_quant import scan, run_review` identity and avoid eager orchestrator/review imports.

---

### Task 1: Lazy paths and schema-safe storage connections

**Files:**
- Modify: `dragon_quant/storage/paths.py`
- Modify: `dragon_quant/storage/db.py`
- Modify: `dragon_quant/storage/assets_db.py`
- Modify: `dragon_quant/cache/data_cache.py`
- Modify: `dragon_quant/storage/manager.py`
- Modify: report-writing call sites only where they previously relied on import-time directories
- Modify: `web_ui/server.py` only for `_get_db` initialization if required
- Create: `tests/test_lazy_storage.py`

**Interfaces:**
- Produces: `ensure_data_dirs()` as an idempotent directory initializer
- Produces: first-connection schema initialization with a lock and failure-safe retry
- Preserves: `init_db()`, `_connect()`, and all storage function signatures/semantics after the first actual access

- [ ] **Step 1: Add failing path/connection tests**

Cover no directory creation on path import, first-connect schema creation, direct `init_db`, patched connections, failed-initialization retry, concurrent first access, assets create/read behavior, cache/report/manager write-time initialization, and web `_get_db` behavior.

- [ ] **Step 2: Implement on-demand initialization**

Remove import-time mkdir/init calls. Use a lock around the first schema check and call `_ensure_schema(conn)` directly rather than recursing into `_connect`. Keep `init_db()` idempotent and let actual read/write entry points ensure only what they need.

- [ ] **Step 3: Run focused storage and regression tests**

Run: `.venv/bin/python -m pytest tests/test_lazy_storage.py tests/test_storage.py tests/test_assets_db.py tests/test_cache.py tests/test_isolation.py -q`
Expected: all PASS; no import-time side effects; first-access schemas match the old ones.

### Task 2: Lazy package root, approved E2E expectation update, and final gate

**Files:**
- Modify: `dragon_quant/__init__.py`
- Modify: `tests/e2e/test_cli_global.py`, `tests/e2e/test_logs_data_storage.py`, or only the specific B1 expectations identified by current failures
- Modify: `tests/e2e/coverage-map.json` only for required notes/new tests
- Modify: `AGENTS.md` and `README.md` only for the approved import-initialization behavior

**Interfaces:**
- Produces: lazy `scan` and `run_review` attributes with unchanged function identity
- Produces: B1 evidence showing help/version are side-effect free while actual storage commands still initialize correctly

- [ ] **Step 1: Add failing package-root and B1 behavior tests**

Assert package-root imports do not load orchestrator/review/storage eagerly, exported functions retain identity, help/version create nothing, and direct library/first-storage calls still initialize. Do not weaken any unrelated CLI golden.

- [ ] **Step 2: Implement lazy root exports and update only B1 documentation/expectations**

Use PEP 562 without adding a second scan/review wrapper. Document that initialization moved from import time to first real storage access. Any changed E2E expectation must name B1 explicitly in its comment and node report.

- [ ] **Step 3: Run complete M8 verification**

Run focused lazy-storage/CLI tests, full E2E, full unit suite, offline `npm test`, ruff on changed files, and M0 self-check. Expected: all PASS; help/version create zero paths; storage behavior and all other golden remain unchanged; no new import cycle.

- [ ] **Step 4: Run code-self-review and hand off**

Report first-access guarantees, failure/retry behavior, exact B1 expectation changes, full test evidence, and any remaining code path that still initializes at import. M9 must not begin until main accepts M8.
