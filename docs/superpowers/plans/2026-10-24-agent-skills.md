# Agent Skills 包 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 dragon-quant CLI 交付一个仓库内置的 Agent Skill（单主 skill + references）与根目录零依赖 npx 安装器，使 agent 能标准化地路由、预检、执行和解读 CLI，同时不复制项目规则与业务逻辑。

**Architecture:** `skills/dragon-quant/SKILL.md` 是薄路由层，细节按需加载 `references/*.md`；根目录 `package.json` 提供 `dragon-quant-skills` 安装器（`bin/` + `lib/`，零运行时依赖），默认复制安装到项目 `.claude/skills`，用 manifest + SHA-256 做幂等/升级/冲突/卸载保护；Python CLI 只被探测和调用，不被安装。

**Tech Stack:** Node.js >= 22.20.0（ESM、`node:test`、`node:util.parseArgs`、`node:crypto`、`node:fs`）、Python >= 3.10 仅作为被调用的 CLI；无 npm 运行时依赖、无 Python 新依赖。

**Spec:** `docs/solutions/2026-10-24-agent-skills-design.md`

## Global Constraints

- npm 包名 `dragon-quant-skills`；首发版本 `0.7.1`；`type: module`；`license: MIT`。
- `engines.node >= 22.20.0`；零 `dependencies` / `devDependencies`；无 `preinstall` / `postinstall`。
- `files` 白名单只含 `bin/`、`lib/`、`skills/`、`README.md`、`LICENSE`。
- 默认 project scope；只有显式 `--global` 才写用户目录；`--target-dir` 表示**项目根**，不是最终 skill 目录。
- 安装器默认复制，不做 symlink；manifest 记录内容 SHA-256 与版本。
- 安装成功不依赖 Python CLI；Python 缺失/版本过低只警告；`doctor` 才失败。
- SKILL 正文中文；`description` 中英文触发词并存；不复制评分权重、策略参数数值、SQLite 表结构。
- `AGENTS.md` 是规则权威，`review_account/STRATEGY.md` 是策略权威；skill 只做路由/预检/解读/异常/安全门禁。
- 不改 Python 业务逻辑；不新增 Python 依赖；不执行 `npm publish`；不 git commit/push/PR（用户自己控制 git）。
- 测试不触发真实 `scan`/`prep`/`auction`/`buy`/`sell` 网络请求，不写业务库。
- 最终实现同 commit 更新 `AGENTS.md` / `README.md` / `CHANGELOG.md`（由用户在阶段 3 后自行提交）。

## Review Focus

- `--target-dir` 被误当成最终 skill 目录：期望 `<DIR>/.claude/skills/dragon-quant`，不是 `<DIR>/dragon-quant`。Task 2 的路径测试钉住。
- 用户手动改过 SKILL.md 后执行 install/uninstall：期望拒绝并保留用户文件；`--force` 也必须先备份。Task 5/6 钉住。
- global 与 project 同时安装同名 skill：期望 doctor/install 报告遮蔽关系，不静默让旧版本生效。Task 8/9 钉住。
- manifest 在 rename 后写失败会留下“有文件无 manifest”的 foreign 目录：期望 manifest 先写入 staging 再整体 rename，失败不留下目标。Task 5 钉住。
- 路由表把 `storage clear`/`logs clear` 等混合子命令整行标成 T0：期望每行恰好一个 write_class，安全矩阵做集合相等校验。Task 10/12 钉住。
- Python CLI 缺失或 PATH 上版本过旧：期望 install 仍 exit 0 并提示，doctor exit 1 并给修复命令。Task 7/8 钉住。
- 手工复制或 `npx skills` 安装的同名目录：期望识别为 `foreign`，不静默覆盖；卸载不删除用户文件。Task 4/6 钉住。
- skill 触发过宽/过窄或要求读完整 AGENTS：期望 6 条独立 agent smoke 通过，且结构测试禁止权重/参数/表结构复制。Task 10–13 钉住。

---

### Task 0: 基线行为（RED，先看 agent 没有 skill 时怎么错）

**Files:**
- No repo files. Evidence goes into the stage-3 delivery report.

**Interfaces:**
- Consumes: installed `dragon-quant` CLI in the project venv.
- Produces: verbatim baseline failures used to shape SKILL.md.

- [ ] **Step 1: Run baseline scenarios without any skill**

Use an independent agent (fresh context, no `skills/` directory installed) inside an isolated sandbox: a temporary clone of the repo (or `git worktree`) plus `DQ_DATA_DIR=<tempdir>` and no real credential files. Prefix every prompt with: “只输出你会执行的命令和理由，不要实际执行任何命令；不得读整篇 AGENTS.md；不得写数据库。”

Give it the repo path and these prompts, one session each:

1. “今天有哪些真龙？我该怎么扫？”
2. “现在 15:30，收盘后帮我复盘一下。”
3. “把今天的竞价结果解读一下。”
4. “这些持仓今天要不要卖？”
5. “把数据推到云端。”
6. “清一下信号库和存储。”

For each, record verbatim: did it read all of `AGENTS.md`? did it invent commands/JSON fields? did it treat `scan`/`buy` as read-only? did it ask for confirmation before writes? did it misread `unverified`/`data_quality`? did it attempt to execute a command despite the instruction?

- [ ] **Step 2: Classify baseline failures**

Group into: routing miss, missing preflight, output hallucination, unsafe write, AGENTS over-read, attempted execution. Keep the exact quotes; Task 10–13 must address them.

- [ ] **Step 3: Do not write SKILL.md yet**

The skill content tasks come after the structural tests and after the baseline is recorded. No production skill file in Task 0.

---

### Task 1: npm package metadata and Python sdist exclusion

**Files:**
- Create: `package.json`
- Create: `MANIFEST.in`
- Modify: `.gitignore`
- Test: `tests/test_skill_package.py`

**Interfaces:**
- Consumes: `pyproject.toml` version `0.7.1`.
- Produces: package metadata consumed by `lib/skill-package.mjs` and `npm pack`.

- [ ] **Step 1: Write the failing metadata test**

Create `tests/test_skill_package.py`:

