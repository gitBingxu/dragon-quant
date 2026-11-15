"""策略装配与统一运行时：内建规则 Adapter + 用户 workspace 策略（方案 §13.2）。

关键契约（main 验收修订）：

- 冻结优先：任何已知 ``strategy_ref`` 都只从快照执行；即使 strategy_id 与活动策略
  相同也不得冒用当前源码/参数。快照缺失/损坏/不兼容 → 明确拒绝，不静默退当前或内建。
- 只有真正无 ref 的旧仓才走 legacy 内建；内建旧仓使用自己冻结的参数。
- 用户 Context 按可得时点裁剪：开盘/盘中不暴露当日最终 OHLCV，分时按事件切片，
  KBar 转为不可变纯数据（不共享可变引用）。
"""

from __future__ import annotations

import dataclasses
from dataclasses import asdict
from typing import Optional

from dragon_quant.review_account.models import Position, StrategyConfig
from dragon_quant.review_account.strategy.main import evaluate_buy, evaluate_sell
from dragon_quant.strategy_sdk import (
    FACTOR_STATUS_MISSING,
    StrategyContext,
    StrategyIntent,
    validate_intent,
)
from dragon_quant.workspace.config import BUILTIN_STRATEGY_ID
from dragon_quant.workspace.loader import coerce_intent, create_strategy

STRATEGY_SOURCE_BUILTIN = "builtin"
STRATEGY_SOURCE_USER = "user"

#: 用户 Context 中「当日最终」聚合字段：close 之前一律不得暴露。
_DAILY_AGGREGATE_FIELDS = ("close", "high", "low", "pct", "amount", "turnover", "volume")
#: daily_fallback 开盘事件可公开的字段（其余派生指标都含当日最终值，必须删除）。
#: 需要均线等历史指标时读 ``row["prev_row"]``（上一交易日已完成数据）。
_DAILY_FALLBACK_OPEN_FIELDS = frozenset({
    "date", "phase", "observed_at", "data_quality", "execution_price",
    "execution_timestamp", "open", "open_gap_pct", "prev_close", "prev_volume",
    "prev_high", "prev_low", "limit_up", "limit_down", "previous_ma5",
    "bar_high", "bar_low", "bar_close", "bar_timestamp", "prev_row", "hist_rows",
})


class StrategyRuntimeError(ValueError, RuntimeError):
    """用户策略执行失败（带策略 ID / 版本，不得静默退内建）。

    同时继承 ``ValueError``：CLI/Web 的既有域错误通道会把它映射为可操作错误
    （非零退出 / 400），而不是未捕获 traceback。
    """

    def __init__(self, strategy_id: str, message: str):
        self.strategy_id = strategy_id
        super().__init__(f"策略 {strategy_id} 执行失败：{message}")


class FrozenReferenceError(StrategyRuntimeError):
    """已知归属的策略快照缺失/损坏/环境不兼容：拒绝本次写操作。"""


class BuiltinStrategyRuntime:
    """内建规则适配器：包装既有 `evaluate_buy` / `evaluate_sell`。

    ``controls`` 可为该持仓冻结的内建参数；``evaluate_*`` 收到的 ``cfg`` 是账户
    级基础配置，用于该持仓时优先使用冻结参数，避免「旧仓用当前 cfg 止损」。
    """

    source = STRATEGY_SOURCE_BUILTIN
    strategy_id = BUILTIN_STRATEGY_ID

    def __init__(self, *, parameters: Optional[dict] = None,
                 strategy_ref: Optional[dict] = None,
                 controls: Optional[StrategyConfig] = None):
        self.parameters = dict(parameters or {})
        self.strategy_ref = dict(strategy_ref or {})
        self.controls = controls
        self.last_note: Optional[str] = None
        self.last_note_code: Optional[str] = None

    def _cfg(self, base: StrategyConfig) -> StrategyConfig:
        return self.controls if self.controls is not None else base

    def evaluate_entry(self, *, candidate, row, cfg, prev_row, hist_rows,
                       intraday_bars, sentiment_pct, trade_date, timestamp,
                       phase, account) -> Optional[dict]:
        self.last_note = None
        self.last_note_code = None
        return evaluate_buy(candidate, row, self._cfg(cfg), prev_row, hist_rows,
                            intraday_bars, sentiment_pct)

    def evaluate_exit(self, *, position, row, hold_days, cfg, intraday_bars,
                      trade_date, timestamp, phase, account) -> Optional[dict]:
        signals = evaluate_sell(position, row, hold_days, self._cfg(cfg), intraday_bars)
        return signals[0] if signals else None

    def explain_buy_candidate(self, *args, **kwargs):
        from dragon_quant.review_account.strategy.main import explain_buy_candidate

        if self.controls is not None and len(args) >= 3:
            args = list(args)
            args[2] = self.controls
            args = tuple(args)
        return explain_buy_candidate(*args, **kwargs)

    def explain_buy_checks(self, *args, **kwargs):
        from dragon_quant.review_account.strategy.main import explain_buy_checks

        return explain_buy_checks(*args, **kwargs)


