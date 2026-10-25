# M1 Complete Offline CLI E2E Baseline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Establish a complete, deterministic, subprocess-level CLI behavior baseline before any production refactor, while preserving the M0 guarantee that real databases, credentials, browsers, cloud services, and networks are never touched.

**Architecture:** Build on the M0 sandbox and mandatory preflight. Complete fake provider/backend/browser contracts, seed only synthetic temporary databases, and compare real CLI output, exit codes, files, and temporary SQLite rows against reviewed golden fixtures. Tasks M1A–M1F are separate execute nodes and separate main acceptance gates; M2 starts only after all six pass.

**Tech Stack:** Python 3.14 stdlib, existing pytest, sqlite3 in per-case temporary directories, Node `node:test`, M0 `sitecustomize` injection and process/network guards.

**Spec:** `/tmp/dragon-quant-architecture-solution/e2e-baseline.md` §4–§6 defines the command matrix and field assertions. `/tmp/dragon-quant-architecture-solution/migration-plan.md` M1 defines the baseline gate. M0 implementation is under `tests/e2e/`.

## Global Constraints

- M1 changes test code and synthetic fixtures only; do not modify production code under `dragon_quant/` or `web_ui/`.
- Never read/write/migrate/delete real databases or read their contents. Real data directories are observed only with `os.stat` metadata sentinels.
- Every business process requires M0 preflight and all required seams. Never weaken a guard to make a test pass.
- No real network, browser, Cookie, credential, R2/B2, npm registry, or Playwright download. Any real access attempt is an immediate stop condition.
- Write commands always use a fresh per-case root. Read-only sharing is allowed only when no test can mutate the shared state.
- Golden values must be reviewed, not blindly regenerated. Never widen tolerances or delete assertions to hide a behavior difference.
- `scan` / `review` algorithms and production behavior remain untouched. No production refactor until M1F is accepted.
- No third-party dependency and no git commit/push/PR. Each task ends with execute self-review and main acceptance.

## Review Focus

- A missing provider method or incomplete macro fake must fail locally rather than fall through to a real provider/Yahoo (Tasks 1 and 4: provider contract and C18).
- Global clock replacement must not break `isinstance(value, datetime)`; audit and pin this before factor evaluation (Task 1).
- The scan fixture must exercise diverse dimensions, at least two true dragons, and at least one rejection; a neutral-score fixture is not a valid baseline (Task 2).
- Destructive commands must affect only temporary assets and preserve protected databases (Tasks 3 and 4: storage clear, assets prune/pull, logs clear).
- Web tests must release the process and port on both success and failure (Task 6).
- Existing unit tests may be run only after their isolation path is verified; a missing patch or real access is a stop condition, not a reason to disable the sentinel (Task 6).

---

### Task 1 (M1A): Provider contracts, clock compatibility, seed/assert helpers, and global/independent CLI

**Files:**
- Modify: `tests/e2e/injection/fake_providers.py`
- Modify: `tests/e2e/injection/fake_clock.py`
- Modify: `tests/e2e/injection/fake_assets_backend.py`
- Create: `tests/e2e/harness/canonical.py`
- Create: `tests/e2e/harness/assertlib.py`
- Create: `tests/e2e/harness/seed.py`
- Create: `tests/e2e/test_provider_contract.py`
- Create: `tests/e2e/test_cli_global.py`
- Create: `tests/e2e/test_independent_entries.py`
- Modify: `tests/e2e/test_architecture_rules.py`

**Interfaces:**
- Consumes: M0 `EnvSandbox`, `run_cli`, `run_module`, `run_python`, sentinel, preflight, and guards
- Produces: fake provider classes implementing the full `StockProvider` public method surface, including macro/finance/auction methods used by later tasks
- Produces: per-method call logging and fixture-driven responses; unknown scenario behavior fails closed
- Produces: `seed_db(sandbox, fixture_name)` and JSON/file/SQLite assertion helpers for later tasks
- Produces: global and independent-entry behavior tests used unchanged by production milestones

- [ ] **Step 1: Add failing contract, clock, and global CLI tests**

Cover every provider method against `StockProvider` and concrete call sites; a `datetime` instance/`isinstance` canary; G1–G3, G6–G8; I1–I4; JSON normalization and synthetic seed isolation. Do not execute a business path whose fake is incomplete. G4 default scan and G5 hidden alias belong to M1B's complete scan fixture.

- [ ] **Step 2: Verify tests fail locally without external access**

Run: `.venv/bin/python -m pytest tests/e2e/test_provider_contract.py tests/e2e/test_cli_global.py tests/e2e/test_independent_entries.py -q`
Expected: FAIL only on missing fake methods, assertions, or fixtures; preflight and real-data sentinel remain effective.

- [ ] **Step 3: Implement contracts and helpers, then re-run**

