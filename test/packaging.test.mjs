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

test("local tarball installs two agents, is idempotent, conflicts and uninstalls", () => {
  const outDir = fs.mkdtempSync(path.join(os.tmpdir(), "dq-pack-e2e-"));
  const packed = run("npm", ["pack", "--json", "--pack-destination", outDir]);
  assert.equal(packed.status, 0, packed.stderr);
  const tarball = path.join(outDir, JSON.parse(packed.stdout)[0].filename);
  const project = fs.mkdtempSync(path.join(os.tmpdir(), "dq-project-"));
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "dq-home-"));
  const env = { ...process.env, HOME: home, npm_config_cache: path.join(home, ".npm") };
  for (const key of ["OPENCLAW_STATE_DIR", "DSH_HOME", "PI_CODING_AGENT_DIR"]) delete env[key];

  const install = (extra = []) => runSkillBin(
    tarball,
    ["install", "--project", "--agent", "claude-code,hermes", "--target-dir", project, "--json", ...extra],
    env,
  );

  const first = install();
  assert.equal(first.status, 0, first.stderr);
  const firstJson = parseLastJsonObject(first.stdout);
  assert.equal(firstJson.ok, true);
  assert.deepEqual(firstJson.agents.map((item) => item.agent), ["claude-code", "hermes"]);
  const claude = path.join(project, ".claude", "skills", "dragon-quant");
  const hermes = path.join(project, ".hermes", "skills", "dragon-quant");
  for (const skill of [claude, hermes]) {
    assert.equal(fs.existsSync(path.join(skill, "SKILL.md")), true, skill);
    assert.equal(fs.existsSync(path.join(skill, ".dragon-quant-skill.json")), true, skill);
  }

  const second = install();
  assert.equal(second.status, 0);
  assert.deepEqual(
    parseLastJsonObject(second.stdout).agents.map((item) => item.state),
    ["up-to-date", "up-to-date"],
  );

  fs.appendFileSync(path.join(claude, "SKILL.md"), "\nuser edit\n");
  const conflict = install();
  assert.equal(conflict.status, 1);
  assert.equal(parseLastJsonObject(conflict.stdout).error.code, "conflict");
  assert.equal(fs.existsSync(path.join(hermes, "SKILL.md")), true);

  const uninstallConflict = runSkillBin(
    tarball,
    ["uninstall", "--project", "--agent", "claude-code,hermes", "--target-dir", project, "--json"],
    env,
  );
  assert.equal(uninstallConflict.status, 1);
  assert.equal(parseLastJsonObject(uninstallConflict.stdout).error.code, "conflict");

  const forced = install(["--force"]);
  assert.equal(forced.status, 0, forced.stderr);
  const uninstall = runSkillBin(
    tarball,
    ["uninstall", "--project", "--agent", "claude-code,hermes", "--target-dir", project, "--json"],
    env,
  );
  assert.equal(uninstall.status, 0, uninstall.stderr);
  assert.deepEqual(
    parseLastJsonObject(uninstall.stdout).agents.map((item) => item.agent),
    ["claude-code", "hermes"],
  );
  assert.equal(fs.existsSync(claude), false);
  assert.equal(fs.existsSync(hermes), false);
});

test("npm pack includes the interactive agent-select module", () => {
  const outDir = fs.mkdtempSync(path.join(os.tmpdir(), "dq-pack-select-"));
  const packed = run("npm", ["pack", "--json", "--pack-destination", outDir]);
  assert.equal(packed.status, 0, packed.stderr);
  const files = JSON.parse(packed.stdout)[0].files.map((f) => f.path);
  assert.ok(files.includes("lib/agent-select.mjs"), files.join("\n"));
});
