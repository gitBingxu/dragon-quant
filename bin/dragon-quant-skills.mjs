#!/usr/bin/env node
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { parseArgs } from "node:util";
import { fileURLToPath } from "node:url";
import {
  expandAgents, findProjectRoot, resolveSkillsRoot,
} from "../lib/paths.mjs";
import { loadSkillPackage, readPackageVersion } from "../lib/skill-package.mjs";
import { install, InstallerError } from "../lib/install.mjs";
import { uninstall } from "../lib/uninstall.mjs";
import { runDoctor } from "../lib/doctor.mjs";
import { detectCli, compareVersions, meetsMinVersion } from "../lib/python.mjs";

const NODE_FLOOR = "22.20.0";
const PACKAGE_ROOT = process.env.DQ_SKILLS_PACKAGE_ROOT
  ? path.resolve(process.env.DQ_SKILLS_PACKAGE_ROOT)
  : path.resolve(import.meta.dirname, "..");

const USAGE = `dragon-quant-skills — dragon-quant Agent Skill 安装器

用法：
  dragon-quant-skills install   [--project|--global] [--agent NAME[,NAME...]]
                                [--target-dir DIR] [--python PATH] [--dry-run] [--force] [--json]
  dragon-quant-skills doctor    [--project|--global|--all-scopes] [--agent NAME[,NAME...]]
                                [--target-dir DIR] [--python PATH] [--json]
  dragon-quant-skills uninstall [--project|--global] [--agent NAME[,NAME...]]
                                [--target-dir DIR] [--dry-run] [--force] [--json]
  dragon-quant-skills version   [--json]

默认 scope 是 project；--global 才会写入用户目录。--target-dir 表示项目根目录。
`;

function printJson(payload) {
  process.stdout.write(`${JSON.stringify(payload)}\n`);
}

function printUsage() {
  process.stdout.write(USAGE);
}

function fail(command, code, message, hint, jsonMode, exitCode) {
  if (jsonMode) {
    printJson({ ok: false, command, error: { code, message, hint } });
  } else {
    process.stderr.write(`错误：${message}\n`);
    if (hint) process.stderr.write(`提示：${hint}\n`);
  }
  return exitCode;
}

function detectCliSafe(pythonArg) {
  try {
    return detectCli({ pythonArg });
  } catch (err) {
    return { found: false, command: null, version: null, source: null, error: err.message };
  }
}

