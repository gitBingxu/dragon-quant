import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { inspectInstall } from "./manifest.mjs";
import { meetsMinVersion } from "./python.mjs";
import { getAgentHints, getSameNameNote } from "./paths.mjs";

const CLEAN_STATES = new Set(["clean", "up-to-date"]);

/** 无 manifest 时回退读取 SKILL.md frontmatter 的 skill-version（只读）。 */
export function readFrontmatterVersion(skillDir) {
  try {
    const text = fs.readFileSync(path.join(skillDir, "SKILL.md"), "utf8");
    const match = /skill-version:\s*"?([^"\n]+)"?/.exec(text);
    return match ? match[1].trim() : null;
  } catch {
    return null;
  }
}

/** 已知宿主候选路径（仅提示，不代表宿主一定加载）。 */
function candidateSkillDirs({ projectRoot, home }) {
  const list = [];
  const add = (base) => list.push(path.join(base, "dragon-quant"));
  if (projectRoot) {
    add(path.join(projectRoot, ".claude", "skills"));
    add(path.join(projectRoot, ".agents", "skills"));
    add(path.join(projectRoot, ".hermes", "skills"));
  }
  add(path.join(home, ".claude", "skills"));
  add(path.join(home, ".agents", "skills"));
  add(path.join(home, ".hermes", "skills"));
  add(path.join(home, ".dsh", "skills"));
  add(path.join(home, ".pi", "agent", "skills"));
  return [...new Set(list)];
}

