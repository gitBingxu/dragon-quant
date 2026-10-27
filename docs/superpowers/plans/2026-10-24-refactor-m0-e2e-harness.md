# M0 Offline E2E Safety Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a subprocess-based E2E harness that proves path, database, credential, network, injection, and process isolation before any production refactor begins.

**Architecture:** A stdlib-only parent harness creates a per-case temporary root and launches real CLI entry points with a whitelist environment. A preflight interpreter verifies `sitecustomize` injection before every business command; the child then uses fake providers/backends/browser, a frozen clock, and a socket guard. Real databases are never opened; only metadata sentinels are observed.

**Tech Stack:** Python 3.14 stdlib, existing pytest, subprocess, importlib hooks, sqlite3 only inside per-case temporary directories, Node for later milestones (not M0).

**Spec:** `/tmp/dragon-quant-architecture-solution/e2e-baseline.md` §1–§3 and `/tmp/dragon-quant-architecture-solution/migration-plan.md` M0. User-approved constraints and M0–M12 order are in `solution.md`.

## Global Constraints

- M0 adds test infrastructure only; do not modify production code under `dragon_quant/` or `web_ui/`.
- Never read, write, migrate, or delete real `dragon.db`, `assets.db`, or `prep.db`; real data files may only be inspected with `os.stat`.
- Every child gets an isolated `DQ_DATA_DIR`, `HOME`, `TMPDIR`, `APPDATA`, and `XDG_DATA_HOME`; credentials, cookies, proxies, and `DQ_TEST_ALLOW_REAL_DATA` are never inherited.
- A harmless preflight must pass before every business command. Missing or incorrect injection means the business process is never launched.
- No third-party dependency, network smoke, real browser, real R2/B2, git commit, push, or PR.
- `scan` / `review` behavior and algorithms are untouched in M0.
- Test code may use pytest, but `tests/e2e/harness/` itself must remain stdlib-only and must not import `dragon_quant` or pytest.

## Review Focus

- Missing, relative, symlinked, or real-data paths must abort before child launch (Task 1: C1–C4/C16).
- A disabled or incorrect `sitecustomize` must prevent the business command, not merely fail after it exits (Task 2: C9/C15).
- Sentinel comparison must detect changes using a fake production directory, without touching the real one (Task 1: C10).
- External network and DNS must fail closed; localhost is allowed only for an explicit web case (Task 2: C5/C6/C13).
- Provider, backend, clock, and Playwright fakes must prove they are active before fixtures rely on them (Task 3: C7/C8/C17/C19).
- Timeout, process-group, and temporary-directory cleanup must not leave a running child (Task 4).

---

### Task 1: Sandbox, environment whitelist, and metadata sentinel

**Files:**
- Create: `tests/e2e/harness/__init__.py`
- Create: `tests/e2e/harness/sandbox.py`
- Create: `tests/e2e/harness/sentinel.py`
- Create: `tests/e2e/test_harness_safety.py`
- Create: `tests/e2e/test_architecture_rules.py`

**Interfaces:**
- Produces: `EnvSandbox.for_case(case_id: str, *, now: str) -> EnvSandbox`
- Produces: `sandbox.root`, `sandbox.data_dir`, `sandbox.home`, `sandbox.tmp`, `sandbox.remote`, `sandbox.out`
- Produces: `sandbox.env(extra: Mapping[str, str] | None = None) -> dict[str, str]`
- Produces: `sandbox.assert_output_path(path: str | Path) -> Path`
- Produces: `sandbox.cleanup() -> None`
- Produces: `RealDataSentinel.snapshot() -> dict[str, tuple]`, `RealDataSentinel.assert_unchanged(before, after) -> None`
- Produces: `E2EHarnessError`

- [ ] **Step 1: Write failing C1–C4, C10, C12, and C16 tests**

Cover: missing `DQ_DATA_DIR`; relative path; symlink escape; real default path; output outside root; metadata change in a fake production directory; harness AST imports; inherited `DQ_TEST_ALLOW_REAL_DATA=1`. Assert failure occurs before any child `Popen`/`run` call.

- [ ] **Step 2: Run the focused tests and verify they fail for missing harness code**

Run: `.venv/bin/python -m pytest tests/e2e/test_harness_safety.py tests/e2e/test_architecture_rules.py -q`
Expected: FAIL on missing `tests.e2e.harness` modules or unimplemented behavior.

- [ ] **Step 3: Implement the sandbox and sentinel**

Create per-case `<root>/{data,home,tmp,remote,out}` and a whitelist environment containing the approved variables from the spec. Resolve paths before comparison, reject escapes and real-data locations, remove credentials/proxies, and honor `DQ_E2E_KEEP=1` only for diagnostics. The sentinel computes the platform default without importing application code and uses `os.stat` only.

- [ ] **Step 4: Re-run the focused tests**

Run the Step 2 command. Expected: all Task 1 tests PASS; no real database is opened and project files outside `tests/e2e/` are unchanged.

### Task 2: Network guard and mandatory preflight runner

**Files:**
- Create: `tests/e2e/harness/runner.py`
- Create: `tests/e2e/injection/sitecustomize.py`
- Create: `tests/e2e/injection/netguard.py`
- Modify: `tests/e2e/test_harness_safety.py`

