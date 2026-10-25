# M3 CLI Command Modules and Lazy Dispatch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split the 1713-line `cli_commands.py` into navigable command modules wired to the M2 Composition Root while preserving every parser node, output channel, exit code, legacy handler, and monkeypatch contract.

**Architecture:** `cli.py` retains the parser and performs lazy dispatch through `commands.COMMANDS`. Each command exposes `handle(args, services)` and obtains dependencies from `app.build_services` only when needed. Migration is incremental: unmigrated commands temporarily use the legacy handler, and M3C converts `cli_commands.py` into a forwarding shim and removes the static CLI cycle.

**Tech Stack:** Python 3.14 argparse/importlib, M2 `app.Services`, existing feature APIs, pytest and the 51-node offline E2E baseline.

**Spec:** `/tmp/dragon-quant-architecture-solution/migration-plan.md` M3 and `/tmp/dragon-quant-architecture-solution/target-architecture.md` §2–§5. M4 auction service extraction and M6 assets cli_service are later milestones.

## Global Constraints

- Do not change parser commands, aliases, flags, defaults, choices, help text, stdout/stderr, JSON key order, or exit codes.
- No-argument behavior remains the observed `help`, not an invented default scan. `scan_v2` remains a hidden alias.
- `scan` / `review` algorithms and M1 golden are immutable. Other behavior changes require a new explicit user decision and are not part of M3.
- `handle(args, services)` is the command protocol. Data commands build the default bundle through `app.build_services`; read-only/no-data commands keep `providers=None`.
- Preserve legacy `cli_commands._cmd_*`, `cli.orchestrate_scan`, `cli._strategy_params`, and provider/db patch targets during migration. Final shim removal is M11.
- M3C `commands/auction.py` may temporarily contain the old auction orchestration; M4 extracts it. `commands/assets.py` may temporarily contain backend assembly; M6 extracts it.
- Do not modify production outside the authorized command/CLI/factor-engine scope. Do not edit E2E golden or run real network/smoke. No git commit/push/PR.

## Review Focus

- Parser behavior must be compared node by node, including hidden/no-action behavior and no-argument help (all tasks).
- Every command’s error channel and `sys.exit`/return behavior must match its old handler (all tasks).
- Old monkeypatch targets must continue to affect runtime behavior, not merely exist as aliases (all tasks).
- Services must reach internal finance snapshot sync, not stop at the command boundary (Task 2).
- Static `cli ↔ cli_commands` and new command→CLI import cycles must be absent after M3C (Task 3).
- `commands/auction.py` is allowed to be temporarily large; do not claim M3 has made it thin (Task 3).
- New architecture tests must be registered in `coverage-map.json` without changing behavior golden (all tasks).

---

### Task 1 (M3A): Lazy registry and foundational command modules

**Files:**
- Create: `dragon_quant/commands/__init__.py`
- Create: `dragon_quant/commands/_shared.py`
- Create: `dragon_quant/commands/{scan,review,logs,data,blacklist,dragons,storage,vpa}.py`
- Modify: `dragon_quant/cli.py`
- Create: `tests/test_commands.py`
- Modify: `tests/e2e/test_architecture_rules.py` and `tests/e2e/coverage-map.json` only for new rule registration

**Interfaces:**
- Consumes: M2 `build_services`, `build_provider_bundle`, feature optional `services` parameters
- Produces: `COMMANDS` mapping command names to lazy module/handler references, with a temporary legacy fallback for unmigrated commands
- Produces: `handle(args, services)` for scan/review/logs/data/blacklist/dragons/storage/vpa
- Preserves: all other commands through the existing `cli_commands` handlers

- [ ] **Step 1: Add failing command-registry and foundational-command tests**

Pin lazy imports, `handle(args, services)`, app/provider construction rules, parser snapshot equality, direct-handler behavior, and old patch behavior for `orchestrate_scan`. Do not move auction/assets/account/prep/factor in this task.

