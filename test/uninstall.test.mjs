import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { uninstall } from "../lib/uninstall.mjs";
import { InstallerError } from "../lib/install.mjs";
import { MANIFEST_NAME } from "../lib/manifest.mjs";
import { sha256 } from "../lib/skill-package.mjs";

function installFixture({ extra = null, edit = null, foreign = false } = {}) {
  const skillsRoot = path.join(fs.mkdtempSync(path.join(os.tmpdir(), "dq-un-")), "skills");
  const skillDir = path.join(skillsRoot, "dragon-quant");
  fs.mkdirSync(path.join(skillDir, "references"), { recursive: true });
  const files = {
    "SKILL.md": Buffer.from("v1"),
    "references/a.md": Buffer.from("a"),
  };
  for (const [rel, buf] of Object.entries(files)) {
    fs.writeFileSync(path.join(skillDir, rel), buf);
  }
  if (!foreign) {
    const hashes = {};
    for (const [rel, buf] of Object.entries(files)) hashes[rel] = sha256(buf);
    fs.writeFileSync(path.join(skillDir, MANIFEST_NAME), JSON.stringify({
      schema: 1,
      installer: "dragon-quant-skills",
      skill: "dragon-quant",
      agent: "claude-code",
      scope: "project",
      package_version: "0.7.1",
      skill_version: "0.7.1",
      min_cli_version: "0.7.1",
      installed_at: "2026-10-24T12:00:00.000Z",
      source: "npm:dragon-quant-skills@0.7.1",
      files: hashes,
    }));
  }
  if (edit) fs.writeFileSync(path.join(skillDir, "SKILL.md"), edit);
  if (extra) fs.writeFileSync(path.join(skillDir, extra), "extra");
  return { skillsRoot, skillDir };
}

test("missing install returns not-installed and writes nothing", () => {
  const skillsRoot = fs.mkdtempSync(path.join(os.tmpdir(), "dq-un-"));
  const report = uninstall({ skillsRoot, skillDir: path.join(skillsRoot, "dragon-quant") });
  assert.equal(report.ok, true);
  assert.equal(report.state, "not-installed");
  assert.equal(report.backupPath, null);
  assert.deepEqual(fs.readdirSync(skillsRoot), []);
});

test("clean uninstall removes only manifest files and manifest", () => {
  const { skillsRoot, skillDir } = installFixture();
  const report = uninstall({ skillsRoot, skillDir });
  assert.equal(report.state, "removed");
  assert.equal(fs.existsSync(skillDir), false);
});

test("clean uninstall keeps the parent skills root", () => {
  const { skillsRoot, skillDir } = installFixture();
  uninstall({ skillsRoot, skillDir });
  assert.equal(fs.existsSync(skillsRoot), true);
  assert.deepEqual(fs.readdirSync(skillsRoot), []);
});

test("modified file causes conflict without force", () => {
  const { skillsRoot, skillDir } = installFixture({ edit: "user edit" });
  assert.throws(
    () => uninstall({ skillsRoot, skillDir }),
    (err) => err instanceof InstallerError && err.code === "conflict",
  );
  assert.equal(fs.existsSync(skillDir), true);
});

test("force uninstall moves the whole skill dir to backup", () => {
  const { skillsRoot, skillDir } = installFixture({ edit: "user edit" });
  const report = uninstall({ skillsRoot, skillDir, force: true });
  assert.equal(report.state, "backed-up");
  assert.ok(report.backupPath);
  assert.equal(fs.existsSync(skillDir), false);
  assert.equal(fs.readFileSync(path.join(report.backupPath, "SKILL.md"), "utf8"), "user edit");
});

test("foreign directory is refused without force", () => {
  const { skillsRoot, skillDir } = installFixture({ foreign: true });
  assert.throws(
    () => uninstall({ skillsRoot, skillDir }),
    (err) => err instanceof InstallerError && err.code === "conflict",
  );
});

test("force uninstall of foreign directory moves it to backup", () => {
  const { skillsRoot, skillDir } = installFixture({ foreign: true });
  const report = uninstall({ skillsRoot, skillDir, force: true });
  assert.equal(report.state, "backed-up");
  assert.equal(fs.existsSync(skillDir), false);
  assert.equal(fs.existsSync(path.join(report.backupPath, MANIFEST_NAME)), false);
});

test("user-added file is preserved by force backup", () => {
  const { skillsRoot, skillDir } = installFixture({ extra: "notes.md" });
  const report = uninstall({ skillsRoot, skillDir, force: true });
  assert.equal(fs.readFileSync(path.join(report.backupPath, "notes.md"), "utf8"), "extra");
});

test("symlink inside skill dir is refused", { skip: process.platform === "win32" }, () => {
  const { skillsRoot, skillDir } = installFixture();
  const outside = path.join(fs.mkdtempSync(path.join(os.tmpdir(), "dq-out-")), "target.md");
  fs.writeFileSync(outside, "outside");
  fs.unlinkSync(path.join(skillDir, "references", "a.md"));
  fs.symlinkSync(outside, path.join(skillDir, "references", "a.md"));
  assert.throws(
    () => uninstall({ skillsRoot, skillDir }),
    (err) => err instanceof InstallerError && err.code === "conflict",
  );
  assert.equal(fs.existsSync(outside), true);
});

test("references dir symlink is refused", { skip: process.platform === "win32" }, () => {
  const { skillsRoot, skillDir } = installFixture();
  const outside = fs.mkdtempSync(path.join(os.tmpdir(), "dq-out-"));
  fs.rmSync(path.join(skillDir, "references"), { recursive: true, force: true });
  fs.symlinkSync(outside, path.join(skillDir, "references"));
  assert.throws(
    () => uninstall({ skillsRoot, skillDir }),
    (err) => err instanceof InstallerError && err.code === "conflict",
  );
});

test("force dry-run reports would-back-up and writes nothing", () => {
  const { skillsRoot, skillDir } = installFixture({ edit: "user edit" });
  const before = fs.readdirSync(skillsRoot).sort();
  const report = uninstall({ skillsRoot, skillDir, force: true, dryRun: true });
  assert.equal(report.state, "would-back-up");
  assert.match(report.actions[0], /^would move /);
  assert.equal(fs.existsSync(skillDir), true);
  assert.equal(fs.readFileSync(path.join(skillDir, "SKILL.md"), "utf8"), "user edit");
  assert.deepEqual(fs.readdirSync(skillsRoot).sort(), before);
});
