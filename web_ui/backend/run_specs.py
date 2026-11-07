"""命令运行注册表：参数定义 / 时间窗 / argv 组装（纯数据，无 I/O）。

Web 层**不 import** ``dragon_quant`` 的写入实现：这里只把允许暴露给 UI 的参数
与对应 CLI 形态收敛成一份注册表，路由按 job 查表分发。新增命令只需加一条
``JobSpec``，不改路由 / 状态机 / 前端表单结构（前端由 ``spec_payload`` 驱动）。

安全边界：
  - 只接受注册表声明的字段；未知字段直接 400，不做静默透传；
  - argv 以**列表**交给 ``Popen``，绝不拼 shell 字符串；
  - 刻意不暴露会绕过门禁或打爆数据源的开关（``scan --force`` /
    ``scan --no-cache`` / ``scan --date`` / ``auction --recompute``）。
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from typing import Any, Callable, Literal

JobName = Literal["scan", "prep", "auction"]
ParamKind = Literal["int", "bool", "enum"]

#: 三个命令的统一时间窗上限：避免跨日误跑（次日 00:00 起当日记录口径已变）。
WINDOW_END = (23, 55)


class UnknownJob(KeyError):
    """未注册的 job（路由转 404）。"""


class ParamError(ValueError):
    """参数非法（路由转 400，``message`` 直接给用户看）。"""

    def __init__(self, field: str, message: str) -> None:
        super().__init__(message)
        self.field = field
        self.message = message


@dataclass(frozen=True)
class ParamSpec:
    """一个可配置参数；``to_dict`` 是前端表单的唯一真源。"""

    name: str
    label: str
    kind: ParamKind
    default: Any
    minimum: int | None = None
    maximum: int | None = None
    choices: tuple[tuple[str, str], ...] = ()  # (value, label)
    help: str = ""
    depends_on: tuple[str, str] | None = None  # (字段名, 该取值下才生效)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "name": self.name,
            "label": self.label,
            "kind": self.kind,
            "default": self.default,
            "help": self.help,
        }
        if self.minimum is not None:
            payload["minimum"] = self.minimum
        if self.maximum is not None:
            payload["maximum"] = self.maximum
        if self.choices:
            payload["choices"] = [
                {"value": value, "label": label} for value, label in self.choices
            ]
        if self.depends_on is not None:
            payload["depends_on"] = {
                "param": self.depends_on[0],
                "value": self.depends_on[1],
            }
        return payload


@dataclass(frozen=True)
class JobSpec:
    """一个可从 dashboard 触发的 CLI 命令。"""

    name: JobName
    label: str
    quick_label: str
    custom_label: str
    window_start: tuple[int, int]
    window_end: tuple[int, int]
    done_text: str
    params: tuple[ParamSpec, ...]
    command: Callable[[dict[str, Any], str], list[str]]
    requires_scan: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "job": self.name,
            "label": self.label,
            "quick_label": self.quick_label,
            "custom_label": self.custom_label,
            "window": {
                "start": f"{self.window_start[0]:02d}:{self.window_start[1]:02d}",
                "end": f"{self.window_end[0]:02d}:{self.window_end[1]:02d}",
            },
            "params": [param.to_dict() for param in self.params],
        }


# ─── argv 组装（只读注册表字段；列表形式，无 shell） ─────────────────────────


def _base() -> list[str]:
    return [sys.executable, "-m", "dragon_quant"]


def _scan_command(params: dict[str, Any], trade_date: str) -> list[str]:
    _ = trade_date  # scan 固定当日实时扫描；历史查询不进 UI
    return _base() + [
        "scan",
        "--top", str(params["top"]),
        "--candidates", str(params["candidates"]),
        "--workers", str(params["workers"]),
    ]


def _prep_command(params: dict[str, Any], trade_date: str) -> list[str]:
    # 显式 --date 当日：避免 CLI 缺省回退到「最新龙头日」而静默复盘到昨天。
    argv = _base() + ["prep", "--date", trade_date.replace("-", "")]
    mode = params["mode"]
    if mode == "force":
        argv.append("--force")
    elif mode == "recompute":
        argv.append("--recompute")
    return argv


def _auction_command(params: dict[str, Any], trade_date: str) -> list[str]:
    _ = trade_date  # auction 只采集当日盘口
    argv = _base() + ["auction", "--pool", str(params["pool"])]
    if params["pool"] == "legacy":
        argv += ["--days", str(params["days"])]
    if params["force"]:
        argv.append("--force")
    return argv


# ─── 注册表 ────────────────────────────────────────────────────────────────

RUN_SPECS: dict[str, JobSpec] = {
    "scan": JobSpec(
        name="scan",
        label="龙头扫描",
        quick_label="龙头扫描",
        custom_label="自定义扫描",
        window_start=(15, 0),
        window_end=WINDOW_END,
        done_text="今日已有龙头扫描记录，不能重复执行",
        params=(
            ParamSpec("top", "真龙数量", "int", 5, minimum=1, maximum=100,
                      help="最多展示和入选的真龙数量"),
            ParamSpec("candidates", "每板块候选", "int", 5, minimum=1, maximum=20,
                      help="每个板块取前 N 只"),
            ParamSpec("workers", "并发线程", "int", 2, minimum=1, maximum=8,
                      help="数据源并发线程数"),
        ),
        command=_scan_command,
    ),
    "prep": JobSpec(
        name="prep",
        label="盘后复盘",
        quick_label="今日复盘",
        custom_label="自定义复盘",
        window_start=(21, 0),
        window_end=WINDOW_END,
        done_text="今日已完成复盘，不能重复执行",
        requires_scan=True,
        params=(
            ParamSpec(
                "mode", "运行模式", "enum", "incremental",
                choices=(
                    ("incremental", "增量采集（默认）"),
                    ("force", "强制重采全部资产"),
                    ("recompute", "只用已存资产离线重算"),
                ),
                help="强制重采不绕过 16:40 与交易日校验",
            ),
        ),
        command=_prep_command,
    ),
    "auction": JobSpec(
        name="auction",
        label="集合竞价",
        quick_label="竞价强度判断",
        custom_label="自定义竞价判断",
        window_start=(9, 25),
        window_end=WINDOW_END,
        done_text="今日已执行过竞价判断，不能重复执行",
        params=(
            ParamSpec(
                "pool", "候选池来源", "enum", "prep",
                choices=(("prep", "盘后复盘推荐（默认）"), ("legacy", "最近 N 日龙头并集")),
                help="prep 池为空时 CLI 会回退到旧口径",
            ),
            ParamSpec("days", "回看交易日", "int", 5, minimum=1, maximum=30,
                      help="仅 legacy / 回退路径生效", depends_on=("pool", "legacy")),
            ParamSpec("force", "强制重新采集", "bool", False,
                      help="忽略当日已存快照，重新采集并替换"),
        ),
        command=_auction_command,
    ),
}


def get_spec(job: str) -> JobSpec:
    spec = RUN_SPECS.get(job)
    if spec is None:
        raise UnknownJob(job)
    return spec


def spec_payload(job: str) -> dict[str, Any]:
    return get_spec(job).to_dict()


# ─── 参数校验（前端同规则，但服务端是最终权威） ─────────────────────────────

_INT_TEXT = re.compile(r"[+-]?\d+")


def _coerce_int(spec: ParamSpec, raw: Any) -> int:
    if isinstance(raw, bool):
        raise ParamError(spec.name, f"{spec.label} 必须是整数")
    if isinstance(raw, int):
        value = raw
    elif isinstance(raw, float) and raw.is_integer():
        value = int(raw)
    elif isinstance(raw, str) and _INT_TEXT.fullmatch(raw.strip()):
        value = int(raw.strip())
    else:
        raise ParamError(spec.name, f"{spec.label} 必须是整数")
    if spec.minimum is not None and value < spec.minimum:
        raise ParamError(spec.name, f"{spec.label} 不能小于 {spec.minimum}")
    if spec.maximum is not None and value > spec.maximum:
        raise ParamError(spec.name, f"{spec.label} 不能大于 {spec.maximum}")
    return value


def _coerce_bool(spec: ParamSpec, raw: Any) -> bool:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, int) and raw in (0, 1):
        return bool(raw)
    if isinstance(raw, str):
        text = raw.strip().lower()
        if text in ("true", "1", "yes", "on"):
            return True
        if text in ("false", "0", "no", "off", ""):
            return False
    raise ParamError(spec.name, f"{spec.label} 必须是布尔值")


def _coerce_enum(spec: ParamSpec, raw: Any) -> str:
    allowed = {value for value, _ in spec.choices}
    value = raw.strip() if isinstance(raw, str) else raw
    if value not in allowed:
        joined = " / ".join(sorted(allowed))
        raise ParamError(spec.name, f"{spec.label} 只能是 {joined}")
    return str(value)


def validate_params(job: str, raw: dict[str, Any] | None) -> dict[str, Any]:
    """把请求体收敛成完整参数表；未知字段 / 类型 / 范围非法都抛 ``ParamError``。"""
    spec = get_spec(job)
    payload = raw or {}
    if not isinstance(payload, dict):
        raise ParamError("", "请求体必须是 JSON 对象")
    known = {param.name for param in spec.params}
    unknown = sorted(set(payload) - known)
    if unknown:
        raise ParamError(unknown[0], f"未知参数：{unknown[0]}")

    clean: dict[str, Any] = {}
    for param in spec.params:
        value = payload.get(param.name, param.default)
        if param.kind == "int":
            clean[param.name] = _coerce_int(param, value)
        elif param.kind == "bool":
            clean[param.name] = _coerce_bool(param, value)
        else:
            clean[param.name] = _coerce_enum(param, value)
    return clean


def build_argv(job: str, params: dict[str, Any], *, trade_date: str) -> list[str]:
    """按注册表组装 argv；``trade_date`` 为 ISO（``YYYY-MM-DD``）。"""
    return list(get_spec(job).command(params, trade_date))


__all__ = [
    "JobName",
    "JobSpec",
    "ParamError",
    "ParamSpec",
    "RUN_SPECS",
    "UnknownJob",
    "WINDOW_END",
    "build_argv",
    "get_spec",
    "spec_payload",
    "validate_params",
]
