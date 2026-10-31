import type { AccountSnapshot } from "../../api/account";
import { Badge } from "../../components/Badge";
import { Section } from "../../components/Section";
import { IconWallet } from "../../components/icons";
import { fmtNum, fmtPct, pnlClass } from "../../lib/format";

export function CurrentPosition({ latest }: { latest: AccountSnapshot | null }) {
  const hasPosition = Boolean(latest?.position_code);
  return (
    <Section
      title="当前持仓"
      description={latest ? `快照 ${latest.trade_date}` : "等待快照"}
      bodyClassName="px-4 py-3.5"
    >
      {!hasPosition ? (
        <div className="flex flex-col items-center gap-2 py-8 text-center">
          <div className="grid h-10 w-10 place-items-center rounded-xl border border-hairline bg-white/[0.03] text-ink-2">
            <IconWallet size={19} />
          </div>
          <div className="text-[13px] text-ink">空仓</div>
          <div className="text-[11.5px] text-ink-3">当前无持仓，资金处于现金状态。</div>
        </div>
      ) : (
        <div className="grid gap-3">
          <div className="flex items-center justify-between gap-2">
            <div className="min-w-0">
              <div className="truncate text-[15px] font-semibold text-ink">{latest!.position_name}</div>
              <div className="num mt-0.5 text-[11.5px] text-ink-3">{latest!.position_code}</div>
            </div>
            <Badge tone="brand">持仓中</Badge>
          </div>
          <div className="grid grid-cols-2 gap-x-4 gap-y-2.5 rounded-lg border border-hairline bg-white/[0.015] px-3.5 py-3">
            <Metric label="数量" value={latest!.position_qty} />
            <Metric label="成本" value={fmtNum(latest!.position_cost)} />
            <Metric label="市价" value={fmtNum(latest!.position_market_price)} />
            <Metric
              label="浮盈亏"
              value={fmtPct(latest!.position_unrealized_return)}
              className={pnlClass(latest!.position_unrealized_return)}
            />
          </div>
          <div className="flex items-center justify-between text-[11.5px] text-ink-3">
            <span>现金</span>
            <span className="num text-ink-2">{fmtNum(latest!.cash, 2)}</span>
          </div>
        </div>
      )}
    </Section>
  );
}

function Metric({
  label,
  value,
  className = "",
}: {
  label: string;
  value: number | string | null;
  className?: string;
}) {
  return (
    <div>
      <div className="text-[10.5px] uppercase tracking-[0.08em] text-ink-3">{label}</div>
      <div className={`num mt-0.5 text-[12.5px] text-ink ${className}`}>{value ?? "—"}</div>
    </div>
  );
}
