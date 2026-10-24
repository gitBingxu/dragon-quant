import test from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const ROOT = path.resolve(import.meta.dirname, "..");
const BIN = path.join(ROOT, "bin", "dragon-quant-skills.mjs");

function tempDir(prefix = "dq-cli-") {
  return fs.mkdtempSync(path.join(os.tmpdir(), prefix));
}

function makeFixturePackage() {
  const root = tempDir("dq-cli-pkg-");
  fs.writeFileSync(path.join(root, "package.json"), JSON.stringify({ version: "0.7.1" }));
  const skill = path.join(root, "skills", "dragon-quant");
  fs.mkdirSync(path.join(skill, "references"), { recursive: true });
  fs.writeFileSync(path.join(skill, "SKILL.md"), [
    "---",
    "name: dragon-quant",
    'description: "Use when scanning dragons."',
    "license: MIT",
    "metadata:",
    '  min-cli-version: "0.7.1"',
    '  skill-version: "0.7.1"',
    "---",
    "# dragon-quant",
    "",
  ].join("\n"));
  fs.writeFileSync(path.join(skill, "references", "commands.md"), "# commands\n");
  return root;
}

const FIXTURE_PACKAGE = makeFixturePackage();

function run(args, { cwd, home, env } = {}) {
  const project = cwd ?? tempDir("dq-cli-cwd-");
  const isolatedHome = home ?? tempDir("dq-cli-home-");
  return spawnSync(process.execPath, [BIN, ...args], {
    cwd: project,
    env: {
      ...process.env,
      HOME: isolatedHome,
      DQ_PYTHON: "",
      DQ_SKILLS_PACKAGE_ROOT: FIXTURE_PACKAGE,
      ...env,
    },
    encoding: "utf8",
  });
}

function json(stdout) {
  return JSON.parse(stdout);
}

test("version prints package version", () => {
  const result = run(["version"]);
  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stdout, /0\.7\.1/);
});

test("install defaults to project scope", () => {
  const project = tempDir("dq-cli-proj-");
  const result = run(["install", "--json"], { cwd: project });
  assert.equal(result.status, 0, result.stderr);
  assert.equal(json(result.stdout).scope, "project");
  assert.equal(fs.existsSync(path.join(project, ".claude", "skills", "dragon-quant", "SKILL.md")), true);
});

test("install --global writes under isolated HOME", () => {
  const home = tempDir("dq-cli-home-");
  const result = run(["install", "--global", "--json"], { home });
  assert.equal(result.status, 0, result.stderr);
  assert.equal(fs.existsSync(path.join(home, ".claude", "skills", "dragon-quant", "SKILL.md")), true);
});

test("install --target-dir treats DIR as project root", () => {
  const target = tempDir("dq-cli-target-");
  const result = run(["install", "--target-dir", target, "--json"], { cwd: tempDir("dq-cli-cwd-") });
  assert.equal(result.status, 0, result.stderr);
  assert.equal(fs.existsSync(path.join(target, ".claude", "skills", "dragon-quant", "SKILL.md")), true);
  assert.equal(fs.existsSync(path.join(target, "dragon-quant")), false);
});

test("install --dry-run does not create files", () => {
  const project = tempDir("dq-cli-proj-");
  const result = run(["install", "--dry-run", "--json"], { cwd: project });
  assert.equal(result.status, 0, result.stderr);
  assert.equal(fs.existsSync(path.join(project, ".claude")), false);
});

test("install --dry-run reports the absolute final path", () => {
  const project = tempDir("dq-cli-proj-");
  const result = run(["install", "--dry-run", "--json"], { cwd: project });
  const payload = json(result.stdout);
  assert.equal(
    payload.agents[0].path,
    path.join(fs.realpathSync(project), ".claude", "skills", "dragon-quant"),
  );
  assert.equal(path.isAbsolute(payload.agents[0].path), true);
});

test("install warns when another scope has the same skill", () => {
  const home = tempDir("dq-cli-home-");
  const project = tempDir("dq-cli-proj-");
  assert.equal(run(["install", "--global", "--json"], { home }).status, 0);
  const result = run(["install", "--project", "--json"], { cwd: project, home });
  assert.equal(result.status, 0, result.stderr);
  assert.ok(json(result.stdout).warnings.some((item) => /scope|global/i.test(item)));
});

