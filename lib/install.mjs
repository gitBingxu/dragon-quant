import fs from "node:fs";
import path from "node:path";
import { randomBytes } from "node:crypto";
import { assertSafeRelPath, isInside } from "./paths.mjs";
import { buildManifest, diffFiles, inspectInstall, MANIFEST_NAME } from "./manifest.mjs";

export class InstallerError extends Error {
  constructor(code, message, hint = null) {
    super(message);
    this.name = "InstallerError";
    this.code = code;
    this.hint = hint;
  }
}

function randomSuffix() {
  return `${Date.now()}-${randomBytes(4).toString("hex")}`;
}

function assertSkillDirPlacement(skillDir, skillsRoot) {
  if (!isInside(skillsRoot, skillDir)) {
    throw new InstallerError("unsafe-path", `skill directory escapes skills root: ${skillDir}`);
  }
  try {
    const stat = fs.lstatSync(skillDir);
    if (stat.isSymbolicLink()) {
      throw new InstallerError("unsafe-path", `refusing to install over symlink: ${skillDir}`);
    }
  } catch (err) {
    if (err instanceof InstallerError) throw err;
    if (err.code !== "ENOENT") throw err;
  }
}

function validatePackageFiles(packageFiles) {
  for (const rel of packageFiles.keys()) {
    assertSafeRelPath(rel);
  }
}

function writeStaging(stagingDir, packageFiles) {
  fs.mkdirSync(stagingDir, { recursive: true, mode: 0o755 });
  for (const [rel, buf] of packageFiles) {
    const full = path.join(stagingDir, ...rel.split("/"));
    fs.mkdirSync(path.dirname(full), { recursive: true, mode: 0o755 });
    fs.writeFileSync(full, buf, { mode: 0o644 });
  }
}

function removeQuietly(target) {
  try {
    fs.rmSync(target, { recursive: true, force: true });
  } catch {
    // best effort cleanup
  }
}

function conflictError(state, diff, reason) {
  const details = [];
  if (reason) details.push(reason);
  if (diff?.added?.length) details.push(`new files: ${diff.added.join(", ")}`);
  if (diff?.changed?.length) details.push(`changed files: ${diff.changed.join(", ")}`);
  if (diff?.removed?.length) details.push(`missing files: ${diff.removed.join(", ")}`);
  const suffix = details.length ? ` (${details.join("; ")})` : "";
  const label = state === "foreign" ? "foreign install (no installer manifest)" : state;
  return new InstallerError(
    "conflict",
    `target ${state}: ${label}${suffix}`,
    "re-run with --force to move the existing directory to a backup and replace it",
  );
}

export function install({
  skillDir,
  skillsRoot,
  packageFiles,
  packageVersion,
  meta,
  agent,
  scope,
  source,
  force = false,
  dryRun = false,
  now = new Date(),
}) {
  try {
    validatePackageFiles(packageFiles);
  } catch (err) {
    throw new InstallerError("unsafe-path", err.message);
  }
  assertSkillDirPlacement(skillDir, skillsRoot);

  const current = inspectInstall({ skillDir, packageFiles, packageVersion });
  const state = current.state;

  if (state === "up-to-date" && !force) {
    return {
      ok: true,
      state: "up-to-date",
      skillDir: path.resolve(skillDir),
      backupPath: null,
      actions: ["already up to date; nothing written"],
      warnings: [],
    };
  }
  if (["foreign", "modified", "corrupt"].includes(state) && !force) {
    const diff = current.manifest ? diffFiles(current.manifest, packageFiles) : null;
    throw conflictError(state, diff, current.reason);
  }

  const planned = state === "absent" ? "installed" : force ? "forced" : "upgraded";
  const backupPath = path.join(skillsRoot, `.dragon-quant-backup-${randomSuffix()}`);

  if (dryRun) {
    return {
      ok: true,
      state: planned,
      skillDir: path.resolve(skillDir),
      backupPath: null,
      actions: [
        `would write ${packageFiles.size} files to ${path.resolve(skillDir)}`,
        ...(state === "absent" ? [] : [`would move existing directory to ${backupPath}`]),
      ],
      warnings: [],
    };
  }

  fs.mkdirSync(skillsRoot, { recursive: true, mode: 0o755 });
  const stagingDir = path.join(skillsRoot, `.dragon-quant-staging-${randomSuffix()}`);
  try {
    writeStaging(stagingDir, packageFiles);
    const manifest = buildManifest({
      files: packageFiles, packageVersion, meta, agent, scope, source, now,
    });
    fs.writeFileSync(path.join(stagingDir, MANIFEST_NAME), `${JSON.stringify(manifest, null, 2)}\n`, {
      mode: 0o644,
    });
  } catch (err) {
    removeQuietly(stagingDir);
    if (err instanceof InstallerError) throw err;
    throw new InstallerError("io", `failed to stage skill files: ${err.message}`);
  }

  let movedOld = false;
  if (state !== "absent") {
    try {
      fs.renameSync(skillDir, backupPath);
      movedOld = true;
    } catch (err) {
      removeQuietly(stagingDir);
      throw new InstallerError("io", `failed to move existing install to backup: ${err.message}`);
    }
  }

  try {
    fs.renameSync(stagingDir, skillDir);
  } catch (err) {
    removeQuietly(stagingDir);
    if (movedOld) {
      try {
        fs.renameSync(backupPath, skillDir);
      } catch {
        // backup remains for manual recovery
      }
    }
    throw new InstallerError("io", `failed to activate staged install: ${err.message}`);
  }

  if (movedOld && !force) {
    removeQuietly(backupPath);
  }

  return {
    ok: true,
    state: planned,
    skillDir: path.resolve(skillDir),
    backupPath: movedOld ? backupPath : null,
    actions: [
      `wrote ${packageFiles.size} files to ${path.resolve(skillDir)}`,
      ...(movedOld ? [`moved previous install to ${backupPath}`] : []),
    ],
    warnings: [],
  };
}