```python
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _npm_version() -> str:
    return json.loads((ROOT / "package.json").read_text(encoding="utf-8"))["version"]


def _py_version() -> str:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    assert match, "version not found in pyproject.toml"
    return match.group(1)


class TestPackageMetadata(unittest.TestCase):

    def test_package_json_contract(self):
        data = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
        self.assertEqual(data["name"], "dragon-quant-skills")
        self.assertEqual(data["type"], "module")
        self.assertEqual(data["license"], "MIT")
        self.assertEqual(data["engines"]["node"], ">=22.20.0")
        self.assertEqual(data["bin"], {"dragon-quant-skills": "bin/dragon-quant-skills.mjs"})
        self.assertEqual(data["files"], ["bin/", "lib/", "skills/", "README.md", "LICENSE"])
        self.assertNotIn("dependencies", data)
        self.assertNotIn("devDependencies", data)
        for key in ("preinstall", "postinstall", "prepare"):
            self.assertNotIn(key, data.get("scripts", {}))

    def test_npm_minor_matches_python_minor(self):
        npm = _npm_version().split(".")
        py = _py_version().split(".")
        self.assertEqual(npm[:2], py[:2])

    def test_manifest_in_excludes_node_artifacts(self):
        text = (ROOT / "MANIFEST.in").read_text(encoding="utf-8")
        for token in ("exclude package.json", "prune bin", "prune lib",
                      "prune skills", "prune test", "prune node_modules"):
            self.assertIn(token, text)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_skill_package.py -v`
Expected: FAIL with `FileNotFoundError: package.json` or `MANIFEST.in`.

- [ ] **Step 3: Create `package.json`**

```json
{
  "name": "dragon-quant-skills",
  "version": "0.7.1",
  "description": "Agent Skills for the dragon-quant A-share dragon scanner CLI",
  "type": "module",
  "license": "MIT",
  "bin": { "dragon-quant-skills": "bin/dragon-quant-skills.mjs" },
  "files": ["bin/", "lib/", "skills/", "README.md", "LICENSE"],
  "engines": { "node": ">=22.20.0" },
  "scripts": { "test": "node --test" },
  "keywords": ["agent-skills", "dragon-quant", "a-share", "cli"],
  "repository": { "type": "git", "url": "https://github.com/gitBingxu/dragon-quant.git" }
}
```

- [ ] **Step 4: Create `MANIFEST.in`**

```text
exclude package.json
prune bin
prune lib
prune skills
prune test
prune node_modules
```

- [ ] **Step 5: Update `.gitignore`**

Append:

```text
# Node / npm skill package
node_modules/
*.tgz
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_skill_package.py -v`
Expected: PASS (3 tests).

---

### Task 2: Agent/scope path resolution

**Files:**
- Create: `lib/paths.mjs`
- Test: `test/paths.test.mjs`

**Interfaces:**
- Consumes: `cwd`, `home`, `agent`, `scope`, optional `targetDir`.
- Produces:
  - `AGENT_TARGETS` object: `{ "claude-code": {projectRel, globalRel}, "agents": {projectRel, globalRel} }`
  - `expandAgents(value: string): string[]`
  - `findProjectRoot(cwd: string): string`
  - `resolveSkillsRoot({agent, scope, projectRoot, home, targetDir}): string`
  - `assertSafeRelPath(rel: string): string`
  - `isInside(root: string, candidate: string): boolean`

- [ ] **Step 1: Write the failing path tests**

Create `test/paths.test.mjs`:

```js
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import {
  expandAgents, findProjectRoot, resolveSkillsRoot,
  assertSafeRelPath, isInside,
} from "../lib/paths.mjs";

test("project claude-code resolves under project root", () => {
  assert.equal(
    resolveSkillsRoot({ agent: "claude-code", scope: "project", projectRoot: "/repo" }),
    path.join("/repo", ".claude", "skills"),
  );
});

test("project agents resolves under .agents/skills", () => {
  assert.equal(
    resolveSkillsRoot({ agent: "agents", scope: "project", projectRoot: "/repo" }),
    path.join("/repo", ".agents", "skills"),
  );
});

test("global claude-code resolves under home", () => {
  assert.equal(
    resolveSkillsRoot({ agent: "claude-code", scope: "global", home: "/home/u" }),
    path.join("/home/u", ".claude", "skills"),
  );
});

test("targetDir overrides project root, not the final skill dir", () => {
  assert.equal(
    resolveSkillsRoot({ agent: "claude-code", scope: "project", targetDir: "/tmp/p" }),
    path.join("/tmp/p", ".claude", "skills"),
  );
});

test("global plus targetDir is rejected", () => {
  assert.throws(
    () => resolveSkillsRoot({ agent: "claude-code", scope: "global", home: "/h", targetDir: "/tmp/p" }),
    /target-dir/,
  );
});

test("findProjectRoot walks up to the .git directory", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "dq-root-"));
  fs.mkdirSync(path.join(root, ".git"));
  const nested = path.join(root, "a", "b");
  fs.mkdirSync(nested, { recursive: true });
  assert.equal(findProjectRoot(nested), root);
});

test("findProjectRoot falls back to cwd without git", () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "dq-nogit-"));
  assert.equal(findProjectRoot(dir), dir);
});

test("expandAgents expands all and dedupes", () => {
  assert.deepEqual(expandAgents("all"), ["claude-code", "agents"]);
  assert.deepEqual(expandAgents("claude-code,agents,claude-code"), ["claude-code", "agents"]);
  assert.throws(() => expandAgents("cursor"), /unknown agent/i);
});

test("safe relative paths reject traversal and absolute paths", () => {
  assert.equal(assertSafeRelPath("references/commands.md"), "references/commands.md");
  for (const bad of ["../x", "/x", "a/../../b", "C:\\x", "a\0b", "references//x"]) {
    assert.throws(() => assertSafeRelPath(bad), /unsafe|invalid/i, bad);
  }
});

test("isInside does not accept sibling prefixes", () => {
  assert.equal(isInside("/repo/skills", "/repo/skills/a"), true);
  assert.equal(isInside("/repo/skills", "/repo/skills-evil/a"), false);
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `node --test test/paths.test.mjs`
Expected: FAIL with `Cannot find module '../lib/paths.mjs'`.

- [ ] **Step 3: Implement `lib/paths.mjs`**

Use only `node:fs`, `node:path`, `node:os`. `findProjectRoot` checks for a `.git` directory or file while walking up; no git subprocess. `assertSafeRelPath` normalizes POSIX separators, rejects absolute paths, `..`, NUL, empty segments, Windows drive letters, and reserved device names (`CON`, `PRN`, `AUX`, `NUL`, `COM1`–`COM9`, `LPT1`–`LPT9`). `isInside` compares with `path.relative` and rejects `..` / absolute results.

- [ ] **Step 4: Run to verify it passes**

Run: `node --test test/paths.test.mjs`
Expected: PASS (10 tests).

---

### Task 3: Load and validate the shipped skill package

**Files:**
- Create: `lib/skill-package.mjs`
- Test: `test/skill-package.test.mjs`

**Interfaces:**
- Consumes: `packageRoot` (directory containing `package.json`).
- Produces:
  - `sha256(buf: Buffer): string`
  - `parseFrontmatter(text: string): {name, description, license, compatibility, metadata}`
  - `loadSkillPackage(packageRoot: string): {packageVersion, meta, files: Map<string, Buffer>, relativePaths: string[]}`

- [ ] **Step 1: Write the failing package-loading tests**

Create `test/skill-package.test.mjs`:

```js
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `node --test test/skill-package.test.mjs`
Expected: FAIL with `Cannot find module '../lib/skill-package.mjs'`.

