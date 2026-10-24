import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { install, InstallerError } from "../lib/install.mjs";
import { MANIFEST_NAME } from "../lib/manifest.mjs";
import { sha256 } from "../lib/skill-package.mjs";

const meta = () => ({ metadata: { "skill-version": "0.7.1", "min-cli-version": "0.7.1" } });
const pkgFiles = () => new Map([
  ["SKILL.md", Buffer.from("v1")],
  ["references/a.md", Buffer.from("a")],
]);

function tempSkillsRoot() {
  return path.join(fs.mkdtempSync(path.join(os.tmpdir(), "dq-inst-")), "skills");
}

function args(overrides = {}) {
  const skillsRoot = overrides.skillsRoot ?? tempSkillsRoot();
  return {
    skillsRoot,
    skillDir: path.join(skillsRoot, "dragon-quant"),
    packageFiles: pkgFiles(),
    packageVersion: "0.7.1",
    meta: meta(),
    agent: "claude-code",
    scope: "project",
    source: "npm:dragon-quant-skills@0.7.1",
    ...overrides,
  };
}

test("fresh install writes files and manifest", () => {
  const a = args();
  const report = install(a);
  assert.equal(report.ok, true);
  assert.equal(report.state, "installed");
  assert.equal(fs.readFileSync(path.join(a.skillDir, "SKILL.md"), "utf8"), "v1");
  const manifest = JSON.parse(fs.readFileSync(path.join(a.skillDir, MANIFEST_NAME), "utf8"));
  assert.equal(manifest.schema, 1);
  assert.equal(manifest.files["SKILL.md"], sha256(Buffer.from("v1")));
  assert.equal(manifest.package_version, "0.7.1");
  assert.equal(manifest.installed_at instanceof Date, false);
  assert.match(manifest.installed_at, /^\d{4}-\d{2}-\d{2}T/);
});

test("second install is up-to-date and does not create a backup", () => {
  const a = args();
  install(a);
  const report = install(a);
  assert.equal(report.state, "up-to-date");
  assert.equal(report.backupPath, null);
  const backups = fs.readdirSync(a.skillsRoot).filter((n) => n.startsWith(".dragon-quant-backup-"));
  assert.deepEqual(backups, []);
});

test("clean upgrade replaces files and removes obsolete ones", () => {
  const a = args();
  install(a);
  const next = new Map([
    ["SKILL.md", Buffer.from("v2")],
    ["references/b.md", Buffer.from("b")],
  ]);
  const report = install({ ...a, packageFiles: next, packageVersion: "0.7.2" });
  assert.equal(report.state, "upgraded");
  assert.equal(fs.readFileSync(path.join(a.skillDir, "SKILL.md"), "utf8"), "v2");
  assert.equal(fs.existsSync(path.join(a.skillDir, "references", "b.md")), true);
  assert.equal(fs.existsSync(path.join(a.skillDir, "references", "a.md")), false);
  const manifest = JSON.parse(fs.readFileSync(path.join(a.skillDir, MANIFEST_NAME), "utf8"));
  assert.deepEqual(Object.keys(manifest.files).sort(), ["SKILL.md", "references/b.md"]);
  assert.equal(manifest.package_version, "0.7.2");
  const backups = fs.readdirSync(a.skillsRoot).filter((n) => n.startsWith(".dragon-quant-backup-"));
  assert.deepEqual(backups, []);
});

test("modified file causes conflict without force", () => {
  const a = args();
  install(a);
  fs.writeFileSync(path.join(a.skillDir, "SKILL.md"), "user edit");
  assert.throws(
    () => install(a),
    (err) => err instanceof InstallerError && err.code === "conflict" && /SKILL\.md/.test(err.message),
  );
});

test("force install backs up user-modified directory", () => {
  const a = args();
  install(a);
  fs.writeFileSync(path.join(a.skillDir, "SKILL.md"), "user edit");
  const report = install({ ...a, force: true });
  assert.equal(report.state, "forced");
  assert.ok(report.backupPath);
  assert.equal(fs.readFileSync(path.join(report.backupPath, "SKILL.md"), "utf8"), "user edit");
  assert.equal(fs.readFileSync(path.join(a.skillDir, "SKILL.md"), "utf8"), "v1");
});

test("foreign directory causes conflict without force", () => {
  const a = args();
  fs.mkdirSync(a.skillDir, { recursive: true });
  fs.writeFileSync(path.join(a.skillDir, "notes.md"), "mine");
  assert.throws(
    () => install(a),
    (err) => err instanceof InstallerError && err.code === "conflict",
  );
});

test("dry-run writes nothing", () => {
  const a = args();
  const report = install({ ...a, dryRun: true });
  assert.equal(report.ok, true);
  assert.equal(fs.existsSync(a.skillDir), false);
  assert.equal(path.isAbsolute(report.skillDir), true);
  assert.ok(report.actions.length > 0);
});

test("install refuses a symlinked skill directory", { skip: process.platform === "win32" }, () => {
  const a = args();
  fs.mkdirSync(a.skillsRoot, { recursive: true });
  const real = path.join(a.skillsRoot, "real");
  fs.mkdirSync(real);
  fs.symlinkSync(real, a.skillDir);
  assert.throws(
    () => install(a),
    (err) => err instanceof InstallerError && err.code === "unsafe-path",
  );
});

test("install refuses a symlinked references dir", { skip: process.platform === "win32" }, () => {
  const a = args();
  install(a);
  const realRefs = path.join(a.skillsRoot, "real-refs");
  fs.mkdirSync(realRefs);
  fs.rmSync(path.join(a.skillDir, "references"), { recursive: true, force: true });
  fs.symlinkSync(realRefs, path.join(a.skillDir, "references"));
  assert.throws(
    () => install(a),
    (err) => err instanceof InstallerError && err.code === "conflict",
  );
});

test("manifest is staged before rename and contains all hashes", () => {
  const a = args();
  install(a);
  const manifest = JSON.parse(fs.readFileSync(path.join(a.skillDir, MANIFEST_NAME), "utf8"));
  for (const [rel, buf] of a.packageFiles) {
    assert.equal(manifest.files[rel], sha256(buf), rel);
  }
  const leftovers = fs.readdirSync(a.skillsRoot).filter((n) => n.startsWith(".dragon-quant-staging-"));
  assert.deepEqual(leftovers, []);
});

test("staging directory is removed on success", () => {
  const a = args();
  install(a);
  install(a);
  const entries = fs.readdirSync(a.skillsRoot);
  assert.deepEqual(entries.sort(), ["dragon-quant"]);
});

test("fresh install leaves no target when staging fails", () => {
  const a = args();
  const bad = new Map([
    ["SKILL.md", Buffer.from("v1")],
    ["../escape.md", Buffer.from("nope")],
  ]);
  assert.throws(
    () => install({ ...a, packageFiles: bad }),
    (err) => err instanceof InstallerError && err.code === "unsafe-path",
  );
  assert.equal(fs.existsSync(a.skillDir), false);
  assert.equal(fs.existsSync(path.join(a.skillsRoot, "escape.md")), false);
});
