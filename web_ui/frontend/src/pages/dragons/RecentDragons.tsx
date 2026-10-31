import { useEffect, useState } from "react";
import {
  fetchDragonTradeDates,
  type DragonDateEntry,
} from "../../api/dragons";
import type { ReviewCaliber } from "../../api/legacy";
import { EmptyState } from "../../components/EmptyState";
import { Section } from "../../components/Section";
import { IconClock } from "../../components/icons-extra";
import { fmtNum } from "../../lib/format";

export function RecentDragons({
  source,
  caliber,
  onSelect,
}: {
  source: "v1" | "v2";
  caliber: ReviewCaliber;
  onSelect: (date: string, code: string) => void;
}) {
  const [dates, setDates] = useState<DragonDateEntry[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let active = true;
    setLoading(true);
    fetchDragonTradeDates(source, 5)
      .then((payload) => {
        if (active) setDates(payload.data);
      })
      .catch(() => {
        if (active) setDates([]);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [source]);

  return (
    <Section
      title="近几日榜单"
      description={`点击个股查看五维评分与严格/宽松对照 · ${caliber === "strict" ? "严格口径" : "宽松口径"}`}
      right={
        <span className="inline-flex items-center gap-1.5 text-[11.5px] text-ink-3">
          <IconClock size={13} />
          最近 5 个交易日
        </span>
      }
      bodyClassName="p-3.5"
    >
      {dates.length === 0 ? (
        <EmptyState
          title={loading ? "正在加载榜单…" : "暂无龙头交易日记录"}
          description={loading ? undefined : "运行 dragon-quant scan 生成首份榜单。"}
        />
      ) : (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-5">
          {dates.map((entry) => (
            <div
              key={entry.trade_date}
              className="rounded-lg border border-hairline bg-white/[0.015] p-2.5"
            >
              <div className="mb-1.5 flex items-center justify-between px-1">
                <span className="num text-[12px] text-ink">{entry.trade_date}</span>
                <span className="font-mono text-[10.5px] text-ink-3">{entry.count} 只</span>
              </div>
              <div className="grid gap-0.5">
                {entry.preview.map((item) => (
                  <button
                    key={item.code}
                    type="button"
                    onClick={() => onSelect(entry.trade_date, item.code)}
                    className="group flex w-full items-center gap-2 rounded-md border-0 bg-transparent px-1.5 py-1.5 text-left transition-colors hover:bg-brand-soft"
                  >
                    <span className="num w-5 shrink-0 text-[11px] text-ink-3">
                      {item.rank ?? "—"}
                    </span>
                    <span className="min-w-0 flex-1 truncate text-[12.5px] text-ink">
                      {item.name}
                      {item.is_true_dragon ? (
                        <span className="ml-1.5 text-[10px] text-brand">🐉</span>
                      ) : null}
                    </span>
                    <span className="num shrink-0 text-[11.5px] text-ink-2">
                      {fmtNum(item.composite_score, 1)}
                    </span>
                  </button>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </Section>
  );
}
