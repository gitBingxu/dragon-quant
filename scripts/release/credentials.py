"""Credential decryption for the unified release flow.

Both encrypted tokens live next to the release scripts and must share one
password.  A run asks for that password at most once (from
``DQ_PUBLISH_PASSWORD`` or a hidden ``getpass`` prompt), decrypts only the
tokens of selected packages that still need an upload, and never writes
plaintext credentials to disk or argv.
"""

from __future__ import annotations

import getpass
import re
import subprocess
import sys
from pathlib import Path

from release.common import ReleaseError, Runner

PYPI_ENC = Path("scripts") / "publish_token.enc"
NPM_ENC = Path("scripts") / "npm_token.enc"


def _reject_unsafe(token: str, package: str) -> str:
    if not token:
        raise ReleaseError("credentials", f"{package} token 为空")
    if len(token) > 4096:
        raise ReleaseError("credentials", f"{package} token 异常过长，拒绝使用")
    if any(ord(char) < 32 or ord(char) == 127 or char.isspace() for char in token):
        raise ReleaseError(
            "credentials", f"{package} token 含空白/控制字符，拒绝使用")
    return token


class Credentials:
    def __init__(self, repo: Path, runner: Runner):
        self.repo = Path(repo)
        self.runner = runner
        self._password: str | None = None
        self._pypi: str | None = None
        self._npm: str | None = None

    # ─── one password, cached ─────────────────────────────────────────────
    def password(self) -> str:
        if self._password is not None:
            return self._password
        direct = self.runner.base_env.get("DQ_PUBLISH_PASSWORD", "")
        if direct:
            self.runner.secrets.append(direct)
            self._password = direct
            return direct
        if not sys.stdin.isatty():
            raise ReleaseError(
                "credentials",
                "非交互环境缺少 DQ_PUBLISH_PASSWORD；无法解密发布 token",
            )
        entered = getpass.getpass("发布密码（解密 scripts/*_token.enc）: ")
        if not entered:
            raise ReleaseError("credentials", "发布密码为空")
        self.runner.secrets.append(entered)
        self._password = entered
        return entered

    # ─── tokens ────────────────────────────────────────────────────────────
    def pypi_token(self) -> str:
        if self._pypi is not None:
            return self._pypi
        direct = self.runner.base_env.get("TWINE_PASSWORD", "")
        if direct:
            token = _reject_unsafe(direct.strip(), "PyPI")
            self.runner.secrets.append(token)
            self._pypi = token
            return token
        token = self._decrypt(self.repo / PYPI_ENC, "PyPI")
        if not token.startswith("pypi-"):
            raise ReleaseError(
                "credentials",
                "密码错误：解密结果不是有效 PyPI token（应为 pypi- 前缀）",
            )
        self.runner.secrets.append(token)
        self._pypi = token
        return token

    def npm_token(self) -> str:
        if self._npm is not None:
            return self._npm
        direct = self.runner.base_env.get("NPM_TOKEN", "")
        if direct:
            token = _reject_unsafe(direct.strip(), "npm")
            self.runner.secrets.append(token)
            self._npm = token
            return token
        token = _reject_unsafe(self._decrypt(self.repo / NPM_ENC, "npm"), "npm")
        if not re.fullmatch(r"[A-Za-z0-9._~+/=-]+", token):
            raise ReleaseError(
                "credentials", "密码错误：解密结果不是有效的 npm token")
        self.runner.secrets.append(token)
        self._npm = token
        return token

    def _decrypt(self, enc: Path, package: str) -> str:
        if not enc.is_file():
            raise ReleaseError(
                "credentials",
                f"缺少 {enc}；请先运行 scripts/encrypt_token.sh 用同一发布密码"
                f"加密 {package} token（不会误用另一份密文）",
            )
        password = self.password()
        result = subprocess.run(
            ["openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-d", "-in", str(enc),
             "-pass", "env:DQ_PUBLISH_PASSWORD"],
            capture_output=True,
            check=False,
            env={**self.runner.env_for(scrub=True), "DQ_PUBLISH_PASSWORD": password},
        )
        # Decode only after checking the return code: a wrong password can emit
        # garbage bytes (and strict UTF-8 decoding would raise before the
        # return-code check, leaking a traceback instead of a clean error).
        stdout = result.stdout.decode("utf-8", errors="replace").strip()
        if result.returncode != 0:
            raise ReleaseError(
                "credentials",
                f"密码错误：openssl 解密 {package} token 失败（口令不匹配或密文损坏）",
            )
        if not stdout:
            raise ReleaseError("credentials", f"{package} token 为空")
        return _reject_unsafe(stdout, package)
