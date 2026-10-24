import fs from "node:fs";
import path from "node:path";
import { randomBytes } from "node:crypto";
import { isInside } from "./paths.mjs";
import { inspectInstall, MANIFEST_NAME, readManifest } from "./manifest.mjs";
import { InstallerError } from "./install.mjs";
import { sha256 } from "./skill-package.mjs";

function randomSuffix() {
  return `${Date.now()}-${randomBytes(4).toString("hex")}`;
}

function scanTree(skillDir) {
  const files = new Map();
  const dirs = [];
  let link = null;

  function visit(dir, prefix) {
    const entries = fs
      .readdirSync(dir, { withFileTypes: true })
      .map((entry) => entry.name)
      .sort();
    for (const name of entries) {
      const full = path.join(dir, name);
      const rel = prefix ? `${prefix}/${name}` : name;
      const stat = fs.lstatSync(full);
      if (stat.isSymbolicLink()) {
        link = rel;
        return;
      }
      if (stat.isDirectory()) {
        dirs.push(rel);
        visit(full, rel);
      } else if (stat.isFile()) {
        files.set(rel, fs.readFileSync(full));
      } else {
        link = rel;
        return;
      }
    }
  }

  visit(skillDir, "");
  return { files, dirs, link };
}

function conflict(state, reason) {
  return new InstallerError(
    "conflict",
    `cannot uninstall: ${state}${reason ? ` (${reason})` : ""}`,
    "re-run with --force to move the directory to a backup instead of deleting it",
  );
}

export function uninstall({ skillDir, skillsRoot, force = false, dryRun = false }) {
  if (!isInside(skillsRoot, skillDir)) {
    throw new InstallerError("unsafe-path", `skill directory escapes skills root: ${skillDir}`);
  }
  let stat;
  try {
    stat = fs.lstatSync(skillDir);
  } catch {
    return { ok: true, state: "not-installed", backupPath: null, actions: ["nothing to remove"] };
  }
  if (stat.isSymbolicLink()) {
    throw new InstallerError("integrity", `refusing to uninstall symlink: ${skillDir}`);
  }
  if (!stat.isDirectory()) {
    throw conflict("foreign", "target is not a directory");
  }

  const read = readManifest(skillDir);
  let state;
  let reason = read.reason;
  if (read.state === "missing") {
    state = "foreign";
  } else if (read.state === "corrupt") {
    state = "corrupt";
  } else {
    const tree = scanTree(skillDir);
    if (tree.link) {
      state = "modified";
      reason = `symlink or special file: ${tree.link}`;
    } else {
      state = "clean";
      for (const [rel, digest] of Object.entries(read.manifest.files)) {
        const buf = tree.files.get(rel);
        if (!buf) {
          state = "modified";
          reason = `missing file: ${rel}`;
          break;
        }
        if (sha256(buf) !== digest) {
          state = "modified";
          reason = `hash mismatch: ${rel}`;
          break;
        }
      }
      if (state === "clean") {
        for (const rel of tree.files.keys()) {
          if (rel !== MANIFEST_NAME && !Object.hasOwn(read.manifest.files, rel)) {
            state = "modified";
            reason = `extra file: ${rel}`;
            break;
          }
        }
      }
    }
  }

  if (state !== "clean") {
    if (!force) throw conflict(state, reason);
    const backupPath = path.join(skillsRoot, `.dragon-quant-uninstalled-${randomSuffix()}`);
    if (dryRun) {
      return {
        ok: true,
        state: "would-back-up",
        backupPath: null,
        actions: [`would move ${skillDir} to ${backupPath}`],
      };
    }
    fs.renameSync(skillDir, backupPath);
    return {
      ok: true,
      state: "backed-up",
      backupPath,
      actions: [`moved ${skillDir} to ${backupPath}`],
    };
  }

  if (dryRun) {
    return {
      ok: true,
      state: "removed",
      backupPath: null,
      actions: [`would delete ${Object.keys(read.manifest.files).length} files from ${skillDir}`],
    };
  }

  for (const rel of Object.keys(read.manifest.files)) {
    fs.rmSync(path.join(skillDir, ...rel.split("/")), { force: true });
  }
  const tree = scanTree(skillDir);
  for (const rel of tree.dirs.sort((a, b) => b.length - a.length)) {
    try {
      fs.rmdirSync(path.join(skillDir, ...rel.split("/")));
    } catch {
      // non-empty directory stays
    }
  }
  fs.rmSync(path.join(skillDir, MANIFEST_NAME), { force: true });
  try {
    fs.rmdirSync(skillDir);
  } catch {
    // leave the directory if user files remain
  }
  return { ok: true, state: "removed", backupPath: null, actions: [`removed ${skillDir}`] };
}
