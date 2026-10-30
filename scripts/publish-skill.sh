#!/bin/sh
# Publish dragon-quant-skills without persisting the npm access token.
# Usage: scripts/publish-skill.sh [--dry-run] [additional npm publish args]
set -eu

PACKAGE_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
REGISTRY="https://registry.npmjs.org"
TEMP_NPMRC=""

if ! command -v npm >/dev/null 2>&1; then
    printf '错误：未找到 npm，请先安装 Node.js 与 npm。\n' >&2
    exit 127
fi

cleanup() {
    if [ -n "$TEMP_NPMRC" ]; then
        rm -f "$TEMP_NPMRC"
    fi
    stty echo 2>/dev/null || true
}
trap cleanup EXIT
trap 'exit 130' HUP INT TERM

if [ -z "${NPM_TOKEN:-}" ]; then
    printf 'npm access token: '
    stty -echo
    IFS= read -r NPM_TOKEN
    stty echo
    printf '\n'
fi

if [ -z "$NPM_TOKEN" ]; then
    printf '错误：token 为空，未执行发布。\n' >&2
    exit 1
fi

TEMP_NPMRC=$(mktemp)
chmod 600 "$TEMP_NPMRC"
printf 'registry=%s/\n//registry.npmjs.org/:_authToken=%s\n' \
    "$REGISTRY" "$NPM_TOKEN" > "$TEMP_NPMRC"

NPM_CONFIG_USERCONFIG="$TEMP_NPMRC" \
    npm publish "$PACKAGE_ROOT" "--registry=$REGISTRY" "$@"
