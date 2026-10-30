import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import {
  AGENT_TARGETS, HARNESS_AGENTS,
  expandAgents, findProjectRoot, resolveSkillsRoot,
  getAgentHints, getSameNameNote,
  assertSafeRelPath, isInside,
} from "../lib/paths.mjs";

const SIX = ["claude-code", "codex", "openclaw", "hermes", "deepseek", "pi"];

test("HARNESS_AGENTS is exactly the six real harnesses in order", () => {
  assert.deepEqual(HARNESS_AGENTS, SIX);
  assert.equal(Object.hasOwn(AGENT_TARGETS, "agents"), true);
});

test("six harness project roots match the official matrix", () => {
  const projectRoot = "/repo";
  const cwd = "/workspace";
  const expected = {
    "claude-code": path.join("/repo", ".claude", "skills"),
    codex: path.join("/repo", ".agents", "skills"),
    openclaw: path.join("/workspace", "skills"),
    hermes: path.join("/repo", ".hermes", "skills"),
    deepseek: path.join("/repo", ".dsh", "skills"),
    pi: path.join("/workspace", ".pi", "skills"),
  };
  for (const agent of SIX) {
    assert.equal(
      resolveSkillsRoot({ agent, scope: "project", projectRoot, cwd, env: {} }),
      expected[agent],
      agent,
    );
  }
});

test("agents alias keeps the generic project directory", () => {
  assert.equal(
    resolveSkillsRoot({ agent: "agents", scope: "project", projectRoot: "/repo" }),
    path.join("/repo", ".agents", "skills"),
  );
});

test("six harness global roots match the official matrix", () => {
  const home = "/home/u";
  const env = {};
  const expected = {
    "claude-code": path.join(home, ".claude", "skills"),
    codex: path.join(home, ".agents", "skills"),
    openclaw: path.join(home, ".openclaw", "skills"),
    hermes: path.join(home, ".hermes", "skills"),
    deepseek: path.join(home, ".dsh", "skills"),
    pi: path.join(home, ".pi", "agent", "skills"),
  };
  for (const agent of SIX) {
    assert.equal(
      resolveSkillsRoot({ agent, scope: "global", home, cwd: "/work", env }),
      expected[agent],
      agent,
    );
  }
  assert.equal(
    resolveSkillsRoot({ agent: "agents", scope: "global", home, env }),
    path.join(home, ".agents", "skills"),
  );
});

test("global env overrides are honored for openclaw, deepseek, pi", () => {
  const home = "/home/u";
  const cwd = "/work";
  assert.equal(
    resolveSkillsRoot({
      agent: "openclaw", scope: "global", home, cwd, env: { OPENCLAW_STATE_DIR: "/state" },
    }),
    path.join("/state", "skills"),
  );
  assert.equal(
    resolveSkillsRoot({
      agent: "deepseek", scope: "global", home, cwd, env: { DSH_HOME: "/dsh" },
    }),
    path.join("/dsh", "skills"),
  );
  assert.equal(
    resolveSkillsRoot({
      agent: "pi", scope: "global", home, cwd, env: { PI_CODING_AGENT_DIR: "/agent" },
    }),
    path.join("/agent", "skills"),
  );
});

test("empty env strings fall back to the official default", () => {
  const home = "/home/u";
  const env = { OPENCLAW_STATE_DIR: "", DSH_HOME: "", PI_CODING_AGENT_DIR: "   " };
  assert.equal(
    resolveSkillsRoot({ agent: "openclaw", scope: "global", home, cwd: "/w", env }),
    path.join(home, ".openclaw", "skills"),
  );
  assert.equal(
    resolveSkillsRoot({ agent: "deepseek", scope: "global", home, cwd: "/w", env }),
    path.join(home, ".dsh", "skills"),
  );
  assert.equal(
    resolveSkillsRoot({ agent: "pi", scope: "global", home, cwd: "/w", env }),
    path.join(home, ".pi", "agent", "skills"),
  );
});

test("relative env paths resolve against cwd", () => {
  const home = "/home/u";
  assert.equal(
    resolveSkillsRoot({
      agent: "openclaw", scope: "global", home, cwd: "/work", env: { OPENCLAW_STATE_DIR: "state" },
    }),
    path.join("/work", "state", "skills"),
  );
});

test("env paths expand a leading ~ to home and keep ~/ subpaths", () => {
  const home = "/home/u";
  assert.equal(
    resolveSkillsRoot({
      agent: "deepseek", scope: "global", home, cwd: "/w", env: { DSH_HOME: "~" },
    }),
    path.join(home, "skills"),
  );
  assert.equal(
    resolveSkillsRoot({
      agent: "pi", scope: "global", home, cwd: "/w", env: { PI_CODING_AGENT_DIR: "~/custom" },
    }),
    path.join(home, "custom", "skills"),
  );
});