- [ ] **Step 3: Implement `lib/skill-package.mjs`**

`parseFrontmatter` reads only the leading `---` block; supports `key: value`, quoted values, and one nested `metadata:` map with two-space indentation. `loadSkillPackage` recursively walks `skills/dragon-quant`, uses `lstatSync` and rejects symlinks/special files, sorts paths for deterministic output, requires `SKILL.md`, validates `meta.name === "dragon-quant"`, and returns file keys as POSIX relative paths.

- [ ] **Step 4: Run to verify it passes**

Run: `node --test test/skill-package.test.mjs`
Expected: PASS (5 tests).

---

### Task 4: Manifest and six-state install classification

**Files:**
- Create: `lib/manifest.mjs`
- Test: `test/manifest.test.mjs`

**Interfaces:**
- Consumes: skill directory contents, package file map.
- Produces:
  - `MANIFEST_NAME = ".dragon-quant-skill.json"`
  - `MANIFEST_SCHEMA = 1`
  - `buildManifest({files, packageVersion, meta, agent, scope, source, now}): object`
  - `readManifest(skillDir): {state: "missing"|"ok"|"corrupt", manifest?, reason?}`
  - `inspectInstall({skillDir, packageFiles, packageVersion}): {state, reason?, manifest?, entries}`
  - `diffFiles(manifest, packageFiles): {added, changed, removed}`

State values: `absent`, `foreign`, `corrupt`, `clean`, `modified`, `up-to-date`.

- [ ] **Step 1: Write the failing manifest tests**

Create `test/manifest.test.mjs` with a helper `makeInstall({files, manifest})` that writes a temp skill dir. Tests:

```js
test("absent when skill dir missing", ...);
test("foreign when dir exists without manifest", ...);
test("corrupt when manifest is not valid JSON", ...);
test("clean when disk hashes match manifest", ...);
test("up-to-date when manifest and package files/version match", ...);
test("modified when a file hash changed", ...);
test("modified when an extra file exists", ...);
test("modified when a manifest file is missing", ...);
test("corrupt when manifest contains traversal path", ...);
test("diffFiles reports added changed removed", ...);
```

Exact assertions:

```js
const pkg = new Map([["SKILL.md", Buffer.from("new")], ["references/a.md", Buffer.from("a")]]);
assert.equal(inspectInstall({ skillDir: absentDir, packageFiles: pkg, packageVersion: "0.7.1" }).state, "absent");
assert.equal(inspectInstall({ skillDir: foreignDir, packageFiles: pkg, packageVersion: "0.7.1" }).state, "foreign");
```

- [ ] **Step 2: Run to verify it fails**

Run: `node --test test/manifest.test.mjs`
Expected: FAIL with `Cannot find module '../lib/manifest.mjs'`.

- [ ] **Step 3: Implement `lib/manifest.mjs`**

`inspectInstall` order: directory missing → `absent`; manifest missing → `foreign`; JSON/schema/path invalid → `corrupt`; any symlink/missing/hash mismatch/extra file → `modified`; all match and source version/files match package → `up-to-date`; otherwise `clean`. Extra files include anything not in `manifest.files`, except `MANIFEST_NAME` itself. Hash files as raw bytes.

- [ ] **Step 4: Run to verify it passes**

Run: `node --test test/manifest.test.mjs`
Expected: PASS (10 tests).

---

### Task 5: Install, no-op, upgrade, conflict, force backup

**Files:**
- Create: `lib/install.mjs`
- Test: `test/install.test.mjs`

**Interfaces:**
- Consumes: `inspectInstall`, `buildManifest`, `assertSafeRelPath`.
- Produces:
  - `class InstallerError extends Error { code: "conflict"|"integrity"|"unsafe-path"|"io" }`
  - `install({skillDir, skillsRoot, packageFiles, packageVersion, meta, agent, scope, source, force=false, dryRun=false, now=new Date()}): Report`
  - `Report = {ok: true, state: "installed"|"up-to-date"|"upgraded"|"forced", skillDir, backupPath: string|null, actions: string[], warnings: string[]}`

- [ ] **Step 1: Write the failing install tests**

Create `test/install.test.mjs` with `pkgFiles()` returning `new Map([["SKILL.md", Buffer.from("v1")], ["references/a.md", Buffer.from("a")]])` and a temp `skillsRoot`. Tests:

```js
test("fresh install writes files and manifest", ...);
test("second install is up-to-date and does not create a backup", ...);
test("clean upgrade replaces files and removes obsolete ones", ...);
test("modified file causes conflict without force", ...);
test("force install backs up user-modified directory", ...);
test("foreign directory causes conflict without force", ...);
test("dry-run writes nothing", ...);
test("install refuses a symlinked skill directory", { skip: ... }, ...);
test("install refuses a symlinked references dir", { skip: ... }, ...);
test("manifest is staged before rename and contains all hashes", ...);
test("staging directory is removed on success", ...);
test("fresh install leaves no target when staging fails", ...);
```

Expected conflict:
```js
assert.throws(
  () => install({...args, force: false}),
  (err) => err instanceof InstallerError && err.code === "conflict",
);
```

- [ ] **Step 2: Run to verify it fails**

Run: `node --test test/install.test.mjs`
Expected: FAIL with `Cannot find module '../lib/install.mjs'`.

- [ ] **Step 3: Implement `lib/install.mjs`**

Fresh: create `skillsRoot`, stage under it, write files with mode `0o644`, **write the manifest into staging**, then rename the whole staging directory into place (single atomic swap; never write manifest after rename). Upgrade: stage new files + new manifest, rename old skill dir to `<skillsRoot>/.dragon-quant-backup-<UTC timestamp>-<rand>`, rename staging into place, remove backup on success; on failure restore backup. `--force` uses the same backup path for foreign/modified/corrupt. `dryRun` returns planned actions without touching disk and reports the absolute final `skillDir`. Never follow symlinks; use `lstatSync` on the skill dir and every nested path (including `references/`).

- [ ] **Step 4: Run to verify it passes**

Run: `node --test test/install.test.mjs`
Expected: PASS (12 tests).

---

### Task 6: Safe uninstall

**Files:**
- Create: `lib/uninstall.mjs`
- Test: `test/uninstall.test.mjs`

