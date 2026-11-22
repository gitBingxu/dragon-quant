"""外层 owner 保护解析器（Adapter）：账本批次 → 不可变保护义务。

分层（R6/L4）：

- **唯一 I/O 点**：按每个批次的**完整冻结身份**解析运行时/冻结参数；运行时可读
  快照、可起 worker，但只读、不写库、不撮合。
- 解析结果只输出不可变的 :class:`ProtectionObligation`（含 owner/batch/plan
  scope），交给纯核 :func:`evaluate_account_policy`；纯核不接触 DB/worker/回调。
- 同 code 的不同归属/计划逐批评估，绝不先聚合加权成本再触发；坏 owner 只产生
  受限义务与原因，不取消其它 owner 的可确定保护。
- 旧空 ref 沿用既有兼容口径（内建规则 + 当前 controls）；已知归属缺快照/参数/
  版本不兼容时显式受限，不回退当前源码或活动 BS 参数。
"""

from __future__ import annotations

from typing import Mapping, Optional, Sequence

from dragon_quant.review_account.models import Position, StrategyConfig
from dragon_quant.review_account.strategy.bs_policy import config as bs_config
from dragon_quant.review_account.strategy.bs_policy.owner_scope import (
    ProtectionBatch, batch_facts, batch_obligations, owner_group,
)
from dragon_quant.review_account.strategy.bs_policy.plans import (
    ProtectionObligation,
)
from dragon_quant.review_account.strategy.execution import sell_quantity
from dragon_quant.review_account.strategy.runtime import (
    FrozenReferenceError, StrategyRuntimeError, build_builtin_runtime,
)


def _owner_key(ref: Optional[Mapping]) -> tuple:
    from dragon_quant.review_account.execution_core.identity import ref_identity

    return ref_identity(ref)


def _is_bs_owner(ref: Mapping) -> bool:
    return str((ref or {}).get("strategy_id") or "") \
        in bs_config.BS_ACCOUNT_STRATEGY_ALIASES


def _plain_ref(ref: Mapping) -> dict:
    from dragon_quant.review_account.evidence import thaw

    return dict(thaw(dict(ref or {})))


def policy_config_from_ref(ref: Mapping):
    """从**已验证**的冻结引用解析 BS 政策配置；缺失/非法显式失败，不套当前配置。"""
    ref = _plain_ref(ref)
    params = dict(ref.get("params") or {})
    if not params:
        raise FrozenReferenceError(
            str((ref or {}).get("strategy_id") or "<unknown>"),
            "冻结 BS 参数缺失，拒绝用当前配置冒用")
    from dragon_quant.workspace import snapshots

    if not ref.get("params_hash"):
        raise FrozenReferenceError(
            str(ref.get("strategy_id")), "冻结 BS 引用缺少 params_hash")
    if ref["params_hash"] != snapshots.params_hash(params):
        raise FrozenReferenceError(
            str(ref.get("strategy_id")), "params 与 params_hash 不一致")
    nested = params.get("bs_policy")
    document = nested if isinstance(nested, Mapping) else params
    try:
        return bs_config.BSPolicyConfig.from_dict(dict(document))
    except Exception as exc:  # noqa: BLE001 - 配置非法即受限，不猜默认值
        raise FrozenReferenceError(
            str(ref.get("strategy_id")), f"冻结 BS 配置非法：{exc}") from exc


def verify_builtin_ref(ref: Mapping) -> None:
    """内建冻结身份验证：**完整性字段不可缺省**（新 BS 无裸 ref 兼容）。

    缺 ``params_hash``/``snapshot_id``/``toolkit_version`` 任一项即拒绝，避免把
    任意 params 或空指纹当成冻结事实（O3）。旧内建（非 BS ID）的只读兼容仍由
    ``FrozenRuntimeProvider._builtin`` 按既有契约处理。
    """
    from dragon_quant.workspace import snapshots

    ref = _plain_ref(ref)
    strategy_id = str(ref.get("strategy_id") or "")
    params = dict(ref.get("params") or {})
    if not params:
        raise FrozenReferenceError(strategy_id, "内建冻结引用缺少 params")
    missing = [key for key in ("params_hash", "snapshot_id", "toolkit_version")
               if not ref.get(key)]
    if missing:
        raise FrozenReferenceError(
            strategy_id, f"内建冻结引用缺少完整性字段: {missing}")
    current = snapshots.builtin_ref(strategy_id=strategy_id, params=params)
    for key in ("params_hash", "snapshot_id", "toolkit_version"):
        if ref[key] != current.get(key):
            raise FrozenReferenceError(
                strategy_id, f"内建冻结 {key} 与当前工具指纹不一致（不兼容/被篡改）")


