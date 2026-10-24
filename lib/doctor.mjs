import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { inspectInstall } from "./manifest.mjs";
import { meetsMinVersion } from "./python.mjs";

const CLEAN_STATES = new Set(["clean", "up-to-date"]);

export function runDoctor({
  targets = [],
  packageFiles,
  packageVersion,
  meta,
  detect,
  projectRoot,
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
    installs.push({
      agent: target.agent,
      scope: target.scope,
      skillDir: path.resolve(target.skillDir),
      state: result.state,
      packageVersion: result.manifest?.package_version ?? null,
      skillVersion: result.manifest?.skill_version ?? null,
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
        .map(([agent, items]) => `${agent}: ${items.map((item) => `${item.scope}@${item.skillVersion ?? "?"}`).join(", ")}`)
        .join("; ") + " (Claude Code precedence: Enterprise > personal > project)"
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

  checks.push({
    id: "reload.hint",
    status: "pass",
    detail: "after installing or upgrading: run /reload-skills in Claude Code, or restart the session",
    fix: null,
  });

  return {
    ok: checks.every((check) => check.status !== "fail"),
    checks,
    installs,
  };
}
