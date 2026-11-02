import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { runDoctor } from "../lib/doctor.mjs";
import { install } from "../lib/install.mjs";

const meta = () => ({ metadata: { "skill-version": "0.7.1", "min-cli-version": "0.7.1" } });
const pkgFiles = () => new Map([
  ["SKILL.md", Buffer.from("v1")],
  ["references/a.md", Buffer.from("a")],
]);
const goodDetect = () => ({
  found: true, command: "/venv/bin/python", version: "0.7.1", source: "pythonArg", error: null,
});

function makeInstall(rootName = "project") {
  const projectRoot = fs.mkdtempSync(path.join(os.tmpdir(), `dq-doc-${rootName}-`));
  const skillsRoot = path.join(projectRoot, ".claude", "skills");
  const skillDir = path.join(skillsRoot, "dragon-quant");
  install({
    skillDir, skillsRoot, packageFiles: pkgFiles(), packageVersion: "0.7.1",
    meta: meta(), agent: "claude-code", scope: "project",
    source: "npm:dragon-quant-skills@0.7.1",
  });
  return { projectRoot, skillsRoot, skillDir };
}

function target(skillDir, scope = "project", agent = "claude-code") {
  return { agent, scope, skillDir };
}

function run(args) {
  return runDoctor({
    packageFiles: pkgFiles(),
    packageVersion: "0.7.1",
    meta: meta(),
    detect: goodDetect,
    projectRoot: fs.mkdtempSync(path.join(os.tmpdir(), "dq-doc-lock-")),
    ...args,
  });
}

test("ok when install clean and CLI meets min", () => {
  const { skillDir } = makeInstall();
  const report = run({ targets: [target(skillDir)] });
  assert.equal(report.ok, true);
  assert.ok(report.checks.some((c) => c.id === "install.integrity" && c.status === "pass"));
  assert.ok(["clean", "up-to-date"].includes(report.installs[0].state));
});

test("fail when install missing", () => {
  const missing = path.join(fs.mkdtempSync(path.join(os.tmpdir(), "dq-doc-miss-")), "dragon-quant");
  const report = run({ targets: [target(missing)] });
  assert.equal(report.ok, false);
  assert.ok(report.checks.some((c) => c.id === "install.present" && c.status === "fail"));
});

test("fail when install modified", () => {
  const { skillDir } = makeInstall();
  fs.writeFileSync(path.join(skillDir, "SKILL.md"), "user edit");
  const report = run({ targets: [target(skillDir)] });
  assert.equal(report.ok, false);
  assert.ok(report.checks.some((c) => c.id === "install.integrity" && c.status === "fail"));
});

test("fail when CLI missing", () => {
  const { skillDir } = makeInstall();
  const report = run({
    targets: [target(skillDir)],
    detect: () => ({ found: false, command: null, version: null, source: null, error: "ENOENT" }),
  });
  assert.equal(report.ok, false);
  assert.ok(report.checks.some((c) => c.id === "cli.present" && c.status === "fail"));
});

test("fail when CLI below min", () => {
  const { skillDir } = makeInstall();
  const report = run({
    targets: [target(skillDir)],
    detect: () => ({ found: true, command: "/venv/bin/python", version: "0.7.0", source: "pythonArg", error: null }),
  });
  assert.equal(report.ok, false);
  assert.ok(report.checks.some((c) => c.id === "cli.version" && c.status === "fail"));
});

test("warn when global and project both install the skill", () => {
  const a = makeInstall("proj");
  const b = makeInstall("glob");
  const report = run({ targets: [target(a.skillDir, "project"), target(b.skillDir, "global")] });
  assert.equal(report.ok, true);
  assert.ok(report.checks.some((c) => c.id === "scope.shadowing" && c.status === "warn"));
});

test("warn when project skills-lock.json exists", () => {
  const { skillDir } = makeInstall();
  const projectRoot = fs.mkdtempSync(path.join(os.tmpdir(), "dq-doc-lock-"));
  fs.writeFileSync(path.join(projectRoot, "skills-lock.json"), "{}");
  const report = run({ targets: [target(skillDir)], projectRoot });
  assert.ok(report.checks.some((c) => c.id === "foreign.lockfile" && c.status === "warn"));
});

test("report includes reload hint", () => {
  const { skillDir } = makeInstall();
  const report = run({ targets: [target(skillDir)] });
  const hint = report.checks.find((c) => c.id === "reload.hint");
  assert.equal(hint.status, "pass");
  assert.match(hint.detail, /reload-skills/);
});

