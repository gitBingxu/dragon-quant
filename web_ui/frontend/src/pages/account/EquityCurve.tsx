import { useState } from "react";
import type { AccountBenchmarkPoint, AccountSnapshot } from "../../api/legacy";
import { EmptyState } from "../../components/EmptyState";
import { Section } from "../../components/Section";
import { fmtMoney, fmtPct } from "../../lib/format";
import { EquityChart } from "./EquityChart";
import { EquityDataTable } from "./EquityDataTable";
import { BENCH_COLOR } from "./equityTheme";

export function EquityCurve({
  data,
  benchmark,
}: {
  data: AccountSnapshot[];
  benchmark: AccountBenchmarkPoint[];
}) {
  const [showTable, setShowTable] = useState(false);
  const last = data[data.length - 1] ?? null;

  return (
    <Section
      title="权益与回撤曲线"
      description="上：总权益逐日红涨 / 绿跌 vs 上证（中性灰）；下：回撤（绿，独立面板，不设双 y 轴）"
      right={
        <div className="flex flex-wrap items-center justify-end gap-x-4 gap-y-1.5">
          <TrendLegend />
          <Legend color={BENCH_COLOR} label="上证指数" />
          {last ? (
            <>
              <span className="text-[11.5px] text-ink-3">
                最新权益 <span className="num text-ink">{fmtMoney(last.total_equity)}</span>
              </span>
              <span className="text-[11.5px] text-ink-3">
                回撤 <span className="num text-down">{fmtPct(last.drawdown)}</span>
              </span>
            </>
          ) : null}
          <button
            type="button"
            className="btn btn-subtle btn-sm"
            onClick={() => setShowTable((prev) => !prev)}
          >
            {showTable ? "隐藏数据表" : "数据表"}
          </button>
        </div>
      }
    >
      {data.length === 0 ? (
        <EmptyState title="暂无快照数据" description="运行一次回测后展示权益与回撤曲线。" />
      ) : (
        <EquityChart data={data} benchmark={benchmark} />
      )}
      {showTable ? <EquityDataTable data={data} benchmark={benchmark} /> : null}
    </Section>
  );
}

function TrendLegend() {
  return (
    <span className="inline-flex items-center gap-1.5 text-[11.5px] text-ink-2">
      <span className="flex h-[2px] w-3 overflow-hidden rounded-full">
        <span className="h-full w-1/2" style={{ background: "var(--color-up)" }} />
        <span className="h-full w-1/2" style={{ background: "var(--color-down)" }} />
      </span>
      总权益（红涨 / 绿跌）
    </span>
  );
}

function Legend({ color, label }: { color: string; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-[11.5px] text-ink-2">
      <span className="h-[2px] w-3 rounded-full" style={{ background: color }} />
      {label}
    </span>
  );
}
