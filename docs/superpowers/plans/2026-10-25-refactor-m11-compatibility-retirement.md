# M11 Compatibility Retirement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the user-approved CLI compatibility layers and confirmed dead code while preserving the explicitly retained factors, storage, prep, trader, and scan compatibility contracts.

**Architecture:** M11A moves strategy-parameter parsing into `commands/_shared`, removes the CLI aliases and a proven unused prep helper. M11B migrates every remaining test consumer to `commands.dispatch` and deletes `cli_commands.py` together with stale shim documentation. The factors’ 25 lazy names, `storage._base`, prep `legacy()` facade, `trader.datetime`, and scan provider exception remain untouched.

**Tech Stack:** Python 3.14, existing `commands` public entry, pytest and the full offline E2E suite.

**Spec:** `/tmp/dragon-quant-m11-compatibility-audit/` inline report and explicit user decisions: delete CLI compatibility, keep factors lazy compatibility, keep storage/prep/trader/scan exceptions.

## Global Constraints

- Delete only `cli.orchestrate_scan`, `cli._strategy_params`, `cli_commands.py`, and `review_account/prep/service._bar_date` plus their directly associated tests/docs.
- Do not remove or alter factors’ 25 lazy names, `storage._base`, prep `legacy()` or its active exports, `trader.datetime`, or `COMMANDS['scan']` provider behavior.
- `commands.dispatch` and public command/helper modules become the test entry points; do not create a replacement shim under another name.
- Keep all CLI output, exit codes, provider policy, JSON, and golden behavior unchanged.
- Do not edit E2E golden, run real network/smoke, execute git, or create process artifacts. All application evidence runs through pytest/harness.

## Review Focus

- Every former `_cmd_*` direct call must migrate to the equivalent `commands.dispatch` invocation with the same provider policy.
- Helper imports must move to their single implementation, not to a new forwarding module.
- Tests must stop referencing `cli.orchestrate_scan`/`_strategy_params` without changing what they assert about behavior.
- `cli_commands` must disappear from package imports, architecture tests, coverage notes, and current AGENTS/README navigation; historical archives are not rewritten to imply it never existed.
- The retained factors/prep/storage/trader contracts need explicit tests after M11B, not just an absence of edits.

---

### Task 1 (M11A): Strategy helper, CLI alias removal, and dead-code cleanup

**Files:**
- Modify: `dragon_quant/commands/_shared.py`
- Modify: `dragon_quant/commands/scan.py`
- Modify: `dragon_quant/commands/review_account.py`
- Modify: `dragon_quant/commands/buy_sell.py`
- Modify: `dragon_quant/cli.py`
- Modify: `dragon_quant/review_account/prep/service.py`
- Modify: `tests/test_commands.py`, `tests/test_cli.py`, and `tests/test_prep_package_boundary.py`

**Interfaces:**
- Produces: `commands._shared.load_strategy_params(path)` as the single strategy JSON parsing helper
- Produces: direct `commands.scan` use of `orchestrator.scan` without runtime CLI lookup
- Removes: `cli.orchestrate_scan`, `cli._strategy_params`, and prep `service._bar_date`

- [ ] **Step 1: Add failing helper and removal tests**

Assert strategy config behavior is unchanged, scan calls the orchestrator directly, both account/live commands consume the shared helper, and the removed prep helper has no remaining references. Do not alter factors/storage/prep/trader contracts.

- [ ] **Step 2: Implement the shared helper and remove the aliases/dead code**

Move the existing parsing implementation without changing validation. Update test patch targets to the new single implementation. Remove only the unused `_bar_date` entry and its boundary-test listing.

- [ ] **Step 3: Run focused and complete M11A gates**

Run command/CLI/prep tests, full E2E and full unit suite. Expected: all PASS; no `cli.orchestrate_scan`/`_strategy_params` consumers remain; all retained compatibility tests still pass. Main accepts M11A before M11B.

### Task 2 (M11B): Delete cli_commands and migrate all consumers

**Files:**
- Delete: `dragon_quant/cli_commands.py`
- Modify: `dragon_quant/cli.py`, `dragon_quant/commands/_shared.py`, `dragon_quant/commands/__init__.py`, `dragon_quant/commands/auction.py`, `dragon_quant/auction/service.py`, `dragon_quant/assets_sync/errors.py`, `dragon_quant/assets_sync/cli_service.py` for stale references only
- Modify: `tests/test_commands.py`, `tests/test_prep.py`, `tests/test_cli.py`, `tests/test_auction_pool.py`, `tests/test_auction_serialize.py`, `tests/test_p1_main_acceptance.py`, `tests/test_assets_sync_cli.py`
- Modify: `tests/e2e/test_architecture_rules.py` and `tests/e2e/coverage-map.json` to remove obsolete shim tests/mappings
- Modify: `AGENTS.md` and `README.md` for current command navigation and explicitly retained compatibility exceptions

**Interfaces:**
- Consumes: M11A shared helper and current command dispatch
- Produces: all former direct `_cmd_*` calls expressed as `commands.dispatch(name, args)`
- Produces: helper imports from `commands.assets`, `auction.scoring`/`auction.service`, `commands.prep`, or `commands._shared` as appropriate

- [ ] **Step 1: Migrate tests and architecture assertions before deleting the shim**

Replace each of the 57 test references with its public command/helper equivalent, preserving test intent and provider policies. Remove shim-specific architecture tests and update coverage-map notes without weakening behavior assertions.

- [ ] **Step 2: Delete cli_commands and stale references**

Remove `dragon_quant/cli_commands.py` and current documentation references to it. Update AGENTS/README to describe the command modules and record why factors lazy names, storage._base, prep legacy, trader.datetime, and scan’s provider exception are intentionally retained. Do not rewrite historical archives.

- [ ] **Step 3: Run complete M11 verification**

Run all formerly affected test files, full E2E, full unit suite, offline `npm test`, ruff on changed files, and M0 self-check. Expected: all PASS; no import/reference to `cli_commands` remains in current production/tests/docs; all explicitly retained compatibility contracts still pass.

- [ ] **Step 4: Run code-self-review and hand off**

Report deleted versus retained contracts, test-migration counts, full test evidence, and any external-compatibility notes for release documentation. M12 must not begin until main accepts M11 as a whole.