test("json shape is stable", () => {
  const { skillDir } = makeInstall();
  const report = run({ targets: [target(skillDir)] });
  assert.deepEqual(Object.keys(report).sort(), ["checks", "installs", "ok"]);
  for (const check of report.checks) {
    assert.equal(typeof check.id, "string");
    assert.ok(["pass", "warn", "fail"].includes(check.status));
    assert.equal(typeof check.detail, "string");
    assert.ok("fix" in check);
  }
  const entry = report.installs[0];
  assert.deepEqual(
    Object.keys(entry).sort(),
    ["agent", "packageVersion", "scope", "skillDir", "skillVersion", "state"],
  );
});

test("symlinked install fails with an actionable non-force fix", { skip: process.platform === "win32" }, () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "dq-doc-link-"));
  const skillsRoot = path.join(root, ".claude", "skills");
  fs.mkdirSync(skillsRoot, { recursive: true });
  const real = path.join(root, "real");
  fs.mkdirSync(real);
  const skillDir = path.join(skillsRoot, "dragon-quant");
  fs.symlinkSync(real, skillDir);
  const report = run({ targets: [target(skillDir)] });
  assert.equal(report.ok, false);
  const integrity = report.checks.find((c) => c.id === "install.integrity");
  assert.equal(integrity.status, "fail");
  assert.match(integrity.detail, /symlink/i);
  assert.doesNotMatch(integrity.fix, /--force/);
  assert.match(integrity.fix, /symlink|remove/i);
});

// --- Target-aware hints (Task 3) ---

function reloadDetail(report) {
  return report.checks.find((c) => c.id === "reload.hint").detail;
}

test("reload hint keeps Claude reload text for claude-code", () => {
  const { skillDir } = makeInstall();
  const detail = reloadDetail(run({ targets: [target(skillDir)] }));
  assert.match(detail, /reload-skills/);
});

test("reload hint for codex does not claim Claude precedence", () => {
  const missing = path.join(fs.mkdtempSync(path.join(os.tmpdir(), "dq-doc-codex-")), "dragon-quant");
  const detail = reloadDetail(run({ targets: [target(missing, "project", "codex")] }));
  assert.doesNotMatch(detail, /Enterprise/);
  assert.match(detail, /Codex/);
});

test("reload hint for hermes project names the actual trust path", () => {
  const missing = path.join(fs.mkdtempSync(path.join(os.tmpdir(), "dq-doc-hermes-")), "dragon-quant");
  const projectRoot = path.resolve("/repo/hermes-project");
  const detail = reloadDetail(run({
    targets: [target(missing, "project", "hermes")],
    projectRoot,
  }));
  assert.ok(detail.includes(`hermes skills trust ${projectRoot}`), detail);
});

test("reload hint for pi mentions /reload", () => {
  const missing = path.join(fs.mkdtempSync(path.join(os.tmpdir(), "dq-doc-pi-")), "dragon-quant");
  const detail = reloadDetail(run({ targets: [target(missing, "project", "pi")] }));
  assert.match(detail, /\/reload/);
});

test("reload hint for deepseek says skills are auto-discovered", () => {
  const missing = path.join(fs.mkdtempSync(path.join(os.tmpdir(), "dq-doc-dsh-")), "dragon-quant");
  const detail = reloadDetail(run({ targets: [target(missing, "project", "deepseek")] }));
  assert.match(detail, /自动发现/);
});

test("reload hint dedupes identical hints from repeated targets", () => {
  const { skillDir } = makeInstall();
  const detail = reloadDetail(run({ targets: [target(skillDir), target(skillDir)] }));
  const occurrences = detail.split("/reload-skills").length - 1;
  assert.equal(occurrences, 1, detail);
});

test("scope.shadowing for codex avoids Claude precedence text", () => {
  const a = makeInstall("proj");
  const b = makeInstall("glob");
  const report = run({ targets: [target(a.skillDir, "project", "codex"), target(b.skillDir, "global", "codex")] });
  const shadow = report.checks.find((c) => c.id === "scope.shadowing");
  assert.equal(shadow.status, "warn");
  assert.doesNotMatch(shadow.detail, /Enterprise/);
  assert.match(shadow.detail, /同名/);
});

test("scope.shadowing for claude-code keeps the precedence text", () => {
  const a = makeInstall("proj");
  const b = makeInstall("glob");
  const report = run({ targets: [target(a.skillDir, "project"), target(b.skillDir, "global")] });
  const shadow = report.checks.find((c) => c.id === "scope.shadowing");
  assert.match(shadow.detail, /Enterprise/);
});

test("scope.shadowing uses generic wording for targets without a shared root", () => {
  const a = makeInstall("proj");
  const b = makeInstall("glob");
  const report = run({ targets: [target(a.skillDir, "project", "pi"), target(b.skillDir, "global", "pi")] });
  const shadow = report.checks.find((c) => c.id === "scope.shadowing");
  assert.doesNotMatch(shadow.detail, /Enterprise/);
  assert.match(shadow.detail, /all-scopes/);
});
