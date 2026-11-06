#!/usr/bin/env bash
# PyPI 发布入口（薄壳）。所有编排在 scripts/release/publish_pypi.py：
# 版本经 argv 传入、先准备与校验、仅在成功后做精确 git 变更与原子推送。
#
# 用法: scripts/publish.sh --v X.Y.Z [--dry-run] [--resume]
# 凭据: DQ_PUBLISH_PASSWORD（解密 scripts/publish_token.enc）或 TWINE_PASSWORD
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

exec "${PYTHON}" "${SCRIPT_DIR}/release/publish_pypi.py" --repo-root "${REPO_ROOT}" "$@"
