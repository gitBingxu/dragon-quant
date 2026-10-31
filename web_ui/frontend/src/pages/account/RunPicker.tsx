import { useRef, useState } from "react";
import type { AccountRun } from "../../api/account";
import { IconChevronDown, IconTrash } from "../../components/icons";
import { fmtPct, pnlClass, runReturnLabel } from "../../lib/format";
import { useClickOutside } from "../../lib/hooks";

function runLabel(run: AccountRun): string {
  return run.display_name || `#${run.id} ${run.date_from} ~ ${run.date_to}`;
}

export function RunPicker({
  runs,
  runId,
  onSelect,
  onDelete,
}: {
  runs: AccountRun[];
  runId: number | null;
  onSelect: (runId: number) => void;
  onDelete: (run: AccountRun) => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement | null>(null);
  useClickOutside(ref, () => setOpen(false), open);
  const selected = runs.find((run) => run.id === runId) ?? null;

  return (
    <div ref={ref} className="relative w-full sm:w-[360px]">
      <button
        type="button"
        className="btn btn-ghost w-full justify-between"
        onClick={() => setOpen((prev) => !prev)}
        aria-haspopup="listbox"
        aria-expanded={open}
      >
        <span className="min-w-0 truncate text-left">
          {selected ? runLabel(selected) : "选择回测批次"}
        </span>
        <span className="flex shrink-0 items-center gap-2">
          {selected ? (
            <span className={`num text-[11.5px] ${pnlClass(selected.total_return)}`}>
              {runReturnLabel(selected)}
            </span>
          ) : null}
          <IconChevronDown size={14} className="text-ink-3" />
        </span>
      </button>

      {open ? (
        <div
          className="absolute right-0 z-30 mt-1.5 max-h-[320px] w-full min-w-[320px] overflow-auto rounded-lg border border-hairline bg-panel-2 p-1"
          style={{ boxShadow: "var(--shadow-overlay)" }}
          role="listbox"
        >
          {runs.length === 0 ? (
            <div className="px-3 py-4 text-[12px] text-ink-2">暂无回测批次</div>
          ) : (
            runs.map((run) => (
              <div
                key={run.id}
                className={`flex items-center gap-1 rounded-md pr-1 transition-colors ${
                  run.id === runId ? "bg-brand-soft" : ""
                }`}
              >
                <button
                  type="button"
                  role="option"
                  aria-selected={run.id === runId}
                  className="min-w-0 flex-1 rounded-md px-2.5 py-2 text-left transition-colors hover:bg-hover"
                  onClick={() => {
                    onSelect(run.id);
                    setOpen(false);
                  }}
                >
                  <div className="truncate text-[12.5px] text-ink">{runLabel(run)}</div>
                  <div className="mt-0.5 flex items-center gap-2 font-mono text-[10.5px] text-ink-3">
                    <span>
                      {run.date_from} ~ {run.date_to}
                    </span>
                    <span className={pnlClass(run.total_return)}>{runReturnLabel(run)}</span>
                    <span>· {run.trade_count} 笔</span>
                  </div>
                </button>
                <button
                  type="button"
                  className="icon-btn h-7 w-7 hover:bg-critical-soft hover:text-critical-text"
                  aria-label={`删除记录 ${run.id}`}
                  title="删除该记录"
                  onClick={(event) => {
                    event.stopPropagation();
                    onDelete(run);
                    setOpen(false);
                  }}
                >
                  <IconTrash size={14} />
                </button>
              </div>
            ))
          )}
          <div className="mt-1 border-t border-hairline px-2.5 py-1.5 font-mono text-[10px] text-ink-3">
            {runs.length} 个批次 · 收益 {fmtPct(selected?.total_return ?? null)}
          </div>
        </div>
      ) : null}
    </div>
  );
}
