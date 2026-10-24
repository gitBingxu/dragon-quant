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
