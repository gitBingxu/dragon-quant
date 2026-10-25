# M7 Factors Facade Narrowing and Dependency Decycling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce the factors package root to 13 intentional public names, retain one-cycle lazy compatibility for older exports, and remove package-root import cycles without changing factor behavior.

**Architecture:** `factors/__init__.py` exposes the approved public facade and resolves legacy names lazily. Internal modules use direct sibling imports instead of importing back through the package root. `commands/factor.py` consumes only the public facade, while existing service/engine patch points and factors’ layering restrictions remain intact.

**Tech Stack:** Python 3.14 PEP 562 lazy attributes, relative imports, existing factor registry/engine/service, AST architecture tests and offline E2E.

**Spec:** User-approved B3 and `/tmp/dragon-quant-architecture-solution/migration-plan.md` M7 plus `target-architecture.md` §4–§5.

## Global Constraints

- The 13 public names are: `build`, `list_factors`, `query`, `status`, `export_matrix`, `evaluate_factor`, `fin_sync`, `fin_calibrate`, `fin_status`, `parse_only`, `FactorModel`, `annotate_pool`, `CleanConfig`.
- Older package-root exports remain available through lazy compatibility for one migration cycle; removal is an M11 decision, not M7.
- Factors must not import scorers/review_account/auction; only `factors/finance.py` may use the approved providers exception. `storage` must not import factors.
- Do not change factor values, PIT, registry hashes, build/eval/export/fin output, JSON key order, or CLI exit codes.
- Do not edit E2E golden, run real network/smoke, execute git, or create process artifacts. All application evidence runs through pytest/harness.
- Any additional scorers/auction import change is allowed only if required by the measured cycle gate and must be minimal and behavior-preserving.

## Review Focus

- `from dragon_quant.factors import <legacy_name>` must still work for names retained by the compatibility map, not only appear in `__all__`.
- `factors.service.engine` and other existing monkeypatch targets must remain usable after sibling-import cleanup.
- Public facade narrowing must not accidentally narrow `factors`’ internal capabilities or default-enabled factor set.
- AST cycle checks must distinguish real package-root cycles from direct sibling dependencies; no suppression comments are allowed to hide a cycle.
- `commands/factor.py` must not deep-import `factors.matrix`, `factors.service.engine`, or other internals after the facade is introduced.

---

### Task 1: Public facade and lazy compatibility

**Files:**
- Modify: `dragon_quant/factors/__init__.py`
- Modify: `dragon_quant/commands/factor.py`
- Create or modify: `tests/test_factors_facade.py`

**Interfaces:**
- Produces: `__all__` with exactly the 13 approved names and lazy legacy attribute resolution
- Consumes: existing factor service/engine/matrix/decision functions without changing their behavior

- [ ] **Step 1: Add failing facade and compatibility tests**

Assert the exact 13-name `__all__`, each public function’s behavior, lazy legacy imports, unknown-name `AttributeError`, no eager feature initialization, and the command’s exclusive use of the public facade.

- [ ] **Step 2: Implement the facade and migrate the command**

Map each public name to its existing implementation. Keep compatibility resolution in one small map and preserve monkeypatch behavior where the command previously patched service/engine attributes.

- [ ] **Step 3: Run focused facade and factor tests**

Expected: all PASS; factor list/build/query/status/export/eval/fin golden unchanged; no legacy import regression.

### Task 2: Sibling imports and final cycle gate

**Files:**
- Modify: `dragon_quant/factors/{service,engine,evaluation,matrix,decision,storage}.py` only where package-root imports create cycles
- Modify: `dragon_quant/scorers/*` or `dragon_quant/auction/*` only if the final measured cycle gate requires a minimal sibling-import correction
- Modify: `tests/e2e/test_architecture_rules.py` and `tests/e2e/coverage-map.json` for the final cycle assertions

**Interfaces:**
- Consumes: Task 1 facade
- Produces: direct sibling imports and zero package-root import cycles without breaking service.engine or other patch seams

- [ ] **Step 1: Add failing sibling-import and cycle tests**

Assert no factor submodule imports its package root, no command deep-imports internals, no package/module cycles in the measured import graph, and all existing layering restrictions remain enforced.

- [ ] **Step 2: Replace package-root imports with direct sibling imports**

Change import style only; do not move factor algorithms or rename public functions. Preserve intentionally lazy imports used to break runtime cycles and keep provider exceptions unchanged. If another package still reports a cycle, make only the minimal required sibling-import change.

- [ ] **Step 3: Run complete M7 verification**

Run focused factor/facade tests, full E2E, full unit suite, offline `npm test`, ruff on changed files, and M0 self-check. Expected: all PASS; exact 13-name facade with working legacy aliases; zero measured package-root cycles; golden unchanged.

- [ ] **Step 4: Run code-self-review and hand off**

Report the public export list, lazy compatibility map, dependency-cycle evidence, full test results, and any names intentionally left for M11. M8 must not begin until main accepts M7.
