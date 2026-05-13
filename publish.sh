#!/usr/bin/env bash
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; NC='\033[0m'

usage() {
  echo "Usage: $0 --v <version> --token <pypi_token>"
  exit 1
}

# ─── 解析参数 ───
while [[ $# -gt 0 ]]; do
  case "$1" in
    --v) VERSION="$2"; shift 2 ;;
    --token) TOKEN="$2"; shift 2 ;;
    *) usage ;;
  esac
done

[[ -z "${VERSION:-}" || -z "${TOKEN:-}" ]] && usage

# ─── 校验版本号格式 ───
if ! python3 -c "import re; assert re.match(r'^\d+\.\d+\.\d+$', '$VERSION')" 2>/dev/null; then
  echo -e "${RED}错误: 版本号格式错误 (期望 x.y.z，实际: $VERSION)${NC}"
  exit 1
fi

# ─── 校验版本号与 pyproject.toml 一致 ───
PYPROJECT_VERSION=$(python3 -c "
import re
with open('pyproject.toml') as f:
    for line in f:
        m = re.match(r'^version\s*=\s*\"(.+)\"', line)
        if m:
            print(m.group(1))
            break
")

if [ "$VERSION" != "$PYPROJECT_VERSION" ]; then
  echo -e "${RED}错误: --v $VERSION 与 pyproject.toml 中 version=$PYPROJECT_VERSION 不一致${NC}"
  echo -e "${RED}请先更新 pyproject.toml 的 version 字段${NC}"
  exit 1
fi


# ─── 发布流程 ───
echo -e "${GREEN}==> 1/6 提交代码${NC}"
git add .
git commit -m "🔖 bump: ${VERSION}"

echo -e "${GREEN}==> 2/6 打标签${NC}"
git tag "v${VERSION}"

echo -e "${GREEN}==> 3/6 推送代码和标签${NC}"
git push && git push --tags

echo -e "${GREEN}==> 4/6 构建${NC}"
rm -rf dist && python3 -m build

echo -e "${GREEN}==> 5/6 检查${NC}"
twine check dist/*

echo -e "${GREEN}==> 6/6 上传 PyPI${NC}"
twine upload -u __token__ -p "${TOKEN}" dist/*

echo -e "${GREEN}✅ v${VERSION} 发布完成${NC}"