**Interfaces:**
- Consumes: `inspectInstall`-like manifest inspection.
- Produces:
  - `uninstall({skillDir, skillsRoot, force=false, dryRun=false, now=new Date()}): {ok: true, state: "not-installed"|"removed"|"backed-up", backupPath: string|null, actions: string[]}`

- [ ] **Step 1: Write the failing uninstall tests**

Create `test/uninstall.test.mjs`. Tests:

```js
test("missing install returns not-installed and writes nothing", ...);
test("clean uninstall removes only manifest files and manifest", ...);
test("clean uninstall keeps the parent skills root", ...);
test("modified file causes conflict without force", ...);
test("force uninstall moves the whole skill dir to backup", ...);
test("foreign directory is refused without force", ...);
test("force uninstall of foreign directory moves it to backup", ...);
test("user-added file is preserved by force backup", ...);
test("symlink inside skill dir is refused", { skip: ... }, ...);
test("references dir symlink is refused", { skip: ... }, ...);
```

- [ ] **Step 2: Run to verify it fails**

Run: `node --test test/uninstall.test.mjs`
Expected: FAIL with `Cannot find module '../lib/uninstall.mjs'`.

- [ ] **Step 3: Implement `lib/uninstall.mjs`**

Clean path: preflight all manifest entries, require disk hash match and no extra files; delete only listed files, remove empty `references/`, then remove manifest and skill dir; never remove `skillsRoot`. Modified/foreign without force → `InstallerError("conflict")`. With force: rename whole skill dir to `<skillsRoot>/.dragon-quant-uninstalled-<timestamp>-<rand>`; never `rm -rf` user content.

- [ ] **Step 4: Run to verify it passes**

Run: `node --test test/uninstall.test.mjs`
Expected: PASS (10 tests).

---

### Task 7: Python CLI detection without installing anything

**Files:**
- Create: `lib/python.mjs`
- Test: `test/python.test.mjs`

**Interfaces:**
- Consumes: optional `--python` path, `DQ_PYTHON`, PATH.
- Produces:
  - `parseCliVersion(text: string): string|null`
  - `compareVersions(a: string, b: string): number`
  - `detectCli({pythonArg, env, exec, timeoutMs=5000}): {found: boolean, command: string|null, version: string|null, source: string|null, error: string|null}`
  - `meetsMinVersion(version: string|null, minVersion: string): boolean`

- [ ] **Step 1: Write the failing detection tests**

Create `test/python.test.mjs` with an injected `exec` that records `(file, args)` and returns configured stdout:

```js
test("explicit --python wins", ...);
test("DQ_PYTHON is used when no --python", ...);
test("falls back to dragon-quant on PATH", ...);
test("falls back to python3 -m dragon_quant", ...);
test("falls back to python -m dragon_quant", ...);
test("missing CLI returns found false without throwing", ...);
test("old version is detected but meetsMinVersion is false", ...);
test("parseCliVersion accepts stdout and stderr style output", ...);
test("compareVersions orders 0.7.0 < 0.7.1 < 0.8.0", ...);
```

Expected precedence assertion:

```js
const calls = [];
const exec = (file, args) => { calls.push([file, ...args]); return "dragon-quant 0.7.1\n"; };
detectCli({ pythonArg: "/venv/bin/python", env: { DQ_PYTHON: "/other/bin/python" }, exec });
assert.deepEqual(calls[0], ["/venv/bin/python", "-m", "dragon_quant", "-v"]);
```

- [ ] **Step 2: Run to verify it fails**

Run: `node --test test/python.test.mjs`
Expected: FAIL with `Cannot find module '../lib/python.mjs'`.

- [ ] **Step 3: Implement `lib/python.mjs`**

Use `node:child_process.execFileSync` with `{encoding: "utf8", timeout: timeoutMs, stdio: ["ignore", "pipe", "pipe"]}`. Try each candidate; combine stdout/stderr; parse `dragon-quant\s+(\d+\.\d+\.\d+)`. Return first success; if all fail, return `found:false` with the last error. Never call pip/npm.

- [ ] **Step 4: Run to verify it passes**

Run: `node --test test/python.test.mjs`
Expected: PASS (9 tests).

---

### Task 8: Doctor checks

**Files:**
- Create: `lib/doctor.mjs`
- Test: `test/doctor.test.mjs`

**Interfaces:**
- Consumes: target list `[{agent, scope, skillDir}]`, package files, `detectCli`.
- Produces:
  - `runDoctor({targets, packageFiles, packageVersion, meta, detect}): {ok, checks, installs}`
  - `checks[] = {id, status: "pass"|"warn"|"fail", detail, fix}`
  - `installs[] = {agent, scope, skillDir, state, packageVersion?, skillVersion?}`

Checks: `install.present`, `install.integrity`, `cli.present`, `cli.version`, `scope.shadowing`, `foreign.lockfile`, `reload.hint`.

- [ ] **Step 1: Write the failing doctor tests**

Create `test/doctor.test.mjs`. Use fake `detect` and temp installs:

```js
test("ok when install clean and CLI meets min", ...);
test("fail when install missing", ...);
test("fail when install modified", ...);
test("fail when CLI missing", ...);
test("fail when CLI below min", ...);
test("warn when global and project both install the skill", ...);
test("warn when project skills-lock.json exists", ...);
test("report includes reload hint", ...);
test("json shape is stable", ...);
```

- [ ] **Step 2: Run to verify it fails**

Run: `node --test test/doctor.test.mjs`
Expected: FAIL with `Cannot find module '../lib/doctor.mjs'`.

- [ ] **Step 3: Implement `lib/doctor.mjs`**

For each target, call `inspectInstall`; collect a single `install.present`/`install.integrity` per agent/scope (fail if missing or not clean). Call `detect` once. `ok = checks.every(c => c.status !== "fail")`. Shadowing: if >1 install for the same agent, warn with path/version and note Claude Code precedence `Enterprise > personal > project`. Foreign lock detection: look for `skills-lock.json` in project root and `.skill-lock.json` under `~/.agents`; warn only.

- [ ] **Step 4: Run to verify it passes**

Run: `node --test test/doctor.test.mjs`
Expected: PASS (9 tests).

---

### Task 9: Installer CLI entrypoint

**Files:**
- Create: `bin/dragon-quant-skills.mjs`
- Test: `test/cli.test.mjs`

**Interfaces:**
- Consumes: `paths.mjs`, `skill-package.mjs`, `install.mjs`, `uninstall.mjs`, `doctor.mjs`, `python.mjs`.
- Produces: executable `dragon-quant-skills`, plus `main(argv: string[], deps?): Promise<number>` for tests.

Commands: `install`, `doctor`, `uninstall`, `version`.
Options: `--project`, `--global`, `--agent`, `--target-dir`, `--python`, `--dry-run`, `--force`, `--json`, `-h/--help`, `-v/--version`.

