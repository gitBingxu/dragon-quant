import type { BsRun, BsStrategy } from "../../api/bs";
import { Badge } from "../../components/Badge";
import { Section } from "../../components/Section";

/** 策略说明卡：四类买点 + 十条卖出规则 + 阈值 chips（文案来自 API/strategy_doc）。 */
export function StrategyCard({
  strategy,
  run,
}: {
  strategy: BsStrategy;
  run: BsRun | null;
}) {
  const thresholds = strategy.thresholds;
  return (
    <Section
      title="当前操盘策略"
      description="假想买卖点回放使用的四类买点与十条卖出规则；数值阈值为代码内唯一来源。"
      right={
        <>
          <Badge tone="outline">策略 v{strategy.version}</Badge>
          {run ? (
            <Badge tone="muted" className="font-mono">
              {run.strategy_hash.slice(0, 6)}
            </Badge>
          ) : null}
        </>
      }
    >
      <div className="grid gap-4 lg:grid-cols-2">
        <div>
          <div className="mb-2 text-body font-semibold text-ink">四类买点</div>
          <ol className="grid gap-2">
            {strategy.buy_signals.map((item) => (
              <li key={item.code} className="rounded-lg border border-hairline bg-raise px-3 py-2">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-body font-medium text-ink">{item.label}</span>
                  <Badge tone="brand">{item.window}</Badge>
                </div>
                <p className="mt-1 text-body leading-relaxed text-ink-2">{item.summary}</p>
              </li>
            ))}
          </ol>
        </div>
        <div>
          <div className="mb-2 text-body font-semibold text-ink">十条卖出规则（按优先级）</div>
          <ol className="grid gap-2">
            {strategy.sell_rules.map((item, index) => (
              <li key={item.name} className="rounded-lg border border-hairline bg-raise px-3 py-2">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="num text-ink-3">{index + 1}</span>
                  <span className="text-body font-medium text-ink">{item.label}</span>
                  <Badge tone={item.default_enabled ? "normal" : "muted"}>
                    {item.default_enabled ? "默认开" : "默认关"}
                  </Badge>
                </div>
                <p className="mt-1 text-body leading-relaxed text-ink-2">{item.summary}</p>
              </li>
            ))}
          </ol>
        </div>
      </div>

      <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-hairline pt-3">
        <span className="text-caption font-semibold uppercase tracking-[0.09em] text-ink-2">
          评估口径
        </span>
        <Badge tone="outline">买入窗口 {thresholds.buy_window_days} 个交易日</Badge>
        <Badge tone="outline">
          收益窗口第 {thresholds.window_skip + 1}–{thresholds.window_skip + thresholds.window_size} 日
        </Badge>
        <Badge tone="outline">买点胜：收益 &gt; {thresholds.buy_win_return_gt}% 且回撤 &gt; {thresholds.buy_win_drawdown_gt}%</Badge>
        <Badge tone="outline">卖得对：回撤 ≤ {thresholds.sell_good_drawdown_le}%</Badge>
        <Badge tone="outline">卖飞：收益 ≥ +{thresholds.sell_fly_return_ge}%</Badge>
        <Badge tone="outline">回放上限 {thresholds.max_replay_trading_days} 个交易日</Badge>
      </div>
    </Section>
  );
}
