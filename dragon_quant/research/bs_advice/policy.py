"""BS 政策配置文件的显式加载与冻结（W4a）。

``--bs-policy`` 只接受**完整可解释**的 ``BSPolicyConfig``：加载后保存规范化内容
与其内容哈希（而不是只存本机文件路径），供运行/研究身份引用。教学 fixture 允许
加载但显式标注 ``fixture=True``（非推荐），严格研究不得把它当真实参数。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional

from dragon_quant.review_account.strategy.bs_policy import BSPolicyConfig

#: 配置文件中的 ``schema_version``（领域模型自带；此处只做入口提示）。
POLICY_FILE_VERSION = 1


class PolicyFileError(ValueError):
    """配置非法/不可读；``exit_code`` 供研究命令映射退出码。"""

    def __init__(self, message: str, *, code: str = "policy_config_invalid",
                 exit_code: int = 2) -> None:
        super().__init__(message)
        self.code = code
        self.exit_code = int(exit_code)


def _reject_constant(token: str):
    raise PolicyFileError(f"配置含非有限数值 {token}（不允许 NaN/Infinity）",
                          code="policy_config_invalid")


def read_json_object(path: str, *, what: str = "JSON") -> Mapping:
    """显式本机路径读取 JSON 对象；拒绝 NaN/Infinity 与非对象。"""
    try:
        text = Path(path).expanduser().read_text(encoding="utf-8")
    except OSError as exc:
        raise PolicyFileError(f"无法读取{what} {path}: {exc}",
                              code="file_unreadable") from exc
    try:
        payload = json.loads(text, parse_constant=_reject_constant)
    except json.JSONDecodeError as exc:
        raise PolicyFileError(f"{what}不是合法 JSON：{exc}",
                              code="file_invalid") from exc
    if not isinstance(payload, Mapping):
        raise PolicyFileError(f"{what}必须是 JSON 对象", code="file_invalid")
    return payload


@dataclass(frozen=True)
class LoadedPolicy:
    """已校验并规范化的 BS 政策配置（内容哈希即冻结身份）。"""

    config: BSPolicyConfig
    normalized: dict
    content_hash: str
    fixture: bool
    source: str = "<inline>"

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "schema_version": self.config.schema_version,
            "policy_id": self.config.policy_id,
            "profile_id": self.config.profile_id,
            "profile_version": self.config.profile_version,
            "content_hash": self.content_hash,
            "fixture": self.fixture,
            "label": self.config.label,
            "normalized": self.normalized,
        }


def load_policy_payload(payload: Mapping, *, source: str = "<inline>"
                        ) -> LoadedPolicy:
    """从已解析 JSON 对象构造；未知字段/缺项/NaN 由领域配置显式拒绝。"""
    if not isinstance(payload, Mapping):
        raise PolicyFileError("策略配置必须是 JSON 对象",
                              code="policy_config_invalid")
    try:
        config = BSPolicyConfig.from_dict(dict(payload))
    except Exception as exc:  # noqa: BLE001 - 领域错误统一映射为配置错误
        raise PolicyFileError(str(exc), code="policy_config_invalid") from exc
    normalized = config.to_dict()
    return LoadedPolicy(config=config, normalized=normalized,
                        content_hash=config.content_hash,
                        fixture=bool(config.fixture), source=str(source))


def load_policy_file(path: Optional[str]) -> LoadedPolicy:
    if not path:
        raise PolicyFileError("缺少 --bs-policy 文件路径",
                              code="policy_config_missing")
    return load_policy_payload(read_json_object(path, what="策略配置"), source=str(path))


__all__ = [
    "POLICY_FILE_VERSION", "PolicyFileError", "LoadedPolicy",
    "load_policy_file", "load_policy_payload", "read_json_object",
]
