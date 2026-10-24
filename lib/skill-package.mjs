import fs from "node:fs";
import path from "node:path";
import { createHash } from "node:crypto";

export function sha256(buf) {
  return createHash("sha256").update(buf).digest("hex");
}

function unquote(value) {
  const text = value.trim();
  if (text.length >= 2) {
    const first = text[0];
    const last = text[text.length - 1];
    if ((first === '"' && last === '"') || (first === "'" && last === "'")) {
      return text.slice(1, -1);
    }
  }
  return text;
}

export function parseFrontmatter(text) {
  const lines = String(text).split(/\r?\n/);
  if (lines[0]?.trim() !== "---") {
    throw new Error("missing YAML frontmatter");
  }
  let end = -1;
  for (let i = 1; i < lines.length; i += 1) {
    if (lines[i].trim() === "---") {
      end = i;
      break;
    }
  }
  if (end === -1) throw new Error("unterminated YAML frontmatter");

  const meta = { metadata: {} };
  let inMetadata = false;
  for (const line of lines.slice(1, end)) {
    if (line.trim() === "") continue;
    const nested = /^\s+([A-Za-z0-9_-]+):\s*(.*)$/.exec(line);
    if (nested && inMetadata) {
      meta.metadata[nested[1]] = unquote(nested[2]);
      continue;
    }
    const top = /^([A-Za-z0-9_-]+):\s*(.*)$/.exec(line);
    if (!top) continue;
    const key = top[1];
    const value = unquote(top[2]);
    if (key === "metadata") {
      inMetadata = true;
      continue;
    }
    inMetadata = false;
    meta[key] = value;
  }
  return meta;
}

function toPosix(rel) {
  return rel.split(path.sep).join("/");
}

function walkSkillDir(skillRoot) {
  const files = new Map();

  function visit(dir) {
    const entries = fs
      .readdirSync(dir, { withFileTypes: true })
      .map((entry) => entry.name)
      .sort();
    for (const name of entries) {
      const full = path.join(dir, name);
      const stat = fs.lstatSync(full);
      if (stat.isSymbolicLink()) {
        throw new Error(`symlink not allowed in skill package: ${full}`);
      }
      if (stat.isDirectory()) {
        visit(full);
      } else if (stat.isFile()) {
        files.set(toPosix(path.relative(skillRoot, full)), fs.readFileSync(full));
      } else {
        throw new Error(`unsupported file type in skill package: ${full}`);
      }
    }
  }

  visit(skillRoot);
  return files;
}

export function readPackageVersion(packageRoot) {
  const root = path.resolve(packageRoot);
  const pkgPath = path.join(root, "package.json");
  let version;
  try {
    version = JSON.parse(fs.readFileSync(pkgPath, "utf8")).version;
  } catch (err) {
    throw new Error(`cannot read package.json at ${pkgPath}: ${err.message}`);
  }
  if (!version) throw new Error(`package.json at ${pkgPath} has no version`);
  return version;
}

export function loadSkillPackage(packageRoot) {
  const root = path.resolve(packageRoot);
  const packageVersion = readPackageVersion(root);

  const skillRoot = path.join(root, "skills", "dragon-quant");
  if (!fs.existsSync(skillRoot)) {
    throw new Error(`missing SKILL.md: ${path.join(skillRoot, "SKILL.md")}`);
  }
  if (fs.lstatSync(skillRoot).isSymbolicLink()) {
    throw new Error(`symlink not allowed: ${skillRoot}`);
  }

  const files = walkSkillDir(skillRoot);
  const skillText = files.get("SKILL.md");
  if (!skillText) {
    throw new Error(`missing SKILL.md: ${path.join(skillRoot, "SKILL.md")}`);
  }

  const meta = parseFrontmatter(skillText.toString("utf8"));
  if (meta.name !== "dragon-quant") {
    throw new Error(`SKILL.md frontmatter name must be dragon-quant, got ${meta.name}`);
  }

  return {
    packageVersion,
    meta,
    files,
    relativePaths: [...files.keys()],
  };
}
