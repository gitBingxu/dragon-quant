import test from "node:test";
import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const ROOT = path.resolve(import.meta.dirname, "..");
const SCRIPT = path.join(ROOT, "scripts", "publish-skill.sh");

function fakeTools() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "dq-publish-skill-"));
  const bin = path.join(dir, "bin");
  fs.mkdirSync(bin);
  fs.mkdirSync(path.join(dir, "tmp"));
  const argsLog = path.join(dir, "args.log");
  const configLog = path.join(dir, "config.log");
  const pythonLog = path.join(dir, "python.log");
  const offlineLog = path.join(dir, "offline.log");
  fs.writeFileSync(
    path.join(bin, "npm"),
    `#!/bin/sh
set -eu
cmd="$1"; shift
if [ -n "\${FAKE_NPM_OFFLINE_LOG:-}" ]; then
  printf '%s %s\n' "$cmd" "\${npm_config_offline:-}" >> "$FAKE_NPM_OFFLINE_LOG"
fi
case "$cmd" in
  test) exit 0 ;;
  view) echo "npm error code E404" >&2; exit 1 ;;
  pack)
    repo="$1"; shift
    version=$(sed -n 's/.*"version": *"\\([^"]*\\)".*/\\1/p' "$repo/package.json" | head -1)
    tarball="dragon-quant-skills-$version.tgz"
    for arg in "$@"; do
      if [ "$arg" = "--dry-run" ]; then
        printf '[{"path":"bin/dragon-quant-skills.mjs"},{"path":"lib/cli.mjs"},{"path":"skills/dragon-quant/SKILL.md"},{"path":"README.md"},{"path":"LICENSE"}]\n'
        exit 0
      fi
    done
    dest=""
    while [ $# -gt 0 ]; do
      case "$1" in
        --pack-destination) dest="$2"; shift 2 ;;
        *) shift ;;
      esac
    done
    : > "$dest/$tarball"
    printf '%s\n' "$tarball"
    exit 0 ;;
  install)
    dest=""
    while [ $# -gt 0 ]; do
      case "$1" in
        --prefix) dest="$2"; shift 2 ;;
        *) shift ;;
      esac
    done
    mkdir -p "$dest/node_modules/dragon-quant-skills/bin"
    : > "$dest/node_modules/dragon-quant-skills/bin/dragon-quant-skills.mjs"
    exit 0 ;;
  publish)
    config=""
    for arg in "$@"; do
      case "$arg" in --userconfig=*) config="\${arg#--userconfig=}" ;; esac
    done
    test -n "$config"
    test -f "$config"
    stat -f '%Lp' "$config" > "${configLog}.mode"
    cp "$config" "${configLog}.copy"
    printf 'publish\n%s\n' "$@" >> "${argsLog}"
    exit 0 ;;
esac
exit 0
`,
    { mode: 0o755 },
  );
  fs.writeFileSync(
    path.join(bin, "python-fake"),
    `#!/bin/sh
set -eu
printf '%s\n' "$*" >> "${pythonLog}"
if [ "\${FAKE_PY_CONTRACT_FAIL:-}" = "1" ] && [ "$1" = "-" ]; then
  cat >/dev/null
  exit 3
fi
if [ "$1" = "-" ]; then cat >/dev/null; exit 0; fi
if [ "$1" = "-c" ]; then echo "1.0.0"; exit 0; fi
exit 0
`,
    { mode: 0o755 },
  );
  return { dir, bin, argsLog, configLog, pythonLog, offlineLog };
}

function makeRepo(version = "1.0.0") {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "dq-skill-repo-"));
  fs.mkdirSync(path.join(dir, "skills", "dragon-quant"), { recursive: true });
  fs.mkdirSync(path.join(dir, "dragon_quant", "tests"), { recursive: true });
  fs.writeFileSync(
    path.join(dir, "package.json"),
    JSON.stringify({ name: "dragon-quant-skills", version }, null, 2),
  );
  fs.writeFileSync(
    path.join(dir, "pyproject.toml"),
    `[project]\nname = "dragon-quant"\nversion = "${version}"\n`,
  );
  fs.writeFileSync(
    path.join(dir, "skills", "dragon-quant", "SKILL.md"),
    `---\nname: dragon-quant\ndescription: x\nlicense: MIT\nmetadata:\n  skill-version: "${version}"\n  min-cli-version: "${version}"\n---\n`,
  );
  for (const name of ["test_skill_package.py", "test_skill_content.py", "test_skill_routes.py"]) {
    fs.writeFileSync(path.join(dir, "dragon_quant", "tests", name), "");
  }
  const origin = `${dir}-origin.git`;
  const git = (...args) => spawnSync("git", ["-C", dir, ...args], { encoding: "utf8" });
  git("init", "-q", "-b", "release/test");
  git("config", "user.email", "test@example.com");
  git("config", "user.name", "Test");
  git("add", "-A");
  git("commit", "-q", "-m", "base");
  spawnSync("git", ["init", "-q", "--bare", origin]);
  git("remote", "add", "origin", origin);
  git("push", "-q", "-u", "origin", "release/test");
  return dir;
}

