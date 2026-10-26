# Offline CLI E2E baseline (M0 + M1)

This directory is the deterministic, subprocess-level baseline for the whole
`dragon_quant` CLI.  It exists so that the M2+ production refactor can prove
"behavior unchanged" against reviewed golden files *before* touching production
code.

## P0 safety boundary

- Real `dragon.db` / `assets.db` / `prep.db` are **never opened**.  The harness
  only observes their directory/file metadata with `os.stat` (`harness/sentinel.py`).
  A metadata change fails the case; the consumer must guarantee an exclusive
  environment rather than retry.
- Every business command runs in a fresh temporary root with an isolated
  `DQ_DATA_DIR`, `HOME`, `TMPDIR`, `APPDATA`, `XDG_DATA_HOME`, and a whitelisted
  environment.  Credentials, cookies, proxies, and `DQ_TEST_ALLOW_REAL_DATA` are
  not inherited.
- A harmless preflight child proves the injection *before* the business process
  is launched.  Missing/wrong injection aborts; it never degrades to a real
  provider, browser, or network.
- External sockets and DNS fail closed in every child.  `localhost` is allowed
  only for the Web cases, and the child still proves external access is blocked.
- No real network, npm registry, Playwright download, Cookie, R2/B2, or cloud
  call is permitted.  Any real-access attempt is a stop condition, not a test to
  relax.

## Layout

| Path | Purpose |
|---|---|
| `harness/` | Stdlib-only parent harness: sandbox, sentinel, runner/preflight, seed/assert helpers, HTTP server helper, Node helper |
| `injection/` | Child-side `sitecustomize`: fake providers/backends, fake Playwright, frozen clock, socket/process guards |
| `fixtures/` | Synthetic per-scenario JSON plus `seed_builders.py` / `factor_builders.py` (no production data) |
| `golden/` | Reviewed output/DB snapshots consumed by the tests |
| `test_*.py` | One module per command family; every case is a real CLI subprocess |
| `coverage-map.json` | Machine-checkable map: 51 parser nodes / 15 top-level commands / hidden alias / invocation facts / independent entries → real tests |
| `test_coverage_map.py` | Audit that enforces the map (and rejects unexplained skips) |

## Run

```bash
# M0 harness proof (no business cases)
.venv/bin/python tests/e2e/harness/runner.py --self-check
.venv/bin/python -m pytest tests/e2e/test_harness_safety.py tests/e2e/test_architecture_rules.py -q

# Full offline E2E gate
.venv/bin/python -m pytest tests/e2e -q

# Node installer gate (isolated HOME/TMPDIR/npm cache; no registry access)
npm test

# Full unit suite (written by M1F2 to be offline + real-data-sentinel guarded)
.venv/bin/python -m pytest tests -q --ignore=tests/e2e --ignore=tests/smoke
```

Expected: zero failures, zero skips in `tests/e2e` on the default macOS APFS
volume, real-data sentinel unchanged, and no external call.

## Coverage map

`coverage-map.json` embeds the stage-1 `parser_dump.json` snapshot (51 nodes,
15 top-level commands) and maps each node to one or more real test functions.
It also pins:

- the hidden `scan_v2` compatibility alias (and G1 keeping it out of help);
- invocation facts, including the **actual** no-argument behavior
  (`python -m dragon_quant` prints help; the documented "no args == scan" is not
  what the parser does — this is a pinned baseline fact, not a fix);
- group-without-action behavior (`logs`/`data`/`storage` silent exit 0;
  `blacklist` defaults to list; `assets`/`factor`/`factor fin` error exit 2);
- independent entries: real HTTP server and real Node installer; Cookie is no
  longer an independent entry and is covered by the formal
  `data cookie-status|cookie-set|cookie-fetch` cases (D7/D8/D9);
- cross-cutting notes: R9–R11/RA7 Web mappings, the merged npm N4/N5 lifecycle,
  and AS16's shared `push`/`pull` lock.

`test_coverage_map.py` fails if a parser node is unmapped, a map entry points to
a nonexistent test, an E2E test is silently unmapped, or a skip appears without
a declared reason.  Adding a command or test therefore requires updating the map.

## Golden update discipline

- Golden values are reviewed artifacts, not generated output.  Never run a test
  with auto-accept/overwrite semantics to make it green.
- Use the spec tolerances: scores `1e-9`, monetary/price `1e-6`, `elapsed_s` by
  range/type, absolute paths normalized, timestamps exact under the frozen clock.
- A behavior difference is investigated first: fixture/clock/calendar, then the
  assertion.  Never widen a tolerance or delete an assertion to hide a diff.
- M2–M7 must not edit existing E2E cases or golden unless a new behavior is
  explicitly in scope; they may only add assertions.
- Two controlled harness migrations are allowed: M9 replaces global
  datetime/time freezing with `runtime.clock`; M10 changes Web ports from
  pre-selected to `port=0`.  The assertions stay the same.

## Failure diagnostics

The runner prints the case, sandbox root (`DQ_E2E_KEEP=1` keeps it), command,
exit code, stdout/stderr tails, injection marker, call log tail, sentinel diff,
temporary DB table counts, and a shallow file tree.  Attribution order is:
harness precondition → missing injection → sentinel/network → command output →
golden diff.  A sentinel failure stops attribution: the environment is not
exclusive, so business diffs are not interpreted.

## Non-gating network smoke

`tests/smoke/` is an explicit opt-in, non-gating read-only smoke:

```bash
DQ_RUN_NETWORK_SMOKE=1 .venv/bin/python -m pytest tests/smoke -q
```

It is skipped by default and is not part of any M1 gate.  It never writes a real
database (temporary `DQ_DATA_DIR`/`HOME`) and never runs in CI/gate mode.
