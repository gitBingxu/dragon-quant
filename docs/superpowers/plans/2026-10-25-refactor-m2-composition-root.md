# M2 Composition Root and Optional Dependency Injection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Introduce a small Composition Root and optional provider/clock injection without changing CLI behavior, so M3 command modules can share one assembly seam instead of constructing dependencies throughout the codebase.

**Architecture:** `runtime/providers.py` owns `ProviderBundle`; `app.py` owns `RuntimeConfig`, `Services`, `build_services`, and `ensure_storage`. Feature APIs accept an optional `services` and preserve their current construction path when it is absent. The work is split into scan/review, account/live/data, and prep/finance gates so every group can be accepted independently.

**Tech Stack:** Python 3.14 stdlib dataclasses/Protocol, existing providers and storage, pytest, the M0/M1 offline harness and golden suite.

**Spec:** `/tmp/dragon-quant-architecture-solution/solution.md` §6.2, §10.1, §10.8 and `/tmp/dragon-quant-architecture-solution/migration-plan.md` M2. M3—not M2—creates `commands/`.

## Global Constraints

- Do not create `commands/` and do not split `cli.py` or `cli_commands.py` in M2.
- `scan` / `review` external behavior and core algorithms must remain identical; M1 golden may not be edited to obtain green.
- Every new dependency parameter is optional. Omitted `services` follows the existing construction and monkeypatch behavior.
- `ProviderBundle` has one definition in `runtime/providers.py`; `app.py` only imports/uses it. Do not construct MarketData, simulators, LiveTrader, assets backends, or a DI container in `app.py`.
- `app.py` must not import `commands` or `cli`; feature modules must not import `app`.
- M2 may define the minimal clock contract for `build_services(clock=...)`, but time call-site migration remains M9. Do not alter time behavior in M2.
- Do not remove import-time database initialization; that is M8 with separately approved B1.
- Run all tests with the M1 isolation guards. No real network, real database content, real credentials, or git commit/push/PR.

## Review Focus

- Existing tests that patch provider classes or `_connect` must behave exactly as before when `services` is absent (all tasks).
- A supplied bundle must be used at every provider call within the target feature, not only at the outer factory (all tasks).
- Read-only or no-data operations must not eagerly construct providers (Tasks 1–3).
- `RuntimeConfig.data_dir` must not bypass the established `DQ_DATA_DIR`/storage path semantics (Task 1).
- Clock injection must be a passive contract in M2; no timestamp, gate, or scan_id may change (Task 1).
- Import direction must remain acyclic; feature code cannot import the Composition Root (all tasks).

---

### Task 1 (M2A): Runtime/app contracts and scan/review optional injection

**Files:**
- Create: `dragon_quant/runtime/__init__.py`
- Create: `dragon_quant/runtime/providers.py`
- Create: `dragon_quant/runtime/clock.py`
- Create: `dragon_quant/app.py`
- Create: `tests/test_app.py`
- Modify: `dragon_quant/orchestrator.py`
- Modify: `dragon_quant/review.py`
- Modify: `tests/test_review.py` and scan tests only where new optional-injection coverage belongs

**Interfaces:**
- Produces: `ProviderBundle(tdx, ths, xueqiu, tencent)` and `build_provider_bundle()`
- Produces: `Clock` Protocol plus `SystemClock`/`FixedClock` contract; no production call-site migration until M9
- Produces: `RuntimeConfig(data_dir, source='v2', verbose=True)`
- Produces: `Services(config, clock, providers)` and `build_services(*, providers=None, clock=None, env=None)`
- Produces: `ensure_storage(services)`
- Produces: `orchestrator.scan(..., services: Services | None = None)`
- Produces: `review.run_review(..., provider=None, services: Services | None = None)`

- [ ] **Step 1: Write failing app and injection tests**

Assert bundle construction and lazy provider behavior; no `app`↔feature import cycle; no omitted-services change; supplied fake providers are used for scan Phase A–F and review K-line loading; no-data calls do not construct providers; clock injection stores the supplied clock without changing current timestamps.

- [ ] **Step 2: Run focused tests and verify the intended failures**

