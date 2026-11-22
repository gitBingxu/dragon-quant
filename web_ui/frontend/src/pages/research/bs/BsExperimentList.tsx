import type { ReactNode } from "react";

/** 实验列表：分页 + 打开详情；空列表也有说明。 */
export function BsExperimentList({
  view,
  busy,
  onOpen,
  onLoadMore,
}: {
  view: Record<string, any>;
  busy: boolean;
  onOpen: (id: number) => void;
  onLoadMore: () => void;
}): ReactNode {
  if (!view || view.kind !== "experiment_list") return null;
  const items = (view.items || []) as Record<string, any>[];
  const page = view.page || {};
  return (
    <div className="rounded-lg border border-hairline">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-hairline px-3 py-2 text-caption font-semibold text-ink-2">
        <span>已存冻结实验</span>
        <span className="text-ink-3">共 {page.total ?? items.length} 条</span>
      </div>
      {items.length ? (
        <ul className="divide-y divide-hairline">
          {items.map((item) => (
            <li key={item.id} className="flex flex-wrap items-center gap-3 px-3 py-2 text-caption">
              <button type="button" className="btn btn-xs btn-ghost" disabled={busy}
                      onClick={() => onOpen(Number(item.id))}>
                打开 #{item.id}
              </button>
              <span className={`badge ${item.status === "completed" ? "badge-ghost" : "badge-warning"}`}>
                {item.status ?? "—"}
              </span>
              <span>效果：{item.effect_status === "evaluated" ? "合格" : "证据不足"}</span>
              <span>研究：{item.study_id ?? "默认对照"}</span>
              <span>预期/完成：{item.expected_events ?? "—"}/{item.completed_events ?? "—"}</span>
              <span className="text-ink-3">{item.created_at ?? ""}</span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="px-3 py-3 text-caption text-ink-3">
          暂无已存实验：显式运行「冻结对照」后才会写入；只读查看不会创建数据。
        </p>
      )}
      {page.has_more ? (
        <div className="border-t border-hairline px-3 py-2">
          <button type="button" className="btn btn-sm btn-ghost" disabled={busy} onClick={onLoadMore}>
            加载更多实验
          </button>
        </div>
      ) : null}
    </div>
  );
}
