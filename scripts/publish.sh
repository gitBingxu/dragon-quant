#!/usr/bin/env bash
# 统一发布入口（薄壳）。所有编排在 scripts/release/：
# 参数/交互 → main.py；批次状态机 → orchestrator.py；PyPI / npm → 两个 adapter。
#
# 用法: scripts/publish.sh [--dry-run] [--resume] [--packages pypi|npm|both]
#                         [--pypi-version X.Y.Z] [--npm-version X.Y.Z]
#                         [--min-cli-version X.Y.Z] [--status-dir DIR] [--yes]
# 凭据: DQ_PUBLISH_PASSWORD（解密 scripts/*_token.enc）或 TWINE_PASSWORD / NPM_TOKEN
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

PYTHON="${DQ_RELEASE_PYTHON:-}"
if [ -z "${PYTHON}" ] && [ -x "${REPO_ROOT}/.venv/bin/python" ]; then
  PYTHON="${REPO_ROOT}/.venv/bin/python"
fi
if [ -z "${PYTHON}" ] || [ ! -x "${PYTHON}" ]; then
  echo "错误：未找到发布解释器。请设置 DQ_RELEASE_PYTHON，或在仓库创建 .venv 后安装 build/twine。" >&2
  echo "例如: python3 -m venv .venv && .venv/bin/pip install build twine" >&2
  exit 1
fi

exec "${PYTHON}" "${SCRIPT_DIR}/release/main.py" --repo-root "${REPO_ROOT}" "$@"
