# M5 Prep Package Decomposition Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split the 1626-line `review_account/prep.py` into a navigable package for orchestration, collection, computation, and rebuild while preserving the public import path, patch behavior, snapshot semantics, and CLI output.

**Architecture:** `review_account.prep` becomes a package whose `__init__.py` exposes `run_prep`/`PrepError` and temporary compatibility re-exports. M5A separates pure compute and rebuild, leaving collection+orchestration in a transitional service. M5B separates collectors from the service. Existing provider patches continue to affect runtime lookup rather than being preserved only as inert names.

**Tech Stack:** Python 3.14 packages and relative imports, existing prep collectors/compute, M2 optional services, temporary assets.db fixtures and E2E golden.

**Spec:** `/tmp/dragon-quant-architecture-solution/migration-plan.md` M5 and `/tmp/dragon-quant-architecture-solution/solution.md` §10.5.

## Global Constraints

- `from dragon_quant.review_account.prep import PrepError, run_prep` and existing provider-class patch targets remain valid throughout the migration.
- Two-stage collection/compute, `ASSET_COLLECTORS`, `same_day_only`, collection report merging, snapshot `version=0.6`, macro caliber, and all CLI output/exit behavior remain unchanged.
- Do not leave both an importable `prep.py` and `prep/` package competing for the same import path. M5A performs the package conversion atomically.
- Do not create empty placeholder modules. M5A need not create `collectors.py` until M5B gives it real responsibilities.
- `commands/prep.py` stays thin and uses the public prep facade; prep internals must not import commands/cli/app.
- Do not edit E2E golden, weaken old patch tests, run real network/smoke, execute git, or create SDD/process artifacts.

## Review Focus

- Existing patches to provider classes, `run_prep`, macro collection, and storage functions must still change behavior after the package split.
- Pure recompute must remain offline and must not construct providers.
- `collection_report` merge/health semantics must not change when collectors move.
- New package-relative imports must not recreate package-root cycles.
- Compatibility re-exports must not hide duplicate implementations of collectors, compute, or rebuild.
- M5A’s transitional service may still be large; only M5B can claim the final service boundary.

---

### Task 1 (M5A): Atomic package conversion with compute and rebuild separation

**Files:**
- Create: `dragon_quant/review_account/prep/__init__.py`
- Create: `dragon_quant/review_account/prep/service.py`
- Create: `dragon_quant/review_account/prep/compute.py`
- Create: `dragon_quant/review_account/prep/rebuild.py`
- Delete: `dragon_quant/review_account/prep.py` only when the package replacement is complete in the same change
- Modify: focused prep tests and architecture tests as required

**Interfaces:**
- Produces: public `review_account.prep.run_prep` and `PrepError`
- Produces: `compute.py` for sentiment/main_boards/candidates/macro pure computation
- Produces: `rebuild.py` for local five-day dragons-window rebuild
- Produces: transitional `service.py` for collection and run_prep orchestration
- Preserves: provider classes and other actively patched names via package-level compatibility lookup

- [ ] **Step 1: Add package-boundary and compatibility tests before conversion**

Assert public imports, provider patch behavior, compute/rebuild responsibilities, recompute offline behavior, and no duplicate implementation. Use existing prep tests as the primary behavior gate; do not delete old patch assertions to make the package easier.

- [ ] **Step 2: Atomically create the package and remove the old module**

Move pure compute and rebuild into their files, move remaining collection/orchestration into service, and make `__init__.py` the stable facade with compatibility lookup. Use package-relative imports that avoid cycles.

- [ ] **Step 3: Run M5A focused and complete gates**

Run `tests/test_prep.py`, `tests/test_verdicts.py`, prep E2E, full E2E and full unit suite. Expected: all PASS; prep JSON/human output/golden unchanged; no import/path/patch regressions. Main accepts M5A before M5B.

### Task 2 (M5B): Collector extraction and final service boundary

**Files:**
- Create: `dragon_quant/review_account/prep/collectors.py`
- Modify: `dragon_quant/review_account/prep/service.py`
- Modify: `dragon_quant/review_account/prep/__init__.py` only for required compatibility exports
- Modify: `tests/e2e/test_architecture_rules.py` and `tests/e2e/coverage-map.json` only for new architecture assertions

**Interfaces:**
- Consumes: M5A public package and M2 services
- Produces: `collectors.py` for `PrepContext`, collector registry, eight collectors, classification/health and collection-report helpers
- Produces: a service limited to date/time gates, incremental planning, and collection→compute orchestration

- [ ] **Step 1: Add collector/service responsibility and cycle tests**

Assert collectors own all eight collection definitions and health/report behavior; service owns orchestration only; provider patches still affect collectors through the compatibility lookup; no collector imports the service or package root cyclically.

- [ ] **Step 2: Move collectors and slim the service**

Keep collector registration order, same-day rules, dependency gating, report merging, and error isolation unchanged. Do not move presentation code from `review_account/advice.py` and do not change compute/rebuild in this task.

- [ ] **Step 3: Run complete M5 verification**

Run focused prep/collector tests, full E2E, full unit suite, offline `npm test`, ruff on changed files, and M0 self-check. Expected: all PASS; each new file has a distinct responsibility; no duplicate collector implementation; golden unchanged; no new cycle.

- [ ] **Step 4: Run code-self-review and hand off**

Report final package responsibilities, compatibility evidence, full test results, and any remaining oversized but cohesive files with rationale. M6 must not begin until main accepts M5 as a whole.
