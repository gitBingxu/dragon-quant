#!/bin/sh
# npm Skill 包发布薄入口：只做参数校验与转发，统一流程见 scripts/publish.sh。
#
# Usage: scripts/publish-skill.sh [--dry-run] [--tag TAG] [--access public|restricted]
#                                [--npm-version X.Y.Z] [--min-cli-version X.Y.Z]
#                                [--status-dir DIR] [--yes] [--resume]
# 2FA OTP：仅通过环境变量 NPM_OTP 传入（argv 不接收）。
#
# 该脚本不再拥有独立的 Git / 版本 / 状态编排：bump、commit、tag、push 与
# GitHub Release 都只由统一主流程执行；本入口固定 --packages npm。
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

fail() { printf '%s\n' "错误：$*" >&2; exit 2; }

# 校验并重建参数：每行一个参数（值内禁止换行）。
ARGS="--packages
npm"
while [ $# -gt 0 ]; do
    case "$1" in
        *'
'*) fail "参数不能包含换行" ;;
    esac
    case "$1" in
        --dry-run|--yes|--resume)
            ARGS="$ARGS
$1"; shift ;;
        --tag|--access|--npm-version|--min-cli-version|--status-dir)
            [ $# -ge 2 ] || fail "$1 缺少值"
            value="$2"
            [ -n "$value" ] || fail "$1 缺少值"
            case "$value" in
                *'
'*) fail "$1 的值不能包含换行" ;;
            esac
            case "$1" in
                --tag)
                    case "$value" in
                        -*|"") fail "--tag 值非法：$value" ;;
                        *[!A-Za-z0-9._-]*) fail "--tag 只允许 [A-Za-z0-9._-]" ;;
                    esac
                    ARGS="$ARGS
--npm-tag
$value" ;;
                --access)
                    case "$value" in
                        public|restricted)
                            ARGS="$ARGS
--npm-access
$value" ;;
                        *) fail "--access 只接受 public/restricted" ;;
                    esac ;;
                *) ARGS="$ARGS
$1
$value" ;;
            esac
            shift 2 ;;
        --packages|--packages=*|--pypi-version|--pypi-version=*|--npm-tag|--npm-access)
            fail "本入口固定 npm 单包；PyPI / 这些参数请用 scripts/publish.sh" ;;
        --otp|--otp=*)
            fail "--otp 不再从 argv 接收；请用 NPM_OTP 环境变量传入 2FA 验证码" ;;
        --registry|--registry=*|--userconfig=*|--ignore-scripts)
            fail "不支持复制 npm/registry 参数：$1（统一流程固定 registry 与临时 npmrc）" ;;
        -*)
            fail "不支持的参数：$1" ;;
        *)
            fail "不接受包路径位置参数：$1" ;;
    esac
done

OLDIFS=$IFS
IFS='
'
# Values are validated to contain no newline; disable globbing so a status-dir
# or version containing '*'/'?' is forwarded literally instead of expanding.
set -f
set -- $ARGS
set +f
IFS=$OLDIFS
exec "$SCRIPT_DIR/publish.sh" "$@"
