#!/usr/bin/env bash
# 交互式加密发布令牌。统一发布流程用同一密码解密 PyPI 与 npm 两份独立密文：
#   scripts/publish_token.enc  （PyPI, 以 pypi- 开头）
#   scripts/npm_token.enc      （npm）
#
# 凭据与密码只从隐藏输入读取，绝不进入 argv；输出先写 0600 临时文件，全部
# 成功后才原子替换，绝不覆盖未选中的密文。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
umask 077

RED='\033[0;31m'; GREEN='\033[0;32m'; NC='\033[0m'
err() { printf '%s\n' "$*" >&2; }

# Restore terminal echo even when a signal interrupts a hidden read: the
# cleanup is installed before any input starts, never after.
TTY_SAVED=""
ECHO_DISABLED=0
restore_tty() {
  if [ "${ECHO_DISABLED}" = "1" ]; then
    if [ -n "${TTY_SAVED}" ]; then
      stty "${TTY_SAVED}" 2>/dev/null || true
    else
      stty echo 2>/dev/null || true
    fi
    ECHO_DISABLED=0
  fi
}
trap 'restore_tty' EXIT
trap 'restore_tty; exit 130' INT
trap 'restore_tty; exit 143' TERM

if [ $# -gt 0 ]; then
  err "错误：不接受任何 argv 参数（token/密码不得出现在进程参数与 shell 历史）。"
  err "用法：$0   # 交互选择 PyPI / npm / 两者，隐藏输入令牌与两次密码"
  exit 2
fi

read_secret() {
  # $1 = prompt；TTY 下关闭回显，非 TTY 直接读管道（便于受控测试）。
  local prompt="$1" value=""
  if [ -t 0 ]; then
    printf '%s' "$prompt" >&2
    TTY_SAVED=$(stty -g 2>/dev/null || true)
    stty -echo 2>/dev/null || true
    ECHO_DISABLED=1
    IFS= read -r value || true
    restore_tty
    printf '\n' >&2
  else
    IFS= read -r value || true
  fi
  printf '%s' "$value"
}

printf '加密哪个令牌？1=PyPI 2=npm 3=两者: ' >&2
IFS= read -r TARGET || TARGET=""
case "$TARGET" in
  1|pypi|PyPI) SELECT="pypi" ;;
  2|npm) SELECT="npm" ;;
  3|both|两者) SELECT="both" ;;
  *) err "错误：无效选择：$TARGET"; exit 2 ;;
esac

TOKEN_PYPI=""
TOKEN_NPM=""
if [ "$SELECT" = "pypi" ] || [ "$SELECT" = "both" ]; then
  TOKEN_PYPI=$(read_secret "PyPI token（pypi-...，隐藏输入）: ")
  [ -n "$TOKEN_PYPI" ] || { err "错误：PyPI token 为空"; exit 1; }
  case "$TOKEN_PYPI" in
    pypi-*) : ;;
    *) err "错误：PyPI token 应以 pypi- 开头"; exit 1 ;;
  esac
fi
if [ "$SELECT" = "npm" ] || [ "$SELECT" = "both" ]; then
  TOKEN_NPM=$(read_secret "npm token（隐藏输入）: ")
  [ -n "$TOKEN_NPM" ] || { err "错误：npm token 为空"; exit 1; }
fi

PASSWORD=$(read_secret "发布密码（两份密文共用）: ")
CONFIRM=$(read_secret "再次输入发布密码: ")
if [ -z "$PASSWORD" ]; then err "错误：发布密码为空"; exit 1; fi
if [ "$PASSWORD" != "$CONFIRM" ]; then err "错误：两次密码不一致"; exit 1; fi
export DQ_NEW_TOKEN_PASSWORD="$PASSWORD"

WORKDIR=$(mktemp -d "${TMPDIR:-/tmp}/dq-encrypt.XXXXXX")
trap 'rm -rf "$WORKDIR"' EXIT

encrypt_one() {
  # $1 = token, $2 = final path；写 0600 临时文件，失败不触碰正式密文。
  local token="$1" target="$2" tmp="$WORKDIR/$(basename "$2").tmp"
  if ! printf '%s' "$token" \
      | openssl enc -aes-256-cbc -pbkdf2 -pass env:DQ_NEW_TOKEN_PASSWORD -out "$tmp"; then
    err "错误：加密 $(basename "$target") 失败"
    return 1
  fi
  chmod 600 "$tmp"
  printf '%s' "$tmp"
}

PYPI_TMP=""; NPM_TMP=""
if [ "$SELECT" = "pypi" ] || [ "$SELECT" = "both" ]; then
  PYPI_TMP=$(encrypt_one "$TOKEN_PYPI" "$SCRIPT_DIR/publish_token.enc") || exit 1
fi
if [ "$SELECT" = "npm" ] || [ "$SELECT" = "both" ]; then
  NPM_TMP=$(encrypt_one "$TOKEN_NPM" "$SCRIPT_DIR/npm_token.enc") || exit 1
fi
if [ "$SELECT" = "both" ] && { [ -z "$PYPI_TMP" ] || [ -z "$NPM_TMP" ]; }; then
  err "错误：两份密文未全部准备成功，未替换任何文件"
  exit 1
fi

# Replace atomically per file and roll back on failure: a dual replacement must
# never leave one ciphertext on the new password and the other on the old one.
backup_target() {
  # $1 = final path；失败时返回非零（此时尚未替换任何文件）。
  local target="$1" backup="$WORKDIR/backup-$(basename "$1")"
  if [ -f "$target" ]; then
    cp -p "$target" "$backup" || { err "错误：无法备份 $(basename "$target")，未替换"; return 1; }
  fi
  return 0
}
restore_target() {
  local target="$1" backup="$WORKDIR/backup-$(basename "$1")"
  if [ -f "$backup" ]; then
    cp -p "$backup" "$target" 2>/dev/null || true
  else
    rm -f "$target"
  fi
}

if [ -n "$PYPI_TMP" ]; then
  backup_target "$SCRIPT_DIR/publish_token.enc" || exit 1
fi
if [ -n "$NPM_TMP" ]; then
  backup_target "$SCRIPT_DIR/npm_token.enc" || exit 1
fi

if [ -n "$PYPI_TMP" ] && ! mv -f "$PYPI_TMP" "$SCRIPT_DIR/publish_token.enc"; then
  restore_target "$SCRIPT_DIR/publish_token.enc"
  err "错误：替换 publish_token.enc 失败，已回滚"
  exit 1
fi
if [ -n "$NPM_TMP" ] && ! mv -f "$NPM_TMP" "$SCRIPT_DIR/npm_token.enc"; then
  restore_target "$SCRIPT_DIR/npm_token.enc"
  if [ -n "$PYPI_TMP" ]; then
    restore_target "$SCRIPT_DIR/publish_token.enc"
  fi
  err "错误：替换 npm_token.enc 失败，已回滚，未留下半套结果"
  exit 1
fi

echo -e "${GREEN}✅ 密文生成完成${NC}"
if [ "$SELECT" = "pypi" ] || [ "$SELECT" = "both" ]; then
  echo "   - scripts/publish_token.enc"
fi
if [ "$SELECT" = "npm" ] || [ "$SELECT" = "both" ]; then
  echo "   - scripts/npm_token.enc"
fi
echo "   下一步: git add scripts/*_token.enc && git commit"
