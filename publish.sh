#!/usr/bin/env bash
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; NC='\033[0m'

usage() {
  echo "Usage: $0 --v <version> --passwd <password>"
  exit 1
}

# ─── 解析参数 ───
while [[ $# -gt 0 ]]; do
  case "$1" in
    --v) VERSION="$2"; shift 2 ;;
    --passwd) PASSWD="$2"; shift 2 ;;
    *) usage ;;
  esac
done

[[ -z "${VERSION:-}" || -z "${PASSWD:-}" ]] && usage

# ─── 校验版本号格式 ───
if ! python3 -c "import re; assert re.match(r'^\d+\.\d+\.\d+$', '$VERSION')" 2>/dev/null; then
  echo -e "${RED}错误: 版本号格式错误 (期望 x.y.z，实际: $VERSION)${NC}"
  exit 1
fi

# ─── 解密 PyPI token ───
TOKEN=$(openssl enc -aes-256-cbc -pbkdf2 -d -in publish_token.enc -pass pass:"${PASSWD}" 2>/dev/null)
if [[ -z "${TOKEN:-}" ]]; then
  echo -e "${RED}错误: 解密失败（密码错误或 publish_token.enc 不存在）${NC}"
  exit 1
fi

# ─── 自动写入版本号 ───
echo -e "${GREEN}==> 写入版本号: ${VERSION}${NC}"

sed -i '' "s/^version = \".*\"/version = \"${VERSION}\"/" pyproject.toml
sed -i '' "s/__version__ = \".*\"/__version__ = \"${VERSION}\"/" dragon_quant/_version.py


# ─── 发布流程 ───
echo -e "${GREEN}==> 1/7 提交代码${NC}"
git add .
git commit -m "🔖 bump: ${VERSION}"

echo -e "${GREEN}==> 2/7 打标签${NC}"
git tag "v${VERSION}"

echo -e "${GREEN}==> 3/7 推送代码和标签${NC}"
git push && git push --tags

echo -e "${GREEN}==> 4/7 构建${NC}"
rm -rf dist && python3 -m build

echo -e "${GREEN}==> 5/7 检查${NC}"
twine check dist/*

echo -e "${GREEN}==> 6/7 上传 PyPI${NC}"
twine upload -u __token__ -p "${TOKEN}" dist/*

# ─── 7/7 发布 GitHub Release（release notes 取自 CHANGELOG.md 对应版本段）───
echo -e "${GREEN}==> 7/7 发布 GitHub Release${NC}"
NOTES_FILE=$(mktemp)
trap 'rm -f "$NOTES_FILE"' EXIT

if python3 - "$VERSION" "$NOTES_FILE" <<'PY'
import re, sys
ver, out = sys.argv[1], sys.argv[2]
lines = open("CHANGELOG.md", encoding="utf-8").read().splitlines()
pat = re.compile(r'^## \[' + re.escape(ver) + r'\](\s|$)')
start = None
for i, ln in enumerate(lines):
    if pat.match(ln.strip()):
        start = i
        break
if start is None:
    print(f"warning: 未在 CHANGELOG.md 找到 [{ver}] 段", file=sys.stderr)
    sys.exit(2)
body = []
for ln in lines[start + 1:]:
    if ln.startswith("## ["):
        break
    if ln.strip().startswith(("版本依据", "合入记录")):
        continue
    body.append(ln)
while body and not body[0].strip():
    body.pop(0)
while body and not body[-1].strip():
    body.pop()
open(out, "w", encoding="utf-8").write("\n".join(body) + "\n")
PY
then
  if command -v gh >/dev/null 2>&1; then
    gh release create "v${VERSION}" --title "v${VERSION}" --notes-file "$NOTES_FILE"
    echo -e "${GREEN}✅ GitHub Release v${VERSION} 已创建${NC}"
  else
    echo -e "${RED}⚠️ 未安装 gh CLI，跳过 GitHub Release${NC}"
  fi
else
  echo -e "${RED}⚠️ 未在 CHANGELOG.md 找到 [${VERSION}] 段，跳过 GitHub Release（PyPI 已发布）${NC}"
fi

echo -e "${GREEN}✅ v${VERSION} 发布完成${NC}"