def _restricted(batches: Sequence[ProtectionBatch], reason: str, detail: str = ""
                ) -> list[ProtectionObligation]:
    return [ProtectionObligation(
        obligation_id=f"restricted:{item.code}:{item.batch_id}",
        code=item.code, kind="restricted", action="exit", qty=item.remaining,
        executable=False, reasons=(reason, detail) if detail else (reason,),
        owner=dict(item.strategy_ref), batch_ids=(item.batch_id,),
        plan_id=item.plan_id)
        for item in sorted(batches, key=lambda value: value.batch_id)
        if item.remaining > 0]


def _held_days(entry_date: str, trade_date: str, calendar: Sequence[str],
               override: Optional[int]) -> Optional[int]:
    if override is not None:
        return override
    if not calendar or not entry_date or not trade_date:
        return None
    return len([day for day in calendar if entry_date < day <= trade_date])


def _position(item: ProtectionBatch, owner: Mapping) -> Position:
    """**单个批次**的只读 Position：成本/入场日/峰值/半仓都取自身事实。

    不再按 code 聚合并用当前 mark 伪造峰值（A2）：``highest_return/price`` 只来自
    已持久化的先前已知峰值，未知即为 0（不触发依赖它的移动止盈/保本）。
    """
    qty = int(item.remaining)
    cost = float(item.cost_price)
    peak = item.peak_price
    highest_return = (((float(peak) / cost) - 1) * 100
                      if peak is not None and cost > 0 else 0.0)
    highest_price = float(peak) if peak is not None else 0.0
    return Position(
        code=item.code, name="", qty=qty, entry_date=item.entry_date,
        entry_price=cost, cost=cost * qty, entry_reason_code="frozen_exit",
        entry_reason_text="", strategy_ref=dict(owner),
        highest_return=highest_return, highest_price=highest_price,
        took_profit_half=bool(item.took_profit_half))


