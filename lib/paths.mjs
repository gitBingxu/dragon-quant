import fs from "node:fs";
import os from "node:os";
import path from "node:path";

export const AGENT_TARGETS = {
  "claude-code": {
    projectRel: path.join(".claude", "skills"),
    globalRel: path.join(".claude", "skills"),
  },
  agents: {
    projectRel: path.join(".agents", "skills"),
    globalRel: path.join(".agents", "skills"),
  },
};

const RESERVED_WIN_NAMES = /^(con|prn|aux|nul|com[1-9]|lpt[1-9])(\..*)?$/i;

export function expandAgents(value) {
  const raw = String(value ?? "claude-code");
  const parts = raw.split(",").map((part) => part.trim()).filter(Boolean);
  const names = parts.includes("all") ? Object.keys(AGENT_TARGETS) : parts;
  const out = [];
  for (const name of names) {
    if (!Object.hasOwn(AGENT_TARGETS, name)) {
      throw new Error(`unknown agent: ${name}`);
    }
    if (!out.includes(name)) out.push(name);
  }
  if (out.length === 0) throw new Error("unknown agent: (empty)");
  return out;
}

export function findProjectRoot(cwd) {
  let current = path.resolve(cwd ?? process.cwd());
  for (;;) {
    const marker = path.join(current, ".git");
    if (fs.existsSync(marker)) return current;
    const parent = path.dirname(current);
    if (parent === current) break;
    current = parent;
  }
  return path.resolve(cwd ?? process.cwd());
}

export function resolveSkillsRoot({ agent, scope, projectRoot, home, targetDir } = {}) {
  if (!Object.hasOwn(AGENT_TARGETS, agent)) {
    throw new Error(`unknown agent: ${agent}`);
  }
  if (scope !== "project" && scope !== "global") {
    throw new Error(`unknown scope: ${scope}`);
  }
  const target = AGENT_TARGETS[agent];
  if (scope === "global") {
    if (targetDir) throw new Error("--target-dir cannot be combined with --global");
    return path.join(home ?? os.homedir(), target.globalRel);
  }
  const root = targetDir ?? projectRoot ?? process.cwd();
  return path.join(root, target.projectRel);
}

export function assertSafeRelPath(rel) {
  if (typeof rel !== "string" || rel.length === 0) {
    throw new Error(`invalid path: ${String(rel)}`);
  }
  if (rel.includes("\0")) throw new Error(`unsafe path (NUL): ${rel}`);
  if (/^[A-Za-z]:/.test(rel)) throw new Error(`unsafe path (drive letter): ${rel}`);
  if (rel.includes("\\")) throw new Error(`unsafe path (backslash): ${rel}`);
  if (rel.startsWith("/")) throw new Error(`unsafe path (absolute): ${rel}`);
  const segments = rel.split("/");
  for (const segment of segments) {
    if (segment === "" || segment === "." || segment === "..") {
      throw new Error(`unsafe path (segment): ${rel}`);
    }
    if (RESERVED_WIN_NAMES.test(segment)) {
      throw new Error(`unsafe path (reserved name): ${rel}`);
    }
  }
  return rel;
}

export function isInside(root, candidate) {
  const rel = path.relative(path.resolve(root), path.resolve(candidate));
  return rel !== "" && !rel.startsWith("..") && !path.isAbsolute(rel);
}
