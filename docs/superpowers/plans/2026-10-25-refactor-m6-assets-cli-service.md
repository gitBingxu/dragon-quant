# M6 Assets CLI Service Extraction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move assets backend/source/path assembly and action dispatch out of the command module while preserving all sync, restore, backfill, error, and output contracts.

**Architecture:** `assets_sync/cli_service.py` becomes the CLI-facing application service that constructs the default S3 backend or accepts an injected backend and coordinates the existing sync service. `commands/assets.py` remains a thin parameter/output adapter. The cohesive `assets_sync/service.py` is not split.

**Tech Stack:** Python 3.14, existing ObjectBackend/S3Backend/LocalDirBackend and TableSpec registry, temporary LocalDirBackend E2E remote and SQLite fixtures.

**Spec:** `/tmp/dragon-quant-architecture-solution/migration-plan.md` M6 and `/tmp/dragon-quant-architecture-solution/solution.md` §10.6.

## Global Constraints

- Preserve doctor/push/pull/status/verify/backfill payload shape, JSON key order, human stderr channel, exit codes 0/1/2, state/manifest/schema, and row-level merge behavior.
- Default production behavior still constructs S3Backend with existing credentials; E2E continues to use only temporary LocalDirBackend.
- `assets_sync/service.py`, table specs, manifests, codec, credentials behavior, and sync algorithms are not split or rewritten.
- `cli_service` must not import `commands`, `cli`, or `app`; command code must not construct backends directly.
- `cli_commands._cmd_assets*` shims remain functional. Do not edit E2E golden, run real network/cloud/smoke, execute git, or create process artifacts.
- All business evidence must come from pytest/harness; no inline application-import scripts. Any real metadata sentinel change is an immediate stop condition.

## Review Focus

- `pull --apply --target-dir` must never write the main temporary database, and prune must delete only mirror orphans.
- Missing credentials must fail before any backend call; lock conflicts must not produce remote writes.
- Backend injection must cover every remote action, not just push.
- JSON remains on stdout and human summaries remain on stderr; do not unify channels during extraction.
- The command may be thin without forcing a new result DTO if the existing output structure is simpler; avoid unnecessary abstraction.

---

### Task 1: CLI application service and backend injection

**Files:**
- Create: `dragon_quant/assets_sync/cli_service.py`
- Modify: `dragon_quant/assets_sync/__init__.py`
- Create: `tests/test_assets_cli_service.py`

**Interfaces:**
- Produces: `run(action: str, args: argparse.Namespace, *, backend: ObjectBackend | None = None, services: Services | None = None) -> int` (or a small result type only if the current output flow proves it necessary)
- Consumes: existing sync service, source builders, credential loader, and backend interface
- Preserves: current `S3Backend` default and all action behavior

- [ ] **Step 1: Write failing application-service tests**

Cover every action’s backend use, default S3 construction, LocalDirBackend injection, missing credentials, lock handling, and representative push/pull/status/verify/backfill flows. Assert no command/cli/app imports and no duplicate sync algorithms.

- [ ] **Step 2: Move assembly and action dispatch into cli_service**

Move backend/source/path construction and date/action/error coordination from `commands/assets.py` with minimal changes. Keep output ownership simple and consistent with the current flow; do not invent a second assets service layer.

- [ ] **Step 3: Run service and assets E2E tests**

Run: `.venv/bin/python -m pytest tests/test_assets_cli_service.py tests/test_assets_sync_cli.py tests/e2e/test_assets.py -q`
Expected: all PASS, including all 18 assets scenarios and remote-layout golden.

### Task 2: Thin assets command and final M6 gate

**Files:**
- Modify: `dragon_quant/commands/assets.py`
- Modify: focused command tests and `tests/e2e/test_architecture_rules.py`/`coverage-map.json` only for new assertions

**Interfaces:**
- Consumes: `assets_sync.cli_service.run`
- Produces: `handle(args, services)` with only argument mapping, output handling, and exit behavior

- [ ] **Step 1: Add thin-command and architecture tests**

Assert the command contains no backend construction, source registry assembly, or sync orchestration; the public cli_service is used for all actions; legacy shims still forward correctly.

- [ ] **Step 2: Replace the command body with the adapter**

Keep only CLI-specific presentation and argument handling. Do not move assets helpers into `commands/_shared.py` and do not split `assets_sync/service.py`.

- [ ] **Step 3: Run complete M6 verification**

Run focused service/command tests, full E2E, full unit suite, offline `npm test`, ruff on changed files, and M0 self-check. Expected: all PASS; golden unchanged; no real backend/network access; no new dependency cycle.

- [ ] **Step 4: Run code-self-review and hand off**

Report cli_service/command responsibilities, backend injection coverage, dangerous-path evidence, full test results, and deviations. M7 must not begin until main accepts M6.
