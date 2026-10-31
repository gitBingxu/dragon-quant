import type { PrepSnapshotView } from "../../api/assets";
import { Badge } from "../../components/Badge";
import { StatCard } from "../../components/StatCard";
import { fmtNum } from "../../lib/format";

const JSON_BLOCKS: Array<{ key: keyof PrepSnapshotView; label: string }> = [
  { key: "sentiment", label: "情绪" },
  { key: "macro", label: "宏观" },
  { key: "dragon_source", label: "龙头来源" },
  { key: "review", label: "复盘" },
  { key: "feedback", label: "反馈" },
  { key: "plan_review", label: "计划复盘" },
];

export function SnapshotSection({ snapshot }: { snapshot: PrepSnapshotView }) {
  return (
    <div className="grid gap-3">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <StatCard label="数据块" value={snapshot.blocks_present.length} tone="brand" />
        <StatCard label="候选" value={snapshot.candidates?.length ?? 0} />
        <StatCard label="review 候选" value={snapshot.review_candidates?.length ?? 0} />
        <StatCard
          label="体积"
          value={`${fmtNum(snapshot.raw_size_bytes / 1024, 1)} KB`}
          tone="muted"
        />
      </div>

      <div className="rounded-lg border border-hairline bg-raise px-3.5 py-3">
        <div className="mb-2 text-[10.5px] font-semibold uppercase tracking-[0.09em] text-ink-2">
          数据块
        </div>
        <div className="flex flex-wrap gap-1.5">
          {snapshot.blocks_present.length ? (
            snapshot.blocks_present.map((block) => (
              <Badge key={block} tone="outline">
                <span className="font-mono">{block}</span>
              </Badge>
            ))
          ) : (
            <span className="text-[12px] text-ink-3">—</span>
          )}
        </div>
        <div className="mt-2 font-mono text-[10.5px] text-ink-3">
          version {snapshot.version ?? "—"} · 创建于 {snapshot.created_at ?? "—"}
        </div>
      </div>

      <div className="grid gap-2">
        {JSON_BLOCKS.map((block) => {
          const value = snapshot[block.key];
          const present = value != null;
          return (
            <details
              key={block.key}
              className="overflow-hidden rounded-lg border border-hairline bg-raise"
            >
              <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-3.5 py-2.5">
                <span className="text-[12px] font-medium text-ink-2">{block.label}</span>
                <Badge tone={present ? "brand" : "neutral"}>{present ? "已启用" : "未启用"}</Badge>
              </summary>
              {present ? (
                <pre className="max-h-56 overflow-auto whitespace-pre-wrap border-t border-hairline px-3.5 py-3 font-mono text-[11px] leading-relaxed text-ink-2">
                  {JSON.stringify(value, null, 2)}
                </pre>
              ) : null}
            </details>
          );
        })}
      </div>
    </div>
  );
}
