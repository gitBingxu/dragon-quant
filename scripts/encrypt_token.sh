#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

RED='\033[0;31m'; GREEN='\033[0;32m'; NC='\033[0m'

usage() {
  echo "Usage: $0 --token <pypi_token> --passwd <password>"
  echo "  将 PyPI token 用 AES-256-CBC 加密写入 scripts/publish_token.enc"
  exit 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --token)  TOKEN="$2"; shift 2 ;;
    --passwd) PASSWD="$2"; shift 2 ;;
    *) usage ;;
  esac
done

[[ -z "${TOKEN:-}" || -z "${PASSWD:-}" ]] && usage

cd "${REPO_ROOT}"

echo "${TOKEN}" | openssl enc -aes-256-cbc -pbkdf2 -pass pass:"${PASSWD}" -out "${SCRIPT_DIR}/publish_token.enc"

echo -e "${GREEN}✅ scripts/publish_token.enc 生成完成${NC}"
echo "   下一步: git add scripts/publish_token.enc && git commit"