test("second install exits 0 and reports up-to-date", () => {
  const project = tempDir("dq-cli-proj-");
  assert.equal(run(["install", "--json"], { cwd: project }).status, 0);
  const result = run(["install", "--json"], { cwd: project });
  assert.equal(result.status, 0, result.stderr);
  assert.equal(json(result.stdout).agents[0].state, "up-to-date");
});

test("modified install exits 1 with conflict", () => {
  const project = tempDir("dq-cli-proj-");
  assert.equal(run(["install", "--json"], { cwd: project }).status, 0);
  fs.appendFileSync(path.join(project, ".claude", "skills", "dragon-quant", "SKILL.md"), "\nuser edit\n");
  const result = run(["install", "--json"], { cwd: project });
  assert.equal(result.status, 1);
  assert.equal(json(result.stdout).error.code, "conflict");
});

test("install/doctor --json emit stable top-level keys", () => {
  const project = tempDir("dq-cli-proj-");
  const install = run(["install", "--json"], { cwd: project });
  assert.deepEqual(Object.keys(json(install.stdout)).sort(), [
    "agents", "backup_path", "command", "ok", "scope", "warnings",
  ]);
  const doctor = run(["doctor", "--json"], { cwd: project });
  assert.deepEqual(Object.keys(json(doctor.stdout)).sort(), ["checks", "command", "installs", "ok"]);
});

test("unknown agent exits 2", () => {
  const result = run(["install", "--agent", "cursor", "--json"], { cwd: tempDir() });
  assert.equal(result.status, 2);
  assert.equal(json(result.stdout).error.code, "usage");
});

test("global plus target-dir exits 2", () => {
  const result = run(["install", "--global", "--target-dir", tempDir(), "--json"], { cwd: tempDir() });
  assert.equal(result.status, 2);
});

test("uninstall removes clean install", () => {
  const project = tempDir("dq-cli-proj-");
  assert.equal(run(["install", "--json"], { cwd: project }).status, 0);
  const result = run(["uninstall", "--json"], { cwd: project });
  assert.equal(result.status, 0, result.stderr);
  assert.equal(fs.existsSync(path.join(project, ".claude", "skills", "dragon-quant")), false);
});

test("doctor exits 1 when Python CLI is missing", () => {
  const project = tempDir("dq-cli-proj-");
  const home = tempDir("dq-cli-home-");
  assert.equal(run(["install", "--json"], { cwd: project, home }).status, 0);
  const emptyPath = tempDir("dq-cli-empty-path-");
  const result = run(
    ["doctor", "--json", "--python", "/nonexistent/python"],
    { cwd: project, home, env: { PATH: emptyPath } },
  );
  assert.equal(result.status, 1);
  assert.equal(json(result.stdout).ok, false);
});

function makeCorruptPackage() {
  const root = tempDir("dq-cli-corrupt-");
  fs.writeFileSync(path.join(root, "package.json"), JSON.stringify({ version: "0.7.1" }));
  return root;
}

test("version works when the skill package content is corrupt", () => {
  const result = run(["version"], { env: { DQ_SKILLS_PACKAGE_ROOT: makeCorruptPackage() } });
  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stdout, /0\.7\.1/);
});

test("uninstall works when the skill package content is corrupt", () => {
  const project = tempDir("dq-cli-proj-");
  assert.equal(run(["install", "--json"], { cwd: project }).status, 0);
  const result = run(["uninstall", "--json"], {
    cwd: project,
    env: { DQ_SKILLS_PACKAGE_ROOT: makeCorruptPackage() },
  });
  assert.equal(result.status, 0, result.stderr);
  assert.equal(fs.existsSync(path.join(project, ".claude", "skills", "dragon-quant")), false);
});

test("uninstall still refuses a modified install when the package is corrupt", () => {
  const project = tempDir("dq-cli-proj-");
  assert.equal(run(["install", "--json"], { cwd: project }).status, 0);
  fs.appendFileSync(path.join(project, ".claude", "skills", "dragon-quant", "SKILL.md"), "\nuser edit\n");
  const result = run(["uninstall", "--json"], {
    cwd: project,
    env: { DQ_SKILLS_PACKAGE_ROOT: makeCorruptPackage() },
  });
  assert.equal(result.status, 1);
  assert.equal(json(result.stdout).error.code, "conflict");
  assert.equal(fs.existsSync(path.join(project, ".claude", "skills", "dragon-quant", "SKILL.md")), true);
});
