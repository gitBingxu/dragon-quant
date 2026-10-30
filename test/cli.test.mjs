import test from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { PassThrough } from "node:stream";
import { pathToFileURL } from "node:url";

const ROOT = path.resolve(import.meta.dirname, "..");
const BIN = path.join(ROOT, "bin", "dragon-quant-skills.mjs");
const OFFICIAL_ENV = ["OPENCLAW_STATE_DIR", "DSH_HOME", "PI_CODING_AGENT_DIR"];

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
  const baseEnv = {
    ...process.env,
    HOME: isolatedHome,
    DQ_PYTHON: "",
    DQ_SKILLS_PACKAGE_ROOT: FIXTURE_PACKAGE,
  };
  // Keep developer-machine official env out of the default assertions.
  for (const key of OFFICIAL_ENV) delete baseEnv[key];
  return spawnSync(process.execPath, [BIN, ...args], {
    cwd: project,
    env: { ...baseEnv, ...env },
    encoding: "utf8",
  });
}

function json(stdout) {
  return JSON.parse(stdout);
}

function skillFile(...segments) {
  return path.join(...segments, "dragon-quant", "SKILL.md");
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
  assert.equal(fs.existsSync(skillFile(project, ".claude", "skills")), true);
});

test("install --global writes under isolated HOME", () => {
  const home = tempDir("dq-cli-home-");
  const result = run(["install", "--global", "--json"], { home });
  assert.equal(result.status, 0, result.stderr);
  assert.equal(fs.existsSync(skillFile(home, ".claude", "skills")), true);
});

