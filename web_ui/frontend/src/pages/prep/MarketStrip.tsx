import { Badge } from "../../components/Badge";
import { MARKET_STATE_LABELS, UNKNOWN_REASON_LABELS, labelOf } from "../../lib/labels";
import { fmtNum } from "../../lib/format";
import { positionLabel, type MarketView } from "./prepModel";

function toneForState(state: string | null): "normal" | "warning" | "positive" | "muted" {
  if (state === "strong") return "positive";
  if (state === "weak" || state === "ice") return "warning";
  if (state === "neutral") return "normal";
  return "muted";
}

function Metric({
  label,
  value,
  unit,
  hint,
}: {
  label: string;
  value: string;
  unit?: string;
  hint?: string;
}) {
  return (
    <div className="min-w-[86px] shrink-0 rounded-md border border-hairline bg-raise px-3 py-2" title={hint}>
      <div className="text-caption text-ink-2">{label}</div>
      <div className="mt-0.5 text-title font-semibold text-ink">
        <span className="num">{value}</span>
        {unit ? <span className="ml-0.5 text-caption font-normal text-ink-2">{unit}</span> : null}
      </div>
    </div>
  );
}

function signed(value: number | null, unit = "%"): string {
  if (value == null) return "—";
  return `${value > 0 ? "+" : ""}${fmtNum(value, 1)}${unit}`;
}

/** 市场（横向）：真实原始指标（不是子分冒充）；390px 在容器内横向滚动。 */
export function MarketStrip({ market }: { market: MarketView | null }) {
  if (!market) {
    return <div className="text-body text-ink-3">该日无市场数据。</div>;
  }
  const inputs = market.inputs;

  return (
    <div className="grid gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={toneForState(market.state)}>
          {labelOf(MARKET_STATE_LABELS, market.state, "未评估")}
        </Badge>
        {market.partial ? <Badge tone="warning">数据不完整</Badge> : null}
        {market.unknownReason ? (
          <span className="text-caption text-ink-3">
            {labelOf(UNKNOWN_REASON_LABELS, market.unknownReason, "部分指标缺失")}
          </span>
        ) : null}
        <span className="text-caption text-ink-2">
          评分 <span className="num text-ink">{fmtNum(market.score, 1)}</span>
          {" · "}系数 <span className="num text-ink">{fmtNum(market.coeff, 2)}</span>
          {" · "}仓位建议 <span className="text-ink">{market.positionAdvice ?? "—"}</span>
          {" · "}建议仓位 <span className="text-ink">{positionLabel(market.positionPct)}</span>
        </span>
      </div>

      <div className="overflow-x-auto">
        <div className="flex min-w-max gap-2">
          <Metric label="涨停" value={inputs.limitUpCnt == null ? "—" : String(inputs.limitUpCnt)} unit="家" />
          <Metric label="跌停" value={inputs.limitDownCnt == null ? "—" : String(inputs.limitDownCnt)} unit="家" />
          <Metric label="昨日涨停溢价" value={signed(inputs.premiumPct)} />
          <Metric
            label="晋级率"
            value={inputs.promotionRate == null ? "—" : fmtNum(inputs.promotionRate, 1)}
            unit="%"
          />
          <div className="min-w-[86px] shrink-0 rounded-md border border-hairline bg-raise px-3 py-2">
            <div className="text-caption text-ink-2">最高板</div>
            <div className="mt-0.5 flex items-center gap-1.5">
              <span className="num text-title font-semibold text-ink">
                {inputs.maxBoardHeight == null ? "—" : String(inputs.maxBoardHeight)}
              </span>
              <span className="text-caption text-ink-2">板</span>
              {market.needsRecompute ? <Badge tone="warning">需重算</Badge> : null}
            </div>
          </div>
          <Metric
            label="炸板率"
            value={inputs.brokenRate == null ? "—" : fmtNum(inputs.brokenRate, 1)}
            unit="%"
          />
          <Metric
            label="成交额"
            value={inputs.totalAmountYi == null ? "—" : fmtNum(inputs.totalAmountYi, 0)}
            unit="亿"
          />
          <Metric
            label="分数环比"
            value={signed(market.scoreChange, "")}
            hint="相对上一份复盘快照（上一交易日）的市场情绪评分变化。"
          />
        </div>
      </div>

      {market.warnings.length ? (
        <div className="rounded-md border border-warning-line bg-warning-soft px-2.5 py-1.5 text-body text-warning">
          {market.warnings.join("；")}
        </div>
      ) : null}
    </div>
  );
}
