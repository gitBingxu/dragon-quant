#!/usr/bin/env bash
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; NC='\033[0m'

usage() {
  echo "Usage: $0 --token <pypi_token> --passwd <password>"
  echo "  将 PyPI token 用 AES-256-CBC 加密写入 publish_token.enc"
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

echo "${TOKEN}" | openssl enc -aes-256-cbc -pbkdf2 -pass pass:"${PASSWD}" -out publish_token.enc

echo -e "${GREEN}✅ publish_token.enc 生成完成${NC}"
echo "   下一步: git add publish_token.enc && git commit"
