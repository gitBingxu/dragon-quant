import test from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const ROOT = path.resolve(import.meta.dirname, "..");
const SCRIPT = path.join(ROOT, "scripts", "publish-skill.sh");

// The npm entry is now a thin, validated forwarder to scripts/publish.sh; the
// unified Python flow owns every Git/version/Release side effect.  These tests
// therefore assert delegation and argv hardening, not a second publish flow.
function harness() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "dq-publish-skill-"));
  const bin = path.join(dir, "bin");
  fs.mkdirSync(bin);
  const log = path.join(dir, "entry.log");
  const entry = path.join(bin, "fake-python");
  fs.writeFileSync(
    entry,
    `#!/bin/sh
set -eu
printf '%s\\n' "$@" > "$DQ_FAKE_LOG"
exit 0
`,
    { mode: 0o755 },
  );
  return { dir, log, entry };
}

function envFor(tools, extra = {}) {
  return {
    ...process.env,
    DQ_RELEASE_PYTHON: tools.entry,
    DQ_FAKE_LOG: tools.log,
    HOME: tools.dir,
    TMPDIR: tools.dir,
    ...extra,
  };
}

function run(tools, args, extra = {}) {
  return spawnSync("sh", [SCRIPT, ...args], {
    cwd: tools.dir,
    env: envFor(tools, extra),
    encoding: "utf8",
  });
}

function forwarded(tools) {
  return fs.readFileSync(tools.log, "utf8").trim().split("\n");
}

test("dry-run delegates to the unified npm flow with mapped flags", () => {
  const tools = harness();
  const result = run(tools, [
    "--dry-run", "--npm-version", "1.0.2", "--tag", "next",
    "--access", "restricted",
  ]);
  assert.equal(result.status, 0, result.stderr);
  const args = forwarded(tools);
  assert.deepEqual(args.slice(args.indexOf("--packages"), args.indexOf("--packages") + 2),
    ["--packages", "npm"]);
  assert.ok(args.includes("--dry-run"));
  assert.deepEqual(args.slice(args.indexOf("--npm-version"), args.indexOf("--npm-version") + 2),
    ["--npm-version", "1.0.2"]);
  assert.deepEqual(args.slice(args.indexOf("--npm-tag"), args.indexOf("--npm-tag") + 2),
    ["--npm-tag", "next"]);
  assert.deepEqual(args.slice(args.indexOf("--npm-access"), args.indexOf("--npm-access") + 2),
    ["--npm-access", "restricted"]);
});

test("no arguments still delegates (interactive prompting lives upstream)", () => {
  const tools = harness();
  const result = run(tools, []);
  assert.equal(result.status, 0, result.stderr);
  const args = forwarded(tools);
  assert.deepEqual(args.slice(args.indexOf("--packages"), args.indexOf("--packages") + 2),
    ["--packages", "npm"]);
});

test("--resume and --yes are forwarded verbatim", () => {
  const tools = harness();
  const result = run(tools, ["--resume", "--yes"]);
  assert.equal(result.status, 0, result.stderr);
  const args = forwarded(tools);
  assert.ok(args.includes("--resume"));
  assert.ok(args.includes("--yes"));
});

test("a status-dir with spaces survives forwarding", () => {
  const tools = harness();
  const result = run(tools, ["--status-dir", "/tmp/a b"]);
  assert.equal(result.status, 0, result.stderr);
  const args = forwarded(tools);
  assert.deepEqual(args.slice(args.indexOf("--status-dir"), args.indexOf("--status-dir") + 2),
    ["--status-dir", "/tmp/a b"]);
});

test("a status-dir with glob characters is forwarded literally", () => {
  const tools = harness();
  // Create files that WOULD match if the forwarder re-split/globbed the value.
  fs.writeFileSync(path.join(tools.dir, "dq-one"), "");
  fs.writeFileSync(path.join(tools.dir, "dq-two"), "");
  const result = run(tools, ["--status-dir", "dq-*"]);
  assert.equal(result.status, 0, result.stderr);
  const args = forwarded(tools);
  const index = args.indexOf("--status-dir");
  assert.deepEqual(args.slice(index, index + 2), ["--status-dir", "dq-*"]);
});

test("argv OTP, registry overrides and package paths are rejected", () => {
  for (const args of [
    ["--otp", "123456"],
    ["--registry=https://evil.invalid"],
    ["--userconfig=/tmp/x"],
    ["--ignore-scripts"],
    ["/tmp/some-package"],
    ["--pypi-version", "1.2.3"],
    ["--packages", "pypi"],
  ]) {
    const tools = harness();
    const result = run(tools, args);
    assert.notEqual(result.status, 0, args.join(" "));
    assert.equal(fs.existsSync(tools.log), false, "must not delegate for rejected args");
  }
});

test("tag/access values are strictly validated without delegating", () => {
  for (const args of [
    ["--tag", "latest --registry=https://evil.invalid"],
    ["--tag", "latest*"],
    ["--tag", "bad\nvalue"],
    ["--tag", ""],
    ["--access", "internal"],
    ["--npm-version"],
  ]) {
    const tools = harness();
    const result = run(tools, args);
    assert.notEqual(result.status, 0, args.join(" "));
    assert.equal(fs.existsSync(tools.log), false, "must not delegate for invalid args");
  }
});