def _freeze_bar(value):
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    return value


def _safe_intraday_bars(bars, timestamp: int) -> list[dict]:
    """只暴露截至事件时点的已完成分时，并转为纯 dict（不共享 KBar 可变对象）。"""
    result: list[dict] = []
    for bar in bars or []:
        bar_timestamp = getattr(bar, "timestamp", None)
        if isinstance(bar, dict):
            bar_timestamp = bar.get("timestamp", bar_timestamp)
        if bar_timestamp is not None and int(bar_timestamp) > int(timestamp):
            continue
        result.append(_freeze_bar(bar))
    return result


def sanitize_row_for_context(row: Optional[dict], *, phase: str,
                             timestamp: int) -> Optional[dict]:
    """按可得时点裁剪用户 Context 的行情行（白名单优先，避免补漏）。

    - ``daily_fallback`` 的 **open** 事件使用当日完整日K构造 row，其 close/high/low/
      volume 及全部派生指标（ma5/ma10/ma20/volume_ma5/amount_ma5/close_to_ma5_pct/
      return_3d/return_5d/max_drawdown_5d/avg_amplitude_5/atr 等）都含未来信息：
      只公开「开盘价 + 上一交易日/历史 + 当前 bar_* + 限制/事件字段」。
      需要均线等历史指标时读 ``row["prev_row"]``。
    - 分时 ``build_row`` 的当日累计值只由已完成的 bars 计算，属可知数据，保留；
      ``intraday_bars`` 仍按事件时间切片并转纯 dict。
    - 内建引擎读原始 row，golden 行为不变。
    """
    if row is None:
        return None
    if row.get("data_quality") == "daily_fallback" and phase == "open":
        sanitized = {key: row[key] for key in _DAILY_FALLBACK_OPEN_FIELDS if key in row}
    else:
        sanitized = dict(row)
    sanitized["intraday_bars"] = _safe_intraday_bars(
        row.get("intraday_bars"), timestamp)
    return sanitized


