# M9 Unified Runtime Clock Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move production time acquisition to the existing `runtime.clock` contract and migrate E2E injection from global `datetime`/`time` replacement to one runtime-clock seam without changing timestamps, gates, IDs, cache TTL, or golden behavior.

**Architecture:** M9A first makes the injection support both the legacy global freeze and `runtime.clock`, then migrates scan/review. M9B migrates the remaining production call sites while preserving active monkeypatch contracts. M9C removes global freezing only after an audit proves all E2E-relevant time acquisition goes through the runtime clock. Wall-clock epoch values use `now().timestamp()`; monotonic is used only for duration measurement and never for persisted wall-clock deadlines.

**Tech Stack:** Python 3.14 datetime/timezone, existing `runtime.clock` SystemClock/FixedClock, pytest and the M0/M1 deterministic E2E harness.

**Spec:** `/tmp/dragon-quant-architecture-solution/migration-plan.md` M9 and `/tmp/dragon-quant-architecture-solution/solution.md` §6.3, §10.2.

## Global Constraints

- `scan` / `review` algorithms, time-zone meanings, trading gates, scan_id, report names, review windows, and all golden values remain unchanged.
- M9A cannot remove the old freeze. M9C may remove it only after every production call site needed by E2E has a verified runtime-clock path.
- `SystemClock` must preserve each original call’s naive/aware and Asia/Shanghai/UTC semantics. Do not centralize these meanings into one assumed timezone.
- Wall epoch (`time.time()` equivalents) uses `clock.now().timestamp()`. `clock.monotonic()` is only for elapsed durations and must not replace persistent cache timestamps.
- Existing active patch targets, especially `live_trade.trader.datetime` and prep/provider/calendar patches, must either continue to affect behavior or be migrated in the same change with an explicit compatibility rationale. Inert aliases are not compatibility.
- Do not edit behavioral golden, run real network/smoke, execute git, or create process artifacts. All application evidence comes from pytest/harness; real metadata anomalies stop work immediately.

## Review Focus

- The transition cannot leave a call site reading real time while the E2E clock is frozen (Tasks 1–3).
- `FixedClock.monotonic()` must not be used to infer progress; elapsed fields retain their existing assertion semantics (all tasks).
- Cache TTL and `created_at`-like wall timestamps must not use a monotonic epoch (Task 2).
- Function-local `from datetime import datetime` and `import time` call sites must be included in the audit, not just top-level imports (Tasks 2–3).
- Old patch targets must be exercised by tests after the migration, not merely checked for existence (Tasks 1–2).
- `factors/evaluation.py`’s `isinstance(..., datetime)` behavior must remain valid after global `FrozenDateTime` is removed (Task 3).

---

### Task 1 (M9A): Dual-mode injection and scan/review migration

**Files:**
- Modify: `tests/e2e/injection/fake_clock.py` and preflight marker validation to support both clock modes
- Modify: `dragon_quant/orchestrator.py`
- Modify: `dragon_quant/review.py`
- Modify: focused scan/review/clock tests and architecture tests as required

**Interfaces:**
- Consumes: existing `runtime.clock.now/today/stamp/monotonic` and `set_clock/reset_clock`
- Produces: injection markers that can prove either `datetime+time` legacy mode or `runtime.clock` mode
- Produces: scan/review call sites using `from dragon_quant.runtime import clock`

- [ ] **Step 1: Add failing dual-mode and scan/review clock tests**

Prove legacy E2E remains deterministic, runtime-clock injection returns the fixed time, and scan/review use it for gates, IDs, report names, and review windows without changing outputs. Add before/after assertions for naive versus Shanghai-aware call sites.

- [ ] **Step 2: Implement dual-mode injection and migrate only scan/review**

Keep the old freeze active during this task. Replace time acquisition, not formulas or provider/cache/storage orchestration. Do not migrate other production modules yet.

- [ ] **Step 3: Run focused and complete M9A gates**

Run scan/review focused tests, full E2E and full unit suite. Expected: all PASS with identical golden and no real-time-dependent path. Main accepts M9A before M9B.

### Task 2 (M9B): Remaining production call sites and patch compatibility

**Files:**
- Modify: `dragon_quant/review_account/{data,market,prep/service}.py` and other prep modules only for current time acquisition
- Modify: `dragon_quant/live_trade/trader.py`
- Modify: `dragon_quant/auction/{collect,dates,recompute}.py` only for current time acquisition
- Modify: `dragon_quant/utils/trading.py`
- Modify: `dragon_quant/vpa/*` current-time write paths
- Modify: `dragon_quant/factors/{engine,matrix,service,finance,evaluation}.py` only for current time acquisition
- Modify: `dragon_quant/cache/data_cache.py`, `dragon_quant/logging/*`, `dragon_quant/storage/manager.py` for wall-time/clock semantics
- Modify: focused tests for these modules and old patch targets

**Interfaces:**
- Consumes: M9A dual-mode clock and unchanged feature APIs
- Produces: runtime-clock acquisition for all E2E-relevant current-time calls, with old patch compatibility or same-change test migration rationale

- [ ] **Step 1: Add failing call-site and compatibility tests**

Cover prep 16:40, auction/recompute dates, buy/sell time gates, factor PIT, VPA save date, logs clear, cache TTL, and each active monkeypatch target. Include function-local imports and wall-epoch versus monotonic cases.

- [ ] **Step 2: Migrate each call site with its original semantics**

Use `clock.now`, `today`, `stamp`, and `now().timestamp()` as appropriate. Keep `monotonic` for elapsed-only measurements. Do not rewrite provider parsing, strategy rules, factor calculations, or persistence formats. If a legacy patch must be replaced, implement a working compatibility adapter or migrate its tests in the same change and document why runtime behavior for users is unchanged.

- [ ] **Step 3: Run focused and complete M9B gates**

Expected: all focused tests, full E2E, and full unit suite PASS; every golden and time gate is identical. Main accepts M9B before M9C.

### Task 3 (M9C): Remove global freezing and prove the single clock seam

**Files:**
- Modify: `tests/e2e/injection/fake_clock.py` to use runtime-clock injection only
- Modify: `tests/e2e/harness/runner.py` preflight expectations for runtime-clock mode
- Modify: clock/canary/architecture tests and `tests/e2e/coverage-map.json` for the controlled harness migration
- Modify: any remaining production call site found by the final current-time audit, with evidence for each exception

**Interfaces:**
- Consumes: all M9A/M9B production migrations
- Produces: one `runtime.clock` injection mode and an explicit list of any remaining `datetime.now`/`time.time` sites that are pure parsing, constants, or intentionally outside E2E behavior

- [ ] **Step 1: Add failing no-global-freeze and remaining-call-site tests**

Prove `datetime.datetime` and `time.time` are no longer globally replaced, runtime clock is frozen for every E2E command, `isinstance(..., datetime)` still works, and no production path reads uncontrolled time.

- [ ] **Step 2: Remove the legacy freeze and finish the audit**

Use runtime-clock injection as the sole mechanism. Update canaries rather than deleting them. Document every remaining direct time call by category; do not leave an unexplained current-time call in an E2E-covered feature.

- [ ] **Step 3: Run complete M9 verification**

Run focused clock/architecture tests, full E2E, full unit suite, offline `npm test`, ruff on changed files, and M0 self-check. Expected: all PASS; one clock seam; no global class replacement; all golden unchanged; no old patch target left silently inert without an accepted rationale.

- [ ] **Step 4: Run code-self-review and hand off**

Report the production call-site inventory, compatibility decisions, final injection mechanism, and full test evidence. M10 must not begin until main accepts M9 as a whole.
