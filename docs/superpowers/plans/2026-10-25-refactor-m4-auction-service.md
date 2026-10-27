# M4 Auction Service Extraction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move auction collection/recompute orchestration out of the CLI command module into a feature-owned service while preserving every payload, output, error, and provider call sequence.

**Architecture:** `auction/service.py` owns pool selection, calendar, quotes, collection, scoring, persistence, and payload assembly. `commands/auction.py` becomes a thin adapter that invokes the public `auction` facade and renders the returned `AuctionOutcome`. A missing `services` argument uses the runtime provider factory rather than silently degrading the prep pool to legacy.

**Tech Stack:** Python 3.14 dataclasses, existing auction pure functions, M2 provider bundle, temporary SQLite E2E fixtures and golden outputs.

**Spec:** `/tmp/dragon-quant-architecture-solution/migration-plan.md` M4 and `/tmp/dragon-quant-architecture-solution/solution.md` §8.5, §10.4.

## Global Constraints

- Do not change auction scoring, serialization, report wording, JSON key order, exit codes, pool fallback, or asset/snapshot semantics.
- `auction/strength.py`, `serialize.py`, `report.py`, `types.py`, and `registry.py` remain unchanged.
- `commands/auction.py` must end as a thin CLI adapter (target ≤120 lines), not a renamed home for orchestration.
- `auction/service.py` must not import `commands`, `cli`, or `app`; feature code consumes the runtime provider bundle through `Services` or the existing runtime factory.
- `cli_commands._cmd_auction` remains a working forwarding shim. Do not remove compatibility helpers still used by tests.
- Do not edit E2E golden, run real network/smoke, or execute git commit/push/PR. No SDD ledger/workspace files.

## Review Focus

- Direct service/command invocation without prebuilt services must still obtain a valid calendar provider and must not silently turn a prep pool into legacy.
- `auction --recompute` must remain offline and must not construct providers.
- Collect 25-key, empty-pool 16-key, and recompute 15-key payloads must retain exact key order.
- Collection failure versus snapshot-write failure must preserve their distinct exit codes and error channels.
- The service may use internal helper files only if they have distinct collection/scoring responsibilities; do not create another 500-line service by default.

---

### Task 1: AuctionOutcome and feature service

**Files:**
- Create: `dragon_quant/auction/service.py`
- Modify: `dragon_quant/auction/__init__.py`
- Create: `tests/test_auction_service.py`

**Interfaces:**
- Produces: `AuctionOutcome(payload, exit_code, render_names, mode)`
- Produces: `run_auction(*, pool='prep', days=None, services=None) -> AuctionOutcome`
- Produces: `run_auction_recompute(trade_date, *, services=None) -> AuctionOutcome`
- Produces: public `auction.format_report` forwarding to the existing report implementation

- [ ] **Step 1: Write failing service contract tests**

Cover collect/recompute payload shape, provider usage and default factory fallback, prep-pool calendar behavior, recompute zero-provider behavior, empty pool, collection failure, snapshot failure, and output exit codes. Reuse current E2E fixtures; do not alter production behavior to match a new preferred shape.

- [ ] **Step 2: Implement the service by moving the existing orchestration**

Move pool/calendar/quotes/collection/scoring/persistence/payload logic from `commands/auction.py` with minimal necessary adaptation. Keep internal helpers in `service.py` unless a clearly independent collection or scoring responsibility justifies a second file. Resolve missing services through the runtime provider bundle, not through app import or silent pool fallback.

- [ ] **Step 3: Run service and auction behavior tests**

Run: `.venv/bin/python -m pytest tests/test_auction_service.py tests/test_auction_strength.py tests/test_auction_serialize.py tests/test_auction_report.py tests/e2e/test_auction.py -q`
Expected: all PASS; all existing auction E2E golden values and provider call sequences unchanged.

### Task 2: Thin command adapter and final M4 gate

**Files:**
- Modify: `dragon_quant/commands/auction.py`
- Modify: `tests/test_commands.py` and focused CLI tests only where the public service seam is asserted
- Modify: `tests/e2e/test_architecture_rules.py` and `tests/e2e/coverage-map.json` only for new architecture assertions

**Interfaces:**
- Consumes: Task 1 `auction.run_auction`, `run_auction_recompute`, and `format_report`
- Produces: `handle(args, services)` that selects collect/recompute, prints human/JSON output, and returns/exits with `AuctionOutcome.exit_code`

- [ ] **Step 1: Add failing thin-adapter and architecture tests**

Assert the command contains no pool/calendar/quotes/scoring orchestration, uses the public auction facade, keeps collect/recompute provider policy, and preserves the legacy `_cmd_auction` forwarding behavior.

- [ ] **Step 2: Replace the command body with the adapter**

Move no business helper into `commands/_shared.py`. Keep rendering and process-level error behavior in the command; keep all domain behavior in the service. Target ≤120 lines unless a documented output-only exception is necessary.

- [ ] **Step 3: Run complete M4 verification**

Run focused command/service/CLI tests, full E2E, full unit suite, ruff on changed files, and M0 self-check. Expected: all PASS; command size target met; golden unchanged; no new import cycle; no production files outside auction/command scope changed.

- [ ] **Step 4: Run code-self-review and hand off**

Report service/command responsibilities, default provider behavior, exact payload evidence, full test results, and any deviations. M5 must not begin until main accepts M4.
