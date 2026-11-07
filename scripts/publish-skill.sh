#!/bin/sh
# Publish dragon-quant-skills safely.
#
# Usage: scripts/publish-skill.sh [--dry-run] [--tag TAG] [--access public|restricted]
# 2FA OTP：仅通过环境变量 NPM_OTP 传入（脚本 argv 不接收，避免凭据出现在调用者 shell 历史）。
#
# Gates run before any registry write: version contract (package / skill /
# min-cli), Python skill tests, Node tests, npm pack whitelist and a local
# install smoke.  The same tarball is published.  Tokens are read hidden, kept
# in a 0600 temporary npmrc and never echoed.  Dry-run needs no token and makes
# no long-term npm config changes.
set -eu

PACKAGE_ROOT=${DQ_SKILL_REPO_ROOT:-$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)}
REGISTRY="https://registry.npmjs.org"
PACKAGE_NAME="dragon-quant-skills"
TEMP_DIR=""
TEMP_NPMRC=""
STAGE="preflight"
STTY_SAVED=""

say() { printf '%s\n' "$*"; }
err() { printf '%s\n' "$*" >&2; }
fail() { err "错误（${STAGE}）：$*"; exit 1; }

cleanup() {
    if [ -n "$TEMP_NPMRC" ]; then rm -f "$TEMP_NPMRC"; fi
    if [ -n "$TEMP_DIR" ]; then rm -rf "$TEMP_DIR"; fi
    if [ -n "$STTY_SAVED" ]; then stty "$STTY_SAVED" 2>/dev/null || true; fi
}
trap cleanup EXIT
trap 'exit 130' HUP INT TERM

DRY_RUN=0
NPM_TAG=""
NPM_ACCESS=""
while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run) DRY_RUN=1; shift ;;
        --tag)
            [ $# -ge 2 ] || fail "--tag 缺少值"
            case "$2" in
                -*|"") fail "--tag 值非法：$2" ;;
                *[!A-Za-z0-9._-]*) fail "--tag 只允许 [A-Za-z0-9._-]，禁止空白/glob/其他参数" ;;
            esac
            NPM_TAG="$2"; shift 2 ;;
        --access)
            [ $# -ge 2 ] || fail "--access 缺少值"
            case "$2" in public|restricted) NPM_ACCESS="$2" ;; *) fail "--access 只接受 public/restricted" ;; esac
            shift 2 ;;
        --otp)
            fail "--otp 不再从 argv 接收；请用 NPM_OTP 环境变量传入 2FA 验证码" ;;
        *)
            fail "不支持的参数：$1（只接受 --dry-run/--tag/--access；包路径与 registry/userconfig 覆盖被拒绝）" ;;
    esac
done

command -v npm >/dev/null 2>&1 || fail "未找到 npm，请先安装 Node.js 与 npm"

umask 077
TEMP_DIR=$(mktemp -d "${TMPDIR:-/tmp}/dq-skill-publish.XXXXXX")
TEMP_NPMRC="$TEMP_DIR/npmrc"
: > "$TEMP_NPMRC"
chmod 600 "$TEMP_NPMRC"
export npm_config_cache="$TEMP_DIR/cache"
# 预检/测试/打包/本地安装全部离线；只有真实 registry 查询与发布显式关闭。
export npm_config_offline=true

# ─── 解释器与版本契约 ───
PYTHON=""
if [ -n "${DQ_PYTHON:-}" ]; then
    PYTHON="$DQ_PYTHON"
elif [ -x "$PACKAGE_ROOT/.venv/bin/python" ]; then
    PYTHON="$PACKAGE_ROOT/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON=python3
elif command -v python >/dev/null 2>&1; then
    PYTHON=python
else
    fail "未找到 Python 解释器；Skill 测试与版本契约需要 Python"
fi

STAGE="version-contract"
"$PYTHON" - "$PACKAGE_ROOT" <<'PY' || fail "版本契约不一致"
import json, re, sys
from pathlib import Path