- [ ] **Step 2: Implement registry and move only the foundational handlers**

Move parameter mapping/output/error behavior without changing business code. Use `_shared.py` only for true cross-command CLI helpers; do not move domain logic into it. Keep legacy fallback and old patch targets operational.

- [ ] **Step 3: Run focused and complete gates**

Run `tests/test_commands.py`, `tests/test_cli.py`, full E2E and full unit suite. Expected: all PASS; all unmigrated commands still work; M1 golden unchanged. Main accepts M3A before Task 2.

### Task 2 (M3B): Account, live-trade, prep, and factor command modules

**Files:**
- Create: `dragon_quant/commands/{review_account,buy_sell,prep,factor}.py`
- Modify: `dragon_quant/commands/__init__.py`
- Modify: `dragon_quant/factors/engine.py` only to forward optional `services` into the existing finance snapshot-sync path
- Modify: focused CLI/factor/prep tests and architecture tests as required

**Interfaces:**
- Consumes: Task 1 registry and M2 feature injection
- Produces: `handle(args, services)` for review-account/buy/sell/prep/factor
- Produces: finance `load_fin_source(..., services=...)` forwarding through `factors/engine.py` so automatic same-day snapshot sync also uses the bundle

- [ ] **Step 1: Add failing command and end-to-end finance-injection tests**

Assert command handlers use M2 services, invalid config/errors retain their old channels, and a factor-build same-day auto-sync cannot construct a separate default TDX provider when a bundle was supplied.

- [ ] **Step 2: Move the four command groups and complete the finance forwarding**

Preserve `review_account` missing-date exit 0, buy/sell time gates, prep JSON/stderr behavior, factor parameter exit 2, and fin vintage/calibration behavior. Do not split prep or change factor semantics.

- [ ] **Step 3: Run focused and complete gates**

Expected: account/live/prep/factor golden unchanged, full E2E and unit suite PASS, no new dependency cycle. Main accepts M3B before Task 3.

### Task 3 (M3C): Auction/assets transition, complete shim, and final CLI decoupling

**Files:**
- Create: `dragon_quant/commands/{auction,assets}.py`
- Modify: `dragon_quant/commands/__init__.py`
- Modify: `dragon_quant/cli.py`
- Modify: `dragon_quant/cli_commands.py` to become a compatibility shim only
- Modify: `tests/test_commands.py`, `tests/test_cli.py`, and architecture tests as required

**Interfaces:**
- Consumes: all prior command modules and registry entries
- Produces: `handle(args, services)` for auction/assets with their current orchestration/assembly temporarily preserved
- Produces: legacy `_cmd_*` forwarding to the new command handlers
- Produces: a CLI without bottom-up handler imports or static `cli ↔ cli_commands` cycle

- [ ] **Step 1: Add final dispatch, shim, and cycle tests before changing the legacy module**

Assert every one of the 15 top-level commands resolves to its new handler; direct legacy `_cmd_*` calls still behave; `cli.orchestrate_scan` and `_strategy_params` patches still affect execution; no command imports `cli` statically.

- [ ] **Step 2: Move auction/assets and replace `cli_commands.py` with the shim**

Move the existing auction logic as-is into `commands/auction.py` (M4 will extract it) and existing assets assembly into `commands/assets.py` (M6 will extract it). Remove the CLI bottom import and legacy fallback. Use runtime lookup only where required for patch compatibility; do not introduce a new static cycle.

- [ ] **Step 3: Run final M3 verification**

Run focused CLI/command tests, parser snapshot comparison, full E2E, full unit suite, offline `npm test`, ruff on changed files, and M0 self-check. Expected: all PASS; parser 51 nodes unchanged; all golden unchanged; static CLI cycle absent; no production file outside scope changed.

- [ ] **Step 4: Run code-self-review and hand off**

Report each command’s new location, compatibility shims, parser evidence, full test results, and the intentionally temporary auction/assets internals. M4 must not begin until main accepts M3 as a whole.