- [ ] **Step 1: Write the failing CLI tests**

Create `test/cli.test.mjs` spawning `process.execPath` with the bin path and a temp cwd/home. Tests:

```js
test("version prints package version", ...);
test("install defaults to project scope", ...);
test("install --global writes under isolated HOME", ...);
test("install --target-dir treats DIR as project root", ...);
test("install --dry-run does not create files", ...);
test("install --dry-run reports the absolute final path", ...);
test("install warns when another scope has the same skill", ...);
test("second install exits 0 and reports up-to-date", ...);
test("modified install exits 1 with conflict", ...);
test("install/doctor --json emit stable top-level keys", ...);
test("unknown agent exits 2", ...);
test("global plus target-dir exits 2", ...);
test("uninstall removes clean install", ...);
test("doctor exits 1 when Python CLI is missing", ...);
```

Use `spawnSync(process.execPath, [bin, ...args], {cwd, env: {...process.env, HOME: tempHome}, encoding: "utf8"})`. For missing Python, pass `--python /nonexistent/python` and isolate `PATH` to a temp dir.

- [ ] **Step 2: Run to verify it fails**

Run: `node --test test/cli.test.mjs`
Expected: FAIL because `bin/dragon-quant-skills.mjs` does not exist.

- [ ] **Step 3: Implement the CLI**

Use `node:util.parseArgs` with `allowPositionals: true`. Before dispatch, compare `process.versions.node` with `22.20.0`; below the floor print an upgrade message and return 2. Resolve scope (default `project`), expand agents, resolve project root, compute skill dirs, load package, and dispatch. **Cross-scope preflight:** before `install`, inspect the same agent/skill directory in the other scope (global when installing project, project when installing global) and append a warning to the install report when it exists; do not modify/delete it and do not change exit code. `--dry-run` must include the absolute resolved `agents[].path`. Human output in Chinese; `--json` prints one JSON object to stdout with `{ok, command, scope, agents:[{agent,path,state,actions,warnings}], warnings, backup_path}`; errors print `{ok:false, command, error:{code,message,hint}}`; doctor prints `{ok, command, checks, installs}`. Exit codes: 0 success/no-op/not-installed, 1 conflict/doctor failure/integrity, 2 usage. On missing CLI, install prints the repair commands and still returns 0.

- [ ] **Step 4: Run to verify it passes**

Run: `node --test test/cli.test.mjs`
Expected: PASS (14 tests).

---

### Task 10: SKILL.md core + commands route table

**Files:**
- Create: `skills/dragon-quant/SKILL.md`
- Create: `skills/dragon-quant/references/commands.md`
- Test: `tests/test_skill_content.py` (create)
- Test: `tests/test_skill_routes.py` (create)

**Interfaces:**
- Consumes: baseline failures from Task 0; CLI help from `dragon_quant.cli`.
- Produces: skill trigger surface + route table used by later references.

**SKILL.md required sections (exact headings):**

```markdown
# dragon-quant
## 权威顺序
## 执行前检查
## 意图路由
## 输出翻译
## 写操作门禁
## 运维契约
## 异常分流
## Reference 索引
## 禁止事项
```

Frontmatter requirements:

```yaml
---
name: dragon-quant
description: Use when the user asks about A-share dragon scanning, 真龙/龙头扫榜, 盘后复盘 prep, 集合竞价 auction, 回测 review, 买卖信号 buy/sell, 数据资产同步, 黑名单, or dragon-quant CLI usage. Do not use for generic stock picking or investment advice.
license: MIT
compatibility: Requires dragon-quant CLI >=0.7.1 (Python >=3.10). Node >=22.20 is only needed for installation.
metadata:
  skill-version: "0.7.1"
  min-cli-version: "0.7.1"
---
```

- [ ] **Step 1: Write the failing content tests**

Create `tests/test_skill_content.py`:

```python
import json
import re
import unittest
from dataclasses import fields
from pathlib import Path

from dragon_quant.review_account.models import StrategyConfig

ROOT = Path(__file__).resolve().parents[1] / "skills" / "dragon-quant"
SKILL = ROOT / "SKILL.md"
REFS = ["commands.md", "outputs.md", "playbooks.md", "operations.md", "safety.md"]
STRATEGY_FIELDS = [f.name for f in fields(StrategyConfig) if f.name != "source"]


def _frontmatter() -> str:
    return SKILL.read_text(encoding="utf-8").split("---\n", 2)[1]


class TestSkillContent(unittest.TestCase):

    def test_skill_exists_with_frontmatter(self):
        text = SKILL.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("---\n"))
        front = _frontmatter()
        self.assertIn("name: dragon-quant", front)
        self.assertIn("description:", front)
        self.assertIn("license: MIT", front)
        self.assertIn("min-cli-version:", front)
        self.assertIn("skill-version:", front)

    def test_skill_version_matches_package_version(self):
        package = json.loads(
            (ROOT.parents[1] / "package.json").read_text(encoding="utf-8")
        )
        front = _frontmatter()
        skill_version = re.search(r'skill-version:\s*"([^"]+)"', front).group(1)
        min_cli = re.search(r'min-cli-version:\s*"([^"]+)"', front).group(1)
        self.assertEqual(skill_version, package["version"])
        self.assertLessEqual([int(x) for x in min_cli.split(".")],
                             [int(x) for x in skill_version.split(".")])

    def test_description_has_chinese_and_english_triggers(self):
        text = SKILL.read_text(encoding="utf-8")
        for token in ("真龙", "龙头", "复盘", "竞价", "回测", "scan", "prep", "auction", "buy", "sell"):
            self.assertIn(token, text)

    def test_authority_and_no_advice(self):
        text = SKILL.read_text(encoding="utf-8")
        self.assertIn("AGENTS.md", text)
        self.assertIn("STRATEGY.md", text)
        self.assertIn("权威", text)
        self.assertIn("不提供投资建议", text)

    def test_scan_closed_book_requirements(self):
        text = SKILL.read_text(encoding="utf-8")
        for token in ("执行前检查", "scan", "dragons", "确认"):
            self.assertIn(token, text)

    def test_skill_size_cap(self):
        self.assertLessEqual(len(SKILL.read_text(encoding="utf-8").splitlines()), 300)

    def test_forbidden_content_boundaries_in_existing_files(self):
        paths = [SKILL] + sorted((ROOT / "references").glob("*.md"))
        blob = "\n".join(path.read_text(encoding="utf-8") for path in paths)
        forbidden = [
            r"带动性\s*[:：]?\s*\d+\s*%",
            r"领涨性\s*[:：]?\s*\d+\s*%",
            r"抗跌性\s*[:：]?\s*\d+\s*%",
            r"流动性\s*[:：]?\s*\d+\s*%",
            r"资金承接\s*[:：]?\s*\d+\s*%",
            r"CREATE\s+TABLE",
            r"PRIMARY\s+KEY",
            r"FOREIGN\s+KEY",
            r"xq_a_token\s*=",
        ]
        for pattern in forbidden:
            self.assertIsNone(re.search(pattern, blob, re.IGNORECASE), pattern)
        for name in STRATEGY_FIELDS:
            self.assertIsNone(
                re.search(rf"\b{re.escape(name)}\b", blob),
                f"strategy field copied into skill: {name}",
            )
```

