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
      description="累计贡献按 Σ已实现盈亏 / 初始资金估算，四类之和 ≈ 组合已实现收益"
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
                <th className="text-right">累计贡献收益</th>
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
                        <span className={`num text-[12px] ${total ? "text-ink" : "text-ink-2"}`}>
                          {row.win_rate != null ? `${row.win_rate.toFixed(1)}%` : "—"}
                        </span>
                        {!total && row.trades > 0 && row.trades < 5 ? (
                          <Badge tone="warn">样本不足</Badge>
                        ) : null}
                      </div>
                    </td>
                    <td className={`text-right num ${pnlClass(row.avg_return)} ${total ? "font-semibold" : ""}`}>
                      {fmtPct(row.avg_return)}
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
