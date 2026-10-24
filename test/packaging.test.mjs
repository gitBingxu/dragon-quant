import test from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const ROOT = path.resolve(import.meta.dirname, "..");

function run(cmd, args, opts = {}) {
  return spawnSync(cmd, args, { cwd: ROOT, encoding: "utf8", ...opts });
}

function parseLastJsonObject(text) {
  const start = text.lastIndexOf("\n{");
  return JSON.parse(start >= 0 ? text.slice(start + 1) : text);
}

function runSkillBin(tarball, args, env) {
  return spawnSync(
    "npm",
    ["exec", "--yes", `--package=${tarball}`, "--", "dragon-quant-skills", ...args],
    { cwd: ROOT, env, encoding: "utf8" },
  );
}

test("npm pack contains only the whitelist", () => {
  const outDir = fs.mkdtempSync(path.join(os.tmpdir(), "dq-pack-"));
  const packed = run("npm", ["pack", "--json", "--pack-destination", outDir]);
  assert.equal(packed.status, 0, packed.stderr);
  const info = JSON.parse(packed.stdout)[0];
  const files = info.files.map((f) => f.path);
  for (const file of files) {
    assert.match(file, /^(package\.json|README\.md|LICENSE|bin\/|lib\/|skills\/)/, file);
  }
  for (const forbidden of ["dragon_quant/", "assets/", ".env", "node_modules/", "tests/", "test/", "docs/"]) {
    assert.equal(files.some((f) => f.startsWith(forbidden)), false, forbidden);
  }
});

test("local tarball installs, is idempotent, conflicts and uninstalls", () => {
  const outDir = fs.mkdtempSync(path.join(os.tmpdir(), "dq-pack-e2e-"));
  const packed = run("npm", ["pack", "--json", "--pack-destination", outDir]);
  assert.equal(packed.status, 0, packed.stderr);
  const tarball = path.join(outDir, JSON.parse(packed.stdout)[0].filename);
  const project = fs.mkdtempSync(path.join(os.tmpdir(), "dq-project-"));
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "dq-home-"));
  const env = { ...process.env, HOME: home, npm_config_cache: path.join(home, ".npm") };

  const install = (extra = []) => runSkillBin(
    tarball,
    ["install", "--project", "--target-dir", project, "--json", ...extra],
    env,
  );

  const first = install();
  assert.equal(first.status, 0, first.stderr);
  assert.equal(parseLastJsonObject(first.stdout).ok, true);
  const skill = path.join(project, ".claude", "skills", "dragon-quant");
  assert.equal(fs.existsSync(path.join(skill, "SKILL.md")), true);
  assert.equal(fs.existsSync(path.join(skill, ".dragon-quant-skill.json")), true);

  const second = install();
  assert.equal(second.status, 0);
  assert.equal(parseLastJsonObject(second.stdout).agents[0].state, "up-to-date");

  fs.appendFileSync(path.join(skill, "SKILL.md"), "\nuser edit\n");
  const conflict = install();
  assert.equal(conflict.status, 1);
  assert.equal(parseLastJsonObject(conflict.stdout).error.code, "conflict");

  const uninstall = runSkillBin(
    tarball,
    ["uninstall", "--project", "--target-dir", project, "--json"],
    env,
  );
  assert.equal(uninstall.status, 1);
  assert.equal(parseLastJsonObject(uninstall.stdout).error.code, "conflict");
});