Create `tests/test_skill_routes.py`:

```python
import io
import re
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from dragon_quant import cli
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "skills" / "dragon-quant"


def _help(*args):
    buf = io.StringIO()
    with patch("sys.argv", ["dragon-quant", *args, "-h"]):
        try:
            with redirect_stdout(buf):
                cli.main()
        except SystemExit as exc:
            assert exc.code == 0
    return buf.getvalue()


def _table(text, start, end):
    block = text.split(start, 1)[1].split(end, 1)[0]
    rows = []
    for line in block.splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if all(set(cell) <= {"-", ":"} for cell in cells):
            continue
        rows.append(cells)
    if not rows:
        return []
    header = rows[0]
    return [dict(zip(header, row)) for row in rows[1:]]


def _routes():
    text = (ROOT / "references" / "commands.md").read_text(encoding="utf-8")
    return _table(text, "<!-- routes:start -->", "<!-- routes:end -->")


class TestSkillRoutes(unittest.TestCase):

    def test_routes_parse_non_empty(self):
        self.assertGreaterEqual(len(_routes()), 10)

    def test_top_level_commands_exist(self):
        top = _help()
        for route in _routes():
            command = route["command"].split()[1]
            self.assertIn(command, top, command)

    def test_key_flags_exist(self):
        for route in _routes():
            parts = route["command"].split()
            help_args = [p for p in parts[1:] if not p.startswith("--")]
            help_text = _help(*help_args)
            for flag in route["key_flags"].split():
                if flag.startswith("--"):
                    self.assertIn(flag, help_text, f"{' '.join(help_args)} {flag}")

    def test_each_route_has_one_write_class(self):
        for route in _routes():
            self.assertIn(route["write_class"], {"T0", "T1", "T2"}, route["command"])
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_skill_content.py tests/test_skill_routes.py -v`
Expected: FAIL because `skills/dragon-quant/SKILL.md` does not exist.

- [ ] **Step 3: Write `SKILL.md` and `commands.md`**

`SKILL.md`: 150–250 lines, Chinese prose, all required headings. Preflight must list: CLI/version, Cookie (`data cookie-status` only when the path needs Xueqiu), trading day/time gates (defer to CLI errors; prep >=16:40, auction >=09:25, buy/sell session or historical `--date --at`), data readiness (`storage status`, output fields), output mode (`--json` availability). Routing table links `references/commands.md`; include `参数以 dragon-quant <cmd> -h 为准`. Write gate: T0 free; T1 confirm; T2 dry-run + second confirmation.

`commands.md`: intro + the exact `<!-- routes:start -->` table from the design doc + short “common mistakes” bullets (`scan` realtime has no JSON; `sell` has no JSON; `scan --date` only accepts YYYYMMDD and a bad format prints to stderr but exits 0; `scan --date` error JSON; `auction` time gate; `assets pull` dry-run default). Keep under 300 lines.

- [ ] **Step 4: Run both tests**

Run: `.venv/bin/python -m pytest tests/test_skill_content.py tests/test_skill_routes.py -v`
Expected: PASS. The safety-matrix set-equality test is added in Task 12 together with `safety.md`, so Task 10 stays independently green.

---

### Task 11: Output interpretation and playbooks

**Files:**
- Create: `skills/dragon-quant/references/outputs.md`
- Create: `skills/dragon-quant/references/playbooks.md`
- Modify: `tests/test_skill_content.py` (add token assertions)

**Interfaces:**
- Consumes: CLI contracts verified in design §1.1/§5.5.
- Produces: output/error interpretation used by SKILL.md.

- [ ] **Step 1: Add failing token tests**

Append to `TestSkillContent`:

```python
    def test_outputs_cover_key_contracts(self):
        text = (ROOT / "references" / "outputs.md").read_text(encoding="utf-8")
        for token in ("ranking_mode", "fallback_reason", "data_quality", "strict_5min",
                      "daily_fallback", "unverified", "is_weak", "checks",
                      "resolved_versions", "insufficient_data", "available_top_n"):
            self.assertIn(token, text)

    def test_playbooks_cover_failure_modes(self):
        text = (ROOT / "references" / "playbooks.md").read_text(encoding="utf-8")
        for token in ("Cookie", "403", "停牌", "daily_fallback", "空池",
                      "fallback_reason", "版本漂移", "16:40", "09:30"):
            self.assertIn(token, text)

    def test_new_reference_size_caps(self):
        for name in ("outputs.md", "playbooks.md"):
            lines = (ROOT / "references" / name).read_text(encoding="utf-8").splitlines()
            self.assertLessEqual(len(lines), 300, name)
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_skill_content.py -v`
Expected: FAIL with `FileNotFoundError: outputs.md` / `playbooks.md`.

- [ ] **Step 3: Write `outputs.md`**

Sections: `prep` 默认四行摘要/标的清单、`prep --json` 顶层、`auction --json` 顶层 + `snapshots[]`、`buy --json` 顶层 + `checks` 状态枚举、`sell` 人读、`review-account` 数据质量、`dragons`/`scan --date`（仅 YYYYMMDD；格式错只写 stderr 但 exit 0，不能只看退出码判断成功）、`assets`、`factor`、`storage`。每条写“看到什么 → 怎么向用户解释”。不写完整 schema，不写数值阈值。

- [ ] **Step 4: Write `playbooks.md`**

Sections: Cookie 缺失/失效、THS 403、TDX/数据源回退、非交易日/时段门禁、停牌/日K兜底、空池/快照缺失/回退、版本漂移、因子缺失、资产同步失败。每节格式：症状 / 诊断命令 / 处理 / 停止条件 / 禁止动作。

