"""凭据与 profile 加载。

读取顺序：环境变量 → `<DQ_DATA_DIR>/secrets/assets_sync.json`（0600）。
环境变量只覆盖当前选中 profile 的同名键。
"""

from __future__ import annotations

import json
import os
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from dragon_quant.infra.assets.sync.errors import ConfigError
from dragon_quant.infra.storage.paths import DATA_DIR

_ENV_MAP = {
    "DQ_ASSETS_ENDPOINT": "endpoint",
    "DQ_ASSETS_REGION": "region",
    "DQ_ASSETS_BUCKET": "bucket",
    "DQ_ASSETS_PREFIX": "prefix",
    "DQ_ASSETS_ACCESS_KEY_ID": "access_key_id",
    "DQ_ASSETS_SECRET_ACCESS_KEY": "secret_access_key",
    "DQ_ASSETS_ADDRESSING": "addressing",
}

_PREFIX_RE = re.compile(r"^[A-Za-z0-9._/-]*$")
_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


@dataclass(frozen=True)
class AssetsConfig:
    endpoint: str
    region: str
    bucket: str
    access_key_id: str
    secret_access_key: str = field(repr=False)
    prefix: str = ""
    addressing: str = "path"
    profile: str = ""

    def __post_init__(self):
        if not self.endpoint.startswith(("https://", "http://")):
            raise ConfigError("endpoint 必须以 https:// 或 http:// 开头")
        if not self.region:
            raise ConfigError("region 不能为空")
        if not self.bucket:
            raise ConfigError("bucket 不能为空")
        if not self.access_key_id or not self.secret_access_key:
            raise ConfigError("access_key_id / secret_access_key 不能为空")
        if self.addressing not in ("path", "virtual"):
            raise ConfigError("addressing 必须是 path 或 virtual")
        normalized = self.prefix.strip("/")
        if not _PREFIX_RE.match(normalized):
            raise ConfigError("prefix 只允许 [A-Za-z0-9._/-]")
        object.__setattr__(self, "prefix", normalized)


def _expand(value: str, env: Mapping[str, str]) -> str:
    def repl(match: re.Match) -> str:
        name = match.group(1)
        if name not in env:
            raise ConfigError(f"配置引用了未设置的环境变量: {name}")
        return env[name]

    return _VAR_RE.sub(repl, value)


def _check_permissions(path: Path) -> None:
    if os.name != "posix":
        return
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        raise ConfigError(f"配置文件权限过宽（{oct(mode)}），请执行 chmod 600 {path}")


def load_config(
    profile: str | None = None,
    *,
    env: Mapping[str, str] | None = None,
    config_path: str | Path | None = None,
) -> AssetsConfig:
    env_map: Mapping[str, str] = os.environ if env is None else env
    path = Path(config_path) if config_path is not None else DATA_DIR / "secrets" / "assets_sync.json"

    data: dict = {}
    if path.exists():
        _check_permissions(path)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ConfigError(f"无法读取配置文件 {path}: {exc}") from exc
        if not isinstance(data, dict):
            raise ConfigError("配置文件顶层必须是对象")

    profiles = data.get("profiles")
    selected = profile or env_map.get("DQ_ASSETS_PROFILE") or data.get("default_profile") or "default"
    if isinstance(profiles, dict):
        raw = dict(profiles.get(selected) or {})
        if not raw:
            raise ConfigError(f"profile 不存在或为空: {selected}")
    else:
        # 兼容单层扁平配置
        raw = dict(data)
        selected = selected or "default"

    for env_name, key in _ENV_MAP.items():
        if env_name in env_map:
            raw[key] = env_map[env_name]

    # 方案只要求对 secret 展开；这里对两个 key 字段都展开，属于放宽（便于用环境变量注入 AK）。
    for key in ("access_key_id", "secret_access_key"):
        if isinstance(raw.get(key), str):
            raw[key] = _expand(raw[key], env_map)

    required = ("endpoint", "region", "bucket", "access_key_id", "secret_access_key")
    missing = [key for key in required if not raw.get(key)]
    if missing:
        raise ConfigError("缺少配置项: " + ", ".join(missing))

    return AssetsConfig(
        endpoint=str(raw["endpoint"]).rstrip("/"),
        region=str(raw["region"]),
        bucket=str(raw["bucket"]),
        access_key_id=str(raw["access_key_id"]),
        secret_access_key=str(raw["secret_access_key"]),
        prefix=str(raw.get("prefix", "")),
        addressing=str(raw.get("addressing", "path")),
        profile=selected,
    )
