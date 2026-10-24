import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { createHash } from "node:crypto";
import {
  MANIFEST_NAME, MANIFEST_SCHEMA, buildManifest, readManifest,
  inspectInstall, diffFiles,
} from "../lib/manifest.mjs";

const pkgFiles = () => new Map([
  ["SKILL.md", Buffer.from("new")],
  ["references/a.md", Buffer.from("a")],
]);

function makeInstall({ files = pkgFiles(), manifest } = {}) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "dq-mf-"));
  const skillDir = path.join(root, "dragon-quant");
  fs.mkdirSync(path.join(skillDir, "references"), { recursive: true });
  for (const [rel, buf] of files) {
    const full = path.join(skillDir, rel);
    fs.mkdirSync(path.dirname(full), { recursive: true });
    fs.writeFileSync(full, buf);
  }
  if (manifest !== undefined && manifest !== null) {
    fs.writeFileSync(
      path.join(skillDir, MANIFEST_NAME),
      typeof manifest === "string" ? manifest : JSON.stringify(manifest),
    );
  }
  return { root, skillDir };
}

function manifestFor(files, { packageVersion = "0.7.1", skillVersion = "0.7.1", minCli = "0.7.1" } = {}) {
  const fileHashes = {};
  for (const [rel, buf] of files) {
    fileHashes[rel] = createHash("sha256").update(buf).digest("hex");
  }
  return {
    schema: MANIFEST_SCHEMA,
    installer: "dragon-quant-skills",
    skill: "dragon-quant",
    agent: "claude-code",
    scope: "project",
    package_version: packageVersion,
    skill_version: skillVersion,
    min_cli_version: minCli,
    installed_at: "2026-10-24T12:00:00.000Z",
    source: `npm:dragon-quant-skills@${packageVersion}`,
    files: fileHashes,
  };
}

test("absent when skill dir missing", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "dq-mf-"));
  const result = inspectInstall({
    skillDir: path.join(root, "missing"), packageFiles: pkgFiles(), packageVersion: "0.7.1",
  });
  assert.equal(result.state, "absent");
});

test("foreign when dir exists without manifest", () => {
  const { skillDir } = makeInstall({ manifest: null });
  assert.equal(
    inspectInstall({ skillDir, packageFiles: pkgFiles(), packageVersion: "0.7.1" }).state,
    "foreign",
  );
});

test("corrupt when manifest is not valid JSON", () => {
  const { skillDir } = makeInstall({ manifest: "{" });
  assert.equal(
    inspectInstall({ skillDir, packageFiles: pkgFiles(), packageVersion: "0.7.1" }).state,
    "corrupt",
  );
});

test("clean when disk hashes match manifest", () => {
  const files = pkgFiles();
  const { skillDir } = makeInstall({ files, manifest: manifestFor(files, { packageVersion: "0.6.0" }) });
  assert.equal(
    inspectInstall({ skillDir, packageFiles: files, packageVersion: "0.7.1" }).state,
    "clean",
  );
});

test("up-to-date when manifest and package files/version match", () => {
  const files = pkgFiles();
  const { skillDir } = makeInstall({ files, manifest: manifestFor(files) });
  assert.equal(
    inspectInstall({ skillDir, packageFiles: files, packageVersion: "0.7.1" }).state,
    "up-to-date",
  );
});

test("modified when a file hash changed", () => {
  const files = pkgFiles();
  const { skillDir } = makeInstall({ files, manifest: manifestFor(files) });
  fs.writeFileSync(path.join(skillDir, "SKILL.md"), "user edit");
  assert.equal(
    inspectInstall({ skillDir, packageFiles: files, packageVersion: "0.7.1" }).state,
    "modified",
  );
});

test("modified when an extra file exists", () => {
  const files = pkgFiles();
  const { skillDir } = makeInstall({ files, manifest: manifestFor(files) });
  fs.writeFileSync(path.join(skillDir, "extra.txt"), "x");
  assert.equal(
    inspectInstall({ skillDir, packageFiles: files, packageVersion: "0.7.1" }).state,
    "modified",
  );
});

test("modified when a manifest file is missing", () => {
  const files = pkgFiles();
  const { skillDir } = makeInstall({ files, manifest: manifestFor(files) });
  fs.unlinkSync(path.join(skillDir, "references", "a.md"));
  assert.equal(
    inspectInstall({ skillDir, packageFiles: files, packageVersion: "0.7.1" }).state,
    "modified",
  );
});

test("corrupt when manifest contains traversal path", () => {
  const files = pkgFiles();
  const manifest = manifestFor(files);
  manifest.files["../evil.md"] = "deadbeef";
  const { skillDir } = makeInstall({ files, manifest });
  assert.equal(
    inspectInstall({ skillDir, packageFiles: files, packageVersion: "0.7.1" }).state,
    "corrupt",
  );
});

test("diffFiles reports added changed removed", () => {
  const files = pkgFiles();
  const manifest = manifestFor(files);
  manifest.files["references/removed.md"] = "deadbeef";
  const next = new Map([
    ["SKILL.md", Buffer.from("changed")],
    ["references/a.md", Buffer.from("a")],
    ["references/added.md", Buffer.from("new")],
  ]);
  const diff = diffFiles(manifest, next);
  assert.deepEqual(diff.added, ["references/added.md"]);
  assert.deepEqual(diff.changed, ["SKILL.md"]);
  assert.deepEqual(diff.removed, ["references/removed.md"]);
});

test("readManifest reports missing and ok", () => {
  const { skillDir } = makeInstall({ manifest: null });
  assert.equal(readManifest(skillDir).state, "missing");
  const files = pkgFiles();
  const ok = makeInstall({ files, manifest: manifestFor(files) });
  assert.equal(readManifest(ok.skillDir).state, "ok");
});

test("modified when the skill directory is a symlink", { skip: process.platform === "win32" }, () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "dq-mf-link-"));
  const skillDir = path.join(root, "dragon-quant");
  const real = path.join(root, "real");
  fs.mkdirSync(real);
  fs.symlinkSync(real, skillDir);
  assert.equal(
    inspectInstall({ skillDir, packageFiles: pkgFiles(), packageVersion: "0.7.1" }).state,
    "modified",
  );
});
