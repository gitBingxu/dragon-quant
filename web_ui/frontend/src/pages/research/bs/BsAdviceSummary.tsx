import type { ReactNode } from "react";
import { BsAudit } from "./BsAudit";

function Chips({ items, tone = "neutral" }: { items: string[]; tone?: string }) {
  if (!items.length) return <span className="text-ink-3">—</span>;
  return (
    <div className="flex flex-wrap gap-1">
      {items.map((item) => (
        <span key={item} className={`badge ${tone === "warn" ? "badge-warning" : "badge-ghost"}`}>
          {item}
        </span>
      ))}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="min-w-0">
      <div className="text-caption text-ink-3">{label}</div>
      <div className="num mt-0.5 text-body text-ink">{value}</div>
    </div>
  );
}

/** 只读建议视图：能力/限制/保护/分母/政策/偏好分与 EV 资格。 */
export function BsAdviceSummary({ view }: { view: Record<string, any> }): ReactNode {
  if (!view || view.kind !== "advice") return null;
  const caps = view.capabilities || {};
  const pref = view.preference || {};
  const denom = view.denominators || {};
  const plans = view.plans || {};
  const input = view.input || {};
  return (
    <div className="grid gap-3">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-6">
        <Stat label="状态" value={view.status ?? "—"} />
        <Stat label="截止" value={<span className="text-caption">{view.cutoff ?? "—"}</span>} />
        <Stat label="输入类型" value={input.kind ?? "—"} />
        <Stat label="已落库" value={input.persisted ? "是" : "否"} />
        <Stat label="下单" value={input.orders_placed ? "是" : "否（只读）"} />
        <Stat label="策略配置" value={input.policy_provided ? "已提供" : "缺失（观察）"} />
      </div>

      <div className="grid gap-3 lg:grid-cols-2">
        <div className="rounded-lg border border-hairline p-3">
          <div className="text-caption font-semibold text-ink-2">可用能力</div>
          <div className="mt-1"><Chips items={caps.usable_labels || caps.usable || []} /></div>
          <div className="mt-2 text-caption font-semibold text-ink-2">缺失能力</div>
          <div className="mt-1"><Chips items={caps.missing_labels || caps.missing || []} tone="warn" /></div>
          <p className="mt-2 text-caption text-ink-3">
            可用性：{caps.usability ?? "—"}；缺能力时动作受限但仍保留已有保护。
          </p>
        </div>
        <div className="rounded-lg border border-hairline p-3">
          <div className="text-caption font-semibold text-ink-2">母集与动作分母</div>
          <div className="mt-1 grid grid-cols-2 gap-2">
            <Stat label="母集股票" value={denom.universe_codes ?? "—"} />
            <Stat label="候选" value={denom.candidates ?? "—"} />
            <Stat label="拒绝政策条数" value={denom.rejected_policies ?? "—"} />
            <Stat label="意图" value={denom.intents ?? "—"} />
            <Stat label="未成交" value={denom.unfilled_label ?? "不适用"} />
          </div>
          <p className="mt-2 text-caption text-ink-3">{denom.note}</p>
        </div>
      </div>

      <div className="grid gap-3 lg:grid-cols-2">
        <div className="rounded-lg border border-hairline p-3">
          <div className="text-caption font-semibold text-ink-2">政策选择</div>
          <div className="mt-1 text-body">
            已选：<b>{plans.selected_plan_id ?? plans.selected_status ?? "—"}</b>
          </div>
          <div className="mt-2 text-caption text-ink-2">放弃 / 受限</div>
          {(plans.abandoned || []).length ? (
            <ul className="mt-1 grid gap-1 text-caption">
              {(plans.abandoned as any[]).map((item, index) => (
                <li key={`${item.plan_id}-${index}`}>
                  · {item.plan_id ?? "—"} — {item.reason_summary ?? "未识别原因"}
                </li>
              ))}
            </ul>
          ) : (
            <p className="mt-1 text-caption text-ink-3">无显式放弃政策。</p>
          )}
        </div>
        <div className="rounded-lg border border-hairline p-3">
          <div className="text-caption font-semibold text-ink-2">保护与受限</div>
          <div className="mt-1 text-caption">
            存量保护：{(view.protections || []).length} 条；未知保护：
            {view.unknown_protection ? "存在（按受限处理）" : "未发现"}
          </div>
          {(view.restrictions || []).length ? (
            <ul className="mt-1 grid gap-1 text-caption text-warning">
              {(view.restrictions as any[]).map((item, index) => (
                <li key={index}>· {item.reason_label ?? item.reason}</li>
              ))}
            </ul>
          ) : (
            <p className="mt-1 text-caption text-ink-3">无额外受限项。</p>
          )}
          {(view.protections || []).length ? (
            <ul className="mt-1 grid gap-1 text-caption text-ink-2">
              {(view.protections as any[]).slice(0, 6).map((item, index) => (
                <li key={index}>
                  · {item.rule_id ?? item.obligation_id ?? "保护"}：
                  {(item.reason_labels || []).join("；") || "规则保护（详见审计）"}
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      </div>

      <div className="rounded-lg border border-hairline p-3">
        <div className="text-caption font-semibold text-ink-2">偏好分与 EV 资格</div>
        <div className="mt-1 flex flex-wrap gap-4 text-body">
          <span>Hurwicz ρ：{pref.rho ?? "—"}</span>
          <span>是否 EV：{pref.is_ev ? "是" : "否（偏好分不是 EV）"}</span>
          <span>概率资格：{pref.status_label ?? "缺失"}</span>
          <span>
            EV 数值：{pref.ev_available ? pref.ev_value : "不显示（未合格或无明确 EV）"}
          </span>
        </div>
        {(pref.reasons_labels || []).length ? (
          <ul className="mt-1 text-caption text-warning">
            {(pref.reasons_labels as string[]).map((item) => <li key={item}>· {item}</li>)}
          </ul>
        ) : null}
        <p className="mt-1 text-caption text-ink-3">{pref.note}</p>
      </div>

      <BsAudit label="建议原始审计" value={view.audit} />
    </div>
  );
}
