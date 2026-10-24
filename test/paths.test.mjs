import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import {
  expandAgents, findProjectRoot, resolveSkillsRoot,
  assertSafeRelPath, isInside,
} from "../lib/paths.mjs";

test("project claude-code resolves under project root", () => {
  assert.equal(
    resolveSkillsRoot({ agent: "claude-code", scope: "project", projectRoot: "/repo" }),
    path.join("/repo", ".claude", "skills"),
  );
});

test("project agents resolves under .agents/skills", () => {
  assert.equal(
    resolveSkillsRoot({ agent: "agents", scope: "project", projectRoot: "/repo" }),
    path.join("/repo", ".agents", "skills"),
  );
});

test("global claude-code resolves under home", () => {
  assert.equal(
    resolveSkillsRoot({ agent: "claude-code", scope: "global", home: "/home/u" }),
    path.join("/home/u", ".claude", "skills"),
  );
});

test("targetDir overrides project root, not the final skill dir", () => {
  assert.equal(
    resolveSkillsRoot({ agent: "claude-code", scope: "project", targetDir: "/tmp/p" }),
    path.join("/tmp/p", ".claude", "skills"),
  );
});

test("global plus targetDir is rejected", () => {
  assert.throws(
    () => resolveSkillsRoot({ agent: "claude-code", scope: "global", home: "/h", targetDir: "/tmp/p" }),
    /target-dir/,
  );
});

test("findProjectRoot walks up to the .git directory", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "dq-root-"));
  fs.mkdirSync(path.join(root, ".git"));
  const nested = path.join(root, "a", "b");
  fs.mkdirSync(nested, { recursive: true });
  assert.equal(findProjectRoot(nested), root);
});

test("findProjectRoot falls back to cwd without git", () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "dq-nogit-"));
  assert.equal(findProjectRoot(dir), dir);
});

test("expandAgents expands all and dedupes", () => {
  assert.deepEqual(expandAgents("all"), ["claude-code", "agents"]);
  assert.deepEqual(expandAgents("claude-code,agents,claude-code"), ["claude-code", "agents"]);
  assert.throws(() => expandAgents("cursor"), /unknown agent/i);
});

test("safe relative paths reject traversal and absolute paths", () => {
  assert.equal(assertSafeRelPath("references/commands.md"), "references/commands.md");
  for (const bad of ["../x", "/x", "a/../../b", "C:\\x", "a\0b", "references//x"]) {
    assert.throws(() => assertSafeRelPath(bad), /unsafe|invalid/i, bad);
  }
});

test("isInside does not accept sibling prefixes", () => {
  assert.equal(isInside("/repo/skills", "/repo/skills/a"), true);
  assert.equal(isInside("/repo/skills", "/repo/skills-evil/a"), false);
});
