import type { AccountRun, AccountSnapshot } from "../../api/account";
import { StatCard } from "../../components/StatCard";
import { fmtMoney, fmtPct, pnlStatTone } from "../../lib/format";

export function AccountStats({
  run,
  latest,
}: {
  run: AccountRun | null;
  latest: AccountSnapshot | null;
}) {
  const returnPct = run?.total_return ?? null;
  const drawdown = run?.max_drawdown ?? null;
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-6">
      <StatCard label="初始资金" value={fmtMoney(run?.initial_cash)} tone="muted" />
      <StatCard
        label="当前权益"
        value={fmtMoney(latest?.total_equity ?? run?.final_equity)}
        sub={latest ? `快照 ${latest.trade_date}` : undefined}
      />
      <StatCard
        label="累计收益"
        value={fmtPct(returnPct)}
        tone={pnlStatTone(returnPct)}
        sub={run ? `${run.date_from} ~ ${run.date_to}` : undefined}
      />
      <StatCard label="最大回撤" value={fmtPct(drawdown)} tone={pnlStatTone(drawdown)} />
      <StatCard
        label="胜率"
        value={run?.win_rate != null ? `${run.win_rate.toFixed(1)}%` : "—"}
        sub={`${run?.trade_count ?? 0} 笔交割`}
      />
      <StatCard
        label="策略"
        value={<span className="text-[15px]">{run?.strategy_name ?? "—"}</span>}
        tone="brand"
        sub={run?.display_name ?? undefined}
      />
    </div>
  );
}