**Interfaces:**
- Consumes: Task 1 `EnvSandbox` and `E2EHarnessError`
- Produces: `preflight(sandbox: EnvSandbox, *, allow_localhost: bool = False) -> dict`
- Produces: `run_cli(args: Sequence[str], *, sandbox: EnvSandbox, timeout: int = 60, allow_localhost: bool = False) -> CliResult`
- Produces: `run_python(code: str, *, sandbox: EnvSandbox, timeout: int = 30) -> CliResult`
- Produces: `CliResult(returncode: int, stdout: str, stderr: str)`
- Produces: `E2EInjectionError`, `NetworkBlocked`

- [ ] **Step 1: Write failing C5, C6, C9, C13, and C15 tests**

Use harmless `python -c` child programs for external socket/DNS blocking and localhost exceptions. For disabled or incorrect injection, use a spy on business launch and assert no business process starts, no business DB appears, and the real sentinel is unchanged.

- [ ] **Step 2: Verify the new tests fail before implementation**

Run: `.venv/bin/python -m pytest tests/e2e/test_harness_safety.py -q`
Expected: FAIL because preflight, runner, or guard is absent.

- [ ] **Step 3: Implement child injection and preflight**

`sitecustomize` validates child paths, installs the guard, and writes `injection.json` with its actual path and capabilities. `runner.preflight` executes a harmless interpreter command with identical env/interpreter/PYTHONPATH, validates the marker and guard, and only then permits `run_cli`. Missing preflight or marker mismatch raises `E2EInjectionError` before business launch.

- [ ] **Step 4: Re-run Tasks 1–2 tests**

Run the Step 2 command. Expected: all current safety tests PASS; negative injection tests prove business was not launched.

### Task 3: Provider, backend, clock, and browser injection canaries

**Files:**
- Create: `tests/e2e/injection/fake_providers.py`
- Create: `tests/e2e/injection/fake_clock.py`
- Create: `tests/e2e/injection/fake_assets_backend.py`
- Create: `tests/e2e/injection/playwright/__init__.py`
- Create: `tests/e2e/injection/playwright/sync_api.py`
- Modify: `tests/e2e/injection/sitecustomize.py`
- Modify: `tests/e2e/test_harness_safety.py`
- Create: `tests/e2e/fixtures/scenario_minimal.json`

**Interfaces:**
- Consumes: Task 2 preflight and runner
- Produces: fake modules for `dragon_quant.providers.{tdx,ths,xueqiu,tencent}` with class names `TdxProvider`, `THSProvider`, `XueqiuProvider`, `TencentProvider`
- Produces: `S3Backend` adaptation to `LocalDirBackend(<root>/remote)` while preserving the backend method surface used by assets commands
- Produces: frozen `DQ_E2E_NOW` behavior for `datetime`/`time`, including function-local imports
- Produces: fake `playwright.sync_api.sync_playwright` supporting `DQ_E2E_COOKIE_FETCH=ok|empty|error`
- Produces: call log `<root>/calls.jsonl` and preflight capability declarations

- [ ] **Step 1: Write failing C7, C8, C14, C17, and C19 tests**

Assert `create_providers()` returns fake classes; the clock is fixed even for function-local imports; `storage clear --all` affects only the sandbox and preserves its database files; the real cookie `-m` entry writes a synthetic cookie only under the sandbox for `ok`, and covers `empty`/`error` without browser/network.

- [ ] **Step 2: Run the new canaries and verify failure**

Run: `.venv/bin/python -m pytest tests/e2e/test_harness_safety.py -q`
Expected: FAIL on missing fake behavior, not on real network or real-data access. A failure caused by attempted real access is a stop condition.

- [ ] **Step 3: Implement the four injection seams**

Use precise import hooks for provider modules and backend adaptation. Freeze time without changing production files. Fake only the Playwright calls exercised by the current cookie entry; record calls and never start Chromium. Keep provider methods needed by M1 extensible, but do not build the full M1 scenario catalog in this task.

- [ ] **Step 4: Re-run all M0 canaries**

Run the Step 2 command. Expected: C1–C17 and C19 PASS; every business command has a successful preflight; real sentinel unchanged.

### Task 4: Standalone self-check, cleanup, and final M0 gate

**Files:**
- Modify: `tests/e2e/harness/runner.py`
- Modify: `tests/e2e/test_harness_safety.py`
- Modify: `tests/e2e/test_architecture_rules.py`

**Interfaces:**
- Consumes: all prior harness interfaces
- Produces: `python tests/e2e/harness/runner.py --self-check` with nonzero exit on failure
- Produces: timeout/process-group cleanup for later web E2E without enabling web tests in M0

- [ ] **Step 1: Add failing cleanup and standalone tests**

Cover timeout termination, `DQ_E2E_KEEP` diagnostics, repeat cleanup, standalone self-check success, and the rule that harness/injection files contain no application or pytest imports. Do not start a real web server in M0.

- [ ] **Step 2: Verify the new tests fail, then implement minimal cleanup**

Run the focused tests, implement `start_new_session`, terminate/kill escalation, and idempotent cleanup only as required by the tests.

- [ ] **Step 3: Run the complete M0 verification**

Run:
`.venv/bin/python -m pytest tests/e2e/test_harness_safety.py tests/e2e/test_architecture_rules.py -q`
`.venv/bin/python tests/e2e/harness/runner.py --self-check`
`git status --short`

Expected: all tests PASS; self-check exit 0; changed files are limited to this plan and `tests/e2e/`; no production files changed.

- [ ] **Step 4: Run code-self-review and hand off**

Record real command output, canary coverage, sentinel results, known limitations, and any deviations. Do not commit. Main performs the M0 acceptance; M1 must not begin until that acceptance passes.
