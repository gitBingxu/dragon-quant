"""因子层共享数据模型（P0）。

纯数据 + 异常定义，不 import storage / providers / scorers / review_account
（方案 §3.4 分层依赖规则）。

关键约束（方案 §7）：
  - 因子缺失一律用「无行」表达（``FactorFrame.values`` 里不出现该 code，
    或该因子当日完全无值 → ``factor_values`` 无行），绝不写 0 / 50 / 中性分。
  - ``FactorFrame.available_at`` 是**源数据的实际可知时点**（可为 SQLite 的
    ``YYYY-MM-DD HH:MM:SS`` 或 ISO 格式）；最终落库时点由
    ``engine.resolve_available_at`` 按 ``pit_rule`` 统一解析。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Optional, TypedDict


class SpecDefaults(TypedDict, total=False):
    """``FactorSpec`` 构造参数的默认值包。

    ``builtin/*`` 用 ``SpecDefaults(...)``（等价于 ``dict(...)`` 的运行期行为）
    声明共享默认值，供 ``FactorSpec(**defaults)`` 解包；TypedDict 让每个键
    保留各自的精确类型，避免 ``dict(...)`` 被推断为 ``dict[str, str | bool]``。

    ``params`` 刻意**不在此处**：需要它的注册表使用 ``SpecDefaultsWithParams``，
    本身在个别 ``FactorSpec`` 上显式传 ``params=`` 的注册表继续用本类型，
    两者不会在同一个 ``**`` 解包里与显式参数冲突。
    """

    frequency: str
    source: str
    available_phase: str
    pit_rule: str
    lookback: int
    default_enabled: bool
    owner: str


class SpecDefaultsWithParams(SpecDefaults, total=False):
    """``SpecDefaults`` + 共享 ``params``（仅整族共用同一参数集的注册表使用）。"""

    params: Mapping[str, object]


@dataclass(frozen=True)
class FactorSpec:
    """因子注册元数据（方案 §4.1）。

    ``label`` / ``description`` / ``owner`` 不参与版本哈希；
    ``params`` / ``direction`` / ``pit_rule`` 等语义字段参与。
    """

    name: str
    label: str
    frequency: str
    source: str
    compute: Callable[["FactorContext"], "FactorFrame"]
    impl_key: str
    depends_on: tuple[str, ...] = ()
    lookback: int = 0
    direction: str = "higher_better"
    unit: str = "score"
    available_phase: str = "close"
    pit_rule: str = "recorded"
    scope: tuple[str, ...] = ("candidate",)
    params: Mapping[str, Any] = field(default_factory=dict)
    default_enabled: bool = False
    valid_from: Optional[str] = None
    deprecated_at: Optional[str] = None
    owner: str = ""
    description: str = ""
    version_override: Optional[str] = None


@dataclass(frozen=True)
class FactorContext:
    """单因子单日计算上下文（纯函数入参）。

    ``source_rows`` 由 engine 按 ``spec.source`` 组装（源数据适配），
    compute 只读它，不得触网 / 读库。
    """

    trade_date: str
    codes: tuple[str, ...]
    source_rows: Mapping[str, Any]
    as_of: str
    calendar: tuple[str, ...] = ()
    params: Mapping[str, Any] = field(default_factory=dict)


@dataclass
class FactorFrame:
    """单因子单日计算结果。

    ``values``: code -> value。缺失的 code 直接不出现（绝不填 0）。
    ``available_at``: 源数据实际可知时点；``derived`` 因子可留 None（engine 用 phase）。
    ``meta``: 覆盖率 / 样本数 / 跳过计数等诊断信息；**所有 source 都会序列化进
    ``factor_values.source_meta_json``**（不参与因子值语义，仅作血缘与诊断）。
    """

    values: dict[str, float] = field(default_factory=dict)
    available_at: Optional[str] = None
    source: str = ""
    source_scan_id: Optional[str] = None
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FactorValue:
    """一条待写入 ``factor_values`` 的行。"""

    trade_date: str
    code: str
    factor_name: str
    factor_version: str
    value: float
    available_at: str
    source: str
    source_scan_id: Optional[str] = None
    source_meta_json: Optional[str] = None


class BackfilledSource(Exception):
    """``recorded`` 源跨日产生：计算输入可能晚于观测日，写入即前视泄漏。"""


class AsOfViolation(Exception):
    """``asof`` 源选中的账本记录晚于当日 phase 时点（硬门禁）。"""


class SourceUnavailable(Exception):
    """源可用但其**必需子块**缺失（如旧快照无 ``review``）。

    与「当日无值」严格区分：engine 记 ``missing_source`` 并**不删旧行**，
    绝不把「无法计算」当成「真实空」去 DELETE。
    """
