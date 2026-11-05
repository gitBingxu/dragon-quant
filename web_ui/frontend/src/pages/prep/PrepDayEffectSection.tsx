/**
 * 当前选中日效果（五区块第 3 块）：
 * - 主线板块：仅后端返回的真主线，T 日 / 次日涨跌与匹配方式；
 * - 推荐 / 不推荐逐股：严格最大收益 / 最大回撤 / 持有天数；
 * - 组内汇总：有效 / 无法评估 / 待评估 + 收益与回撤均值、中位数；
 * - 数据不足组折叠为「待评估 N 只」明细。
 *
 * 行情只读本地日 K；未到期与无法评估分开显示，缺数据一律「待评估」。
 */
import type {
  EffectBoardRow,
  EffectGroupSummary,
  EffectStockRow,
  PrepEffect,
} from "../../api/assets";
import { Badge } from "../../components/Badge";
import { CollapsibleSection } from "../../components/CollapsibleSection";
import { type Column } from "../../components/DataTable";
import { EmptyState } from "../../components/EmptyState";
import { PaginatedDataTable } from "../../components/PaginatedDataTable";
import { fmtNum, fmtPct, pnlClass } from "../../lib/format";
import {
  ACCURACY_GROUP_LABELS,
  EFFECT_MATCH_LABELS,
  EFFECT_REASON_LABELS,
  EFFECT_STOCK_STATUS_LABELS,
  labelOf,
} from "../../lib/labels";

function reasonText(reason: string | null): string {
  return reason ? labelOf(EFFECT_REASON_LABELS, reason, reason) : "—";
}

const BOARD_COLUMNS: Column<EffectBoardRow>[] = [
  {
    key: "rank",
    header: "排名",
    align: "right",
    render: (row) => <span className="num text-body text-ink-2">{row.rank ?? "—"}</span>,
  },
  {
    key: "name",
    header: "板块",
    render: (row) => (
      <span className="font-medium text-ink">{row.board_name ?? "—"}</span>
    ),
  },
  {
    key: "category",
    header: "类别",
    render: (row) => <span className="text-body text-ink-2">{row.category ?? "—"}</span>,
  },
  {
    key: "t_change",
    header: "T 日涨跌",
    align: "right",
    render: (row) => (
      <span className={`num text-body ${pnlClass(row.t_change_pct)}`}>
        {row.t_change_pct == null ? "—" : fmtPct(row.t_change_pct)}
      </span>
    ),
  },
  {
    key: "next_change",
    header: "次日涨跌",
    align: "right",
    render: (row) => (
      <span className={`num text-body ${pnlClass(row.next_change_pct)}`}>
        {row.next_change_pct == null ? "—" : fmtPct(row.next_change_pct)}
      </span>
    ),
  },
  {
    key: "match",
    header: "匹配方式",
    render: (row) => (
      <span className="text-body text-ink-2">
        {row.match ? labelOf(EFFECT_MATCH_LABELS, row.match, row.match) : "—"}
      </span>
    ),
  },
  {
    key: "reason",
    header: "待评估原因",
    render: (row) => (
      <span className="text-caption text-ink-3">
        {reasonText(row.exclusion_reason)}
      </span>
    ),
  },
];

const STOCK_COLUMNS: Column<EffectStockRow>[] = [
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
    key: "group",
    header: "组",
    render: (row) => (
      <span className="text-body text-ink-2">
        {labelOf(ACCURACY_GROUP_LABELS, row.group, row.group)}
      </span>
    ),
  },
  {
    key: "status",
    header: "状态",
    render: (row) => (
      <Badge tone={row.status === "completed" ? "normal" : "outline"}>
        {labelOf(EFFECT_STOCK_STATUS_LABELS, row.status, row.status)}
      </Badge>
    ),
  },
  {
    key: "buy_date",
    header: "买入日",
    render: (row) => <span className="num text-body text-ink-2">{row.buy_date ?? "—"}</span>,
  },
  {
    key: "buy_price",
    header: "买价",
    align: "right",
    render: (row) => (
      <span className="num text-body text-ink-2">{fmtNum(row.buy_price, 2)}</span>
    ),
  },
  {
    key: "max_return",
    header: "最大收益",
    align: "right",
    render: (row) => (
      <span className={`num text-body ${pnlClass(row.max_return_5d)}`}>
        {row.max_return_5d == null ? "—" : fmtPct(row.max_return_5d)}
      </span>
    ),
  },
  {
    key: "max_drawdown",
    header: "最大回撤",
    align: "right",
    render: (row) => (
      <span className={`num text-body ${pnlClass(row.max_drawdown_5d)}`}>
        {row.max_drawdown_5d == null ? "—" : fmtPct(row.max_drawdown_5d)}
      </span>
    ),
  },
  {
    key: "hold_days",
    header: "持有天数",
    align: "right",
    render: (row) => (
      <span className="num text-body text-ink-2">
        {row.max_return_hold_days ?? "—"}
      </span>
    ),
  },
  {
    key: "reason",
    header: "待评估原因",
    render: (row) => (
      <span className="text-caption text-ink-3">
        {reasonText(row.reason)}
        {row.partial_window ? " · 部分窗口" : ""}
      </span>
    ),
  },
];

