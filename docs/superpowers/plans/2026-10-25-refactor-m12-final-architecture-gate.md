# M12 Documentation, Root Review, Packaging, and Final Architecture Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring project documentation and package metadata in line with the refactored architecture, verify packaging and navigation, and finish the repository-wide refactor without unapproved file deletion or behavior changes.

**Architecture:** Update AGENTS/README from the actual command and module graph, correct known documentation drift, and classify root entries using evidence rather than aesthetics. Run the full behavior and packaging gates, then refresh the codebase-memory index and perform the `designing-navigable-architecture` ten-item final review.

**Tech Stack:** Markdown documentation, setuptools package metadata, offline `python -m build --no-isolation`, npm/node:test, pytest, codebase-memory indexing.

**Spec:** `/tmp/dragon-quant-architecture-solution/migration-plan.md` M12 and `/tmp/dragon-quant-architecture-solution/target-architecture.md` §7. Earlier research identified documentation drift in fallback windows, prep snapshot version, CLI coverage, package description, and no-argument behavior.

## Global Constraints

- Do not move root files, delete `review/技术方案.md`, or clean `build/`, `dist/`, egg-info, caches, or other artifacts without a separate explicit user decision.
- `publish.sh` and `encrypt_token.sh` remain in place. AGENTS.md and README.md are updated in place, never relocated.
- Do not change production behavior, algorithms, JSON/CLI contracts, or E2E golden in this milestone.
- Historical archives are not rewritten to pretend the old structure never existed. Any archive update must be limited to a clearly marked current-state pointer required by the project’s archive-synchronization rule.
- Packaging must be offline and write artifacts outside the repository. Do not install missing build tools or fetch dependencies.
- All application evidence comes from pytest/harness; no inline application-import evidence, real network, or real database content access.

## Review Focus

- The documented tree must match real files after `cli_commands` deletion and the prep/auction package splits.
- `python -m dragon_quant` documentation must describe the observed help behavior, not the old default-scan assumption.
- `review-account` fallback is 5 days and prep snapshots are 0.6; package metadata describes five dimensions.
- New readers must be able to follow CLI → command module → feature public entry → service/use case → implementation, with app shown as the assembly branch.
- Archive updates must preserve historical context while pointing to the current structure only when required by the project rule.
- Packaging must include all new packages and exclude deleted modules; no missing `prep` or `commands` package is allowed in wheel/sdist verification.

---

### Task 1: Current architecture documentation and metadata

**Files:**
- Modify: `AGENTS.md`
- Modify: `README.md`
- Modify: `pyproject.toml` description only
- Create or modify: focused documentation/consistency tests if useful without adding a documentation framework

**Interfaces:**
- Produces: current architecture tree, command navigation, compatibility exceptions, storage/clock/Web lifecycle descriptions, and complete CLI documentation
- Produces: five-dimensional package description

- [ ] **Step 1: Add documentation consistency checks and a concrete update list**

Compare the documented tree/commands with actual files and the 51-node parser snapshot. Include all known drift: fallback 5, prep 0.6, five dimensions, data cookie commands, logs list, storage clear options, scan --date, and no-argument help. Do not document known defects as intentional behavior.

- [ ] **Step 2: Update AGENTS, README, and package description**

Document `commands/`, `app.py`, `runtime/`, auction collect/recompute/scoring/dates, prep service/collectors/compute/rebuild, assets cli_service, factors’ 13 public names plus retained lazy compatibility, lazy storage, runtime clock, and B2 Web lifecycle. Preserve the original scan abstractions and clearly state intentional exceptions.

- [ ] **Step 3: Run documentation consistency checks**

Verify every listed path exists, every top-level command is covered, no current navigation references `cli_commands` or `review_account/prep.py`, and fallback/version/description statements match production. Main reviews the diff for accuracy before Task 2.

### Task 2: Root and archive review

**Files:**
- Review only: root entries, `review/技术方案.md`, `archived_md/`, `docs/`
- Modify only if required by the archive-synchronization rule: the minimal set of archived documents with a clearly marked current-state pointer

**Interfaces:**
- Produces: root classification and an explicit no-deletion/no-move record
- Produces: a minimal list of archive documents that either need a current-state pointer or are intentionally historical

- [ ] **Step 1: Reconcile root entries and archived references**

Classify project_instruction/tool_mandated/convention/source_root/artifact using consumers and configuration. Confirm no root file needs moving and no artifact is deleted. Identify archived documents whose active feature structure changed and distinguish them from immutable historical context.

- [ ] **Step 2: Apply only necessary archive pointers**

If the project rule requires an update, add a short dated current-state note/link instead of rewriting the historical design. If no update is required, record why. Do not modify `review/技术方案.md` or clean ignored artifacts.

- [ ] **Step 3: Verify the root/archive outcome**

Confirm no unexpected root files were added by this milestone and no historical document was moved/deleted. Report residual known issues separately rather than converting them into structural edits.

### Task 3: Packaging, full verification, and final architecture review

**Files:**
- No intended production changes beyond Tasks 1–2; generated artifacts go only under `/tmp`
- Refresh: `.codebase-memory/` through the repository indexer (ignored generated metadata)

**Interfaces:**
- Produces: offline wheel/sdist with all new packages and no deleted `cli_commands`
- Produces: final full-test evidence and codebase-memory refresh
- Produces: the ten-item navigable-architecture final review and acceptance mapping

- [ ] **Step 1: Run offline packaging and inspect artifacts**

Use `.venv/bin/python -m build --no-isolation --outdir /tmp/dragon-quant-final-dist` (or an equivalent offline command). Inspect wheel/sdist contents for `commands/`, `review_account/prep/`, auction modules, runtime, and absence of `cli_commands.py`. Do not install missing tools or fetch dependencies.

- [ ] **Step 2: Run all final verification**

Run full E2E, full unit suite, offline `npm test`, ruff on changed files, `git diff --check`, and M0 self-check. Confirm E2E golden unchanged, root has no unapproved deletion, and no real metadata sentinel changed.

- [ ] **Step 3: Refresh the architecture index and perform the final review**

Run `index_repository` for dragon-quant and use architecture queries/source checks to verify the new graph. Execute the ten-item `designing-navigable-architecture` review: entry, dependencies, boundaries, root, responsibilities, overdesign, contract consistency, migration safety, artifact hygiene, and project constraints.

- [ ] **Step 4: Run code-self-review and hand off**

Report documentation changes, packaging contents, full test results, index refresh, final review evidence, known issues, and any items deliberately left untouched. Main performs the repository-wide M0–M12 final acceptance.
