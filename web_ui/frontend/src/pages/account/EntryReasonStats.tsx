import type { AccountPositionStats } from "../../api/account";
import { Badge } from "../../components/Badge";
import { EmptyState } from "../../components/EmptyState";
import { Section } from "../../components/Section";
import { fmtPct, pnlClass } from "../../lib/format";

export function EntryReasonStats({ stats }: { stats: AccountPositionStats | null }) {
  const rows = stats?.rows ?? [];
  return (
    <Section
      title="买点统计"
      description={
        stats
          ? `生命周期列只聚合已平仓；期间列含未平仓/半卖。本段贡献分母=期初权益 ${stats.start_equity != null ? stats.start_equity.toFixed(2) : "—"}；`
            + `口径=${stats.contribution_basis ?? "unknown"}`
            + (stats.reconciliation?.diff != null
              ? `；权益对账差 ${stats.reconciliation.diff.toFixed(2)}`
              : "；权益对账不可用")
            + (stats.period_complete === false
              ? `；期间归因不完整（unknown ${stats.unknown_rows ?? 0} 行）`
              : "")
          : "本段归因账本（含继承仓半卖/未平仓）；旧批次明示近似或未知"
      }
      bodyClassName="p-0"
      right={<Badge tone="outline">{stats?.total_trades ?? 0} 笔</Badge>}
    >
      {rows.length === 0 ? (
        <EmptyState
          title={stats == null ? "统计数据不可用" : "暂无已平仓持仓"}
          description={stats == null ? undefined : "完成至少一笔买卖后展示买点胜率与贡献。"}
        />
      ) : (
        <div className="dt-wrap">
          <table className="dt" style={{ minWidth: 760 }}>
            <thead>
              <tr>
                <th>买点</th>
                <th className="text-right">笔数</th>
                <th className="text-right">盈利笔数</th>
                <th>胜率</th>
                <th className="text-right">平均每笔收益</th>
                <th className="text-right">本段盈亏</th>
                <th className="text-right">累计贡献</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => {
                const total = row.reason_code === "__total__";
                const weight = total ? "font-semibold text-ink" : "";
                return (
                  <tr key={row.reason_code}>
                    <td className={weight}>{row.label}</td>
                    <td className={`text-right num ${total ? "text-ink" : "text-ink-2"}`}>
                      {row.trades}
                    </td>
                    <td className={`text-right num ${total ? "text-ink" : "text-ink-2"}`}>
                      {row.wins}
                    </td>
                    <td>
                      <div className="flex items-center gap-2.5">
                        <div className="meter w-16 shrink-0">
                          <span
                            style={{
                              width: `${Math.max(0, Math.min(100, row.win_rate ?? 0))}%`,
                              background: "var(--color-ink-2)",
                            }}
                          />
                        </div>
                        <span className={`num text-body ${total ? "text-ink" : "text-ink-2"}`}>
                          {row.win_rate != null ? `${row.win_rate.toFixed(1)}%` : "—"}
                        </span>
                        {!total && row.trades > 0 && row.trades < 5 ? (
                          <Badge tone="warning">样本不足</Badge>
                        ) : null}
                      </div>
                    </td>
                    <td className={`text-right num ${pnlClass(row.avg_return)} ${total ? "font-semibold" : ""}`}>
                      {fmtPct(row.avg_return)}
                    </td>
                    <td className={`text-right num ${pnlClass(row.period_pnl ?? null)} ${total ? "font-semibold" : ""}`}>
                      {row.period_pnl != null ? row.period_pnl.toFixed(2) : "—"}
                      {row.has_open ? <span className="ml-1 text-caption text-ink-3">含未平</span> : null}
                    </td>
                    <td className={`text-right num ${pnlClass(row.contribution)} ${total ? "font-semibold" : ""}`}>
                      {fmtPct(row.contribution)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </Section>
  );
}