const SUMMARY_COLUMNS: Column<EffectGroupSummary>[] = [
  {
    key: "group",
    header: "组",
    render: (row) => (
      <span className="text-body text-ink">
        {row.label || labelOf(ACCURACY_GROUP_LABELS, row.group, row.group)}
      </span>
    ),
  },
  {
    key: "valid",
    header: "有效",
    align: "right",
    render: (row) => <span className="num text-body text-ink">{row.valid}</span>,
  },
  {
    key: "cannot",
    header: "无法评估",
    align: "right",
    render: (row) => <span className="num text-body text-ink-2">{row.cannot_evaluate}</span>,
  },
  {
    key: "partial",
    header: "窗口未成熟",
    align: "right",
    render: (row) => (
      <span className="num text-body text-ink-3">{row.partial ?? 0}</span>
    ),
  },
  {
    key: "pending",
    header: "待评估",
    align: "right",
    render: (row) => <span className="num text-body text-ink-2">{row.pending}</span>,
  },
  {
    key: "mean_return",
    header: "收益均值",
    align: "right",
    render: (row) => (
      <span className={`num text-body ${pnlClass(row.mean_max_return_5d)}`}>
        {row.mean_max_return_5d == null ? "—" : fmtPct(row.mean_max_return_5d)}
      </span>
    ),
  },
  {
    key: "median_return",
    header: "收益中位数",
    align: "right",
    render: (row) => (
      <span className={`num text-body ${pnlClass(row.median_max_return_5d)}`}>
        {row.median_max_return_5d == null ? "—" : fmtPct(row.median_max_return_5d)}
      </span>
    ),
  },
  {
    key: "mean_drawdown",
    header: "回撤均值",
    align: "right",
    render: (row) => (
      <span className={`num text-body ${pnlClass(row.mean_max_drawdown_5d)}`}>
        {row.mean_max_drawdown_5d == null ? "—" : fmtPct(row.mean_max_drawdown_5d)}
      </span>
    ),
  },
  {
    key: "median_drawdown",
    header: "回撤中位数",
    align: "right",
    render: (row) => (
      <span className={`num text-body ${pnlClass(row.median_max_drawdown_5d)}`}>
        {row.median_max_drawdown_5d == null
          ? "—"
          : fmtPct(row.median_max_drawdown_5d)}
      </span>
    ),
  },
];

function StockGroup({
  title,
  rows,
  empty,
  resetKey,
}: {
  title: string;
  rows: EffectStockRow[];
  empty: string;
  resetKey: string | number | null;
}) {
  return (
    <div className="grid gap-1.5">
      <div className="flex items-center gap-2">
        <div className="text-body font-medium text-ink">{title}</div>
        <Badge tone="outline">{rows.length} 只</Badge>
      </div>
      <div className="overflow-hidden rounded-lg border border-hairline">
        <PaginatedDataTable
          columns={STOCK_COLUMNS}
          rows={rows}
          rowKey={(row) => row.code}
          resetKey={resetKey}
          minWidth={1080}
          empty={empty}
        />
      </div>
    </div>
  );
}