Complete fake methods from one scenario registry, preserve deterministic ordering, and record calls. If global clock replacement breaks `isinstance`, change the freezing mechanism rather than deleting the canary. Implement seed/assert helpers using stdlib and temporary databases only.

- [ ] **Step 4: Run the M1A gate and hand off**

Run the Step 2 command plus M0 safety tests. Expected: all PASS, no real sentinel diff, no external call. Record any methods intentionally deferred with a proven unused-by-M1 reason; otherwise M1A is incomplete. Do not start M1B until main accepts.

### Task 2 (M1B): `scan` and `review` behavior golden

**Files:**
- Create: `tests/e2e/fixtures/scenario_scan.json`
- Create: `tests/e2e/fixtures/scenario_review.json`
- Create: `tests/e2e/golden/scan/`
- Create: `tests/e2e/golden/review/`
- Create: `tests/e2e/test_scan.py`
- Create: `tests/e2e/test_review.py`

**Interfaces:**
- Consumes: M1A providers, seed/assert helpers, sandbox, and frozen clock
- Produces: reviewed scan/review golden and normalization rules from spec §5.2–§5.5
- Produces: stable proof for CLI output, temporary DB rows, report files, cache behavior, and error branches

- [ ] **Step 1: Implement the synthetic fixtures and write the assertions first**

Cover G4 default scan, G5 hidden alias, S1–S10 and R1–R11. Pin the fixture values from spec §5.4–§5.5, including two true dragons, one rejection, non-neutral dimensions, review completed/no_entry/error, v1/v2 separation, and report/cache side effects.

- [ ] **Step 2: Run once to inspect differences, never to auto-accept golden**

Run: `.venv/bin/python -m pytest tests/e2e/test_scan.py tests/e2e/test_review.py -q`
Expected: initial failures may identify fixture or harness gaps. Stop on external access. Do not weaken score, ranking, review arithmetic, key-order, or exit-code assertions.

- [ ] **Step 3: Correct fixtures/harness and review every golden value**

Use exact/tolerance/normalize/ignore rules from the approved field tables. Verify `elapsed_s` only by range/type, timestamps exactly under the frozen clock, score tolerance `1e-9`, review monetary/price tolerance as specified, and created_at only by type.

- [ ] **Step 4: Run the M1B gate and hand off**

Re-run the Step 2 command plus M1A tests. Expected: all PASS; scan DB four tables plus mirror and report/cache contracts hold; review three states and VPA writes hold; no production change. Main must approve the golden before M1C.

### Task 3 (M1C): Local query and data commands

**Files:**
- Create: `tests/e2e/test_logs_data_storage.py`
- Create: `tests/e2e/test_blacklist_dragons_vpa.py`
- Create: `tests/e2e/fixtures/scenario_local.json`
- Create: `tests/e2e/golden/local/`

**Interfaces:**
- Consumes: all prior harness/provider/seed helpers
- Produces: L1–L7, D1–D10, B1–B3, DR1–DR3, V1–V4, and ST1–ST5 coverage

- [ ] **Step 1: Write command tests and synthetic fixtures**

Pin success, key options, group-without-action, errors, read-only no-op behavior, log clear, blacklist mutations, VPA save/no-save, and storage clear protection. Include the existing `logs clear` retained-count `-1` behavior as a baseline fact, not a new bug fix.

- [ ] **Step 2: Implement only fixture/helper gaps and run the focused gate**

Run: `.venv/bin/python -m pytest tests/e2e/test_logs_data_storage.py tests/e2e/test_blacklist_dragons_vpa.py -q`
Expected: all PASS with only temporary SQLite/files changed. No production edits or output normalization beyond approved path/time fields.

- [ ] **Step 3: Run cumulative M1A–M1C tests and hand off**

Expected: all PASS; real sentinel unchanged; call logs prove read-only and no-cache/recompute branches make the expected provider calls. Main acceptance precedes M1D.

### Task 4 (M1D): `prep`, `auction`, and `assets`

**Files:**
- Create: `tests/e2e/fixtures/scenario_prep.json`
- Create: `tests/e2e/fixtures/scenario_auction.json`
- Create: `tests/e2e/fixtures/scenario_assets.json`
- Create: `tests/e2e/golden/prep/`
- Create: `tests/e2e/golden/auction/`
- Create: `tests/e2e/golden/assets/`
- Create: `tests/e2e/test_prep.py`
- Create: `tests/e2e/test_auction.py`
- Create: `tests/e2e/test_assets.py`

**Interfaces:**
- Consumes: full provider/backend fakes, macro fixtures, synthetic dragons mirror and assets seeds
- Produces: P1–P14, A1–A12, AS1–AS18, and C18 macro fallback protection

- [ ] **Step 1: Write scenario tests and fixture data**