test("install --target-dir treats DIR as project root", () => {
  const target = tempDir("dq-cli-target-");
  const result = run(["install", "--target-dir", target, "--json"], { cwd: tempDir("dq-cli-cwd-") });
  assert.equal(result.status, 0, result.stderr);
  assert.equal(fs.existsSync(skillFile(target, ".claude", "skills")), true);
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
  fs.appendFileSync(skillFile(project, ".claude", "skills"), "\nuser edit\n");
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

test("install agents[] keeps existing keys and only adds hints", () => {
  const project = tempDir("dq-cli-proj-");
  const payload = json(run(["install", "--json"], { cwd: project }).stdout);
  assert.deepEqual(Object.keys(payload.agents[0]).sort(), [
    "actions", "agent", "hints", "path", "state", "warnings",
  ]);
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
  fs.appendFileSync(skillFile(project, ".claude", "skills"), "\nuser edit\n");
  const result = run(["uninstall", "--json"], {
    cwd: project,
    env: { DQ_SKILLS_PACKAGE_ROOT: makeCorruptPackage() },
  });
  assert.equal(result.status, 1);
  assert.equal(json(result.stdout).error.code, "conflict");
  assert.equal(fs.existsSync(skillFile(project, ".claude", "skills")), true);
});

// --- Multi-agent installer behaviour ---

test("install claude-code,hermes writes both directories with target-specific hints", () => {
  const project = tempDir("dq-cli-multi-");
  const result = run(["install", "--agent", "claude-code,hermes", "--json"], { cwd: project });
  assert.equal(result.status, 0, result.stderr);
  const payload = json(result.stdout);
  assert.deepEqual(payload.agents.map((item) => item.agent), ["claude-code", "hermes"]);
  assert.equal(fs.existsSync(skillFile(project, ".claude", "skills")), true);
  assert.equal(fs.existsSync(skillFile(project, ".hermes", "skills")), true);

  const root = fs.realpathSync(project);
  const claude = payload.agents[0];
  const hermes = payload.agents[1];
  assert.ok(Array.isArray(claude.hints) && claude.hints.length > 0);
  assert.ok(claude.hints.some((item) => item.includes("/reload-skills")));
  assert.ok(hermes.hints.some((item) => item.includes(`hermes skills trust ${root}`)));
});

test("multi-agent rerun is idempotent for both targets", () => {
  const project = tempDir("dq-cli-multi-");
  assert.equal(run(["install", "--agent", "claude-code,hermes", "--json"], { cwd: project }).status, 0);
  const result = run(["install", "--agent", "claude-code,hermes", "--json"], { cwd: project });
  assert.equal(result.status, 0, result.stderr);
  assert.deepEqual(json(result.stdout).agents.map((item) => item.state), ["up-to-date", "up-to-date"]);
});

test("install --agent all --dry-run expands six harnesses, writes nothing, hints empty", () => {
  const project = tempDir("dq-cli-all-");
  const result = run(["install", "--agent", "all", "--dry-run", "--json"], { cwd: project });
  assert.equal(result.status, 0, result.stderr);
  const payload = json(result.stdout);
  assert.deepEqual(payload.agents.map((item) => item.agent), [
    "claude-code", "codex", "openclaw", "hermes", "deepseek", "pi",
  ]);
  for (const item of payload.agents) assert.deepEqual(item.hints, [], item.agent);
  const root = fs.realpathSync(project);
  assert.equal(payload.agents[2].path, path.join(root, "skills", "dragon-quant"));
  assert.equal(payload.agents[5].path, path.join(root, ".pi", "skills", "dragon-quant"));
  for (const dir of [".claude", ".agents", "skills", ".hermes", ".dsh", ".pi"]) {
    assert.equal(fs.existsSync(path.join(project, dir)), false, dir);
  }
});

test("install --dry-run leaves hints empty", () => {
  const project = tempDir("dq-cli-dry-");
  const result = run(["install", "--agent", "hermes", "--dry-run", "--json"], { cwd: project });
  assert.equal(result.status, 0, result.stderr);
  assert.deepEqual(json(result.stdout).agents[0].hints, []);
});

test("dry-run text does not print reload hints", () => {
  const project = tempDir("dq-cli-dry-");
  const result = run(["install", "--agent", "hermes", "--dry-run"], { cwd: project });
  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stdout, /未写入/);
  assert.doesNotMatch(result.stdout, /hermes skills trust/);
});

test("install --agent agents keeps the compatibility alias working", () => {
  const project = tempDir("dq-cli-alias-");
  const result = run(["install", "--agent", "agents", "--json"], { cwd: project });
  assert.equal(result.status, 0, result.stderr);
  assert.deepEqual(json(result.stdout).agents.map((item) => item.agent), ["agents"]);
  assert.equal(fs.existsSync(skillFile(project, ".agents", "skills")), true);
});

test("install --agent all,agents adds agents after the six harnesses", () => {
  const project = tempDir("dq-cli-alias-");
  const result = run(["install", "--agent", "all,agents", "--dry-run", "--json"], { cwd: project });
  assert.equal(result.status, 0, result.stderr);
  assert.deepEqual(json(result.stdout).agents.map((item) => item.agent), [
    "claude-code", "codex", "openclaw", "hermes", "deepseek", "pi", "agents",
  ]);
});

test("codex hints never claim Claude precedence", () => {
  const project = tempDir("dq-cli-codex-");
  const payload = json(run(["install", "--agent", "codex", "--json"], { cwd: project }).stdout);
  assert.equal(payload.agents[0].hints.some((item) => item.includes("Enterprise")), false);
});

test("pi installs into the real cwd, not the git project root", () => {
  const project = tempDir("dq-cli-pi-");
  fs.mkdirSync(path.join(project, ".git"));
  const nested = path.join(project, "packages", "app");
  fs.mkdirSync(nested, { recursive: true });
  const result = run(["install", "--agent", "pi", "--json"], { cwd: nested });
  assert.equal(result.status, 0, result.stderr);
  assert.equal(fs.existsSync(skillFile(nested, ".pi", "skills")), true);
  assert.equal(fs.existsSync(path.join(project, ".pi")), false);
});

test("openclaw installs into cwd/skills unless --target-dir overrides the workspace", () => {
  const project = tempDir("dq-cli-oc-");
  fs.mkdirSync(path.join(project, ".git"));
  const nested = path.join(project, "workspace");
  fs.mkdirSync(nested);
  const result = run(["install", "--agent", "openclaw", "--json"], { cwd: nested });
  assert.equal(result.status, 0, result.stderr);
  assert.equal(fs.existsSync(skillFile(nested, "skills")), true);
  assert.equal(fs.existsSync(path.join(project, "skills")), false);

  const target = tempDir("dq-cli-oc-target-");
  assert.equal(run(["install", "--agent", "openclaw", "--target-dir", target, "--json"], { cwd: nested }).status, 0);
  assert.equal(fs.existsSync(skillFile(target, "skills")), true);
});

test("install --target-dir overrides the pi base", () => {
  const target = tempDir("dq-cli-pi-target-");
  const result = run(["install", "--agent", "pi", "--target-dir", target, "--json"], { cwd: tempDir() });
  assert.equal(result.status, 0, result.stderr);
  assert.equal(fs.existsSync(skillFile(target, ".pi", "skills")), true);
});

test("global env overrides are used for install and the cross-scope warning", () => {
  const project = tempDir("dq-cli-env-");
  const home = tempDir("dq-cli-env-home-");
  const dshHome = tempDir("dq-cli-dsh-");
  const env = { DSH_HOME: dshHome };

  const global = run(["install", "--global", "--agent", "deepseek", "--json"], { cwd: project, home, env });
  assert.equal(global.status, 0, global.stderr);
  assert.equal(fs.existsSync(skillFile(dshHome, "skills")), true);

  const projectInstall = run(["install", "--project", "--agent", "deepseek", "--json"], { cwd: project, home, env });
  assert.equal(projectInstall.status, 0, projectInstall.stderr);
  const expectedOther = path.join(dshHome, "skills", "dragon-quant");
  assert.ok(
    json(projectInstall.stdout).warnings.some((item) => item.includes(expectedOther)),
    json(projectInstall.stdout).warnings.join("|"),
  );
});

test("doctor --all-scopes resolves the other scope through env overrides", () => {
  const project = tempDir("dq-cli-doc-env-");
  const home = tempDir("dq-cli-doc-home-");
  const piDir = tempDir("dq-cli-pi-agent-");
  const env = { PI_CODING_AGENT_DIR: piDir };

  assert.equal(run(["install", "--global", "--agent", "pi", "--json"], { cwd: project, home, env }).status, 0);
  assert.equal(run(["install", "--project", "--agent", "pi", "--json"], { cwd: project, home, env }).status, 0);

  const result = run(
    ["doctor", "--project", "--all-scopes", "--agent", "pi", "--json"],
    { cwd: project, home, env },
  );
  const installs = json(result.stdout).installs.map((item) => item.skillDir);
  assert.ok(installs.includes(path.join(piDir, "skills", "dragon-quant")), installs.join("|"));
  assert.ok(
    installs.includes(path.join(fs.realpathSync(project), ".pi", "skills", "dragon-quant")),
    installs.join("|"),
  );
});

test("doctor and uninstall support multi-select", () => {
  const project = tempDir("dq-cli-multi-doc-");
  assert.equal(run(["install", "--agent", "claude-code,hermes", "--json"], { cwd: project }).status, 0);

  const doctor = run(["doctor", "--agent", "claude-code,hermes", "--json"], { cwd: project });
  const installs = json(doctor.stdout).installs;
  assert.deepEqual(
    installs.filter((item) => item.scope === "project").map((item) => item.agent).sort(),
    ["claude-code", "hermes"],
  );

  const uninstall = run(["uninstall", "--agent", "claude-code,hermes", "--json"], { cwd: project });
  assert.equal(uninstall.status, 0, uninstall.stderr);
  assert.deepEqual(json(uninstall.stdout).agents.map((item) => item.agent), ["claude-code", "hermes"]);
  assert.equal(fs.existsSync(path.join(project, ".claude", "skills", "dragon-quant")), false);
  assert.equal(fs.existsSync(path.join(project, ".hermes", "skills", "dragon-quant")), false);
});

test("a partial multi-agent failure keeps already-written targets", () => {
  const project = tempDir("dq-cli-partial-");
  const foreign = path.join(project, ".hermes", "skills", "dragon-quant");
  fs.mkdirSync(foreign, { recursive: true });
  fs.writeFileSync(path.join(foreign, "SKILL.md"), "foreign");

  const result = run(["install", "--agent", "claude-code,hermes", "--json"], { cwd: project });
  assert.equal(result.status, 1);
  assert.equal(json(result.stdout).error.code, "conflict");
  assert.equal(fs.existsSync(skillFile(project, ".claude", "skills")), true);
  assert.equal(fs.readFileSync(path.join(foreign, "SKILL.md"), "utf8"), "foreign");
});

test("self-hosting guard blocks openclaw install --force at the package root", () => {
  const sourceSkill = skillFile(FIXTURE_PACKAGE, "skills");
  const before = fs.readFileSync(sourceSkill, "utf8");
  const result = run(["install", "--agent", "openclaw", "--force", "--json"], { cwd: FIXTURE_PACKAGE });
  assert.equal(result.status, 2, result.stdout);
  assert.equal(json(result.stdout).error.code, "usage");
  assert.equal(fs.readFileSync(sourceSkill, "utf8"), before);
  assert.equal(
    fs.existsSync(path.join(FIXTURE_PACKAGE, "skills", "dragon-quant", ".dragon-quant-skill.json")),
    false,
  );
});

test("self-hosting guard blocks openclaw uninstall --force at the package root", () => {
  const sourceSkill = skillFile(FIXTURE_PACKAGE, "skills");
  const result = run(["uninstall", "--agent", "openclaw", "--force", "--json"], { cwd: FIXTURE_PACKAGE });
  assert.equal(result.status, 2, result.stdout);
  assert.equal(json(result.stdout).error.code, "usage");
  assert.equal(fs.existsSync(sourceSkill), true);
});

test("self-hosting guard rejects --agent all before writing any target", () => {
  const result = run(["install", "--agent", "all", "--force", "--json"], { cwd: FIXTURE_PACKAGE });
  assert.equal(result.status, 2, result.stdout);
  assert.equal(json(result.stdout).error.code, "usage");
  assert.equal(fs.existsSync(path.join(FIXTURE_PACKAGE, ".claude")), false);
  assert.equal(fs.existsSync(path.join(FIXTURE_PACKAGE, ".hermes")), false);
});

test("self-hosting guard does not block explicit --target-dir", () => {
  const target = tempDir("dq-cli-self-target-");
  const result = run(
    ["install", "--agent", "openclaw", "--target-dir", target, "--json"],
    { cwd: FIXTURE_PACKAGE },
  );
  assert.equal(result.status, 0, result.stderr);
  assert.equal(fs.existsSync(skillFile(target, "skills")), true);
});

test("doctor is not blocked by the self-hosting guard", () => {
  const result = run(["doctor", "--agent", "openclaw", "--json"], { cwd: FIXTURE_PACKAGE });
  // Read-only check: it may report a missing install but must not exit as a usage error.
  assert.notEqual(result.status, 2);
  assert.equal(json(result.stdout).error?.code, undefined);
});

// --- Interactive agent selection (TTY only) ---

const { main } = await import(pathToFileURL(BIN).href);

const KEY_BYTES = {
  up: "\u001b[A",
  down: "\u001b[B",
  space: " ",
  a: "a",
  enter: "\r",
  q: "q",
};

function ttyInput() {
  const input = new PassThrough();
  input.isTTY = true;
  input.isRaw = false;
  input.rawCalls = [];
  input.setRawMode = (value) => {
    input.rawCalls.push(value);
    input.isRaw = value;
  };
  return input;
}

function capturedStream({ tty = true } = {}) {
  const stream = new PassThrough();
  stream.isTTY = tty;
  let text = "";
  stream.on("data", (chunk) => {
    text += chunk.toString();
  });
  stream.text = () => text;
  return stream;
}

function isolatedIO(overrides = {}) {
  const home = tempDir("dq-cli-int-home-");
  const env = { HOME: home, DQ_PYTHON: "" };
  for (const key of OFFICIAL_ENV) delete env[key];
  return {
    stdin: ttyInput(),
    stdout: capturedStream(),
    stderr: capturedStream(),
    home,
    env,
    ...overrides,
  };
}

function installArgs(target) {
  return ["install", "--target-dir", target, "--python", "/nonexistent/python"];
}

function pressKeys(io, keys) {
  for (const key of keys) io.stdin.write(KEY_BYTES[key]);
}

test("bare install without a TTY never renders the menu", () => {
  const project = tempDir("dq-cli-nontty-");
  const result = run(["install"], { cwd: project });
  assert.equal(result.status, 0, result.stderr);
  assert.doesNotMatch(result.stdout, /选择要安装的 Agent/);
  assert.doesNotMatch(result.stderr, /选择要安装的 Agent/);
  assert.equal(fs.existsSync(skillFile(project, ".claude", "skills")), true);
});

test("interactive Enter installs the default claude-code only", { timeout: 15000 }, async () => {
  const target = tempDir("dq-cli-int-enter-");
  const io = isolatedIO();
  const promise = main(installArgs(target), io);
  pressKeys(io, ["enter"]);
  const code = await promise;

  assert.equal(code, 0);
  assert.equal(fs.existsSync(skillFile(target, ".claude", "skills")), true);
  assert.equal(fs.existsSync(path.join(target, ".agents")), false);
  assert.match(io.stderr.text(), /选择要安装的 Agent/);
  assert.match(io.stderr.text(), /已选择：claude-code/);
  assert.doesNotMatch(io.stdout.text(), /选择要安装的 Agent/);
  assert.equal(io.stdin.listenerCount("keypress"), 0);
  assert.deepEqual(io.stdin.rawCalls, [true, false]);
});

test("interactive multi-select installs two real targets", { timeout: 15000 }, async () => {
  const target = tempDir("dq-cli-int-multi-");
  const io = isolatedIO();
  const promise = main(installArgs(target), io);
  pressKeys(io, ["down", "down", "down", "space", "enter"]);
  const code = await promise;

  assert.equal(code, 0);
  assert.equal(fs.existsSync(skillFile(target, ".claude", "skills")), true);
  assert.equal(fs.existsSync(skillFile(target, ".hermes", "skills")), true);
  assert.equal(fs.existsSync(path.join(target, ".agents")), false);
  assert.match(io.stderr.text(), /已选择：claude-code, hermes/);
});

test("interactive cancel exits 0 and writes nothing", { timeout: 15000 }, async () => {
  const target = tempDir("dq-cli-int-cancel-");
  const io = isolatedIO();
  const promise = main(installArgs(target), io);
  pressKeys(io, ["q"]);
  const code = await promise;

  assert.equal(code, 0);
  assert.equal(io.stdout.text(), "");
  assert.match(io.stderr.text(), /已取消，未写入任何文件。/);
  for (const dir of [".claude", ".agents", "skills", ".hermes", ".dsh", ".pi"]) {
    assert.equal(fs.existsSync(path.join(target, dir)), false, dir);
  }
});

test("explicit --agent skips the interactive menu", { timeout: 15000 }, async () => {
  const target = tempDir("dq-cli-int-agent-");
  const io = isolatedIO();
  const code = await main(["install", "--agent", "hermes", "--target-dir", target, "--python", "/nonexistent/python"], io);

  assert.equal(code, 0);
  assert.doesNotMatch(io.stderr.text(), /选择要安装的 Agent/);
  assert.equal(fs.existsSync(skillFile(target, ".hermes", "skills")), true);
});

test("--json skips the menu and keeps stdout as pure JSON", { timeout: 15000 }, async () => {
  const target = tempDir("dq-cli-int-json-");
  const io = isolatedIO();
  const code = await main(["install", "--json", "--target-dir", target, "--python", "/nonexistent/python"], io);

  assert.equal(code, 0);
  assert.doesNotMatch(io.stderr.text(), /选择要安装的 Agent/);
  assert.doesNotMatch(io.stdout.text(), /选择要安装的 Agent/);
  const payload = JSON.parse(io.stdout.text());
  assert.equal(payload.command, "install");
  assert.deepEqual(payload.agents.map((item) => item.agent), ["claude-code"]);
});

test("a redirected stdin, stdout or stderr skips the menu", { timeout: 15000 }, async () => {
  for (const stream of ["stdin", "stdout", "stderr"]) {
    const target = tempDir(`dq-cli-int-${stream}-`);
    const io = isolatedIO({ [stream]: capturedStream({ tty: false }) });
    if (stream === "stdin") io.stdin.isTTY = false;
    const code = await main(installArgs(target), io);

    assert.equal(code, 0, stream);
    assert.doesNotMatch(io.stderr.text(), /选择要安装的 Agent/, stream);
    assert.equal(fs.existsSync(skillFile(target, ".claude", "skills")), true, stream);
  }
});

test("interactive --dry-run prompts and still writes nothing", { timeout: 15000 }, async () => {
  const target = tempDir("dq-cli-int-dry-");
  const io = isolatedIO();
  const promise = main(["install", "--dry-run", "--target-dir", target, "--python", "/nonexistent/python"], io);
  pressKeys(io, ["enter"]);
  const code = await promise;

  assert.equal(code, 0);
  assert.match(io.stderr.text(), /选择要安装的 Agent/);
  for (const dir of [".claude", ".agents", "skills", ".hermes", ".dsh", ".pi"]) {
    assert.equal(fs.existsSync(path.join(target, dir)), false, dir);
  }
});

test("explicit empty --agent skips the menu and fails as usage", { timeout: 15000 }, async () => {
  const target = tempDir("dq-cli-int-empty-");
  const io = isolatedIO();
  const code = await main(["install", "--agent", "", "--target-dir", target, "--python", "/nonexistent/python"], io);

  assert.equal(code, 2);
  assert.doesNotMatch(io.stderr.text(), /选择要安装的 Agent/);
  assert.match(io.stderr.text(), /unknown agent/);
  assert.equal(fs.existsSync(path.join(target, ".claude")), false);
});

test("doctor and uninstall never prompt", { timeout: 15000 }, async () => {
  const target = tempDir("dq-cli-int-doctor-");
  assert.equal(run(["install", "--target-dir", target, "--json"], { cwd: tempDir() }).status, 0);

  const doctorIO = isolatedIO();
  const doctorCode = await main(
    ["doctor", "--target-dir", target, "--python", "/nonexistent/python"],
    doctorIO,
  );
  assert.ok(doctorCode === 0 || doctorCode === 1, String(doctorCode));
  assert.doesNotMatch(doctorIO.stderr.text(), /选择要安装的 Agent/);

  const uninstallIO = isolatedIO();
  const uninstallCode = await main(
    ["uninstall", "--target-dir", target],
    uninstallIO,
  );
  assert.equal(uninstallCode, 0);
  assert.doesNotMatch(uninstallIO.stderr.text(), /选择要安装的 Agent/);
  assert.equal(fs.existsSync(path.join(target, ".claude", "skills", "dragon-quant")), false);
});