export function runDoctor({
  targets = [],
  packageFiles,
  packageVersion,
  meta,
  detect,
  projectRoot,
  capabilities = null,
}) {
  const checks = [];
  const installs = [];
  const reasons = new Map();
  for (const target of targets) {
    const result = inspectInstall({
      skillDir: target.skillDir,
      packageFiles,
      packageVersion,
    });
    reasons.set(`${target.agent}/${target.scope}`, result.reason ?? null);
    const frontmatterVersion = readFrontmatterVersion(target.skillDir);
    const manifestVersion = result.manifest?.skill_version ?? null;
    installs.push({
      agent: target.agent,
      scope: target.scope,
      skillDir: path.resolve(target.skillDir),
      state: result.state,
      packageVersion: result.manifest?.package_version ?? null,
      skillVersion: manifestVersion ?? frontmatterVersion,
      skillVersionSource: manifestVersion
        ? "manifest"
        : frontmatterVersion
          ? "frontmatter"
          : "unknown",
    });
  }

  const absent = installs.filter((item) => item.state === "absent");
  checks.push({
    id: "install.present",
    status: absent.length ? "fail" : "pass",
    detail: absent.length
      ? `missing install: ${absent.map((item) => `${item.agent}/${item.scope}`).join(", ")}`
      : `installed: ${installs.map((item) => `${item.agent}/${item.scope}`).join(", ")}`,
    fix: absent.length ? "npx dragon-quant-skills install (add --global for user scope)" : null,
  });

  const broken = installs.filter((item) => !CLEAN_STATES.has(item.state) && item.state !== "absent");
  const symlinkBroken = broken.filter((item) => /symlink/i.test(reasons.get(`${item.agent}/${item.scope}`) ?? ""));
  checks.push({
    id: "install.integrity",
    status: broken.length ? "fail" : "pass",
    detail: broken.length
      ? `not clean: ${broken.map((item) => {
        const reason = reasons.get(`${item.agent}/${item.scope}`);
        return `${item.agent}/${item.scope}=${item.state}${reason ? `: ${reason}` : ""}`;
      }).join(", ")}`
      : "all installed files match the manifest",
    fix: symlinkBroken.length
      ? "remove the symlink at the skill directory, then re-run install"
      : broken.length
        ? "re-run install --force to back up and replace, or remove the foreign directory first"
        : null,
  });

  const cli = typeof detect === "function" ? detect() : detect;
  checks.push({
    id: "cli.present",
    status: cli?.found ? "pass" : "fail",
    detail: cli?.found
      ? `found ${cli.command} via ${cli.source}`
      : `dragon-quant CLI not found: ${cli?.error ?? "unknown error"}`,
    fix: cli?.found
      ? null
      : "python3 -m venv .venv && .venv/bin/pip install -e . && .venv/bin/python -m dragon_quant -v",
  });

  const minVersion = meta?.metadata?.["min-cli-version"] ?? meta?.["min-cli-version"] ?? packageVersion;
  const versionOk = cli?.found && meetsMinVersion(cli.version, minVersion);
  checks.push({
    id: "cli.version",
    status: versionOk ? "pass" : "fail",
    detail: cli?.found
      ? `detected ${cli.version}; required >= ${minVersion}`
      : `CLI not detected; required >= ${minVersion}`,
    fix: versionOk
      ? null
      : `upgrade dragon-quant to >= ${minVersion} in the project virtualenv`,
  });

  if (capabilities) {
    checks.push({
      id: "cli.capabilities",
      status: capabilities.ok ? "pass" : "warn",
      detail: capabilities.ok
        ? `leaf capabilities present: ${capabilities.found.join(", ")}`
        : `missing leaf capabilities: ${(capabilities.missing ?? []).join(", ")}`
          + (capabilities.error ? `（${capabilities.error}）` : ""),
      fix: capabilities.ok
        ? null
        : "升级 dragon-quant CLI；Skill 只使用探测到的叶子能力，缺失时降级为只读解释",
    });
  }

  const byAgent = new Map();
  for (const item of installs) {
    if (!byAgent.has(item.agent)) byAgent.set(item.agent, []);
    byAgent.get(item.agent).push(item);
  }
  const shadowed = [...byAgent.entries()].filter(([, items]) => items.length > 1);
  checks.push({
    id: "scope.shadowing",
    status: shadowed.length ? "warn" : "pass",
    detail: shadowed.length
      ? shadowed
        .map(([agent, items]) => {
          const note = getSameNameNote(agent) ?? "可能按客户端优先级遮蔽；可用 --all-scopes 检查";
          return `${agent}: ${items.map((item) => `${item.scope}@${item.skillVersion ?? "?"}`).join(", ")}（${note}）`;
        })
        .join("; ")
      : "no same-skill install in another scope",
    fix: shadowed.length ? "uninstall the shadowing copy or keep only one scope" : null,
  });

  const lockPaths = [];
  if (projectRoot) lockPaths.push(path.join(projectRoot, "skills-lock.json"));
  lockPaths.push(path.join(os.homedir(), ".agents", ".skill-lock.json"));
  const locks = lockPaths.filter((item) => fs.existsSync(item));
  checks.push({
    id: "foreign.lockfile",
    status: locks.length ? "warn" : "pass",
    detail: locks.length
      ? `foreign skill lockfile found: ${locks.join(", ")}`
      : "no foreign skill lockfile",
    fix: locks.length ? "foreign installers may also manage this directory; the manifest stays authoritative" : null,
  });

  // 实际发现的版本来源（manifest vs frontmatter vs unknown）：便于判断宿主
  // 看到的版本是否与仓库/包一致；不把候选路径推断为活动 Skill。
  checks.push({
    id: "install.version_source",
    status: "pass",
    detail: installs.length
      ? installs
        .map((item) => `${item.agent}/${item.scope}@${item.skillDir}: `
          + `${item.skillVersion ?? "unknown"} (${item.skillVersionSource})`)
        .join("; ")
      : "no inspected install targets",
    fix: installs.some((item) => item.skillVersionSource === "unknown")
      ? "re-run install so the manifest/SKILL.md declares a version"
      : null,
  });

  const inspected = new Set(installs.map((item) => item.skillDir));
  const candidates = candidateSkillDirs({ projectRoot, home: os.homedir() })
    .filter((dir) => !inspected.has(path.resolve(dir)) && fs.existsSync(dir));
  checks.push({
    id: "install.candidates",
    status: candidates.length ? "warn" : "pass",
    detail: candidates.length
      ? `same-name skill found outside inspected targets: ${candidates.join(", ")}`
        + "（仅提示候选路径；doctor 未验证宿主实际加载哪个）"
      : "no same-name skill outside inspected targets",
    fix: candidates.length
      ? "确认宿主实际加载的路径与版本；需要时用支持的安装器同步或移除旧副本"
      : null,
  });

  const reloadHints = [];
  const seenHints = new Set();
  for (const target of targets) {
    let hints;
    try {
      hints = getAgentHints(target.agent, { scope: target.scope, projectRoot });
    } catch {
      continue;
    }
    for (const hint of hints) {
      if (seenHints.has(hint)) continue;
      seenHints.add(hint);
      reloadHints.push(hint);
    }
  }
  checks.push({
    id: "reload.hint",
    status: "pass",
    detail: reloadHints.length ? reloadHints.join("；") : "no targets to reload",
    fix: null,
  });

  return {
    ok: checks.every((check) => check.status !== "fail"),
    checks,
    installs,
  };
}
