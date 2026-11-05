import { useEffect, useState } from "react";
import {
  fetchDragonTradeDates,
  type DragonDateEntry,
} from "../../api/dragons";
import { EmptyState } from "../../components/EmptyState";
import { Section } from "../../components/Section";
import { IconChevronDown, IconChevronUp } from "../../components/icons";
import { IconClock } from "../../components/icons-extra";
import { fmtNum } from "../../lib/format";

const COLLAPSED_COUNT = 3;

export function RecentDragons({
  onSelect,
}: {
  onSelect: (date: string, code: string) => void;
}) {
  const [dates, setDates] = useState<DragonDateEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  useEffect(() => {
    let active = true;
    setLoading(true);
    fetchDragonTradeDates(5)
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
  }, []);

  const toggle = (tradeDate: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(tradeDate)) next.delete(tradeDate);
      else next.add(tradeDate);
      return next;
    });
  };

  return (
    <Section
      title="近几日榜单"
      description="点击个股查看五维评分与入选后表现"
      right={
        <span className="inline-flex items-center gap-1.5 text-body text-ink-3">
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
          {dates.map((entry) => {
            // 旧后端无 items 时回退 preview（防御）。
            const all = entry.items ?? entry.preview;
            const isExpanded = expanded.has(entry.trade_date);
            const visible = isExpanded ? all : all.slice(0, COLLAPSED_COUNT);
            return (
              <div
                key={entry.trade_date}
                className="rounded-lg border border-hairline bg-raise p-2.5"
              >
                <div className="mb-1.5 flex items-center justify-between px-1">
                  <span className="num text-body text-ink">{entry.trade_date}</span>
                  <span className="font-mono text-caption text-ink-3">{entry.count} 支</span>
                </div>
                <div className="grid gap-0.5">
                  {visible.map((item) => (
                    <button
                      key={item.code}
                      type="button"
                      onClick={() => onSelect(entry.trade_date, item.code)}
                      className="group flex w-full items-center gap-2 rounded-md border-0 bg-transparent px-1.5 py-1.5 text-left transition-colors hover:bg-brand-soft"
                    >
                      <span className="num w-5 shrink-0 text-caption text-ink-3">
                        {item.rank ?? "—"}
                      </span>
                      <span className="min-w-0 flex-1 truncate text-body text-ink">
                        {item.name}
                        {item.is_true_dragon ? (
                          <span className="ml-1.5 text-caption text-brand">🐉</span>
                        ) : null}
                      </span>
                      <span className="num shrink-0 text-body text-ink-2">
                        {fmtNum(item.composite_score, 1)}
                      </span>
                    </button>
                  ))}
                </div>
                {all.length > COLLAPSED_COUNT ? (
                  <div className="mt-1.5 flex justify-center">
                    <button
                      type="button"
                      className="inline-flex items-center gap-1 rounded-md border-0 bg-transparent px-2 py-1 text-caption text-ink-3 transition-colors hover:text-ink-2"
                      onClick={() => toggle(entry.trade_date)}
                    >
                      {isExpanded ? (
                        <>
                          收起
                          <IconChevronUp size={12} />
                        </>
                      ) : (
                        <>
                          展开全部 {all.length} 支
                          <IconChevronDown size={12} />
                        </>
                      )}
                    </button>
                  </div>
                ) : null}
              </div>
            );
          })}
        </div>
      )}
    </Section>
  );
}
