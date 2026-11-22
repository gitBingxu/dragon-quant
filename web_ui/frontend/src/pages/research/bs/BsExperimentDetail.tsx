import type { ReactNode } from "react";
import { fmtMoney } from "../../../lib/format";
import { BsAudit } from "./BsAudit";
import { BsMetricTable } from "./BsMetricTable";

function Money({ value }: { value: number | null | undefined }) {
  if (value == null) return <span className="text-ink-3">待证据</span>;
  return <span className="num">{fmtMoney(value)}</span>;
}

function MatrixTable({ matrix }: { matrix: Record<string, any> }) {
  const rows = (matrix.rows || []) as Record<string, any>[];
  if (!rows.length) {
    return <p className="text-caption text-ink-3">无多政策研究规范；默认仅 BS 与无新增风险基线。</p>;
  }
  return (
    <div className="overflow-auto">
      <table className="table table-sm w-full">
        <thead>
          <tr>
            {["政策", "类别", "工程", "效果", "期末权益", "成交", "未成交率", "可执行", "可比", "合格错过", "描述差", "能力"].map((label) => (
              <th key={label} className="text-caption text-ink-3">{label}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.name}>
              <td className="text-caption font-medium">{row.name}</td>
              <td className="text-caption">{row.kind}</td>
              <td className="text-caption">{row.engineering_label ?? row.engineering_status}</td>
              <td className={`text-caption ${row.effect_status === "evaluated" ? "text-ink" : "text-warning"}`}>
                {row.effect_label}
              </td>
              <td><Money value={row.final_equity} /></td>
              <td className="num text-caption">{row.fills ?? "—"}</td>
              <td className="num text-caption">
                {row.unfilled_ratio == null ? "—" : `${(row.unfilled_ratio * 100).toFixed(1)}%`}
              </td>
              <td className="text-caption">{row.executable_label ?? (row.executable ? "是" : "否")}</td>
              <td className="text-caption">{row.comparable_label ?? (row.comparable ? "是" : "否")}</td>
              <td><Money value={row.qualified_missed_amount} /></td>
              <td><Money value={row.descriptive_equity_diff} /></td>
              <td className="text-caption text-ink-3">
                {row.capability
                  ? [
                      row.capability.evidence_distinct ? "证据不同" : "证据未变",
                      ...(row.capability.used_added_labels || []),
                    ].join(" / ")
                  : "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Alternatives({ items }: { items: Record<string, any>[] }) {
  if (!items.length) return <p className="text-caption text-ink-3">无可执行替代政策。</p>;
  return (
    <ul className="grid gap-1 text-caption">
      {items.map((item) => (
        <li key={item.name} className="flex flex-wrap gap-3">
          <span className="font-medium">{item.name}</span>
          <span>{item.label}</span>
          <Money value={item.qualified_missed_amount} />
          <span className="text-ink-3">描述差：</span>
          <Money value={item.descriptive_equity_diff} />
          <span className="text-ink-3">{item.executable ? "可执行" : "不可执行"} / {item.comparable ? "可比" : "不可比"}</span>
        </li>
      ))}
    </ul>
  );
}

/** 实验详情：工程状态与效果资格分开，矩阵/指标/成熟/分窗/参数尝试/审计。 */
export function BsExperimentDetail({
  view,
  busy,
  onReplay,
  onResume,
  onClose,
}: {
  view: Record<string, any>;
  busy: boolean;
  onReplay: (id: number) => void;
  onResume: (id: number) => void;
  onClose: () => void;
}): ReactNode {
  if (!view || view.kind !== "experiment") return null;
  const identity = view.identity || {};
  const metrics = view.metrics || {};
  const maturity = metrics.maturity || {};
  const splits = metrics.splits || {};
  const id = Number(view.id);
  const partial = view.status !== "completed" ||
    (identity.expected_events != null && identity.completed_events != null &&
      identity.expected_events !== identity.completed_events);
  return (
    <div className="grid gap-3 rounded-lg border border-hairline p-3">
      <div className="flex flex-wrap items-center gap-2 text-caption">
        <span className="text-body font-semibold">实验 #{view.id}</span>
        <span className={`badge ${view.status === "completed" ? "badge-ghost" : "badge-warning"}`}>
          工程：{view.status ?? "—"}
        </span>
        <span className={`badge ${view.effect_status === "evaluated" ? "badge-ghost" : "badge-warning"}`}>
          {view.effect_label}
        </span>
        {partial ? <span className="text-warning">部分/失败运行不当完整</span> : null}
        <button type="button" className="btn btn-xs btn-ghost" onClick={onClose}>收起</button>
        {Number.isFinite(id) ? (
          <>
            <button type="button" className="btn btn-xs btn-outline" disabled={busy}
                    onClick={() => onReplay(id)}>只读重放</button>
            {partial ? (
              <button type="button" className="btn btn-xs btn-outline" disabled={busy}
                      onClick={() => onResume(id)}>显式恢复</button>
            ) : null}
          </>
        ) : null}
      </div>

      <div className="grid grid-cols-2 gap-2 text-caption sm:grid-cols-4">
        <div>研究规范：{identity.study_digest ? identity.study_digest.slice(0, 12) : "默认对照"}</div>
        <div>pack：{identity.pack_digest ? identity.pack_digest.slice(0, 12) : "—"}</div>
        <div>区间：{identity.date_from ?? "—"} ~ {identity.date_to ?? "—"}</div>
        <div>事件：预期 {identity.expected_events ?? "—"} / 完成 {identity.completed_events ?? "—"}</div>
        <div>指标版本：{identity.metrics_version ?? "—"}</div>
        <div>cohort：{identity.cohort_mode ?? "—"}</div>
        <div>参数尝试：{(identity.parameter_attempts || []).length} 条</div>
        <div>研究 digest：{identity.study_digest ? identity.study_digest.slice(0, 8) : "—"}</div>
      </div>

      <div className="rounded-lg border border-hairline p-3 text-caption">
        <div className="font-semibold text-ink-2">数据能力条件</div>
        {Object.entries(view.data_modes || {}).map(([name, mode]) => (
          <div key={name} className="mt-0.5">
            · {name}：{(mode as any).status ?? "—"}
            {(mode as any).reason ? `（${(mode as any).reason}）` : ""}
            {(mode as any).pack_digest ? ` · ${String((mode as any).pack_digest).slice(0, 12)}` : ""}
          </div>
        ))}
        {view.capability_effect?.status ? (
          <div className="mt-0.5 text-ink-3">
            能力影响：{view.capability_effect.status}
            {view.capability_effect.reason ? `（${view.capability_effect.reason}）` : ""}
          </div>
        ) : null}
        {(identity.parameter_attempts || []).length ? (
          <div className="mt-1">
            参数尝试留档：
            <ul className="mt-0.5 text-ink-3">
              {(identity.parameter_attempts as any[]).map((item, index) => (
                <li key={index}>· {JSON.stringify(item)}</li>
              ))}
            </ul>
          </div>
        ) : null}
      </div>

      <div className="rounded-lg border border-hairline p-3">
        <div className="text-caption font-semibold text-ink-2">策略 × 数据能力矩阵</div>
        <MatrixTable matrix={view.matrix || {}} />
        <div className="mt-2 text-caption text-ink-3">
          工程完成 ≠ 效果合格；数据能力不足的格子标“效果证据不足”，全空仓/未成交不显示优于。
        </div>
      </div>

      <div className="rounded-lg border border-hairline p-3">
        <div className="text-caption font-semibold text-ink-2">可执行错过成本（与未来最高价诊断分开）</div>
        <Alternatives items={(view.alternatives || []) as Record<string, any>[]} />
        <p className="mt-1 text-caption text-ink-3">
          合格错过 = 主策略 − 替代（正值表示主策略保留更多），仅同资源、可执行且双方标签合格才给值；
          其余只保留描述性差值，不与合格错过混用。
        </p>
      </div>

      <BsMetricTable title="主策略指标" rows={metrics.primary || []} />
      {metrics.baseline?.length ? <BsMetricTable title="无新增风险基线" rows={metrics.baseline} /> : null}
      {metrics.enhanced?.length ? <BsMetricTable title="增强数据条件" rows={metrics.enhanced} /> : null}

      <div className="grid gap-3 lg:grid-cols-2">
        <div className="rounded-lg border border-hairline p-3 text-caption">
          <div className="font-semibold text-ink-2">成熟与排除</div>
          <div className="mt-1">状态：{maturity.status ?? "—"}；截止：{maturity.evaluation_cutoff ?? "—"}</div>
          <div>合格 {maturity.matured ?? 0} / 未成熟 {maturity.unmatured ?? 0} / 期初旧 {maturity.preexisting ?? 0}</div>
          {Object.entries(maturity.exclusions || {}).map(([reason, count]) => (
            <div key={reason} className="text-ink-3">· {reason}: {String(count)}</div>
          ))}
          {(maturity.blockers || []).length ? (
            <div className="text-warning">阻塞：{(maturity.blockers as string[]).join(", ")}</div>
          ) : null}
        </div>
        <div className="rounded-lg border border-hairline p-3 text-caption">
          <div className="font-semibold text-ink-2">窗口分割</div>
          <div className="mt-1">模式：{splits.mode ?? "—"}；purge {splits.purge_days ?? "—"}（{splits.purge_basis ?? "—"}）</div>
          <div>训练：{splits.training_performed ? "是" : "否（固定参数，不虚称 walk-forward）"}</div>
          {Object.entries(splits.windows || {}).map(([name, window]) => (
            <div key={name}>· {name}: {(window as string[]).join(" ~ ") || "无"}</div>
          ))}
          {(metrics.execution_support?.status) ? (
            <div className="mt-1">执行支持：{metrics.execution_support.status}</div>
          ) : null}
        </div>
      </div>

      <div className="rounded-lg border border-hairline p-3 text-caption">
        <div className="font-semibold text-ink-2">恢复分段</div>
        {(metrics.recovery || []).length ? (
          <ul className="mt-1 grid gap-1">
            {(metrics.recovery as any[]).map((item, index) => (
              <li key={index}>
                阶段 {item.stage}：{item.start} ~ {item.end}（{item.trade_days ?? "—"} 交易日）
                再暴露变化 {item.exposure_change_pct ?? "未知"}
                {item.censored ? "（删失）" : ""}
              </li>
            ))}
          </ul>
        ) : (
          <p className="mt-1 text-ink-3">无恢复阶段记录。</p>
        )}
      </div>

      <BsAudit label="实验原始报告" value={view.audit} />
    </div>
  );
}