Pin JSON key order, human output, asset/snapshot rows, degraded branches, recompute zero-network behavior, remote manifest/state layout, shrink/conflict/lock semantics, and prune/pull target isolation. Cover all four macro instruments and explicit degraded variants.

- [ ] **Step 2: Run focused tests and close only local fixture/fake gaps**

Run: `.venv/bin/python -m pytest tests/e2e/test_prep.py tests/e2e/test_auction.py tests/e2e/test_assets.py -q`
Expected: all PASS; no Yahoo/HTTP/TDX/S3 fallback; `assets pull --apply` writes only `<root>/out`; protected main temporary DB is unchanged where required.

- [ ] **Step 3: Run cumulative M1A–M1D tests and hand off**

Include C18: complete macro success has no Yahoo/urllib call; missing-instrument scenarios degrade deterministically. Main acceptance precedes M1E.

### Task 5 (M1E): `factor`, `review-account`, `buy`, and `sell`

**Files:**
- Create: `tests/e2e/fixtures/scenario_factor.json`
- Create: `tests/e2e/fixtures/scenario_account.json`
- Create: `tests/e2e/golden/factor/`
- Create: `tests/e2e/golden/review_account/`
- Create: `tests/e2e/golden/live_trade/`
- Create: `tests/e2e/test_factor.py`
- Create: `tests/e2e/test_review_account.py`
- Create: `tests/e2e/test_buy_sell.py`

**Interfaces:**
- Consumes: provider/finance/calendar fixtures and account data equivalent to existing `FakeData` golden
- Produces: F1–F16, RA1–RA8, BU1–BU11, SE1–SE6 coverage

- [ ] **Step 1: Write factor/account/live tests and fixture data**

Pin factor PIT/missing semantics, fin vintage/calibration, export sidecars, eval success/insufficient, account `final_equity≈99495.199` and `trade_count=2`, all five account tables, buy idempotence/checks/half-exit, sell reasons, suspension, and every time gate.

- [ ] **Step 2: Run focused tests and investigate every numerical difference**

Run: `.venv/bin/python -m pytest tests/e2e/test_factor.py tests/e2e/test_review_account.py tests/e2e/test_buy_sell.py -q`
Expected: all PASS. If the account golden differs, trace fixture data and clock/calendar semantics; do not increase `1e-6` tolerance or accept a new value without main/user behavior review.

- [ ] **Step 3: Run cumulative M1A–M1E tests and hand off**

Expected: all PASS; clock `isinstance` canary remains valid through factor evaluation; all writes stay in temporary DBs. Main acceptance precedes M1F.

### Task 6 (M1F): Web, npm, coverage audit, and full baseline gate

**Files:**
- Create: `tests/e2e/test_web_api.py`
- Modify: `tests/e2e/test_independent_entries.py`
- Create: `tests/e2e/coverage-map.json`
- Modify: `tests/conftest.py` only if needed to strengthen suite-wide isolation without changing test semantics
- Create: `tests/smoke/conftest.py` and `tests/smoke/test_network_readonly.py` as opt-in non-gate stubs/tests

**Interfaces:**
- Consumes: every prior fixture, golden, provider, backend, and sandbox contract
- Produces: R9–R11, W1–W8 and N1–N7 coverage; a machine-checkable map from all 51 parser nodes and independent entry points to tests
- Produces: the final M1 gate evidence required before M2

- [ ] **Step 1: Write Web/npm tests and coverage audit**

Use `--no-browser`, temporary DBs, a safely selected port, and process-group cleanup. Cover R9/R10 review UI startup, R11 single-row review write-back failure, every listed HTTP endpoint, and npm install/doctor/uninstall lifecycle without registry access. Map every command/subcommand to at least one success/error/side-effect-or-no-op test; any exception requires an explicit user-visible reason.

- [ ] **Step 2: Run Web/npm and full E2E gates**

Run: `.venv/bin/python -m pytest tests/e2e -q` and `npm test` with offline/npm-cache settings and temporary HOME/output paths.
Expected: all PASS; no orphan server/process; all ports released; all 51 nodes and independent entries accounted for.

- [ ] **Step 3: Verify isolation, then run the existing full unit suite**

Audit any `tests/conftest.py` change with focused tests first. Confirm the Tencent 200-codes-per-batch transport behavior has a direct provider unit test (the provider-level fake cannot observe it in CLI E2E); if absent, add a focused test under `tests/` without changing production. Run: `.venv/bin/python -m pytest tests -q --ignore=tests/e2e --ignore=tests/smoke` only with the verified temporary data environment and real-data metadata sentinel. Stop on real access; do not disable the guard to obtain green.

- [ ] **Step 4: Run final M0+M1 verification and hand off**

Run the complete E2E command again, M0 standalone self-check, `git status --short`, and `git diff --stat`. Expected: all PASS; changed files are tests, fixtures, these plans, and optional test-only isolation config; zero production changes. Main confirms M1 complete before any M2 work.