root = Path(sys.argv[1])
package = json.loads((root / "package.json").read_text(encoding="utf-8"))
skill = (root / "skills" / "dragon-quant" / "SKILL.md").read_text(encoding="utf-8")
front = skill.split("---\n", 2)[1]
skill_version = re.search(r'skill-version:\s*"([^"]+)"', front).group(1)
min_cli = re.search(r'min-cli-version:\s*"([^"]+)"', front).group(1)
pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
python_version = re.search(r'^version = "([^"]+)"$', pyproject, re.MULTILINE).group(1)
if package["name"] != "dragon-quant-skills":
    raise SystemExit("package name must be dragon-quant-skills")
if package["version"] != skill_version:
    raise SystemExit(f"package {package['version']} != skill {skill_version}")
if min_cli != python_version:
    raise SystemExit(f"min-cli-version {min_cli} != python {python_version}")
print(f"版本契约通过：skill={skill_version} min-cli={min_cli}")
PY

STAGE="python-tests"
"$PYTHON" -m pytest \
    "$PACKAGE_ROOT/dragon_quant/tests/test_skill_package.py" \
    "$PACKAGE_ROOT/dragon_quant/tests/test_skill_content.py" \
    "$PACKAGE_ROOT/dragon_quant/tests/test_skill_routes.py" \
    -q || fail "Skill Python 内容/路由测试失败"

STAGE="node-tests"
npm test --prefix "$PACKAGE_ROOT" || fail "Node 测试失败"

STAGE="pack-whitelist"
PACK_JSON=$(npm pack "$PACKAGE_ROOT" --dry-run --json --ignore-scripts 2>/dev/null) \
    || fail "npm pack --dry-run 失败"
printf '%s' "$PACK_JSON" | grep -q '"path": *"skills/dragon-quant/SKILL.md"' \
    || fail "npm 包缺少 skills/dragon-quant/SKILL.md"
printf '%s' "$PACK_JSON" | grep -q '"path": *"bin/dragon-quant-skills.mjs"' \
    || fail "npm 包缺少 bin 入口"
for forbidden in '"path": *"docs/' '"path": *"dragon_quant/' '"path": *"test/' '"path": *".env'; do
    if printf '%s' "$PACK_JSON" | grep -Eq "$forbidden"; then
        fail "npm 包包含不该发布的路径：$forbidden"
    fi
done

STAGE="pack-install-smoke"
TARBALL=$(npm pack "$PACKAGE_ROOT" --pack-destination "$TEMP_DIR" --ignore-scripts --silent) \
    || fail "npm pack 失败"
TARBALL="$TEMP_DIR/$TARBALL"
[ -f "$TARBALL" ] || fail "未生成 tarball"
SMOKE="$TEMP_DIR/install"
mkdir -p "$SMOKE"
npm install --prefix "$SMOKE" --ignore-scripts --no-audit --no-fund "$TARBALL" \
    >/dev/null 2>&1 || fail "本地安装冒烟失败"
[ -f "$SMOKE/node_modules/dragon-quant-skills/bin/dragon-quant-skills.mjs" ] \
    || fail "本地安装冒烟：安装产物缺少 bin 入口"

say "预检与打包完成：$TARBALL"

if [ "$DRY_RUN" -eq 1 ]; then
    STAGE="publish-dry-run"
    if [ -n "$NPM_TAG" ] && [ -n "$NPM_ACCESS" ]; then
        npm_config_offline=true npm publish "$TARBALL" --dry-run --ignore-scripts \
            --registry="$REGISTRY" --userconfig="$TEMP_NPMRC" \
            "--tag=$NPM_TAG" "--access=$NPM_ACCESS" || fail "npm publish --dry-run 失败"
    elif [ -n "$NPM_TAG" ]; then
        npm_config_offline=true npm publish "$TARBALL" --dry-run --ignore-scripts \
            --registry="$REGISTRY" --userconfig="$TEMP_NPMRC" \
            "--tag=$NPM_TAG" || fail "npm publish --dry-run 失败"
    elif [ -n "$NPM_ACCESS" ]; then
        npm_config_offline=true npm publish "$TARBALL" --dry-run --ignore-scripts \
            --registry="$REGISTRY" --userconfig="$TEMP_NPMRC" \
            "--access=$NPM_ACCESS" || fail "npm publish --dry-run 失败"
    else
        npm_config_offline=true npm publish "$TARBALL" --dry-run --ignore-scripts \
            --registry="$REGISTRY" --userconfig="$TEMP_NPMRC" || fail "npm publish --dry-run 失败"
    fi
    say "dry-run 完成：零 token、零远端写入、未修改长期 npm 配置"
    exit 0