- [ ] **Step 5: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_skill_content.py -v`
Expected: PASS.

---

### Task 12: Operations and safety matrix

**Files:**
- Create: `skills/dragon-quant/references/operations.md`
- Create: `skills/dragon-quant/references/safety.md`
- Test: `tests/test_skill_routes.py` (safety completeness already added in Task 10)

**Interfaces:**
- Consumes: command tree and write classification from design §5.6.
- Produces: daily operations contract + full T0/T1/T2 matrix.

- [ ] **Step 1: Add failing operations token test**

Append to `TestSkillContent`:

```python
    def test_operations_cover_contracts(self):
        text = (ROOT / "references" / "operations.md").read_text(encoding="utf-8")
        for token in ("T+1", "--date", "--at", "峰值", "并发", "assets push", "dry-run", "16:40"):
            self.assertIn(token, text)

    def test_safety_has_tiers_and_forbidden_ops(self):
        text = (ROOT / "references" / "safety.md").read_text(encoding="utf-8")
        for token in ("T0", "T1", "T2", "buy", "sell", "storage clear",
                      "assets push", "pull --apply", "backfill-dragons --prune",
                      "pull --force", "确认"):
            self.assertIn(token, text)

    def test_all_reference_links_exist(self):
        text = SKILL.read_text(encoding="utf-8")
        for name in REFS:
            self.assertIn(f"references/{name}", text)
            self.assertTrue((ROOT / "references" / name).exists(), name)

    def test_all_reference_size_caps(self):
        total = 0
        for name in REFS:
            lines = (ROOT / "references" / name).read_text(encoding="utf-8").splitlines()
            self.assertLessEqual(len(lines), 300, name)
            total += len(lines)
        self.assertLessEqual(total, 1200)
```

Add to `TestSkillRoutes`:

```python
    def test_top_level_commands_covered_by_safety(self):
        top = _help()
        commands = set(re.findall(r"^\s{2,}([a-z][a-z0-9-]+)\s{2,}", top, re.MULTILINE))
        text = (ROOT / "references" / "safety.md").read_text(encoding="utf-8")
        rows = _table(text, "<!-- safety:start -->", "<!-- safety:end -->")
        safety = {row["command"] for row in rows}
        self.assertEqual(commands, safety)
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_skill_content.py -v`
Expected: FAIL with `FileNotFoundError: operations.md` / `safety.md`.

- [ ] **Step 3: Write `operations.md`**

Include: daily order (scan → prep >=16:40 → auction next day >=09:25 → buy >=09:30; sell T+1 at least once), peak freshness (`buy` or `sell` daily), historical replay (`--date` + `--at`, replay writes, idempotent), concurrency (no prep with scan/rebuild; assets lock), data durability (`assets push` after prep/auction, verify, `--target-dir` restore), and never use today's quotes for historical dates.

- [ ] **Step 4: Write `safety.md`**

Include the full §5.6 matrix (all top-level commands and key subcommands), tier definitions, confirmation templates, forbidden operations, secrets handling, and “user’s explicit request in this turn counts as T1 confirmation; T2 still needs dry-run/second confirmation”. Do not add `allowed-tools` preauthorization. Wrap the top-level command table in `<!-- safety:start -->` / `<!-- safety:end -->` with the exact header `| command | flags | tier | notes |`; the `command` column must contain exactly the top-level command names (`scan`, `logs`, `data`, `blacklist`, `review`, `review-account`, `dragons`, `prep`, `auction`, `assets`, `factor`, `buy`, `sell`, `vpa`, `storage`); put `-h/-v`, the hidden `scan_v2` alias, and the installer `install/uninstall` rows in notes outside the table. Split rows whenever subcommands have different tiers (for example `storage status/size` T0 and `storage clear` T2 are separate rows).

- [ ] **Step 5: Run content + route tests**

Run: `.venv/bin/python -m pytest tests/test_skill_content.py tests/test_skill_routes.py -v`
Expected: PASS. If `test_top_level_commands_covered_by_safety` fails, add the missing command to `safety.md`; if a key flag fails, either fix the route row or the flag token (do not delete the test).

---

### Task 13: Independent agent smoke (GREEN + REFACTOR)

**Files:**
- Modify: `skills/dragon-quant/SKILL.md` and references only if smoke finds holes.
- No automated test file.

**Interfaces:**
- Consumes: installed skill from Task 9 + content from Tasks 10–12.
- Produces: evidence that the skill triggers and shapes behavior.

- [ ] **Step 1: Install into an isolated project**

Run:

```bash
tmp=$(mktemp -d)
node bin/dragon-quant-skills.mjs install --project --target-dir "$tmp" --python .venv/bin/python --json
```

Expected: exit 0; `"ok": true`; skill at `$tmp/.claude/skills/dragon-quant/SKILL.md`.

- [ ] **Step 2: Run the six smoke prompts with an independent agent**

Use the same prompts as Task 0, in a fresh agent with `cwd=$tmp`. Require “give the plan/commands only; do not execute network scans or write DB rows”. Check:

- Skill triggers on all six.
- `scan` prompt gets preflight + a confirmation request, not a fake JSON field.
- 15:30 prep prompt recognizes the 16:40 gate.
- auction prompt distinguishes `unverified`/`is_weak`/`pool_source`.
- sell prompt mentions T+1 and confirmation.
- assets push prompt asks for range + dry-run.
- storage clear prompt treats it as destructive and says DBs are protected.

- [ ] **Step 3: Compare with baseline and close holes**

For every baseline failure that still appears, add an explicit counter to the narrowest reference (routing → `commands.md`, output → `outputs.md`, unsafe write → `safety.md`, over-read → `SKILL.md`). Re-run the affected prompt only.

- [ ] **Step 4: Record results**

Put the pass/fail table and verbatim remaining issues in the stage-3 delivery report. Do not commit a report file.

---

### Task 14: Packaging E2E and docs sync

**Files:**
- Create: `test/packaging.test.mjs`
- Modify: `README.md` (installation section + Agent Skill subsection)
- Modify: `AGENTS.md` (new `### Agent Skill 包（npx 安装）` subsection)
- Modify: `CHANGELOG.md` (skill package entry)

**Interfaces:**
- Consumes: complete package + skill.
- Produces: verified tarball + user/agent documentation.

- [ ] **Step 1: Write the failing packaging test**

Create `test/packaging.test.mjs`:

