/**
 * 全历史逐股判断准确率（prep / auction 共用）。
 *
 * 准确率固定截至「最新可验证日」，**不随选中日期变化**；数据由页面单独
 * 请求 `/api/prep/accuracy` 或 `/api/auction/accuracy`。本组件只做投影：
 * - 顶部给出截止日、最新本地日 K、正确 / 有效 / 排除 / 命中率；
 * - 排除原因分样本级（`excluded_by_reason`）与日级
 *   （`excluded_snapshot_dates`）展示，auction 统一按「条数」表述；
 * - 逐股表按后端顺序（有效数降序、代码升序）渲染；
 * - 文案强制说明：历史样本描述性统计，不构成策略有效性结论；
 *   统一使用「命中率」，不使用策略有效性措辞。
 */
import type {
  AccuracyResponse,
  AccuracyStock,
} from "../api/assets";
import { Badge } from "./Badge";
import { CollapsibleSection } from "./CollapsibleSection";
import { type Column } from "./DataTable";
import { EmptyState } from "./EmptyState";
import { PaginatedDataTable } from "./PaginatedDataTable";
import { Section } from "./Section";
import { StatCard } from "./StatCard";
import {
  ACCURACY_REASON_LABELS,
  DAY_KIND_LABELS,
  labelOf,
} from "../lib/labels";

function pctRate(value: number | null): string {
  return value == null ? "—" : `${(value * 100).toFixed(1)}%`;
}

const STOCK_COLUMNS: Column<AccuracyStock>[] = [
  {
    key: "code",
    header: "代码",
    render: (row) => <span className="font-mono text-body text-ink">{row.code}</span>,
  },
  {
    key: "name",
    header: "名称",
    render: (row) => <span className="text-body text-ink">{row.name ?? "—"}</span>,
  },
  {
    key: "correct",
    header: "正确",
    align: "right",
    render: (row) => <span className="num text-body text-ink">{row.correct}</span>,
  },
  {
    key: "valid",
    header: "有效",
    align: "right",
    render: (row) => <span className="num text-body text-ink-2">{row.valid}</span>,
  },
  {
    key: "direction_valid",
    header: "方向分母",
    align: "right",
    render: (row) => (
      <span className="num text-body text-ink-2">
        {(row as AccuracyStock & { direction_valid?: number }).direction_valid ?? "—"}
      </span>
    ),
  },
  {
    key: "excluded",
    header: "排除",
    align: "right",
    render: (row) => <span className="num text-body text-ink-2">{row.excluded}</span>,
  },
  {
    key: "hit_rate",
    header: "命中率",
    align: "right",
    render: (row) => (
      <span className="num text-body text-ink">{pctRate(row.hit_rate)}</span>
    ),
  },
];

function ReasonBadges({
  reasons,
  emptyText,
}: {
  reasons: Array<[string, number]>;
  emptyText: string;
}) {
  if (!reasons.length) {
    return <span className="text-body text-ink-3">{emptyText}</span>;
  }
  return (
    <>
      {reasons.map(([key, value]) => (
        <Badge key={key} tone="muted">
          {labelOf(ACCURACY_REASON_LABELS, key, key)} {value}
        </Badge>
      ))}
    </>
  );
}

function Insufficient({ data }: { data: AccuracyResponse }) {
  return (
    <div className="grid gap-2">
      <div className="flex flex-wrap items-center gap-2 text-caption text-ink-2">
        <Badge tone="warning">待评估</Badge>
        {data.reason ? (
          <Badge tone="outline">
            {labelOf(ACCURACY_REASON_LABELS, data.reason, data.reason)}
          </Badge>
        ) : null}
        <span>
          最新可验证日 <span className="num text-ink">{data.as_of ?? "—"}</span>
        </span>
      </div>
      <EmptyState
        title="历史样本不足，暂不展示准确率"
        description="缺数据是数据事实；不用 0% 或虚假稳定性代替。"
      />
    </div>
  );
}