class UserStrategyRuntime:
    """用户 workspace 策略运行时：构造只读 Context，校验 Intent，转成引擎信号。"""

    source = STRATEGY_SOURCE_USER

    def __init__(self, *, selection, instance, controls: StrategyConfig,
                 factor_reader=None, strategy_ref: Optional[dict] = None,
                 required_factors=None, factor_versions=None):
        self.selection = selection
        self.instance = instance
        self.controls = controls
        self.factor_reader = factor_reader
        self.strategy_id = selection.strategy_id
        self.descriptor = selection.descriptor
        self.parameters = dict(selection.parameters)
        self.strategy_ref = dict(strategy_ref or {})
        self.required_factors = tuple(
            required_factors if required_factors is not None
            else selection.descriptor.required_factors or ())
        self.factor_versions = dict(factor_versions or {})
        self.last_note: Optional[str] = None
        self.last_note_code: Optional[str] = None

    # ── 决策 ──
    def evaluate_entry(self, *, candidate, row, cfg, prev_row, hist_rows,
                       intraday_bars, sentiment_pct, trade_date, timestamp,
                       phase, account) -> Optional[dict]:
        self.last_note = None
        self.last_note_code = None
        if row is None:
            return None
        candidate = candidate or {}
        factors = self._factors(code=candidate.get("code"),
                                factor_date=candidate.get("trade_date") or row.get("date"),
                                timestamp=timestamp, phase=phase)
        if not self._required_factors_ok(factors):
            return None
        ctx = self._context(candidate=candidate, row=row, position=None,
                            account=account, trade_date=trade_date,
                            timestamp=timestamp, phase=phase, factors=factors)
        intent = self._call("evaluate_entry", ctx)
        if intent is None:
            return None
        intent = self._validate(intent, "buy", "evaluate_entry")
        return self._entry_signal(intent, row, candidate)

    def evaluate_exit(self, *, position, row, hold_days, cfg, intraday_bars,
                      trade_date, timestamp, phase, account) -> Optional[dict]:
        self.last_note = None
        self.last_note_code = None
        if row is None:
            return None
        factors = self._factors(code=position.code,
                                factor_date=row.get("date"),
                                timestamp=timestamp, phase=phase)
        # 退出的缺失处理显式声明：必要因子缺失 → 不产生自定义退出信号并告警。
        if not self._required_factors_ok(factors, for_exit=True):
            return None
        ctx = self._context(candidate={"code": position.code},
                            row=row, position=asdict(position),
                            account=account, trade_date=trade_date,
                            timestamp=timestamp, phase=phase, factors=factors)
        intent = self._call("evaluate_exit", ctx)
        if intent is None:
            return None
        intent = self._validate(intent, "sell", "evaluate_exit")
        return self._exit_signal(intent, row, position)

    def _validate(self, intent, action: str, method: str) -> StrategyIntent:
        try:
            return validate_intent(intent, expected_action=action)
        except Exception as exc:  # noqa: BLE001
            raise StrategyRuntimeError(
                self.strategy_id, f"{method} 返回非法：{exc}") from exc

    def _call(self, method: str, ctx: StrategyContext):
        fn = getattr(self.instance, method)
        try:
            result = fn(ctx)
        except Exception as exc:  # noqa: BLE001 - 用户代码异常必须显式失败
            raise StrategyRuntimeError(
                self.strategy_id, f"{method} 抛出 {type(exc).__name__}: {exc}") from exc
        try:
            return coerce_intent(
                result, expected_action=("buy" if method == "evaluate_entry" else "sell"))
        except Exception as exc:  # noqa: BLE001
            if isinstance(exc, StrategyRuntimeError):
                raise
            raise StrategyRuntimeError(
                self.strategy_id, f"{method} 返回非法：{exc}") from exc

    # ── Context 构造 ──
    def _context(self, *, candidate, row, position, account, trade_date,
                 timestamp, phase, factors) -> StrategyContext:
        return StrategyContext.build(
            timestamp=timestamp, trade_date=trade_date, phase=phase,
            candidate=candidate,
            row=sanitize_row_for_context(row, phase=phase, timestamp=timestamp),
            position=position, account=account,
            parameters=dict(self.parameters), factors=factors)

    def _factors(self, *, code, factor_date, timestamp, phase) -> dict:
        if self.factor_reader is None:
            return {}
        if not code:
            return {}
        try:
            return self.factor_reader.values_for(
                trade_date=factor_date, code=code, as_of_ms=timestamp,
                phase=phase, versions=self.factor_versions)
        except Exception as exc:  # noqa: BLE001 - 读取故障必须显式失败，不伪造缺失
            raise StrategyRuntimeError(
                self.strategy_id, f"因子读取失败：{type(exc).__name__}: {exc}") from exc

    def _required_factors_ok(self, factors: dict, *, for_exit: bool = False) -> bool:
        if not self.required_factors:
            return True
        missing = [name for name in self.required_factors
                   if name not in factors or factors[name].value is None
                   or factors[name].status == FACTOR_STATUS_MISSING]
        if not missing:
            return True
        self.last_note_code = "strategy_required_factor_missing"
        self.last_note = f"必要因子缺失：{'、'.join(missing)}"
        if for_exit:
            self.last_note = f"自定义退出跳过：{self.last_note}"
        return False

    # ── Intent → 引擎信号 ──
    def _entry_signal(self, intent: StrategyIntent, row: dict, candidate: dict) -> dict:
        priority = int(round(intent.priority))
        signal = {
            "strategy_id": self.strategy_id,
            "strategy_source": STRATEGY_SOURCE_USER,
            "strategy_ref": dict(self.strategy_ref),
            "reason_code": intent.reason_code,
            "position_pct": intent.position_pct,
            "evidence": dict(intent.evidence),
            "date": row.get("date"),
            "phase": row.get("phase"),
            "observed_at": row.get("observed_at"),
            "candidate_trade_date": (candidate or {}).get("trade_date"),
            "rank": (candidate or {}).get("rank"),
        }
        return {"action": "BUY", "code": intent.reason_code,
                "reason_text": intent.reason_text or intent.reason_code,
                "priority": priority, "signal": signal}

    def _exit_signal(self, intent: StrategyIntent, row: dict, position: Position) -> dict:
        fraction = float(intent.exit_fraction or 1.0)
        signal = {
            "strategy_id": self.strategy_id,
            "strategy_source": STRATEGY_SOURCE_USER,
            "strategy_ref": dict(self.strategy_ref),
            "reason_code": intent.reason_code,
            "exit_fraction": fraction,
            "evidence": dict(intent.evidence),
            "date": row.get("date"),
            "phase": row.get("phase"),
            "observed_at": row.get("observed_at"),
        }
        return {"action": "SELL", "code": intent.reason_code,
                "reason_text": intent.reason_text or intent.reason_code,
                "signal": signal}

    def explain_buy_candidate(self, candidate, row, cfg, prev_row=None,
                              hist_rows=None, intraday_bars=None,
                              sentiment_pct=None):
        detail = {
            "code": (candidate or {}).get("code"),
            "name": (candidate or {}).get("name", ""),
            "rank": (candidate or {}).get("rank"),
            "passed": False, "reason_code": "no_signal",
            "reason_text": "策略未给出买入信号",
            "signal": {}, "full_signal": None,
            "observed_at": (row or {}).get("observed_at"),
            "phase": (row or {}).get("phase"),
            "checks": [],
            "strategy_id": self.strategy_id,
        }
        if row is None:
            detail["reason_code"] = "no_row"
            detail["reason_text"] = "缺少行情行"
            return detail
        try:
            signal = self.evaluate_entry(
                candidate=candidate, row=row, cfg=self.controls, prev_row=prev_row,
                hist_rows=hist_rows, intraday_bars=intraday_bars,
                sentiment_pct=sentiment_pct, trade_date=row.get("date", ""),
                timestamp=int(row.get("observed_at") or 0),
                phase=row.get("phase", ""), account={"cash": 0, "position_count": 0})
        except StrategyRuntimeError:
            raise
        if signal:
            detail.update({"passed": True, "reason_code": signal["code"],
                           "reason_text": signal["reason_text"],
                           "signal": signal.get("signal") or {},
                           "full_signal": signal})
        elif self.last_note:
            detail["reason_code"] = self.last_note_code or "no_signal"
            detail["reason_text"] = self.last_note
        return detail

    def explain_buy_checks(self, *args, **kwargs):
        return []


