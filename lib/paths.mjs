import fs from "node:fs";
import os from "node:os";
import path from "node:path";

// Ordered single source of truth for `--agent all`. `agents` is a compatibility
// alias for the generic `.agents/skills` directory and is intentionally NOT a
// seventh harness here (it shares its root with `codex`).
export const HARNESS_AGENTS = [
  "claude-code",
  "codex",
  "openclaw",
  "hermes",
  "deepseek",
  "pi",
];

const RELOAD_CLAUDE = "运行 /reload-skills 或重启 Claude Code 会话以加载新 skill。";
const RELOAD_SESSION = "重启会话或按客户端方式重新加载以发现新 skill。";
const DEEPSEEK_HINT = "DeepSeek Harness 处于 developer preview；它会自动发现 skills 根的直接子项，无需手动重载。";

// Registry fields:
// - projectBase: which base a project-scope install uses (projectRoot | cwd | workspace)
// - projectRel: POSIX-style relative path appended to the base
// - global: { homeRel } directly under home, or { env, defaultRel, rel } with an
//   official environment override falling back to home/defaultRel
// - hints: per-scope user-facing hints; {projectRoot} is substituted at runtime
// - sameName: cross-scope same-name note (null = generic client-dependent wording)
export const AGENT_TARGETS = {
  "claude-code": {
    projectBase: "projectRoot",
    projectRel: ".claude/skills",
    global: { homeRel: ".claude/skills" },
    hints: {
      project: [RELOAD_CLAUDE],
      global: [RELOAD_CLAUDE],
    },
    sameName: "Claude Code 优先级 Enterprise > personal(global) > project。",
  },
  codex: {
    projectBase: "projectRoot",
    projectRel: ".agents/skills",
    global: { homeRel: ".agents/skills" },
    hints: {
      project: ["重启 Codex 会话以发现新 skill。"],
      global: ["重启 Codex 会话以发现新 skill。"],
    },
    sameName: "Codex 不合并同名 skill，两者可同时出现在 skill 选择器中。",
  },
  agents: {
    projectBase: "projectRoot",
    projectRel: ".agents/skills",
    global: { homeRel: ".agents/skills" },
    hints: {
      project: [RELOAD_SESSION],
      global: [RELOAD_SESSION],
    },
    sameName: null,
  },
  openclaw: {
    projectBase: "workspace",
    projectRel: "skills",
    global: { env: "OPENCLAW_STATE_DIR", defaultRel: ".openclaw", rel: "skills" },
    hints: {
      project: [
        "OpenClaw 以 workspace（默认当前工作目录）为基准；可用 --target-dir 指定工作区，重启会话刷新 skill。",
      ],
      global: ["重启 OpenClaw 会话以加载新 skill。"],
    },
    sameName: null,
  },
  hermes: {
    projectBase: "projectRoot",
    projectRel: ".hermes/skills",
    global: { homeRel: ".hermes/skills" },
    hints: {
      project: ["运行 hermes skills trust {projectRoot} 让 Hermes 信任该项目。"],
      global: ["重启 Hermes 会话以加载新 skill。"],
    },
    sameName: null,
  },
  deepseek: {
    projectBase: "projectRoot",
    projectRel: ".dsh/skills",
    global: { env: "DSH_HOME", defaultRel: ".dsh", rel: "skills" },
    hints: {
      project: [DEEPSEEK_HINT],
      global: [DEEPSEEK_HINT],
    },
    sameName: null,
  },
  pi: {
    projectBase: "cwd",
    projectRel: ".pi/skills",
    global: { env: "PI_CODING_AGENT_DIR", defaultRel: ".pi/agent", rel: "skills" },
    hints: {
      project: ["需在 Pi 客户端 trust 该目录；编辑后运行 /reload。默认当前工作目录，不向上查找。"],
      global: ["运行 /reload 以加载新 skill。"],
    },
    sameName: null,
  },
};

const RESERVED_WIN_NAMES = /^(con|prn|aux|nul|com[1-9]|lpt[1-9])(\..*)?$/i;

export function expandAgents(value) {
  const raw = String(value ?? "claude-code");
  const parts = raw.split(",").map((part) => part.trim()).filter(Boolean);
  const names = [];
  for (const part of parts) {
    if (part === "all") names.push(...HARNESS_AGENTS);
    else names.push(part);
  }
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

function joinRel(base, rel) {
  return path.join(base, ...rel.split("/"));
}

function resolveEnvBase(raw, home, cwd) {
  if (raw === "~") return home;
  if (raw.startsWith("~/")) return path.resolve(home, raw.slice(2));
  return path.isAbsolute(raw) ? path.resolve(raw) : path.resolve(cwd, raw);
}

export function resolveSkillsRoot({ agent, scope, projectRoot, home, cwd, env, targetDir } = {}) {
  if (!Object.hasOwn(AGENT_TARGETS, agent)) {
    throw new Error(`unknown agent: ${agent}`);
  }
  if (scope !== "project" && scope !== "global") {
    throw new Error(`unknown scope: ${scope}`);
  }
  const target = AGENT_TARGETS[agent];
  const effectiveHome = home ?? os.homedir();
  const effectiveCwd = path.resolve(cwd ?? process.cwd());

  if (scope === "global") {
    if (targetDir) throw new Error("--target-dir cannot be combined with --global");
    const global = target.global;
    if (global.env) {
      const raw = env?.[global.env];
      const base = typeof raw === "string" && raw.trim() !== ""
        ? resolveEnvBase(raw, effectiveHome, effectiveCwd)
        : path.join(effectiveHome, global.defaultRel);
      return joinRel(base, global.rel);
    }
    return joinRel(effectiveHome, global.homeRel);
  }

  let base;
  if (targetDir) {
    base = path.resolve(targetDir);
  } else if (target.projectBase === "projectRoot") {
    base = path.resolve(projectRoot ?? cwd ?? process.cwd());
  } else {
    // `cwd` and `workspace` both use the real working directory; OpenClaw's
    // workspace defaults to cwd and Pi never walks up to the git root.
    base = effectiveCwd;
  }
  return joinRel(base, target.projectRel);
}

export function getAgentHints(agent, { scope = "project", projectRoot } = {}) {
  if (!Object.hasOwn(AGENT_TARGETS, agent)) {
    throw new Error(`unknown agent: ${agent}`);
  }
  if (scope !== "project" && scope !== "global") {
    throw new Error(`unknown scope: ${scope}`);
  }
  const root = projectRoot ? path.resolve(projectRoot) : "<projectRoot>";
  return AGENT_TARGETS[agent].hints[scope].map((hint) => hint.replaceAll("{projectRoot}", root));
}

export function getSameNameNote(agent) {
  if (!Object.hasOwn(AGENT_TARGETS, agent)) {
    throw new Error(`unknown agent: ${agent}`);
  }
  return AGENT_TARGETS[agent].sameName;
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