test("env overrides do not affect project scope", () => {
  assert.equal(
    resolveSkillsRoot({
      agent: "pi",
      scope: "project",
      projectRoot: "/repo",
      cwd: "/work",
      env: { PI_CODING_AGENT_DIR: "/agent" },
    }),
    path.join("/work", ".pi", "skills"),
  );
  assert.equal(
    resolveSkillsRoot({
      agent: "openclaw",
      scope: "project",
      projectRoot: "/repo",
      cwd: "/work",
      env: { OPENCLAW_STATE_DIR: "/state" },
    }),
    path.join("/work", "skills"),
  );
});

test("targetDir overrides the base for every agent, including pi and openclaw", () => {
  for (const agent of [...SIX, "agents"]) {
    const expectedRel = {
      "claude-code": [".claude", "skills"],
      codex: [".agents", "skills"],
      agents: [".agents", "skills"],
      openclaw: ["skills"],
      hermes: [".hermes", "skills"],
      deepseek: [".dsh", "skills"],
      pi: [".pi", "skills"],
    }[agent];
    assert.equal(
      resolveSkillsRoot({ agent, scope: "project", targetDir: "/tmp/p", projectRoot: "/repo", cwd: "/work" }),
      path.join("/tmp/p", ...expectedRel),
      agent,
    );
  }
});

test("global plus targetDir is rejected", () => {
  assert.throws(
    () => resolveSkillsRoot({ agent: "claude-code", scope: "global", home: "/h", targetDir: "/tmp/p" }),
    /target-dir/,
  );
});

test("unknown agent and scope throw", () => {
  assert.throws(() => resolveSkillsRoot({ agent: "cursor", scope: "project" }), /unknown agent/);
  assert.throws(() => resolveSkillsRoot({ agent: "pi", scope: "workspace" }), /unknown scope/);
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

test("all expands in place to exactly six harnesses and excludes agents", () => {
  assert.deepEqual(expandAgents("all"), SIX);
  assert.deepEqual(expandAgents("all,agents"), [...SIX, "agents"]);
  assert.deepEqual(expandAgents("all,claude-code"), SIX);
  assert.deepEqual(expandAgents("agents,all"), ["agents", ...SIX]);
});

test("explicit multi-select dedupes by name and keeps input order", () => {
  assert.deepEqual(expandAgents("claude-code,hermes,claude-code"), ["claude-code", "hermes"]);
  assert.deepEqual(expandAgents("agents"), ["agents"]);
  assert.deepEqual(expandAgents("codex,agents"), ["codex", "agents"]);
  assert.deepEqual(expandAgents(" pi , deepseek "), ["pi", "deepseek"]);
});

test("expandAgents rejects unknown names and empty lists", () => {
  assert.throws(() => expandAgents("cursor"), /unknown agent/i);
  assert.throws(() => expandAgents(" "), /unknown agent/i);
});

test("getAgentHints substitutes the project root and returns copies", () => {
  const hints = getAgentHints("hermes", { scope: "project", projectRoot: "/repo" });
  assert.ok(hints.some((item) => item.includes("hermes skills trust /repo")), hints.join("|"));
  hints.push("mutated");
  assert.equal(
    getAgentHints("hermes", { scope: "project", projectRoot: "/repo" }).length,
    hints.length - 1,
  );
});

test("getAgentHints keeps the placeholder when no project root is given", () => {
  const hints = getAgentHints("hermes", { scope: "project" });
  assert.ok(hints.some((item) => item.includes("<projectRoot>")), hints.join("|"));
});

test("getAgentHints differs by target and scope", () => {
  const piProject = getAgentHints("pi", { scope: "project", projectRoot: "/repo" });
  assert.ok(piProject.some((item) => item.includes("/reload")), piProject.join("|"));
  const codexProject = getAgentHints("codex", { scope: "project", projectRoot: "/repo" });
  assert.equal(codexProject.some((item) => item.includes("Enterprise")), false);
  const deepseek = getAgentHints("deepseek", { scope: "project", projectRoot: "/repo" });
  assert.ok(deepseek.some((item) => /自动发现/.test(item)), deepseek.join("|"));
});

test("getSameNameNote distinguishes codex from claude-code", () => {
  assert.match(getSameNameNote("claude-code"), /Enterprise/);
  assert.doesNotMatch(getSameNameNote("codex"), /Enterprise/);
  assert.match(getSameNameNote("codex"), /同名/);
  assert.equal(getSameNameNote("hermes"), null);
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
