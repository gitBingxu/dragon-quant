"""版本化行情校准规格（纯叶子，无 I/O 副作用，不 import 第三方行情的重量模块）。

W4a：把「TDX 免费行情到底可不可用」从全局布尔变成**按来源/协议/安装版本/转换
代码指纹/证券品类/周期**绑定的显式规格。只有与**当前实际安装解析器**逐项匹配的
规格才把时间/单位/内容标为 verified；未知版本、不同协议或不同周期一律保持
unverified（不全局翻 verified，也不覆盖旧未验证行）。

依据（开发期证据，非生产运行依赖）：
``docs/solutions/2026-11-18-bs-account-decision.md`` 与
``codex-bridge/tasks/bs-ledger-20261118/data-calibration-main.md``：tmdx 3.0.1、
MAC 协议 0x122E、``Adjust.NONE``、``bar_time=start``，对 000001/600000/300750
的日 K 与 1 分 K 实测——bar_time 已是右端时刻、vol 为股、amount 为元，且
分钟量额汇总与日 K 比值≈1（float32 精度）。该证据只覆盖股票日 K/1 分 K 的
当前安装版本，不推广到 5 分/周月/竞价/quote。

设计取舍：本模块只输出**语义布尔与校准 ID**，不 import
``review_account.evidence``（保持 infra 为叶子）；证据层负责把结果包装成
``SourceSemantics``。这样数据采集与证据装配共享唯一校准真值，又不会让 infra
反向依赖 feature。
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import importlib.util
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional

VERIFIED = "verified"
UNVERIFIED = "unverified"

#: 校准规格版本（进入证据快照 manifest，便于回放追溯）。
CALIBRATION_VERSION = "tdx_bar/1"

#: 实测安装指纹（easy_tdx 源码 SHA256）；这些文件任一变化即视为未知解析器。
_TDX_MAC_FINGERPRINTS: tuple[tuple[str, str], ...] = (
    ("mac/client.py",
     "c0ec905a1d6619699d7ffac5ac8ee357b23d4cd6fc59051b3dedffce49906bdb"),
    ("mac/commands/symbol_bar.py",
     "7263212147b599fc1d0e7f462ffa4063c3d13292f571f26c2e01c1f53d83169f"),
    ("_df.py",
     "8d26c236b030cd58138f1261aa090f67bef1e460162ba433e8bd55480c17dc50"),
    ("models/bar.py",
     "79025165be70cbdac90c7d1a9a31ac18363e74ccf21da11c52fdc892e82341e8"),
)


def _sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


def _easy_tdx_root() -> Optional[Path]:
    """定位已安装的 ``easy_tdx`` 包目录，**不 import**（避免加载 pandas）。"""
    try:
        spec = importlib.util.find_spec("easy_tdx")
    except (ImportError, AttributeError, ValueError):
        return None
    if spec is None or not spec.submodule_search_locations:
        return None
    return Path(list(spec.submodule_search_locations)[0])


def _installed_version() -> str:
    for dist in ("tmdx", "easy_tdx"):
        try:
            return str(importlib.metadata.version(dist))
        except importlib.metadata.PackageNotFoundError:
            continue
    return ""


@dataclass(frozen=True)
class CaptureProvenance:
    """一次采集实际使用的解析器身份（来源 + 协议 + 版本 + 代码指纹）。"""

    source: str
    protocol: str
    install_version: str
    converter_sha256: tuple[tuple[str, str], ...]
    bar_time: str = "start"
    adjustment: str = "none"

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "protocol": self.protocol,
            "install_version": self.install_version,
            "converter_sha256": dict(self.converter_sha256),
            "bar_time": self.bar_time,
            "adjustment": self.adjustment,
        }

    @property
    def fingerprint(self) -> str:
        body = ";".join(f"{rel}={sha}" for rel, sha in self.converter_sha256)
        return hashlib.sha256(
            f"{self.protocol}|{self.install_version}|{body}".encode("utf-8")
        ).hexdigest()[:16]


def current_tdx_provenance() -> Optional[CaptureProvenance]:
    """当前安装的 TDX MAC 解析器身份；缺包/指纹不符返回 ``None``。"""
    root = _easy_tdx_root()
    version = _installed_version()
    if root is None or not version:
        return None
    fingerprints = tuple(
        (rel, _sha256(root / rel)) for rel, _expected in _TDX_MAC_FINGERPRINTS)
    if any(not sha for _rel, sha in fingerprints):
        return None
    return CaptureProvenance(
        source="tdx", protocol="mac/0x122e", install_version=version,
        converter_sha256=fingerprints, bar_time="start", adjustment="none")


@dataclass(frozen=True)
class CalibrationSpec:
    """一条**限定范围**的校准规格：匹配即 verified，否则不生效。"""

    calibration_id: str
    provenance: CaptureProvenance
    category: str
    periods: tuple[str, ...]
    time_semantics: str
    unit_semantics: str
    content_semantics: str
    evidence: str
    effective_from: str
    fixture: bool = False

    def matches(self, provenance: Optional[CaptureProvenance], *,
                period: str, category: str) -> bool:
        if provenance is None or category != self.category:
            return False
        if period not in self.periods:
            return False
        return (
            provenance.source == self.provenance.source
            and provenance.protocol == self.provenance.protocol
            and provenance.install_version == self.provenance.install_version
            and provenance.bar_time == self.provenance.bar_time
            and provenance.adjustment == self.provenance.adjustment
            and provenance.converter_sha256 == self.provenance.converter_sha256)

    def to_dict(self) -> dict:
        return {
            "calibration_id": self.calibration_id,
            "provenance": self.provenance.to_dict(),
            "category": self.category,
            "periods": list(self.periods),
            "time_semantics": self.time_semantics,
            "unit_semantics": self.unit_semantics,
            "content_semantics": self.content_semantics,
            "evidence": self.evidence,
            "effective_from": self.effective_from,
            "fixture": self.fixture,
        }


#: 生产规格：仅覆盖「tmdx 3.0.1 + MAC 0x122E + NONE + bar_time=start + 股票
#: 日 K/1 分 K」。安装版本或解析器代码指纹不同则 ``matches`` 失败，采集回到
#: unverified，绝不因存在一条规格而全局放行。
_TDX_MAC_STOCK = CaptureProvenance(
    source="tdx", protocol="mac/0x122e", install_version="3.0.1",
    converter_sha256=_TDX_MAC_FINGERPRINTS, bar_time="start",
    adjustment="none")

PRODUCTION_CALIBRATIONS: tuple[CalibrationSpec, ...] = (
    CalibrationSpec(
        calibration_id="tdx_mac_0x122e_tmdx3.0.1_none_start_stock",
        provenance=_TDX_MAC_STOCK,
        category="stock",
        periods=("daily", "1min"),
        time_semantics=VERIFIED,
        unit_semantics=VERIFIED,
        content_semantics=VERIFIED,
        evidence=(
            "tmdx 3.0.1 / MAC 0x122E / Adjust.NONE / bar_time=start；"
            "000001/600000/300750 日K+1分K 实测：bar_time 右端、vol=股、"
            "amount=元，分钟求和/日K 比值≈1（float32）；amount/vol 落在 OHLC "
            "范围内。范围仅限股票日K/1分K，不推广到 5分/竞价/quote/指数。"
            "原始证据 SHA256 a27c1f41…0c50f（codex-bridge 开发期 JSON，不随包发布）。"
            "effective_from 仅为测量日期标识；严格 PIT 授权以行内 binding 的 "
            "calibrated_at（实际采集/写入时刻）为准，不倒写当天 00:00。"),
        effective_from="2026-11-18T00:00:00+08:00",
    ),
)


def resolve_spec(provenance: Optional[CaptureProvenance], *, period: str,
                 category: str = "stock") -> Optional[CalibrationSpec]:
    """按真实解析器身份/周期/品类解析唯一匹配规格；无匹配返回 None。"""
    for spec in PRODUCTION_CALIBRATIONS:
        if spec.matches(provenance, period=period, category=category):
            return spec
    return None


def semantics_for_capture(provenance: Optional[CaptureProvenance], *,
                          period: str, category: str = "stock") -> dict:
    """采集写入时的行内语义与校准 ID；无匹配时全部 unverified。"""
    spec = resolve_spec(provenance, period=period, category=category)
    if spec is None:
        return {"time_semantics": UNVERIFIED, "unit_semantics": UNVERIFIED,
                "content_semantics": UNVERIFIED, "calibration_id": ""}
    return {
        "time_semantics": spec.time_semantics,
        "unit_semantics": spec.unit_semantics,
        "content_semantics": spec.content_semantics,
        "calibration_id": spec.calibration_id,
    }


def calibration_manifest(provenance: Optional[CaptureProvenance]) -> dict:
    """证据快照/采集审计用 manifest：记录实际身份与命中的规格，未知也写明。"""
    matched = []
    for spec in PRODUCTION_CALIBRATIONS:
        if provenance is not None and spec.matches(
                provenance, period=spec.periods[0], category=spec.category):
            matched.append(spec.calibration_id)
    return {
        "calibration_version": CALIBRATION_VERSION,
        "provenance": provenance.to_dict() if provenance is not None else None,
        "matched_specs": matched,
        "verified": bool(matched),
    }


def binding_for(calibration_id: str, *, calibrated_at: str, source: str,
                period: str, category: str = "stock") -> Optional[dict]:
    """采集写入时冻结到行上的**来源校准绑定**（不依赖读取机器指纹）。"""
    spec = next((item for item in PRODUCTION_CALIBRATIONS
                 if item.calibration_id == calibration_id), None)
    if spec is None or spec.provenance.source != source \
            or period not in spec.periods or category != spec.category:
        return None
    return {
        "calibration_id": spec.calibration_id,
        "source": source,
        "period": period,
        "category": category,
        "protocol": spec.provenance.protocol,
        "install_version": spec.provenance.install_version,
        "converter_sha256": dict(spec.provenance.converter_sha256),
        "bar_time": spec.provenance.bar_time,
        "adjustment": spec.provenance.adjustment,
        "evidence": spec.evidence,
        "effective_from": spec.effective_from,
        "calibrated_at": calibrated_at,
        "time_semantics": spec.time_semantics,
        "unit_semantics": spec.unit_semantics,
        "content_semantics": spec.content_semantics,
    }


def verify_binding(binding: Optional[Mapping], *, source: str, period: str,
                   category: str = "stock") -> Optional[CalibrationSpec]:
    """按**行内冻结绑定**校验来源/协议/版本/代码指纹；不信任读取机器指纹。"""
    if not isinstance(binding, Mapping):
        return None
    spec = next((item for item in PRODUCTION_CALIBRATIONS
                 if item.calibration_id == binding.get("calibration_id")), None)
    if spec is None or spec.provenance.source != source \
            or period not in spec.periods or category != spec.category:
        return None
    if binding.get("source") != source or binding.get("period") != period \
            or binding.get("category") != category:
        return None
    if binding.get("protocol") != spec.provenance.protocol \
            or binding.get("install_version") != spec.provenance.install_version \
            or binding.get("bar_time") != spec.provenance.bar_time \
            or binding.get("adjustment") != spec.provenance.adjustment:
        return None
    if dict(binding.get("converter_sha256") or {}) != dict(
            spec.provenance.converter_sha256):
        return None
    return spec


__all__ = [
    "CALIBRATION_VERSION",
    "VERIFIED",
    "UNVERIFIED",
    "CaptureProvenance",
    "CalibrationSpec",
    "PRODUCTION_CALIBRATIONS",
    "current_tdx_provenance",
    "resolve_spec",
    "semantics_for_capture",
    "calibration_manifest",
    "binding_for",
    "verify_binding",
]