function baseEnv(tools, repo, extra = {}) {
  return {
    ...process.env,
    PATH: `${tools.bin}:${process.env.PATH}`,
    DQ_SKILL_REPO_ROOT: repo,
    DQ_PYTHON: path.join(tools.bin, "python-fake"),
    FAKE_NPM_OFFLINE_LOG: tools.offlineLog,
    HOME: tools.dir,
    TMPDIR: path.join(tools.dir, "tmp"),
    ...extra,
  };
}

test("dry-run works outside the repository and needs no token", () => {
  const tools = fakeTools();
  const repo = makeRepo();
  const result = spawnSync("sh", [SCRIPT, "--dry-run"], {
    cwd: tools.dir,
    env: baseEnv(tools, repo),
    encoding: "utf8",
  });
  assert.equal(result.status, 0, result.stderr);
  const args = fs.readFileSync(tools.argsLog, "utf8").trim().split("\n");
  assert.equal(args[0], "publish");
  assert.match(args[1], /dragon-quant-skills-1\.0\.0\.tgz$/);
  assert.ok(args.includes("--dry-run"));
  const configArg = args.find((arg) => arg.startsWith("--userconfig=")).slice("--userconfig=".length);
  assert.ok(configArg.endsWith("/npmrc"));
  assert.equal(fs.existsSync(configArg), false, "temporary npmrc must be removed");
});

test("dry-run never requires or echoes a token", () => {
  const tools = fakeTools();
  const repo = makeRepo();
  const result = spawnSync("sh", [SCRIPT, "--dry-run"], {
    cwd: tools.dir,
    env: baseEnv(tools, repo, { NPM_TOKEN: "test-token-not-a-secret" }),
    encoding: "utf8",
  });
  assert.equal(result.status, 0, result.stderr);
  assert.doesNotMatch(`${result.stdout}${result.stderr}`, /test-token-not-a-secret/);
});

test("publish uses the same tarball and a 0600 temporary auth config", () => {
  const tools = fakeTools();
  const repo = makeRepo();
  const result = spawnSync("sh", [SCRIPT, "--tag", "next", "--access", "public"], {
    cwd: tools.dir,
    env: baseEnv(tools, repo, { NPM_TOKEN: "publish-secret-token" }),
    encoding: "utf8",
  });
  assert.equal(result.status, 0, result.stderr);
  const args = fs.readFileSync(tools.argsLog, "utf8").trim().split("\n");
  assert.equal(args[0], "publish");
  assert.match(args[1], /dragon-quant-skills-1\.0\.0\.tgz$/);
  assert.ok(args.includes("--tag=next"));
  assert.ok(args.includes("--access=public"));
  const configArg = args.find((arg) => arg.startsWith("--userconfig=")).slice("--userconfig=".length);
  assert.equal(fs.readFileSync(`${tools.configLog}.mode`, "utf8").trim(), "600");
  const config = fs.readFileSync(`${tools.configLog}.copy`, "utf8");
  assert.match(config, /_authToken=publish-secret-token/);
  assert.equal(fs.existsSync(configArg), false, "temporary npmrc must be removed");
  assert.doesNotMatch(`${result.stdout}${result.stderr}`, /publish-secret-token/);
});

test("non-TTY publish without a token stops before npm publish", () => {
  const tools = fakeTools();
  const repo = makeRepo();
  const result = spawnSync("sh", [SCRIPT], {
    cwd: tools.dir,
    env: baseEnv(tools, repo, { NPM_TOKEN: "" }),
    input: "",
    encoding: "utf8",
  });
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /NPM_TOKEN/);
  assert.equal(fs.existsSync(tools.argsLog), false, "npm publish must not run without a token");
});

test("version contract failure stops before running tests or publishing", () => {
  const tools = fakeTools();
  const repo = makeRepo();
  const result = spawnSync("sh", [SCRIPT, "--dry-run"], {
    cwd: tools.dir,
    env: baseEnv(tools, repo, { FAKE_PY_CONTRACT_FAIL: "1" }),
    encoding: "utf8",
  });
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /版本契约/);
  assert.equal(fs.existsSync(tools.argsLog), false);
});

