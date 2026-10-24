import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { sha256, parseFrontmatter, loadSkillPackage } from "../lib/skill-package.mjs";

function fixture() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "dq-pkg-"));
  fs.writeFileSync(path.join(root, "package.json"), JSON.stringify({ version: "0.7.1" }));
  const skill = path.join(root, "skills", "dragon-quant");
  fs.mkdirSync(path.join(skill, "references"), { recursive: true });
  fs.writeFileSync(path.join(skill, "SKILL.md"), [
    "---",
    "name: dragon-quant",
    'description: "Use when scanning dragons, prep, auction, buy or sell."',
    "license: MIT",
    "metadata:",
    '  min-cli-version: "0.7.1"',
    '  skill-version: "0.7.1"',
    "---",
    "# dragon-quant",
  ].join("\n"));
  fs.writeFileSync(path.join(skill, "references", "commands.md"), "# commands\n");
  return root;
}

test("sha256 hashes raw bytes", () => {
  assert.equal(sha256(Buffer.from("abc")), "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
});

test("frontmatter parses name, description and metadata", () => {
  const meta = parseFrontmatter("---\nname: dragon-quant\ndescription: hello\nmetadata:\n  min-cli-version: \"0.7.1\"\n---\n# x\n");
  assert.equal(meta.name, "dragon-quant");
  assert.equal(meta.description, "hello");
  assert.equal(meta.metadata["min-cli-version"], "0.7.1");
});

test("loadSkillPackage returns relative posix paths and buffers", () => {
  const pkg = loadSkillPackage(fixture());
  assert.equal(pkg.packageVersion, "0.7.1");
  assert.equal(pkg.meta.name, "dragon-quant");
  assert.deepEqual([...pkg.files.keys()], ["SKILL.md", "references/commands.md"]);
  assert.equal(pkg.files.get("SKILL.md").toString().includes("dragon-quant"), true);
});

test("loadSkillPackage rejects missing SKILL.md", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "dq-pkg-"));
  fs.writeFileSync(path.join(root, "package.json"), JSON.stringify({ version: "0.7.1" }));
  assert.throws(() => loadSkillPackage(root), /SKILL\.md/);
});

test("loadSkillPackage rejects symlinks inside the skill", { skip: process.platform === "win32" }, () => {
  const root = fixture();
  const skill = path.join(root, "skills", "dragon-quant");
  fs.symlinkSync(path.join(skill, "SKILL.md"), path.join(skill, "references", "link.md"));
  assert.throws(() => loadSkillPackage(root), /symlink/i);
});
