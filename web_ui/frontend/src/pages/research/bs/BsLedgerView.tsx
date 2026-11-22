import type { ReactNode } from "react";
import { BsAudit } from "./BsAudit";

function Table({
  title,
  rows,
  columns,
}: {
  title: string;
  rows: Record<string, any>[];
  columns: Array<[string, string]>;
}) {
  return (
    <div className="rounded-lg border border-hairline">
      <div className="flex items-center justify-between border-b border-hairline px-3 py-2 text-caption font-semibold text-ink-2">
        <span>{title}</span>
        <span className="text-ink-3">{rows.length} 行</span>
      </div>
      {rows.length ? (
        <div className="max-h-64 overflow-auto">
          <table className="table table-sm w-full">
            <thead>
              <tr>
                {columns.map(([, label]) => (
                  <th key={label} className="text-caption text-ink-3">{label}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, index) => (
                <tr key={String(row.batch_id ?? row.order_id ?? row.fill_id ?? index)}>
                  {columns.map(([key]) => (
                    <td key={key} className="num text-caption">
                      {row[key] == null ? "—" : String(row[key])}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="px-3 py-2 text-caption text-ink-3">暂无记录（空样本不补造）。</p>
      )}
    </div>
  );
}

/** 已存账本视图：run 状态/水位/失败原因、批次/订单/成交、事件历史分页。 */
export function BsLedgerView({
  view,
  busy,
  onLoadMore,
}: {
  view: Record<string, any>;
  busy: boolean;
  onLoadMore: () => void;
}): ReactNode {
  if (!view || view.kind !== "ledger") return null;
  const run = view.run || {};
  const events = (view.events || []) as Record<string, any>[];
  const page = view.page || {};
  return (
    <div className="grid gap-3">
      <div className="flex flex-wrap items-center gap-3 rounded-lg border border-hairline p-3 text-caption">
        <span className="text-body font-semibold">账本 {view.ledger_id}</span>
        <span className={`badge ${run.is_partial ? "badge-warning" : "badge-ghost"}`}>
          {run.status_label ?? run.status}
        </span>
        <span>预期事件 {run.expected_events ?? "—"}</span>
        <span>已完成 {run.completed_events ?? "—"}</span>
        <span>水位 {run.watermark ?? "—"}</span>
        {run.failure_reason ? (
          <span className="text-warning">失败原因：{run.failure_reason}</span>
        ) : null}
        {run.resume_available ? (
          <span className="text-caption text-warning">
            部分/失败：恢复需通过共享用例（CLI resume）显式执行；只读 GET 不会触发重跑。
          </span>
        ) : null}
      </div>

      {run.is_partial ? (
        <p className="rounded-lg border border-warning-line bg-warning-soft px-3 py-2 text-caption text-warning">
          该运行不是完整结果：失败/部分事件不当完整统计，恢复需显式按钮。
        </p>
      ) : null}

      <div className="grid gap-3 lg:grid-cols-3">
        <Table title="持仓批次" rows={view.batches || []}
               columns={[["batch_id", "批次"], ["code", "代码"], ["qty", "数量"],
                         ["sellable_qty", "可卖"], ["cost_price", "成本"],
                         ["entry_date", "入场"], ["family_id", "归属"]]} />
        <Table title="订单" rows={view.orders || []}
               columns={[["order_id", "订单"], ["side", "方向"], ["qty", "数量"],
                         ["filled_qty", "已成"], ["status", "状态"],
                         ["reserved_amount", "资金预留"],
                         ["reserved_risk", "风险预留"], ["reason", "原因"]]} />
        <Table title="成交" rows={view.fills || []}
               columns={[["fill_id", "成交"], ["side", "方向"], ["price", "价格"],
                         ["qty", "数量"], ["fee", "费用"]]} />
      </div>

      <div className="rounded-lg border border-hairline">
        <div className="flex items-center justify-between border-b border-hairline px-3 py-2 text-caption font-semibold text-ink-2">
          <span>事件历史</span>
          <span className="text-ink-3">已加载 {events.length} 条{page.has_more ? "（还有更多）" : ""}</span>
        </div>
        <ul className="grid gap-2 p-3">
          {events.map((event, index) => (
            <li key={`${event.timestamp}-${index}`} className="rounded border border-hairline p-2 text-caption">
              <div className="flex flex-wrap gap-3">
                <span className="num">{event.event_time ?? event.trade_date ?? "—"}</span>
                <span>{event.trade_date ?? "—"}</span>
                <span>{event.status ?? "—"}</span>
                <span>{event.decided ? "有决策" : "初始化/无决策事件"}</span>
                <span>计划 {event.selected_plan_id ?? "—"}</span>
                <span>意图 {(event.intents || []).length}</span>
                <span>成交 {(event.fills || []).length}</span>
                <span>订单 {(event.orders || []).length}</span>
                <span>
                  权益 {event.state_available ? (event.equity ?? "—") : "未保存（该历史版本）"}
                </span>
                <span>
                  恢复阶段 {event.state_available ? (event.recovery_stage ?? "—") : "未保存"}
                </span>
              </div>
              {(event.rejected || []).length ? (
                <ul className="mt-1 text-warning">
                  {(event.rejected as any[]).map((item, i) => (
                    <li key={i}>· 拒绝：{item.reason_label ?? item.reason}</li>
                  ))}
                </ul>
              ) : null}
            </li>
          ))}
          {!events.length ? (
            <li className="text-caption text-ink-3">暂无事件记录。</li>
          ) : null}
        </ul>
        {page.has_more ? (
          <div className="border-t border-hairline px-3 py-2">
            <button type="button" className="btn btn-sm btn-ghost" disabled={busy} onClick={onLoadMore}>
              加载更多事件
            </button>
          </div>
        ) : null}
      </div>

      <BsAudit label="账本原始审计" value={view.audit} />
    </div>
  );
}