export async function main(argv = process.argv.slice(2)) {
  if (compareVersions(process.versions.node, NODE_FLOOR) < 0) {
    process.stderr.write(
      `Node ${process.versions.node} 低于最低要求 ${NODE_FLOOR}，请升级 Node 后重试。\n`,
    );
    return 2;
  }

  let parsed;
  try {
    parsed = parseArgs({
      args: argv,
      allowPositionals: true,
      strict: true,
      options: {
        project: { type: "boolean" },
        global: { type: "boolean" },
        "all-scopes": { type: "boolean" },
        agent: { type: "string" },
        "target-dir": { type: "string" },
        python: { type: "string" },
        "dry-run": { type: "boolean" },
        force: { type: "boolean" },
        json: { type: "boolean" },
        help: { type: "boolean", short: "h" },
        version: { type: "boolean", short: "v" },
      },
    });
  } catch (err) {
    return fail("unknown", "usage", err.message, "运行 dragon-quant-skills -h 查看用法", argv.includes("--json"), 2);
  }

  const { values, positionals } = parsed;
  const jsonMode = Boolean(values.json);
  const command = positionals[0] ?? (values.version ? "version" : "help");

  if (values.help || command === "help") {
    printUsage();
    return 0;
  }

  if (command === "version") {
    let version;
    try {
      version = readPackageVersion(PACKAGE_ROOT);
    } catch (err) {
      return fail(command, "integrity", `无法读取包版本：${err.message}`, "请重新安装 npm 包", jsonMode, 1);
    }
    if (jsonMode) {
      printJson({ ok: true, command: "version", version });
    } else {
      process.stdout.write(`dragon-quant-skills ${version}\n`);
    }
    return 0;
  }

  if (!["install", "doctor", "uninstall"].includes(command)) {
    return fail(command, "usage", `未知命令：${command}`, "可用命令：install / doctor / uninstall / version", jsonMode, 2);
  }

  if (values.global && values["target-dir"]) {
    return fail(command, "usage", "--global 与 --target-dir 不能同时使用", "--target-dir 只对 project scope 生效", jsonMode, 2);
  }
  if (values.global && values.project) {
    return fail(command, "usage", "--global 与 --project 不能同时使用", "省略 --project 即为默认 project scope", jsonMode, 2);
  }

  let agentNames;
  try {
    agentNames = expandAgents(values.agent ?? "claude-code");
  } catch (err) {
    return fail(command, "usage", err.message, "支持 claude-code / agents / all", jsonMode, 2);
  }

  const scope = values.global ? "global" : "project";
  const cwd = process.cwd();
  const home = os.homedir();
  const targetDir = values["target-dir"] ? path.resolve(values["target-dir"]) : undefined;
  const projectRoot = targetDir ?? findProjectRoot(cwd);

  const roots = new Map();
  try {
    for (const agent of agentNames) {
      roots.set(
        agent,
        resolveSkillsRoot({ agent, scope, projectRoot, home, targetDir: scope === "project" ? targetDir : undefined }),
      );
    }
  } catch (err) {
    return fail(command, "usage", err.message, "检查 --agent 与 scope 参数", jsonMode, 2);
  }

  const skillDirs = new Map(agentNames.map((agent) => [agent, path.join(roots.get(agent), "dragon-quant")]));

  if (command === "uninstall") {
    const agents = [];
    try {
      for (const agent of agentNames) {
        const report = uninstall({
          skillDir: skillDirs.get(agent),
          skillsRoot: roots.get(agent),
          force: Boolean(values.force),
          dryRun: Boolean(values["dry-run"]),
        });
        agents.push({
          agent,
          path: path.resolve(skillDirs.get(agent)),
          state: report.state,
          actions: report.actions,
        });
      }
    } catch (err) {
      const code = err instanceof InstallerError ? err.code : "io";
      return fail(command, code, err.message, err.hint ?? null, jsonMode, 1);
    }

    if (jsonMode) {
      printJson({ ok: true, command, scope, agents, warnings: [], backup_path: null });
      return 0;
    }
    for (const item of agents) {
      process.stdout.write(`- ${item.agent}: ${item.state} → ${item.path}\n`);
    }
    process.stdout.write(`卸载完成（不会删除 skills 根目录）。\n`);
    return 0;
  }

  // install/doctor need the shipped skill content; uninstall already returned above.
  const pkg = (() => {
    try {
      return loadSkillPackage(PACKAGE_ROOT);
    } catch (err) {
      return fail(command, "integrity", `无法读取包内 skill：${err.message}`, "请重新安装 npm 包", jsonMode, 1);
    }
  })();
  if (typeof pkg === "number") return pkg;

  if (command === "install") {
    const warnings = [];
    for (const agent of agentNames) {
      const otherScope = scope === "project" ? "global" : "project";
      let otherSkillsRoot;
      try {
        otherSkillsRoot = resolveSkillsRoot({ agent, scope: otherScope, projectRoot, home });
      } catch {
        continue;
      }
      const otherDir = path.join(otherSkillsRoot, "dragon-quant");
      if (fs.existsSync(otherDir)) {
        warnings.push(
          `另一 scope 已安装同名 skill：${otherDir}（${otherScope}）。Claude Code 优先级 Enterprise > personal(global) > project，旧版本可能遮蔽本次安装。`,
        );
      }
    }

    const agents = [];
    const backups = [];
    try {
      for (const agent of agentNames) {
        const report = install({
          skillDir: skillDirs.get(agent),
          skillsRoot: roots.get(agent),
          packageFiles: pkg.files,
          packageVersion: pkg.packageVersion,
          meta: pkg.meta,
          agent,
          scope,
          source: `npm:dragon-quant-skills@${pkg.packageVersion}`,
          force: Boolean(values.force),
          dryRun: Boolean(values["dry-run"]),
        });
        if (report.backupPath) backups.push(report.backupPath);
        agents.push({
          agent,
          path: report.skillDir,
          state: report.state,
          actions: report.actions,
          warnings: [],
        });
      }
    } catch (err) {
      const code = err instanceof InstallerError ? err.code : "io";
      return fail(command, code, err.message, err.hint ?? null, jsonMode, 1);
    }

    const minVersion = pkg.meta.metadata?.["min-cli-version"] ?? pkg.packageVersion;
    const cli = detectCliSafe(values.python);
    if (!cli.found) {
      warnings.push(
        `未找到 dragon-quant CLI：${cli.error}。安装已完成，但 skill 运行前需先安装 CLI。修复：python3 -m venv .venv && .venv/bin/pip install -e .`,
      );
    } else if (!meetsMinVersion(cli.version, minVersion)) {
      warnings.push(
        `dragon-quant CLI 版本 ${cli.version} 低于 skill 要求 ${minVersion}。修复：升级项目虚拟环境中的 dragon-quant。`,
      );
    }

    if (jsonMode) {
      printJson({
        ok: true,
        command,
        scope,
        agents,
        warnings,
        backup_path: backups[0] ?? null,
      });
      return 0;
    }

    process.stdout.write(`dragon-quant-skills ${pkg.packageVersion}（${scope}）\n`);
    for (const item of agents) {
      process.stdout.write(`- ${item.agent}: ${item.state} → ${item.path}\n`);
    }
    if (values["dry-run"]) {
      process.stdout.write(`dry-run：未写入任何文件（最终路径见上）。\n`);
    } else {
      process.stdout.write(`已完成。首次安装或升级后请在 Claude Code 运行 /reload-skills，或重启会话。\n`);
      for (const backup of backups) process.stdout.write(`备份：${backup}\n`);
    }
    for (const warning of warnings) process.stdout.write(`警告：${warning}\n`);
    return 0;
  }

  // doctor
  const targets = [];
  for (const agent of agentNames) {
    targets.push({ agent, scope, skillDir: skillDirs.get(agent) });
  }
  for (const agent of agentNames) {
    const otherScope = scope === "project" ? "global" : "project";
    let otherRoot;
    try {
      otherRoot = resolveSkillsRoot({ agent, scope: otherScope, projectRoot, home });
    } catch {
      continue;
    }
    const otherDir = path.join(otherRoot, "dragon-quant");
    const shouldInclude = Boolean(values["all-scopes"]) || fs.existsSync(otherDir);
    if (shouldInclude) targets.push({ agent, scope: otherScope, skillDir: otherDir });
  }

  const report = runDoctor({
    targets,
    packageFiles: pkg.files,
    packageVersion: pkg.packageVersion,
    meta: pkg.meta,
    detect: () => detectCliSafe(values.python),
    projectRoot,
  });

  if (jsonMode) {
    printJson({ ok: report.ok, command: "doctor", checks: report.checks, installs: report.installs });
    return report.ok ? 0 : 1;
  }

  for (const check of report.checks) {
    process.stdout.write(`[${check.status}] ${check.id}: ${check.detail}\n`);
    if (check.fix) process.stdout.write(`        修复：${check.fix}\n`);
  }
  const cli = detectCliSafe(values.python);
  if (cli.found) process.stdout.write(`CLI：${cli.command}（${cli.version}，${cli.source}）\n`);
  return report.ok ? 0 : 1;
}

function isDirectInvocation() {
  if (!process.argv[1]) return false;
  try {
    return fs.realpathSync(process.argv[1]) === fs.realpathSync(fileURLToPath(import.meta.url));
  } catch {
    return path.resolve(process.argv[1]) === fileURLToPath(import.meta.url);
  }
}

if (isDirectInvocation()) {
  main().then((code) => {
    process.exitCode = code;
  }).catch((err) => {
    process.stderr.write(`未处理异常：${err.stack ?? err.message}\n`);
    process.exitCode = 1;
  });
}