```js
import test from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const ROOT = path.resolve(import.meta.dirname, "..");

function run(cmd, args, opts = {}) {
  return spawnSync(cmd, args, { cwd: ROOT, encoding: "utf8", ...opts });
}

function parseLastJsonObject(text) {
  const start = text.lastIndexOf("\n{");
  return JSON.parse(start >= 0 ? text.slice(start + 1) : text);
}

function runSkillBin(tarball, args, env) {
  return spawnSync(
    "npm",
    ["exec", "--yes", `--package=${tarball}`, "--", "dragon-quant-skills", ...args],
    { cwd: ROOT, env, encoding: "utf8" },
  );
}

test("npm pack contains only the whitelist", () => {
  const outDir = fs.mkdtempSync(path.join(os.tmpdir(), "dq-pack-"));
  const packed = run("npm", ["pack", "--json", "--pack-destination", outDir]);
  assert.equal(packed.status, 0, packed.stderr);
  const info = JSON.parse(packed.stdout)[0];
  const files = info.files.map((f) => f.path);
  for (const file of files) {
    assert.match(file, /^(package\.json|README\.md|LICENSE|bin\/|lib\/|skills\/)/, file);
  }
  for (const forbidden of ["dragon_quant/", "assets/", ".env", "node_modules/", "tests/", "test/", "docs/"]) {
    assert.equal(files.some((f) => f.startsWith(forbidden)), false, forbidden);
  }
});

test("local tarball installs, is idempotent, conflicts and uninstalls", () => {
  const outDir = fs.mkdtempSync(path.join(os.tmpdir(), "dq-pack-e2e-"));
  const packed = run("npm", ["pack", "--json", "--pack-destination", outDir]);
  const tarball = path.join(outDir, JSON.parse(packed.stdout)[0].filename);
  const packageRef = path.relative(ROOT, tarball);
  const project = fs.mkdtempSync(path.join(os.tmpdir(), "dq-project-"));
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "dq-home-"));
  const env = { ...process.env, HOME: home, npm_config_cache: path.join(home, ".npm") };

  const install = (extra = []) => runSkillBin(
    packageRef,
    ["install", "--project", "--target-dir", project, "--json", ...extra],
    env,
  );

  const first = install();
  assert.equal(first.status, 0, first.stderr);
  assert.equal(parseLastJsonObject(first.stdout).ok, true);
  const skill = path.join(project, ".claude", "skills", "dragon-quant");
  assert.equal(fs.existsSync(path.join(skill, "SKILL.md")), true);
  assert.equal(fs.existsSync(path.join(skill, ".dragon-quant-skill.json")), true);

  const second = install();
  assert.equal(second.status, 0);
  assert.equal(parseLastJsonObject(second.stdout).agents[0].state, "up-to-date");

  fs.appendFileSync(path.join(skill, "SKILL.md"), "\nuser edit\n");
  const conflict = install();
  assert.equal(conflict.status, 1);
  assert.equal(parseLastJsonObject(conflict.stdout).error.code, "conflict");

  const uninstall = runSkillBin(
    packageRef,
    ["uninstall", "--project", "--target-dir", project, "--json"],
    env,
  );
  assert.equal(uninstall.status, 1);
  assert.equal(parseLastJsonObject(uninstall.stdout).error.code, "conflict");
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `node --test test/packaging.test.mjs`
Expected: PASS once the package (Tasks 1–9) and skill (Tasks 10–12) are complete. If it fails, fix the package/skill before touching docs; docs changes do not affect tarball contents except the root `README.md`.

- [ ] **Step 3: Update `README.md`**

Add to the installation section:

- Prerequisite split: Python >=3.10 for the CLI; Node >=22.20 only for the installer.
- Primary: `npx dragon-quant-skills install` (project scope) and `npx dragon-quant-skills install --global`.
- `npx dragon-quant-skills doctor` and `uninstall`.
- Compatibility: `npx skills add gitBingxu/dragon-quant --skill dragon-quant -a claude-code` (does not check Python CLI), and manual `cp -r skills/dragon-quant .claude/skills/` (no manifest/upgrade).
- Version relationship: npm minor follows Python minor; patch may differ; `min-cli-version` is the compatibility contract.
- Authority: `AGENTS.md` / `STRATEGY.md` remain authoritative; skill is a thin router.
- Write safety: buy/sell/storage clear/assets push/pull --apply require explicit confirmation.

- [ ] **Step 4: Update `AGENTS.md`**

Add `### Agent Skill 包（npx 安装）` under “开发注意事项” with: repo layout (`skills/`, `bin/`, `lib/`, `test/`), install commands, default project scope, manifest/hash/upgrade/conflict/uninstall rules, version strategy, security boundaries, `npx skills` compatibility caveat, and the rule that skill content must not copy weights/strategy params/schema. Keep it short and link to the design doc.

- [ ] **Step 5: Update `CHANGELOG.md`**

Add an entry for the skill package (no publish claim, no release tag claim).

- [ ] **Step 6: Run packaging test**

Run: `node --test test/packaging.test.mjs`
Expected: PASS.

---

### Task 15: Full verification

**Files:** none (verification only).

- [ ] **Step 1: Run Python skill/CLI tests**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_skill_package.py tests/test_skill_content.py tests/test_skill_routes.py tests/test_cli.py -q
```

Expected: PASS.

- [ ] **Step 2: Run the full Node suite**

Run: `npm test`
Expected: PASS (all `test/*.test.mjs`).

- [ ] **Step 3: Run npm pack dry-run**

Run: `npm pack --dry-run`
Expected: only whitelisted files; no Python source/data/secrets.

- [ ] **Step 4: Run the full Python suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS. If unrelated pre-existing failures appear, record them with exact output; do not modify business code to make them pass.

- [ ] **Step 5: Verify no business logic changed**

Run: `git status --short` and review the diff. Expected changes: `package.json`, `MANIFEST.in`, `.gitignore`, `bin/`, `lib/`, `skills/`, `test/`, `tests/test_skill_*.py`, docs. No changes under `dragon_quant/` except none.

- [ ] **Step 6: Do not commit**

Leave the working tree for the user to review. Remind them that this feature’s changes can be committed as one coherent change (or split installer/skill/docs if they prefer), but do not run git commands.

---

## Self-Review

- **Spec coverage:** AC1–AC14 map to Tasks 1–14; safety matrix (Task 12), version rule (Task 1), Python decoupling (Tasks 7–9), packaging (Task 14), and residual risks (design §12) are covered.
- **Step granularity:** each test step names the file, exact assertions, and the command; each implementation step names the signature and the one decision the implementer cannot infer.
- **Type consistency:** `files` is always `Map<string, Buffer>`; `skillDir` is always the final `.../dragon-quant` directory; `skillsRoot` is always its parent; manifest file keys are POSIX relative paths; `inspectInstall` states are used consistently by install/uninstall/doctor.
- **Review Focus coverage:** target-dir semantics → Task 2; user-modified install/uninstall → Tasks 5/6; multi-scope shadowing → Tasks 8/9; Python missing/old → Tasks 7–9; foreign/manual install → Tasks 4/6; manifest staging failure → Task 5; mixed-tier route rows → Tasks 10/12; trigger/closed-book → Tasks 10–13.
- **Proportion:** the plan specifies signatures, tests, and decisions; it does not transcribe full SKILL.md prose or implementation bodies. The skill text itself is written in Task 10–12 from the content-boundary table.