# ─── 独立进程 worker 运行时（Web 执行用户代码的唯一入口） ────────────────


class WorkerUserStrategyRuntime(UserStrategyRuntime):
    """在独立子进程中执行用户策略；父进程只收发版本化 JSON。

    因子读取仍在本进程（只读存储，不执行用户代码）；用户代码异常/超时/坏输出
    显式失败并清理子进程。worker 不是安全沙箱。
    """

    def __init__(self, *, selection, controls: StrategyConfig, init_payload: dict,
                 factor_reader=None, strategy_ref=None, required_factors=None,
                 factor_versions=None, timeout: float = 20.0):
        super().__init__(selection=selection, instance=None, controls=controls,
                         factor_reader=factor_reader, strategy_ref=strategy_ref,
                         required_factors=required_factors,
                         factor_versions=factor_versions)
        import queue
        import subprocess
        import sys
        import threading

        self.timeout = timeout
        self._queue: "queue.Queue" = queue.Queue()
        self._proc = subprocess.Popen(
            [sys.executable, "-m", "dragon_quant.workspace.worker"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, bufsize=1)
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        try:
            self._send({"op": "init", "protocol": 1, **init_payload})
            self._recv()
        except Exception:
            self.close()
            raise

    def _read_loop(self) -> None:
        try:
            for line in self._proc.stdout:  # type: ignore[union-attr]
                self._queue.put(line)
        finally:
            self._queue.put(None)

    def _send(self, payload: dict) -> None:
        import json

        try:
            assert self._proc.stdin is not None
            self._proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
            self._proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise StrategyRuntimeError(self.strategy_id, f"worker 进程已退出：{exc}") from exc

    def _recv(self) -> dict:
        import json

        try:
            line = self._queue.get(timeout=self.timeout)
        except Exception as exc:  # queue.Empty
            self.close()
            raise StrategyRuntimeError(
                self.strategy_id, f"worker 超时（>{self.timeout}s）或已崩溃") from exc
        if line is None:
            self.close()
            raise StrategyRuntimeError(self.strategy_id, "worker 进程无响应（EOF）")
        try:
            reply = json.loads(line)
        except json.JSONDecodeError as exc:
            self.close()
            raise StrategyRuntimeError(self.strategy_id, f"worker 返回坏 JSON：{exc}") from exc
        if not reply.get("ok"):
            raise StrategyRuntimeError(self.strategy_id, str(reply.get("error")))
        return reply

    def _call(self, method: str, ctx: StrategyContext):
        from dragon_quant.strategy_sdk import context_to_payload

        kind = "entry" if method == "evaluate_entry" else "exit"
        self._send({"op": "eval", "kind": kind, "context": context_to_payload(ctx)})
        reply = self._recv()
        intent = reply.get("intent")
        if intent is None:
            return None
        return StrategyIntent(**intent)

    def close(self) -> None:
        if getattr(self, "_closed", False):
            return
        self._closed = True
        try:
            if self._proc.poll() is None:
                self._send({"op": "shutdown"})
        except Exception:  # noqa: BLE001
            pass
        try:
            self._proc.terminate()
        except Exception:  # noqa: BLE001
            pass
        try:
            self._proc.wait(timeout=2)
        except Exception:  # noqa: BLE001
            try:
                self._proc.kill()
            except Exception:  # noqa: BLE001
                pass


# ─── 运行时装配 ────────────────────────────────────────────────────────────


def build_builtin_runtime(cfg: StrategyConfig, *, parameters=None,
                          strategy_ref=None) -> BuiltinStrategyRuntime:
    params = dict(parameters or {})
    ref = dict(strategy_ref or {"schema_version": 1,
                                "strategy_id": BUILTIN_STRATEGY_ID})
    controls = None
    if params:
        try:
            controls = StrategyConfig.from_dict({**cfg.to_json_dict(), **params})
        except (TypeError, ValueError):
            controls = None
    return BuiltinStrategyRuntime(parameters=params, strategy_ref=ref,
                                  controls=controls)


def build_user_runtime(selection, controls: StrategyConfig, *,
                       factor_reader=None, strategy_ref=None,
                       required_factors=None, factor_versions=None) -> UserStrategyRuntime:
    """构造用户策略实例（显式执行路径才导入模块）。"""
    config = selection.workspace
    if config is None:
        raise StrategyRuntimeError(selection.strategy_id, "缺少 workspace")
    try:
        instance, _merged = create_strategy(config, selection.descriptor,
                                            selection.parameters)
    except Exception as exc:  # noqa: BLE001
        raise StrategyRuntimeError(selection.strategy_id, str(exc)) from exc
    return UserStrategyRuntime(
        selection=selection, instance=instance, controls=controls,
        factor_reader=factor_reader,
        strategy_ref=(strategy_ref or {"schema_version": 1,
                                       "strategy_id": selection.strategy_id}),
        required_factors=required_factors, factor_versions=factor_versions)


def build_runtime_for_selection(selection, controls: StrategyConfig, *,
                                factor_reader=None, strategy_ref=None,
                                required_factors=None, factor_versions=None):
    if selection.descriptor.source == "builtin":
        return build_builtin_runtime(controls, parameters=selection.parameters,
                                     strategy_ref=strategy_ref)
    return build_user_runtime(selection, controls, factor_reader=factor_reader,
                              strategy_ref=strategy_ref,
                              required_factors=required_factors,
                              factor_versions=factor_versions)


def ref_identity(ref: Optional[dict]) -> tuple:
    """冻结身份（不含展示字段）；相同身份才允许复用活动运行时。"""
    ref = ref or {}
    versions = ref.get("factor_versions") or {}
    version_key = tuple(sorted((str(k), str(v)) for k, v in versions.items()))
    return (ref.get("workspace_id"), ref.get("strategy_id"), ref.get("snapshot_id"),
            ref.get("params_hash"), version_key)


class FrozenRuntimeProvider:
    """按 ``strategy_ref`` 构造冻结运行时（缓存 + 缺失即拒绝）。"""

    def __init__(self, controls: StrategyConfig, *, factor_reader_factory=None,
                 worker_factory=None):
        self.controls = controls
        self.factor_reader_factory = factor_reader_factory
        self.worker_factory = worker_factory
        self._cache: dict[tuple, object] = {}

    # 供引擎/服务复用
    def resolve(self, strategy_id: str, ref: dict):
        ref = dict(ref or {})
        if strategy_id:
            ref.setdefault("strategy_id", strategy_id)
        return self.build(ref)

    def close(self) -> None:
        for runtime in self._cache.values():
            closer = getattr(runtime, "close", None)
            if callable(closer):
                closer()

    def build(self, ref: dict, *, fresh: bool = False):
        ref = dict(ref or {})
        self._verify_ref(ref)
        key = ref_identity(ref)
        if not fresh and key in self._cache:
            return self._cache[key]
        if not ref.get("strategy_id"):
            raise FrozenReferenceError("<unknown>", "缺少 strategy_id，无法解析策略版本")
        runtime = self._build(ref)
        if not fresh:
            # fresh=True 用于独立模拟（每个 split / 参数变体各建实例），不跨实验复用。
            self._cache[key] = runtime
        return runtime

    @staticmethod
    def _verify_ref(ref: dict) -> None:
        """引用自洽性：params 必须与其 params_hash 一致（缓存前校验，防伪造复用）。"""
        strategy_id = str(ref.get("strategy_id") or "<unknown>")
        if not strategy_id.startswith("user:"):
            return
        from dragon_quant.workspace import snapshots

        params = dict(ref.get("params") or {})
        expected = snapshots.params_hash(params)
        if not ref.get("params_hash") or ref["params_hash"] != expected:
            raise FrozenReferenceError(
                strategy_id, "params 与 params_hash 不一致（拒绝伪造引用）")

    def _build(self, ref: dict):
        strategy_id = str(ref["strategy_id"])
        if strategy_id.startswith("user:"):
            return self._from_snapshot(ref)
        return self._builtin(ref)

    def _builtin(self, ref: dict):
        from dragon_quant.workspace import snapshots

        strategy_id = str(ref["strategy_id"])
        frozen_version = ref.get("toolkit_version")
        current = snapshots.builtin_ref(strategy_id=strategy_id,
                                        params=ref.get("params") or {})
        if frozen_version and frozen_version != current.get("toolkit_version"):
            raise FrozenReferenceError(
                strategy_id,
                f"内建实现版本不一致（冻结 {frozen_version}，当前 "
                f"{current.get('toolkit_version')}），请用匹配版本环境续算")
        return build_builtin_runtime(self.controls, parameters=ref.get("params") or {},
                                     strategy_ref=ref)

    def _from_snapshot(self, ref: dict):
        from pathlib import Path

        from dragon_quant.workspace import catalog, snapshots
        from dragon_quant.workspace.config import WorkspaceConfig
        from dragon_quant.workspace.main import StrategySelection

        strategy_id = str(ref["strategy_id"])
        snapshot_id = ref.get("snapshot_id")
        root = ref.get("workspace")
        if not snapshot_id or not root:
            raise FrozenReferenceError(
                strategy_id, "缺少冻结快照路径/ID，拒绝执行（不冒用当前源码）")
        workspace_id = str(ref.get("workspace_id") or "")
        probe = WorkspaceConfig(root=Path(root), workspace_id=workspace_id, revision=1)
        try:
            snapshot_root = snapshots.snapshot_dir(probe, str(snapshot_id))
            manifest = snapshots.read_snapshot(probe, str(snapshot_id))
        except Exception as exc:  # noqa: BLE001
            raise FrozenReferenceError(
                strategy_id, f"冻结快照 {snapshot_id} 缺失或损坏：{exc}") from exc
        ok, reason = snapshots.snapshot_compatible(manifest)
        if not ok:
            raise FrozenReferenceError(strategy_id, reason)
        verified = snapshots.verify_snapshot(probe, str(snapshot_id))
        if not verified["ok"]:
            raise FrozenReferenceError(
                strategy_id, "冻结快照内容哈希不一致（源码被篡改或损坏）")
        parameters = dict(ref.get("params") or {})
        expected_hash = snapshots.params_hash(parameters)
        if ref.get("params_hash") and ref["params_hash"] != expected_hash:
            raise FrozenReferenceError(
                strategy_id, "params 与 params_hash 不一致（拒绝伪造引用）")
        if manifest.get("params_hash") != expected_hash:
            raise FrozenReferenceError(
                strategy_id, "快照参数哈希与引用不一致（拒绝伪造引用）")
        files = manifest.get("files") or {}
        descriptor_rel = next(
            (rel for rel in files if rel == "strategy.json"
             or rel.endswith("/strategy.json")), None)
        if descriptor_rel is None:
            raise FrozenReferenceError(strategy_id, "冻结快照缺少 strategy.json")
        config = WorkspaceConfig(
            root=snapshot_root, workspace_id=workspace_id, revision=1,
            strategies=[descriptor_rel],
            factors=sorted(rel for rel in files if rel.startswith("factors/")))
        descriptor = catalog.load_user_descriptor(snapshot_root, descriptor_rel)
        if not descriptor.valid:
            raise FrozenReferenceError(
                strategy_id, "冻结快照 descriptor 非法：" + "；".join(descriptor.errors))
        selection = StrategySelection(strategy_id=strategy_id, descriptor=descriptor,
                                      workspace=config, parameters=parameters)
        required = tuple(ref.get("required_factors") or descriptor.required_factors or ())
        versions = dict(ref.get("factor_versions") or {})
        reader = (self.factor_reader_factory(required, versions)
                  if self.factor_reader_factory is not None and required else None)
        if self.worker_factory is not None:
            return self.worker_factory(selection, self.controls, ref,
                                       required, versions, snapshot_root, reader)
        return build_user_runtime(selection, self.controls, factor_reader=reader,
                                  strategy_ref=ref, required_factors=required,
                                  factor_versions=versions)


def make_runtime_resolver(base_selection, controls: StrategyConfig, *,
                          factor_reader_factory=None, worker_factory=None):
    """混合归属退出运行时解析器：严格按持仓 ``strategy_ref`` 冻结身份构造。"""
    provider = FrozenRuntimeProvider(controls, factor_reader_factory=factor_reader_factory,
                                     worker_factory=worker_factory)
    return provider.resolve


def make_frozen_runtime_factories(provider: "FrozenRuntimeProvider", ref: dict):
    """实验用运行时工厂（基线 / 参数变体），每次调用都新建独立实例。

    工厂模式：把「按冻结身份构造运行时」与「参数变体快照发布」收敛到唯一实现，
    研究编排与回归测试共用同一条生产路径，避免测试自建等价逻辑而漏掉真实缺陷。

    - ``baseline_factory``：同一冻结引用，``fresh=True`` 绕过缓存（split / 成本
      压力各自独立，禁止有状态策略跨 split 污染）。
    - ``variant_factory(params)``：基于**已冻结源码**发布新参数快照
      （新 ``snapshot_id`` / ``params_hash``），再构造独立实例；绝不读取当前
      workspace 源码，也不允许改 ``params`` 复用旧引用。
    """
    frozen = dict(ref or {})

    def baseline_factory():
        return provider.build(frozen, fresh=True)

    def variant_factory(params):
        from dragon_quant.workspace import snapshots

        merged = {**(frozen.get("params") or {}), **dict(params or {})}
        variant_ref = snapshots.publish_parameter_variant(frozen, merged)
        return provider.build(variant_ref, fresh=True)

    return baseline_factory, variant_factory


def make_worker_factory(timeout: float = 20.0):
    """构造 worker 运行时工厂（Web 执行用户代码用）。"""

    def factory(selection, controls, ref, required, versions, snapshot_root, reader):
        workspace = selection.workspace
        descriptor_rel = (workspace.strategies[0] if workspace is not None
                          and workspace.strategies else "strategy.json")
        init_payload = {
            "workspace_root": str(snapshot_root),
            "workspace_id": ref.get("workspace_id"),
            "descriptor_rel": descriptor_rel,
            "params": ref.get("params") or {},
        }
        return WorkerUserStrategyRuntime(
            selection=selection, controls=controls, init_payload=init_payload,
            factor_reader=reader, strategy_ref=ref, required_factors=required,
            factor_versions=versions, timeout=timeout)

    return factory


def make_provider(controls: StrategyConfig, *, factor_reader_factory=None,
                  worker_factory=None) -> FrozenRuntimeProvider:
    return FrozenRuntimeProvider(controls, factor_reader_factory=factor_reader_factory,
                                 worker_factory=worker_factory)
