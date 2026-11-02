import test from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const ROOT = path.resolve(import.meta.dirname, "..");
const SCRIPT = path.join(ROOT, "scripts", "publish-skill.sh");

function fakeNpm() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "dq-publish-skill-"));
  const bin = path.join(dir, "bin");
  fs.mkdirSync(bin);
  const argsLog = path.join(dir, "args.log");
  const configLog = path.join(dir, "config.log");
  fs.writeFileSync(path.join(bin, "npm"), `#!/bin/sh
set -eu
test -f "$NPM_CONFIG_USERCONFIG"
printf '%s\\n' "$NPM_CONFIG_USERCONFIG" > "$FAKE_NPM_CONFIG_LOG"
printf '%s\\n' "$@" > "$FAKE_NPM_ARGS_LOG"
`, { mode: 0o755 });
  return { dir, bin, argsLog, configLog };
}

test("publish script works outside the repository and removes temporary auth config", () => {
  const fake = fakeNpm();
  const result = spawnSync("sh", [SCRIPT, "--dry-run"], {
    cwd: fake.dir,
    env: {
      ...process.env,
      PATH: `${fake.bin}:${process.env.PATH}`,
      NPM_TOKEN: "test-token-not-a-secret",
      FAKE_NPM_ARGS_LOG: fake.argsLog,
      FAKE_NPM_CONFIG_LOG: fake.configLog,
    },
    encoding: "utf8",
  });
  assert.equal(result.status, 0, result.stderr);
  const args = fs.readFileSync(fake.argsLog, "utf8").trim().split("\n");
  assert.deepEqual(args, [
    "publish",
    ROOT,
    "--registry=https://registry.npmjs.org",
    "--dry-run",
  ]);
  const configPath = fs.readFileSync(fake.configLog, "utf8").trim();
  assert.equal(fs.existsSync(configPath), false, "temporary npmrc must be removed");
  assert.doesNotMatch(`${result.stdout}${result.stderr}`, /test-token-not-a-secret/);
});

test("publish script rejects an empty token without invoking npm", () => {
  const fake = fakeNpm();
  const result = spawnSync("sh", [SCRIPT], {
    cwd: fake.dir,
    env: {
      ...process.env,
      PATH: `${fake.bin}:${process.env.PATH}`,
      NPM_TOKEN: "",
      FAKE_NPM_ARGS_LOG: fake.argsLog,
      FAKE_NPM_CONFIG_LOG: fake.configLog,
    },
    input: "\n",
    encoding: "utf8",
  });
  assert.notEqual(result.status, 0);
  assert.equal(fs.existsSync(fake.argsLog), false, "npm must not run without a token");
});
