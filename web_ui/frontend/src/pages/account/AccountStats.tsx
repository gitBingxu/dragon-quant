import type { AccountRun, AccountSnapshot, AccountSummary } from "../../api/account";
import { StatCard } from "../../components/StatCard";
import { fmtMoney, fmtPct, pnlClass, pnlStatTone } from "../../lib/format";

function metaText(run: AccountRun | null, key: string): string | null {
  const meta = run?.run_meta || {};
  const value = meta[key];
  if (value == null || value === "") return null;
  if (Array.isArray(value)) return value.length ? value.join("、") : null;
  if (typeof value === "object") return null;
  return String(value);
}

/** 本段归因口径的中文说明：unknown 必须明确展示，绝不填 0 冒充精确。 */
function contributionBasisLabel(
  basis: "period_pnl" | "legacy_approximation" | "unknown" | undefined
): string {
  if (basis === "period_pnl") return "期初盯市价值（精确）";
  if (basis === "legacy_approximation") return "旧数据近似（不可精确归因）";
  return "未知（旧数据缺期初估值 / 盯市价）";
}

export function AccountStats({
  run,
  latest,
  summary,
}: {
  run: AccountRun | null;
  latest: AccountSnapshot | null;
  summary?: AccountSummary | null;
}) {
  const returnPct = run?.total_return ?? null;
  const drawdown = run?.max_drawdown ?? null;
  const metrics = summary;
  const winBase =
    metrics?.closed_count != null && metrics?.win_rate != null
      ? `${metrics.wins}÷${metrics.closed_count} 已平仓`
      : `${run?.trade_count ?? 0} 笔交割`;
  const dataQuality = metrics?.data_quality
    ?? metaText(run, "data_quality") ?? "unknown";
  const startEquity = metrics?.start_equity ?? run?.start_equity ?? run?.initial_cash;
  const fallbackDays = metaText(run, "fallback_days");
  const degraded = metaText(run, "priority_degraded");

  return (
    <div className="grid gap-3">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-6">
        <StatCard
          label="本段起点权益"
          value={fmtMoney(startEquity)}
          tone="muted"
          sub={run?.base_run_id ? `续算自 #${run.base_run_id}` : "全量回测"}
        />
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
          label="胜率（持仓生命周期）"
          value={metrics?.win_rate != null ? `${metrics.win_rate.toFixed(1)}%` : run?.win_rate != null ? `${run.win_rate.toFixed(1)}%` : "—"}
          sub={winBase}
        />
        <StatCard
          label="未平仓"
          value={metrics ? `${metrics.open_count}` : "—"}
          sub={metrics ? `已实现 ${fmtMoney(metrics.realized_amount)} · 未实现 ${fmtMoney(metrics.unrealized_amount)}` : "旧批次无明细"}
        />
      </div>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-6">
        <StatCard
          label="平均盈利 / 亏损"
          value={
            metrics ? (
              <span className="num">
                <span className={pnlClass(metrics.avg_win_pct)}>
                  {fmtPct(metrics.avg_win_pct)}
                </span>
                <span className="text-ink-3"> / </span>
                <span className={pnlClass(metrics.avg_loss_pct)}>
                  {fmtPct(metrics.avg_loss_pct)}
                </span>
              </span>
            ) : (
              "—"
            )
          }
        />
        <StatCard
          label="净盈亏比（金额）"
          value={metrics?.net_profit_ratio != null ? metrics.net_profit_ratio.toFixed(2) : "—"}
          sub="样本全赢/全亏时为不可计算"
        />
        <StatCard
          label="期望整笔收益"
          value={fmtMoney(metrics?.expectancy_amount ?? null)}
          sub={
            metrics?.contribution_basis === "period_pnl"
              ? "精确金额"
              : metrics?.contribution_basis === "unknown"
                ? "未知（旧数据，不填 0）"
                : "旧数据近似"
          }
        />
        <StatCard label="费用合计" value={fmtMoney(metrics?.fees ?? null)} />
        <StatCard
          label="最大单笔亏损"
          value={fmtMoney(metrics?.largest_loss_amount ?? null)}
          tone={pnlStatTone(metrics?.largest_loss_amount ?? null)}
        />
        <StatCard
          label="Sharpe"
          value={metrics?.sharpe != null ? metrics.sharpe.toFixed(2) : "—"}
          sub="日频年化，样本 < 2 不可计算"
        />
      </div>

      <div className="rounded-lg border border-hairline px-3.5 py-2.5 text-caption text-ink-2">
        <div className="flex flex-wrap gap-x-4 gap-y-1">
          <span>数据质量：<span className="font-mono">{dataQuality}</span></span>
          <span>策略：{run?.strategy_name ?? "—"}</span>
          {fallbackDays ? <span>候选池回退：{fallbackDays} 天</span> : null}
          {metaText(run, "ranking_mode") ? (
            <span>排序：{metaText(run, "ranking_mode")}</span>
          ) : null}
          <span>
            本段贡献基准：{contributionBasisLabel(metrics?.contribution_basis)}
          </span>
        </div>
        {degraded ? (
          <div className="mt-1 text-ink-3">降级原因：{degraded}</div>
        ) : null}
      </div>
    </div>
  );
}