export function PrepDayEffectSection({
  effect,
  loading,
  error,
}: {
  effect: PrepEffect | null;
  loading: boolean;
  error: string | null;
}) {
  if (loading) {
    return <div className="text-body text-ink-3">正在评估本地日 K…</div>;
  }
  if (error) {
    return (
      <div className="alert-danger flex items-center gap-2 rounded-lg px-3.5 py-2.5 text-body">
        效果读取失败：{error}
      </div>
    );
  }
  if (!effect) {
    return (
      <EmptyState
        title="暂无当日效果"
        description="选择日期后只读本地日 K 评估；缺数据一律待评估。"
      />
    );
  }

  const recommended = effect.stocks.rows.filter(
    (row) => row.group === "recommended"
  );
  const notRecommended = effect.stocks.rows.filter(
    (row) => row.group === "not_recommended"
  );
  const insufficient = effect.stocks.rows.filter(
    (row) => row.group === "data_insufficient"
  );
  const summaries = effect.stocks.summary;
  const noContent =
    effect.status === "insufficient_data" &&
    !effect.boards.available &&
    !effect.stocks.available;

  return (
    <div className="grid gap-3">
      <div className="flex flex-wrap items-center gap-2 text-caption text-ink-2">
        <Badge tone={effect.status === "ok" ? "positive" : "warning"}>
          {effect.status === "ok" ? "可评估" : "待评估"}
        </Badge>
        {effect.reason ? (
          <Badge tone="outline">{reasonText(effect.reason)}</Badge>
        ) : null}
        <span>
          交易日 <span className="num text-ink">{effect.trade_date}</span>
          {effect.next_trade_date ? (
            <>
              {" · "}次日 <span className="num text-ink">{effect.next_trade_date}</span>
            </>
          ) : null}
        </span>
        {effect.snapshot_version ? (
          <span>快照版本 {effect.snapshot_version}</span>
        ) : null}
      </div>

      {noContent ? (
        <EmptyState
          title="当日无可评估效果"
          description={reasonText(effect.reason)}
        />
      ) : null}

      <div className="grid gap-1.5">
        <div className="flex flex-wrap items-center gap-2">
          <div className="text-body font-medium text-ink">主线板块次日涨跌</div>
          {effect.boards.source === "main_boards" ? (
            <Badge tone="warning">旧快照回退 main_boards</Badge>
          ) : null}
          <span className="text-caption text-ink-3">
            仅真主线；观察位不进入单日效果。
          </span>
        </div>
        {effect.boards.available ? (
          <>
            <div className="overflow-hidden rounded-lg border border-hairline">
              <PaginatedDataTable
                columns={BOARD_COLUMNS}
                rows={effect.boards.rows}
                rowKey={(row) => row.board_code ?? row.board_name ?? String(row.rank)}
                resetKey={effect.trade_date}
                minWidth={900}
                empty="无真主线板块可评估。"
              />
            </div>
            <div className="text-caption text-ink-3">
              已评估 {effect.boards.summary.evaluated} 个 · 缺失{" "}
              {effect.boards.summary.missing} 个 · 次日均值{" "}
              {effect.boards.summary.mean_change_pct == null
                ? "—"
                : fmtPct(effect.boards.summary.mean_change_pct)}
            </div>
          </>
        ) : (
          <div className="text-body text-ink-3">
            {reasonText(effect.boards.reason)}
          </div>
        )}
      </div>

      {effect.stocks.available ? (
        <>
          <div className="grid gap-1.5">
            <div className="text-body font-medium text-ink">组内汇总</div>
            <div className="overflow-hidden rounded-lg border border-hairline">
              <PaginatedDataTable
                columns={SUMMARY_COLUMNS}
                rows={summaries}
                rowKey={(row) => row.group}
                resetKey={effect.trade_date}
                minWidth={900}
                empty="暂无组汇总。"
              />
            </div>
          </div>

          <StockGroup
            title="推荐"
            rows={recommended}
            empty="无推荐标的。"
            resetKey={effect.trade_date}
          />
          <StockGroup
            title="不推荐"
            rows={notRecommended}
            empty="无明确不推荐标的。"
            resetKey={effect.trade_date}
          />

          {insufficient.length ? (
            <CollapsibleSection
              title={`待评估 ${insufficient.length} 只（数据不足，不计入组汇总）`}
              defaultOpen={false}
            >
              <ul className="grid gap-0.5 text-body text-ink-2">
                {insufficient.map((row) => (
                  <li key={row.code}>
                    <span className="font-mono text-ink-3">{row.code}</span>{" "}
                    <span className="text-ink">{row.name ?? "—"}</span>
                    {" · "}
                    {reasonText(row.reason)}
                  </li>
                ))}
              </ul>
            </CollapsibleSection>
          ) : null}
        </>
      ) : (
        <div className="text-body text-ink-3">
          {reasonText(effect.stocks.reason)}
        </div>
      )}

      {effect.caveats.length ? (
        <CollapsibleSection
          title={`口径说明 (${effect.caveats.length})`}
          defaultOpen={false}
        >
          <ul className="grid gap-0.5 text-caption text-ink-2">
            {effect.caveats.map((item, index) => (
              <li key={`${item}-${index}`}>· {item}</li>
            ))}
          </ul>
        </CollapsibleSection>
      ) : null}
    </div>
  );
}