Run: `.venv/bin/python -m pytest tests/test_app.py tests/test_review.py tests/test_orchestrator_helpers.py -q`
Expected: FAIL only because the new app/services API is absent. Existing tests must not be rewritten to hide regressions.

- [ ] **Step 3: Implement the minimal Composition Root and optional feature parameters**

Delegate the default bundle to the existing `create_providers()` contract. Keep provider construction lazy and preserve current monkeypatch targets. Pass the bundle to existing internal helper paths without changing scorer, review arithmetic, cache, persistence, or output code.

- [ ] **Step 4: Run focused and complete behavior gates**

Run the focused command, then `.venv/bin/python -m pytest tests/e2e -q` and `.venv/bin/python -m pytest tests -q --ignore=tests/e2e --ignore=tests/smoke`.
Expected: all PASS with unchanged scan/review golden and no real sentinel diff. Main accepts M2A before Task 2.

### Task 2 (M2B): review-account, live-trade, data, and VPA optional injection

**Files:**
- Modify: `dragon_quant/review_account/service.py`
- Modify: `dragon_quant/review_account/data.py` only if needed to consume an injected provider without duplicating construction
- Modify: `dragon_quant/live_trade/service.py`
- Modify: `dragon_quant/data.py`
- Modify: `dragon_quant/vpa/engine.py` or its existing public `vpa.analyze` entry, whichever owns provider construction
- Create or modify focused tests for these features

**Interfaces:**
- Consumes: Task 1 `Services` and `ProviderBundle`
- Produces: optional `services` on `run_review_account`, `run_buy`, `run_sell`, the data facade’s provider-dependent public functions, and `vpa.analyze`
- Preserves: existing `MarketData(provider=, cache=, offline=)` and direct provider arguments as the lower-level test seam

- [ ] **Step 1: Add failing optional-injection tests**

Prove supplied providers reach MarketData/live signals/data/VPA, omitted services preserve existing provider patch targets, and no account/live/data algorithm or CLI result changes. Avoid passing `Services` into pure factors, strategies, scorers, or execution functions.

- [ ] **Step 2: Implement only the construction-path changes**

Use the bundle’s named providers at the same points that previously called `create_providers()` or concrete constructors. Do not create a second provider registry or duplicate per-feature factories inside `app.py`.

- [ ] **Step 3: Run focused, full E2E, and full unit gates**

Expected: account golden `99495.199`, buy/sell signals, data/VPA output, and all M1 golden remain unchanged. Main accepts M2B before Task 3.

### Task 3 (M2C): prep and financial provider injection, final M2 architecture gate

**Files:**
- Modify: `dragon_quant/review_account/prep.py`
- Modify: `dragon_quant/factors/finance.py` only where the current TDX construction is replaced by the optional bundle
- Modify: `tests/test_prep.py` and finance tests for optional injection
- Modify: `tests/test_app.py` and `tests/e2e/test_architecture_rules.py` for the final dependency contract

**Interfaces:**
- Consumes: all prior `Services`/bundle contracts
- Produces: `run_prep(..., services: Services | None = None)` and optional provider injection for financial sync/calibration paths
- Preserves: prep provider-class monkeypatch targets, macro fallback semantics, financial PIT behavior, and the factors dependency restrictions

- [ ] **Step 1: Add failing prep/finance injection tests**

Prove all four providers and the financial TDX provider come from the supplied bundle, no provider is constructed for pure recompute/status paths, and omitted services retain the existing patch behavior. Cover macro and calendar calls to catch partial injection.

- [ ] **Step 2: Implement the optional prep/finance paths**

Keep `ASSET_COLLECTORS`, same-day gates, snapshot computation, and financial storage semantics unchanged. Do not move prep files or introduce the M5 package in this task.

- [ ] **Step 3: Run all M2 verification**

Run focused prep/finance tests, full E2E, full unit suite, `npm test` with the offline environment, ruff on changed Python files, and M0 standalone self-check. Add AST assertions that `app` does not import `commands`/`cli`, feature modules do not import `app`, and `ProviderBundle` has one definition.

- [ ] **Step 4: Run code-self-review and hand off**

Report the actual commands/results, injection coverage, unchanged golden, deviations, and any remaining construction sites intentionally reserved for M4 auction and M6 assets. M3 must not begin until main accepts M2 as a whole.