function Body({
  data,
  scope,
}: {
  data: AccuracyResponse;
  scope: "prep" | "auction";
}) {
  const isAuction = scope === "auction";
  const contract = (data.contract || {}) as {
    cost_bps?: number; legacy_raw_open_to_close?: boolean;
  };
  const comparable = (data.stratification?.comparable as boolean | undefined) !== false;
  const sampleReasons = Object.entries(data.summary.excluded_by_reason ?? {});
  const dayReasons = Object.entries(data.excluded_snapshot_dates ?? {}).filter(
    ([, value]) => value > 0
  );
  const dayKinds = Object.entries(data.day_kind_counts ?? {});
  const entryLabel =
    data.window.entry_offset === 0 ? "T" : `T+${data.window.entry_offset}`;
  const exitLabel =
    data.window.exit_offset === 0 ? "T" : `T+${data.window.exit_offset}`;

  return (
    <div className="grid gap-3">
      <div className="flex flex-wrap items-center gap-2 text-caption text-ink-2">
        <Badge tone="brand">最新可验证日 {data.as_of ?? "—"}</Badge>
        <span>
          本地日 K 至 <span className="num text-ink">{data.latest_bar_date ?? "—"}</span>
        </span>
        <span>快照 {data.snapshot_count} 个</span>
        <span>
          样本单位：{data.sample_unit === "stock_day" ? "股票 × 快照日" : data.sample_unit}
        </span>
        <span>
          窗口：{entryLabel} → {exitLabel}
        </span>
      </div>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
        <StatCard label="正确" value={data.summary.correct} tone="positive" />
        <StatCard label="有效样本" value={data.summary.valid} sub="含无方向样本" />
        <StatCard
          label="方向分母"
          value={data.summary.direction_valid ?? "—"}
          sub="仅 bull/bear/推荐"
        />
        <StatCard
          label={isAuction ? "排除条数" : "排除"}
          value={data.summary.excluded}
          tone={data.summary.excluded ? "warning" : "neutral"}
          sub="缺数据 / 未成熟 / 信号缺失"
        />
        <StatCard
          label="命中率"
          value={pctRate(data.summary.hit_rate)}
          sub="正确 ÷ 方向分母（不是 ÷ 有效）"
        />
      </div>

      <div className="grid gap-2">
        <div className="text-caption text-ink-2">
          主研究合同：可成交开盘 → 第 N 交易日开盘，扣
          {contract.cost_bps ?? "—"}bp；
          {contract.legacy_raw_open_to_close ? "legacy raw 口径" : "与 decision-review 同源"}。
          独立日 {data.independent_dates ?? "—"} · 独立股票 {data.independent_codes ?? "—"}
          · 主 horizon {data.summary.primary_horizon ?? "—"}
        </div>
        {data.horizons ? (
          <div className="overflow-x-auto rounded-lg border border-hairline">
            <table className="w-full min-w-[560px] border-collapse text-caption">
              <thead>
                <tr className="border-b border-hairline text-left text-ink-3">
                  <th className="py-1.5 pr-3">持有期</th>
                  <th className="py-1.5 pr-3">有效</th>
                  <th className="py-1.5 pr-3">方向分母</th>
                  <th className="py-1.5 pr-3">中性</th>
                  <th className="py-1.5 pr-3">命中率</th>
                  <th className="py-1.5">排除原因</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(data.horizons).map(([key, item]) => (
                  <tr key={key} className="border-b border-hairline">
                    <td className="py-1.5 pr-3 text-ink">T+{item.horizon} 开盘</td>
                    <td className="py-1.5 pr-3">{item.valid}</td>
                    <td className="py-1.5 pr-3">{item.direction_valid}</td>
                    <td className="py-1.5 pr-3">{item.neutral}</td>
                    <td className="py-1.5 pr-3">{pctRate(item.hit_rate)}</td>
                    <td className="py-1.5 text-ink-3">
                      {Object.entries(item.excluded_by_reason ?? {})
                        .map(([reason, count]) => `${reason}:${count}`)
                        .join("，") || "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
        {data.groups && Object.keys(data.groups).length ? (
          <div className="overflow-x-auto rounded-lg border border-hairline">
            <table className="w-full min-w-[560px] border-collapse text-caption">
              <thead>
                <tr className="border-b border-hairline text-left text-ink-3">
                  <th className="py-1.5 pr-3">分组（同窗口净收益）</th>
                  <th className="py-1.5 pr-3">样本</th>
                  <th className="py-1.5 pr-3">均值</th>
                  <th className="py-1.5 pr-3">中位</th>
                  <th className="py-1.5 pr-3">日期等权</th>
                  <th className="py-1.5 pr-3">胜/负/平</th>
                  <th className="py-1.5">避损 / 错失</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(data.groups).map(([name, group]) => (
                  <tr key={name} className="border-b border-hairline">
                    <td className="py-1.5 pr-3 text-ink">{name}</td>
                    <td className="py-1.5 pr-3">{group.samples}</td>
                    <td className="py-1.5 pr-3">{pctRate(group.mean_return_pct)}</td>
                    <td className="py-1.5 pr-3">{pctRate(group.median_return_pct)}</td>
                    <td className="py-1.5 pr-3">{pctRate(group.date_equal_mean_return_pct)}</td>
                    <td className="py-1.5 pr-3">{group.wins}/{group.losses}/{group.flats}</td>
                    <td className="py-1.5">
                      {pctRate(group.avoided_loss_rate)} / {pctRate(group.missed_gain_rate)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
        {data.spread ? (
          <div className="rounded-md border border-hairline px-3 py-2 text-caption text-ink-2">
            推荐−不推荐差值：
            {pctRate((data.spread.value as number | null) ?? null)}
            {(() => {
              const boot = (data.spread?.bootstrap || {}) as Record<string, unknown>;
              if (boot.low == null || boot.high == null) {
                return `（${String(boot.status ?? "无区间")}，不出虚假显著性）`;
              }
              return `（日期级 bootstrap ${pctRate(boot.low as number)} ~ ${pctRate(boot.high as number)}，seed=${String(boot.seed ?? "—")}）`;
            })()}
          </div>
        ) : null}
        <div className="flex flex-wrap items-center gap-2 text-caption text-ink-2">
          <span>版本：</span>
          {Object.entries(data.versions_seen ?? {}).map(([version, count]) => (
            <Badge key={version} tone="outline">{version} × {count}</Badge>
          ))}
          <Badge tone={comparable ? "outline" : "warning"}>
            {comparable ? "单一版本" : "跨版本不可比"}
          </Badge>
          {data.axes ? (
            Object.entries(data.axes).map(([key, value]) => (
              <Badge key={key} tone="muted">{key} {value}</Badge>
            ))
          ) : null}
        </div>
      </div>

      <div className="rounded-md border border-warning-line bg-warning-soft px-3 py-2 text-body text-warning">
        命中率是历史样本的描述性统计，不构成策略有效性结论；样本同源相关，不代表未来表现。
      </div>

      {dayKinds.length ? (
        <div className="flex flex-wrap items-center gap-2 text-caption text-ink-2">
          <span>日级计数</span>
          {dayKinds.map(([key, value]) => (
            <Badge key={key} tone="outline">
              {labelOf(DAY_KIND_LABELS, key, key)} {value}
            </Badge>
          ))}
        </div>
      ) : null}

      <div className="grid gap-1.5">
        <div className="text-caption text-ink-2">排除条数（样本级 / 日级）</div>
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-caption text-ink-3">样本级</span>
          <ReasonBadges reasons={sampleReasons} emptyText="无" />
          <span className="text-caption text-ink-3">日级</span>
          <ReasonBadges reasons={dayReasons} emptyText="无" />
        </div>
      </div>

      <div className="overflow-hidden rounded-lg border border-hairline">
        <PaginatedDataTable
          columns={STOCK_COLUMNS}
          rows={data.stocks}
          rowKey={(row) => row.code}
          minWidth={640}
          empty="暂无逐股样本。"
        />
      </div>

      {data.caveats.length || data.warnings.length ? (
        <CollapsibleSection
          title={`口径说明 (${data.caveats.length + data.warnings.length})`}
          defaultOpen={false}
        >
          <ul className="grid gap-0.5 text-caption text-ink-2">
            {data.caveats.map((item, index) => (
              <li key={`${item}-${index}`}>· {item}</li>
            ))}
          </ul>
          {data.warnings.length ? (
            <ul className="mt-2 grid gap-0.5 font-mono text-caption text-ink-3">
              {data.warnings.map((item, index) => (
                <li key={`warning-${item}-${index}`}>· {item}</li>
              ))}
            </ul>
          ) : null}
        </CollapsibleSection>
      ) : null}
    </div>
  );
}

export function AccuracyBoard({
  scope,
  data,
  loading,
  error,
}: {
  scope: "prep" | "auction";
  data: AccuracyResponse | null;
  loading: boolean;
  error: string | null;
}) {
  const windowText =
    scope === "auction"
      ? "T 日 09:25 可得 → T 日开盘买入 → 第 N 交易日开盘卖出"
      : "T 日 16:40 可得 → T+1 开盘买入 → 第 N 交易日开盘卖出";
  return (
    <Section
      title="全历史逐股判断准确率"
      description={`固定截至最新可验证日；${windowText}；扣 13bp 成本，与 decision-review 同源。`}
    >
      {loading ? (
        <div className="text-body text-ink-3">正在读取历史准确率…</div>
      ) : error ? (
        <div className="alert-danger flex items-center gap-2 rounded-lg px-3.5 py-2.5 text-body">
          准确率读取失败：{error}
        </div>
      ) : !data ? (
        <EmptyState
          title="暂无准确率数据"
          description="历史样本尚未生成；缺数据一律待评估。"
        />
      ) : data.status === "insufficient_data" ? (
        <Insufficient data={data} />
      ) : (
        <Body data={data} scope={scope} />
      )}
    </Section>
  );
}
