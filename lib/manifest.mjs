import fs from "node:fs";
import path from "node:path";
import { sha256 } from "./skill-package.mjs";
import { assertSafeRelPath } from "./paths.mjs";

export const MANIFEST_NAME = ".dragon-quant-skill.json";
export const MANIFEST_SCHEMA = 1;

function sortedObject(entries) {
  const out = {};
  for (const key of [...entries.keys()].sort()) out[key] = entries.get(key);
  return out;
}

export function buildManifest({ files, packageVersion, meta, agent, scope, source, now = new Date() }) {
  const fileHashes = {};
  for (const key of [...files.keys()].sort()) {
    fileHashes[key] = sha256(files.get(key));
  }
  const metadata = meta?.metadata ?? {};
  const skillVersion = metadata["skill-version"] ?? meta?.["skill-version"] ?? packageVersion;
  const minCliVersion = metadata["min-cli-version"] ?? meta?.["min-cli-version"] ?? packageVersion;
  return {
    schema: MANIFEST_SCHEMA,
    installer: "dragon-quant-skills",
    skill: "dragon-quant",
    agent,
    scope,
    package_version: packageVersion,
    skill_version: skillVersion,
    min_cli_version: minCliVersion,
    installed_at: now.toISOString(),
    source,
    files: fileHashes,
  };
}

export function readManifest(skillDir) {
  const manifestPath = path.join(skillDir, MANIFEST_NAME);
  let text;
  try {
    text = fs.readFileSync(manifestPath, "utf8");
  } catch {
    return { state: "missing" };
  }
  let manifest;
  try {
    manifest = JSON.parse(text);
  } catch {
    return { state: "corrupt", reason: "manifest is not valid JSON" };
  }
  if (!manifest || typeof manifest !== "object" || Array.isArray(manifest)) {
    return { state: "corrupt", reason: "manifest is not an object" };
  }
  if (manifest.schema !== MANIFEST_SCHEMA) {
    return { state: "corrupt", reason: `unsupported manifest schema: ${manifest.schema}` };
  }
  if (!manifest.files || typeof manifest.files !== "object" || Array.isArray(manifest.files)) {
    return { state: "corrupt", reason: "manifest files is not an object" };
  }
  for (const [rel, digest] of Object.entries(manifest.files)) {
    try {
      assertSafeRelPath(rel);
    } catch {
      return { state: "corrupt", reason: `unsafe manifest path: ${rel}` };
    }
    if (typeof digest !== "string" || !/^[0-9a-f]{64}$/.test(digest)) {
      return { state: "corrupt", reason: `invalid hash for ${rel}` };
    }
  }
  return { state: "ok", manifest };
}

function walkDisk(skillDir) {
  const files = new Map();
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
  return { files, link };
}

export function inspectInstall({ skillDir, packageFiles, packageVersion }) {
  let stat;
  try {
    stat = fs.lstatSync(skillDir);
  } catch {
    return { state: "absent" };
  }
  if (!stat.isDirectory() && !stat.isSymbolicLink()) {
    return { state: "foreign", reason: "target exists but is not a directory" };
  }
  if (stat.isSymbolicLink()) {
    return { state: "modified", reason: "skill directory is a symlink" };
  }

  const read = readManifest(skillDir);
  if (read.state === "missing") {
    return { state: "foreign", reason: "directory exists without installer manifest" };
  }
  if (read.state === "corrupt") {
    return { state: "corrupt", reason: read.reason };
  }
  const manifest = read.manifest;

  const disk = walkDisk(skillDir);
  if (disk.link) {
    return { state: "modified", reason: `symlink or special file: ${disk.link}`, manifest };
  }

  for (const [rel, digest] of Object.entries(manifest.files)) {
    const buf = disk.files.get(rel);
    if (!buf) {
      return { state: "modified", reason: `missing file: ${rel}`, manifest };
    }
    if (sha256(buf) !== digest) {
      return { state: "modified", reason: `hash mismatch: ${rel}`, manifest };
    }
  }
  for (const rel of disk.files.keys()) {
    if (rel !== MANIFEST_NAME && !Object.hasOwn(manifest.files, rel)) {
      return { state: "modified", reason: `extra file: ${rel}`, manifest };
    }
  }

  const entries = { files: disk.files, manifest };
  const packageKeys = [...packageFiles.keys()].sort();
  const manifestKeys = Object.keys(manifest.files).sort();
  const sameKeys =
    packageKeys.length === manifestKeys.length &&
    packageKeys.every((key, index) => key === manifestKeys[index]);
  const sameContent =
    sameKeys &&
    packageKeys.every((key) => manifest.files[key] === sha256(packageFiles.get(key)));

  if (manifest.package_version === packageVersion && sameContent) {
    return { state: "up-to-date", manifest, entries };
  }
  return { state: "clean", manifest, entries };
}

export function diffFiles(manifest, packageFiles) {
  const known = manifest?.files ?? {};
  const knownKeys = Object.keys(known);
  const packageKeys = [...packageFiles.keys()];
  const added = packageKeys.filter((key) => !Object.hasOwn(known, key)).sort();
  const removed = knownKeys.filter((key) => !packageFiles.has(key)).sort();
  const changed = packageKeys
    .filter((key) => Object.hasOwn(known, key) && known[key] !== sha256(packageFiles.get(key)))
    .sort();
  return { added, changed, removed };
}