class OwnerProtectionResolver:
    """按冻结身份解析各 owner 的保护义务（只读、可关闭资源）。"""

    def __init__(self, *, active_ref: Mapping, active_config,
                 controls: StrategyConfig, provider=None,
                 observation: bool = False) -> None:
        self.active_ref = _plain_ref(active_ref)
        self.active_config = active_config
        self.controls = controls
        self.provider = provider
        self._legacy_builtin = None
        self._closed = False
        self._observation = bool(observation)
        if self._observation:
            # 只读观察模式：没有活动 BS 配置。仍按每个持仓自己的**冻结引用**
            # 解析可确定保护；未声明 BS 的旧归属明确受限，不构造 dummy 配置。
            return
        # 活动 owner 必须是**完整可验证**的新 BS 冻结引用：缺失字段或与显式配置
        # 不同源一律构造期失败（O3），不把裸 ref 放进正式入口；旧内建的只读
        # 兼容只作用于非 BS 归属，不在此放宽。
        if not _is_bs_owner(self.active_ref):
            raise FrozenReferenceError(
                str(self.active_ref.get("strategy_id") or "<unknown>"),
                "活动运行时必须是新 BS 冻结引用（缺少稳定ID/归属）")
        verify_builtin_ref(self.active_ref)
        frozen = policy_config_from_ref(self.active_ref)
        if frozen.content_hash != active_config.content_hash:
            raise FrozenReferenceError(
                str(self.active_ref.get("strategy_id") or "<unknown>"),
                "活动冻结参数与运行时配置不一致（拒绝冒用当前配置）")

    @classmethod
    def for_observation(cls, *, controls: StrategyConfig, provider=None
                        ) -> "OwnerProtectionResolver":
        """只读观察：无活动配置，仅按各持仓冻结引用解析保护（config unknown）。"""
        return cls(active_ref={}, active_config=None, controls=controls,
                   provider=provider, observation=True)


    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self.provider is not None:
            self.provider.close()
        closer = getattr(self._legacy_builtin, "close", None)
        if callable(closer):
            closer()

    # ── 入口 ────────────────────────────────────────────────────────────────
    def resolve(self, state, *, trade_date: str,
                marks: Optional[Mapping] = None,
                rows: Optional[Mapping] = None,
                calendar: Sequence[str] = (),
                held_days: Optional[Mapping] = None,
                held_days_by_batch: Optional[Mapping] = None,
                event_time: Optional[int] = None,
                phase: str = "bar",
                ) -> tuple[list[ProtectionObligation], list[dict]]:
        facts: list[ProtectionBatch] = []
        persisted = getattr(state, "batch_facts", None) or {}
        for batch in getattr(state, "batches", ()) or ():
            if int(getattr(batch, "remaining", 0) or 0) <= 0:
                continue
            code = str(batch.code)
            override = (held_days_by_batch or {}).get(
                str(getattr(batch, "batch_id", "") or ""))
            if override is None:
                override = (held_days or {}).get(code)
            held = _held_days(str(getattr(batch, "entry_date", "") or ""),
                              trade_date, calendar, override)
            facts.append(batch_facts(
                batch, trade_date=trade_date, held_days=held,
                facts=persisted.get(str(batch.batch_id))))
        active_key = (_owner_key(self.active_ref)
                      if self.active_config is not None else None)
        obligations: list[ProtectionObligation] = []
        restrictions: list[dict] = []
        groups: dict[tuple, list[ProtectionBatch]] = {}
        for fact in facts:
            groups.setdefault(_owner_key(fact.strategy_ref), []).append(fact)
        for key in sorted(groups, key=repr):
            owner_facts = groups[key]
            owner = dict(owner_facts[0].strategy_ref)
            if active_key is not None and key == active_key:
                obligations.extend(batch_obligations(
                    owner_facts, self.active_config, marks or {}))
                continue
            if _is_bs_owner(owner):
                try:
                    verify_builtin_ref(owner)
                    config = policy_config_from_ref(owner)
                except Exception as exc:  # noqa: BLE001 - 坏 owner 只受限
                    restrictions.append(self._restriction(
                        "owner_policy_unavailable", owner, exc))
                    obligations.extend(_restricted(
                        owner_facts, "owner_policy_unavailable", str(exc)))
                else:
                    obligations.extend(batch_obligations(
                        owner_facts, config, marks or {}))
                continue
            if self.active_config is None:
                # 只读观察模式不构造 dummy 配置，也不回退当前源码退出旧仓。
                detail = ValueError("仅观察模式：该持仓未声明可验证的 BS 冻结配置")
                restrictions.append(self._restriction(
                    "observation_config_missing", owner, detail))
                obligations.extend(_restricted(
                    owner_facts, "observation_config_missing", str(detail)))
                continue
            try:
                obligations.extend(self._legacy_obligations(
                    owner_facts, owner=owner, trade_date=trade_date,
                    rows=rows or {}, calendar=calendar, event_time=event_time,
                    phase=phase))
            except Exception as exc:  # noqa: BLE001 - 坏 owner 不取消好 owner
                restrictions.append(self._restriction(
                    "owner_frozen_runtime_unavailable", owner, exc))
                obligations.extend(_restricted(
                    owner_facts, "owner_frozen_runtime_unavailable", str(exc)))
        return self._renumber(obligations), restrictions

    # ── 旧内建/用户策略：既有冻结 exit 语义（逐批真实事实）─────────────────
    def _legacy_obligations(self, owner_facts: list[ProtectionBatch], *,
                            owner: Mapping, trade_date: str, rows: Mapping,
                            calendar: Sequence[str],
                            event_time: Optional[int], phase: str
                            ) -> list[ProtectionObligation]:
        runtime = self._runtime_for(owner)
        account = {}
        timestamp = int(event_time or 0)
        result: list[ProtectionObligation] = []
        for (code, _key, _plan_id), items in sorted(
                owner_group(owner_facts).items(), key=lambda item: repr(item[0])):
            row = rows.get(code)
            if row is None:
                result.extend(_restricted(items, "market_row_missing"))
                continue
            # 逐批评估：不同入场日/成本/峰值/半仓状态不能按 code 平均后触发。
            for item in sorted(items, key=lambda value: value.batch_id):
                if item.held_days is None:
                    result.extend(_restricted([item], "held_days_unknown"))
                    continue
                try:
                    result.extend(self._exit_for_batch(
                        item, owner=owner, row=row,
                        trade_date=trade_date, runtime=runtime,
                        timestamp=timestamp, phase=phase, account=account))
                except Exception as exc:  # noqa: BLE001 - 单批失败不取消其它批
                    result.extend(_restricted(
                        [item], "owner_frozen_exit_unavailable", str(exc)))
        return result

    def _exit_for_batch(self, item: ProtectionBatch, *, owner: Mapping,
                        row: Mapping, trade_date: str, runtime,
                        timestamp: int, phase: str, account: Mapping
                        ) -> list[ProtectionObligation]:
        position = _position(item, owner)
        payload = dict(row, **{
            "date": row.get("date") or trade_date,
            "phase": row.get("phase") or phase})
        signal = runtime.evaluate_exit(
            position=position, row=payload, hold_days=int(item.held_days),
            cfg=self.controls, intraday_bars=row.get("intraday_bars"),
            trade_date=trade_date, timestamp=timestamp,
            phase=str(payload["phase"]), account=account)
        if not signal:
            return []
        fraction = (signal.get("signal") or {}).get("exit_fraction")
        qty = sell_quantity(position, str(signal.get("code") or ""),
                            self.controls, fraction)
        if qty <= 0:
            return []
        reasons: list[str] = []
        if not item.peak_known:
            # 依赖历史峰值的移动止盈/保本无法确定：显式说明不足，不伪造峰值。
            reasons.append("peak_history_unavailable")
        if item.sellable <= 0:
            reasons.append("no_sellable_quantity")
        action = "exit" if qty >= position.qty else "reduce"
        return [ProtectionObligation(
            obligation_id=(f"frozen_exit:{item.code}:"
                           f"{item.plan_id or '-'}:{item.batch_id}"),
            code=item.code, kind="frozen_exit", action=action, qty=qty,
            executable=item.sellable > 0, reasons=tuple(reasons),
            condition=None,
            owner=dict(owner), batch_ids=(item.batch_id,),
            plan_id=item.plan_id)]

    def _runtime_for(self, owner: Mapping):
        strategy_id = str((owner or {}).get("strategy_id") or "")
        if not strategy_id:
            # 旧空 ref：沿用既有兼容口径（内建规则 + 当前 controls）。
            if self._legacy_builtin is None:
                self._legacy_builtin = build_builtin_runtime(self.controls)
            return self._legacy_builtin
        if self.provider is None:
            raise StrategyRuntimeError(
                strategy_id, "缺少冻结运行时解析器，拒绝用当前源码退出旧持仓")
        return self.provider.build(dict(owner))

    # ── 审计/稳定排序 ──────────────────────────────────────────────────────
    @staticmethod
    def _restriction(reason: str, owner: Mapping, exc: Exception) -> dict:
        return {"reason": reason,
                "owner": dict(owner),
                "detail": f"{type(exc).__name__}: {exc}"}

    @staticmethod
    def _renumber(obligations: list[ProtectionObligation]
                  ) -> list[ProtectionObligation]:
        from dataclasses import replace

        return [replace(item, sequence=index)
                for index, item in enumerate(obligations)]