test("packages, registry overrides and extra paths are rejected", () => {
  for (const args of [
    ["--registry=https://evil.invalid"],
    ["/tmp/some-package"],
    ["--userconfig=/tmp/x"],
    ["--ignore-scripts"],
    ["--tag"],
  ]) {
    const tools = fakeTools();
    const repo = makeRepo();
    const result = spawnSync("sh", [SCRIPT, ...args], {
      cwd: tools.dir,
      env: baseEnv(tools, repo, { NPM_TOKEN: "x" }),
      encoding: "utf8",
    });
    assert.notEqual(result.status, 0, args.join(" "));
    assert.equal(fs.existsSync(tools.argsLog), false, "npm must not run for rejected args");
  }
});

test("tag/access/OTP values are strictly validated without invoking npm", () => {
  for (const args of [
    ["--tag", "latest --registry=https://evil.invalid"],
    ["--tag", "latest*"],
    ["--tag", "bad\nvalue"],
    ["--access", "internal"],
    ["--otp", "123456"],
  ]) {
    const tools = fakeTools();
    const repo = makeRepo();
    const result = spawnSync("sh", [SCRIPT, ...args], {
      cwd: tools.dir,
      env: baseEnv(tools, repo, { NPM_TOKEN: "x" }),
      encoding: "utf8",
    });
    assert.notEqual(result.status, 0, args.join(" "));
    assert.equal(fs.existsSync(tools.argsLog), false, "npm must not run for invalid args");
  }
});

test("dry-run gates and publish use the same tag/access argv with offline npm", () => {
  const tools = fakeTools();
  const repo = makeRepo();
  const result = spawnSync("sh", [SCRIPT, "--dry-run", "--tag", "next", "--access", "public"], {
    cwd: tools.dir,
    env: baseEnv(tools, repo),
    encoding: "utf8",
  });
  assert.equal(result.status, 0, result.stderr);
  const args = fs.readFileSync(tools.argsLog, "utf8").trim().split("\n");
  assert.ok(args.includes("--tag=next"));
  assert.ok(args.includes("--access=public"));
  const offline = fs.readFileSync(tools.offlineLog, "utf8").trim().split("\n");
  for (const line of offline) {
    assert.match(line, / true$/, `expected offline=true: ${line}`);
  }
});

test("publishing from main with an upstream is allowed", () => {
  const tools = fakeTools();
  const repo = makeRepo();
  const git = (...args) => spawnSync("git", ["-C", repo, ...args], { encoding: "utf8" });
  git("branch", "-m", "main");
  git("push", "-q", "-u", "origin", "main");
  const result = spawnSync("sh", [SCRIPT], {
    cwd: tools.dir,
    env: baseEnv(tools, repo, { NPM_TOKEN: "publish-secret-token" }),
    encoding: "utf8",
  });
  assert.equal(result.status, 0, result.stderr);
  assert.match(fs.readFileSync(tools.argsLog, "utf8"), /publish/);
});

test("SIGTERM cleans the temporary npmrc and restores no token echo", async () => {
  const tools = fakeTools();
  fs.mkdirSync(path.join(tools.dir, "tmp"), { recursive: true });
  const repo = makeRepo();
  const marker = path.join(tools.dir, "publishing");
  const npmPath = path.join(tools.bin, "npm");
  fs.renameSync(npmPath, path.join(tools.bin, "npm-real"));
  fs.writeFileSync(
    npmPath,
    `#!/bin/sh
set -eu
if [ "$1" = "publish" ]; then
  config=""
  for arg in "$@"; do
    case "$arg" in --userconfig=*) config="\${arg#--userconfig=}" ;; esac
  done
  cp "$config" "${tools.configLog}.copy"
  : > "${marker}"
  sleep 10
  exit 0
fi
exec "${path.join(tools.bin, "npm-real")}" "$@"
`,
    { mode: 0o755 },
  );
  const child = spawn("sh", [SCRIPT], {
    cwd: tools.dir,
    env: baseEnv(tools, repo, { NPM_TOKEN: "signal-secret" }),
    stdio: ["ignore", "pipe", "pipe"],
  });
  let stderr = "";
  child.stderr.on("data", (chunk) => { stderr += chunk.toString(); });
  const deadline = Date.now() + 20000;
  while (!fs.existsSync(marker)) {
    if (Date.now() > deadline) {
      child.kill("SIGKILL");
      throw new Error("publish never started: " + stderr);
    }
    await new Promise((resolve) => setTimeout(resolve, 20));
  }
  child.kill("SIGTERM");
  await new Promise((resolve) => child.on("exit", resolve));
  const configArg = fs.readFileSync(`${tools.configLog}.copy`, "utf8");
  assert.ok(configArg.length > 0);
  await new Promise((resolve) => setTimeout(resolve, 100));
  const leftovers = fs.readdirSync(path.join(tools.dir, "tmp"))
    .filter((name) => name.startsWith("dq-skill-publish."));
  assert.deepEqual(leftovers, [], "temp publish dir must be cleaned on SIGTERM");
});