fi

# ─── 真发布门禁 ───
STAGE="release-gate"
BRANCH=$(git -C "$PACKAGE_ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null) \
    || fail "不是 git 仓库"
case "$BRANCH" in HEAD|"") fail "发布必须基于命名分支，当前为 $BRANCH" ;; esac
git -C "$PACKAGE_ROOT" rev-parse --abbrev-ref --symbolic-full-name '@{u}' >/dev/null 2>&1 \
    || fail "当前分支没有 upstream"
[ -z "$(git -C "$PACKAGE_ROOT" status --porcelain)" ] \
    || fail "工作区不干净（含暂存/未跟踪文件），请先提交或清理"
VERSION=$("$PYTHON" -c 'import json,sys; print(json.load(open(sys.argv[1]))["version"])' "$PACKAGE_ROOT/package.json")
REGISTRY_VIEW=$(npm_config_offline=false npm view "$PACKAGE_NAME@$VERSION" version --registry="$REGISTRY" 2>&1) && \
    fail "registry 已存在 $PACKAGE_NAME@$VERSION"
case "$REGISTRY_VIEW" in
    *E404*|*'404 Not Found'*) : ;;
    *) fail "无法确认 registry 版本状态（网络失败不能当作版本不存在）：$REGISTRY_VIEW" ;;
esac

# ─── 隐藏读取 token（仅真发布）───
STAGE="auth"
if [ -z "${NPM_TOKEN:-}" ]; then
    if [ ! -t 0 ] || [ ! -t 1 ]; then
        fail "非交互环境需要 NPM_TOKEN 环境变量（不会在 argv/日志中回显）"
    fi
    STTY_SAVED=$(stty -g 2>/dev/null || true)
    printf 'npm access token: ' >&2
    stty -echo 2>/dev/null || true
    IFS= read -r NPM_TOKEN
    stty echo 2>/dev/null || true
    printf '\n' >&2
fi
[ -n "${NPM_TOKEN:-}" ] || fail "token 为空，未执行发布"

# OTP 只经环境变量传给 npm 子进程，不在脚本 argv 或 npm argv 中出现。
if [ -n "${NPM_OTP:-}" ]; then
    export npm_config_otp="$NPM_OTP"
fi

printf 'registry=%s/\n//registry.npmjs.org/:_authToken=%s\n' "$REGISTRY" "$NPM_TOKEN" > "$TEMP_NPMRC"
chmod 600 "$TEMP_NPMRC"

STAGE="publish"
publish_failed() {
    fail "npm publish 失败；该版本可能已存在，不要盲目重试。可用 --dry-run 复核后人工处理"
}

# 位置参数逐项传入，绝不把选项拼成字符串再做分词/通配符展开。
if [ -n "$NPM_TAG" ] && [ -n "$NPM_ACCESS" ]; then
    npm_config_offline=false npm publish "$TARBALL" --ignore-scripts \
        --registry="$REGISTRY" --userconfig="$TEMP_NPMRC" \
        "--tag=$NPM_TAG" "--access=$NPM_ACCESS" || publish_failed
elif [ -n "$NPM_TAG" ]; then
    npm_config_offline=false npm publish "$TARBALL" --ignore-scripts \
        --registry="$REGISTRY" --userconfig="$TEMP_NPMRC" \
        "--tag=$NPM_TAG" || publish_failed
elif [ -n "$NPM_ACCESS" ]; then
    npm_config_offline=false npm publish "$TARBALL" --ignore-scripts \
        --registry="$REGISTRY" --userconfig="$TEMP_NPMRC" \
        "--access=$NPM_ACCESS" || publish_failed
else
    npm_config_offline=false npm publish "$TARBALL" --ignore-scripts \
        --registry="$REGISTRY" --userconfig="$TEMP_NPMRC" || publish_failed
fi
say "✅ $PACKAGE_NAME@$VERSION 发布完成"
