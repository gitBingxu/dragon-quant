import { useEffect, useMemo, useState } from "react";
import {
  fetchAuctionCalendar,
  fetchAuctionDay,
  fetchAuctionEffect,
  type AuctionCalendar,
  type AuctionDayView,
  type AuctionEffect,
  type AuctionSnapshotRow,
} from "../../api/assets";
import { AssetCalendar } from "../../components/AssetCalendar";
import { Badge } from "../../components/Badge";
import { CollapsibleSection } from "../../components/CollapsibleSection";
import { DataTable, type Column } from "../../components/DataTable";
import { EmptyState } from "../../components/EmptyState";
import { PageHeader } from "../../components/PageHeader";
import { Section } from "../../components/Section";
import { StatCard } from "../../components/StatCard";
import { IconAlert } from "../../components/icons-extra";
import { fmtNum, pnlClass } from "../../lib/format";
import {
  CALENDAR_BASIS_LABELS,
  COLLECTION_STATUS_LABELS,
  DATA_SOURCE_LABELS,
  DIRECTION_LABELS,
  EFFECT_QUALITY_LABELS,
  QUALITY_LABELS,
  VALIDATION_LABELS,
  labelOf,
  reasonLabel,
} from "../../lib/labels";
import { navigate, useSearchParams } from "../../router";

function normalizeIso(value: string): string {
  const text = (value || "").trim();
  if (/^\d{8}$/.test(text)) {
    return `${text.slice(0, 4)}-${text.slice(4, 6)}-${text.slice(6, 8)}`;
  }
  return text;
}

function messageOf(exc: unknown): string {
  return exc instanceof Error ? exc.message : String(exc);
}

function qualityTone(value: string | undefined): "positive" | "warning" | "muted" {
  if (value === "ok") return "positive";
  if (value === "degraded" || value === "vetoed") return "warning";
  return "muted";
}

// ─── 1. 运行概况 ──────────────────────────────────────────────────────────

function RunSection({ day }: { day: AuctionDayView }) {
  const run = day.run;
  return (
    <div className="grid gap-3">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-6">
        <StatCard label="交易日" value={day.trade_date} />
        <StatCard label="快照 / 原始资产" value={`${run.snapshot_count} / ${run.asset_count}`} />
        <StatCard
          label="损坏快照行"
          value={run.error_rows}
          tone={run.error_rows ? "danger" : "neutral"}
        />
        <StatCard
          label="仅快照 / 仅资产"
          value={`${run.snapshot_only_codes.length} / ${run.asset_only_codes.length}`}
          sub={
            run.snapshot_only_codes.length || run.asset_only_codes.length
              ? [...run.snapshot_only_codes, ...run.asset_only_codes].slice(0, 4).join("、")
              : "无"
          }
        />
        <StatCard
          label="评估版本"
          value={run.config_versions.length || "—"}
          sub={run.config_versions.length ? "配置版本数" : "—"}
        />
        <StatCard
          label="快照时间"
          value={run.created_at_min ?? "—"}
          sub={run.created_at_max ? `至 ${run.created_at_max}` : "—"}
        />
      </div>
      <div className="flex flex-wrap items-center gap-2 text-caption text-ink-2">
        {Object.entries(run.quality_counts).map(([key, value]) => (
          <Badge key={key} tone={qualityTone(key)}>
            {labelOf(QUALITY_LABELS, key)} {value}
          </Badge>
        ))}
        <span>
          {run.evaluation_available ? "有可评估快照" : "无可评估快照"} · 否决 / 数据不足属于决策结论，不是采集失败
        </span>
      </div>
      <CollapsibleSection title="技术信息" defaultOpen={false}>
        <div className="grid gap-1.5 text-caption text-ink-2">
          <div>
            校验状态：
            {Object.entries(run.validation_statuses)
              .map(([key, value]) => `${labelOf(VALIDATION_LABELS, key, key)} ${value}`)
              .join(" · ") || "—"}
          </div>
          <div>配置版本：{run.config_versions.join("、") || "—"}</div>
          <div>
            快照时间：{run.created_at_min ?? "—"} ~ {run.created_at_max ?? "—"}
          </div>
        </div>
      </CollapsibleSection>
    </div>
  );
}

// ─── 2. 竞价结果 ──────────────────────────────────────────────────────────

function outcomeMap(effect: AuctionEffect | null) {
  const map = new Map<string, Array<{ horizon: number; pct: number | null; reason: string | null }>>();
  for (const item of effect?.selected_day ?? []) {
    const list = map.get(item.code) ?? [];
    list.push({ horizon: item.horizon, pct: item.net_return_pct, reason: item.exclusion_reason });
    map.set(item.code, list);
  }
  return map;
}

function ResultsSection({
  day,
  effect,
}: {
  day: AuctionDayView;
  effect: AuctionEffect | null;
}) {
  const outcomes = useMemo(() => outcomeMap(effect), [effect]);
  const columns = useMemo<Column<AuctionSnapshotRow>[]>(
    () => [
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
        key: "strength",
        header: "强度",
        align: "right",
        render: (row) => <span className="num text-body text-ink">{fmtNum(row.strength, 1)}</span>,
      },
      {
        key: "level",
        header: "等级",
        render: (row) =>
          row.level ? <Badge tone="normal">{row.level}</Badge> : <span className="text-ink-3">—</span>,
      },
      {
        key: "direction",
        header: "方向",
        render: (row) => (
          <span className="text-body text-ink-2">
            {labelOf(DIRECTION_LABELS, row.direction)}
          </span>
        ),
      },
      {
        key: "rank",
        header: "排名",
        align: "right",
        render: (row) => <span className="num text-body text-ink-2">{row.rank ?? "—"}</span>,
      },
      {
        key: "evidence",
        header: "关键证据",
        render: (row) => {
          const ev = row.key_evidence;
          if (!ev) return <span className="text-ink-3">—</span>;
          const parts = [
            ev.last_time ? `${ev.last_time}` : null,
            ev.official_open_px != null ? `开盘 ${ev.official_open_px}` : null,
            ev.last_indicative_price != null ? `示性 ${ev.last_indicative_price}` : null,
            ev.last_matched_lots != null ? `匹配 ${ev.last_matched_lots}` : null,
            ev.final_unmatched_lots_signed != null ? `未匹配 ${ev.final_unmatched_lots_signed}` : null,
          ].filter(Boolean);
          return <span className="text-caption text-ink-2">{parts.join(" · ") || "—"}</span>;
        },
      },
      {
        key: "risk",
        header: "风险事件",
        render: (row) => (
          <span className="text-caption text-ink-2">
            {row.risk_events?.length
              ? row.risk_events.map((event) =>
                  typeof event === "object" && event && "event_id" in event
                    ? String((event as Record<string, unknown>).event_id)
                    : String(event)
                ).join("、")
              : "—"}
          </span>
        ),
      },
      {
        key: "quality",
        header: "决策质量",
        render: (row) => (
          <Badge tone={row.is_weak ? "warning" : qualityTone(
            (row.data_quality?.status as string | undefined) ?? undefined
          )}>
            {row.is_weak
              ? "否决"
              : labelOf(QUALITY_LABELS, (row.data_quality?.status as string | undefined) ?? null)}
          </Badge>
        ),
      },
      {
        key: "collection",
        header: "采集状态",
        render: (row) => (
          <Badge tone={row.collection_status === "ok" ? "positive" : "outline"}>
            {labelOf(COLLECTION_STATUS_LABELS, row.collection_status)}
          </Badge>
        ),
      },
    ],
    []
  );

  if (day.count === 0) {
    return <EmptyState title="该日无竞价快照" description="无快照则无评估结果，不伪造候选。" />;
  }

  return (
    <div className="grid gap-2">
      <div className="overflow-hidden rounded-lg border border-hairline">
        <DataTable columns={columns} rows={day.snapshots} rowKey={(row) => row.code} minWidth={1200} />
      </div>
      <div className="grid gap-1.5">
        {day.snapshots.map((row) => {
          const list = outcomes.get(row.code) ?? [];
          return (
            <details key={`outcome-${row.code}`} className="rounded-md border border-hairline bg-raise">
              <summary className="cursor-pointer list-none px-3 py-2 text-body text-ink-2">
                {row.code} {row.name ?? ""} · 后续 1/3/5 日（本地日 K）
              </summary>
              <div className="flex flex-wrap gap-3 border-t border-hairline px-3 py-2">
                {list.length === 0 ? (
                  <span className="text-body text-ink-3">待评估：无本地日 K 或不相交</span>
                ) : (
                  list.map((item) => (
                    <span key={`${row.code}-${item.horizon}`} className="text-body text-ink-2">
                      {item.horizon} 日{" "}
                      {item.pct == null ? (
                        <span className="text-ink-3">待评估（{item.reason ?? "无样本"}）</span>
                      ) : (
                        <span className={`num ${pnlClass(item.pct)}`}>{fmtNum(item.pct, 2)}%</span>
                      )}
                    </span>
                  ))
                )}
              </div>
            </details>
          );
        })}
      </div>
    </div>
  );
}

// ─── 3. 成效 ──────────────────────────────────────────────────────────────

function EffectSection({ effect, loading }: { effect: AuctionEffect | null; loading: boolean }) {
  if (loading) return <div className="text-body text-ink-3">正在评估本地日 K…</div>;
  if (!effect) return <div className="text-body text-ink-3">该日无成效数据。</div>;

  const coverage = effect.local_coverage;
  const limitUpCaveat = effect.caveats.find((item) => item.includes("一字板"));
  return (
    <div className="grid gap-3">
      <div className="flex flex-wrap items-center gap-2 text-caption text-ink-2">
        <Badge tone={effect.status === "ok" ? "positive" : "warning"}>
          {effect.status === "ok" ? "可评估" : "待评估"}
        </Badge>
        {effect.reason ? <Badge tone="outline">{reasonLabel(effect.reason)}</Badge> : null}
        <span>
          数据来源 {labelOf(DATA_SOURCE_LABELS, effect.data_source)} · 交易日{" "}
          {labelOf(CALENDAR_BASIS_LABELS, effect.calendar_basis)} · 窗口 {effect.date_from} ~ {effect.date_to}
        </span>
      </div>
      {limitUpCaveat ? (
        <div className="text-caption text-warning">· {limitUpCaveat}</div>
      ) : null}

      <div className="grid gap-2">
        {effect.horizons.map((horizon) => (
          <div key={horizon.horizon} className="grid gap-1.5">
            <div className="flex flex-wrap items-center gap-2 text-body">
              <span className="font-medium text-ink">{horizon.horizon} 日</span>
              <span className="text-caption text-ink-2">
                强弱差 {horizon.spread == null ? "—" : `${fmtNum(horizon.spread, 2)}%`}
              </span>
            </div>
            <div className="overflow-x-auto">
              <table className="dt" style={{ minWidth: 720 }}>
                <thead>
                  <tr>
                    <th>分组</th>
                    <th>样本</th>
                    <th>有效</th>
                    <th>覆盖</th>
                    <th>方向命中</th>
                    <th>净收益均值</th>
                    <th>质量</th>
                  </tr>
                </thead>
                <tbody>
                  {horizon.groups.map((group) => (
                    <tr key={group.group}>
                      <td className="text-body text-ink">{group.label}</td>
                      <td className="num text-body text-ink-2">{group.observations}</td>
                      <td className="num text-body text-ink-2">{group.valid}</td>
                      <td className="num text-body text-ink-2">
                        {group.coverage == null ? "—" : `${(group.coverage * 100).toFixed(1)}%`}
                      </td>
                      <td className="num text-body text-ink-2">
                        {group.direction_hit_rate == null
                          ? "—"
                          : `${(group.direction_hit_rate * 100).toFixed(1)}%`}
                      </td>
                      <td className={`num text-body ${pnlClass(group.mean_net_return_pct)}`}>
                        {group.mean_net_return_pct == null
                          ? "—"
                          : `${fmtNum(group.mean_net_return_pct, 2)}%`}
                      </td>
                      <td>
                        <Badge tone={group.quality === "ok" ? "positive" : "warning"}>
                          {labelOf(EFFECT_QUALITY_LABELS, group.quality)}
                        </Badge>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {horizon.groups.every((group) => group.quality !== "ok") ? (
              <div className="text-caption text-ink-3">
                样本不足，待评估：命中率不作为策略结论。
              </div>
            ) : null}
          </div>
        ))}
      </div>

      <CollapsibleSection title="数据说明与覆盖范围" defaultOpen={false}>
        <div className="grid gap-2 text-caption text-ink-2">
          <div className="flex flex-wrap gap-3">
            <span>快照日 {coverage.snapshot_dates}</span>
            <span>窗口代码 {coverage.window_codes}</span>
            <span>已加载 {coverage.loaded_codes}</span>
            <span>选中日代码 {coverage.selected_codes}</span>
            <span>有日 K {coverage.codes_with_bars}</span>
            <span>交易日数 {coverage.bar_dates}</span>
            {coverage.truncated ? <Badge tone="warning">已截断</Badge> : null}
          </div>
          <ul className="grid gap-0.5">
            {effect.caveats.map((item, index) => (
              <li key={`${item}-${index}`}>· {item}</li>
            ))}
          </ul>
          {effect.warnings.length ? (
            <details className="text-ink-3">
              <summary className="cursor-pointer">技术告警 ({effect.warnings.length})</summary>
              <ul className="mt-1 grid gap-0.5 font-mono">
                {effect.warnings.map((item, index) => (
                  <li key={`${item}-${index}`}>· {item}</li>
                ))}
              </ul>
            </details>
          ) : null}
        </div>
      </CollapsibleSection>
    </div>
  );
}

// ─── 5. 当日逐股明细 ──────────────────────────────────────────────────────

function DayDetailSection({ day }: { day: AuctionDayView }) {
  const codes = useMemo(
    () => Array.from(new Set([...day.asset_codes, ...day.snapshots.map((row) => row.code)])).sort(),
    [day]
  );
  const byCode = useMemo(
    () => new Map(day.snapshots.map((row) => [row.code, row])),
    [day]
  );
  if (codes.length === 0) {
    return <EmptyState title="该日无原始资产与快照" description="空日期也可点击：只展示缺失，不请求成效。" />;
  }
  return (
    <div className="overflow-x-auto">
      <table className="dt" style={{ minWidth: 860 }}>
        <thead>
          <tr>
            <th>代码</th>
            <th>名称</th>
            <th>原始资产</th>
            <th>盘口明细</th>
            <th>快照</th>
            <th>采集状态</th>
            <th>决策质量</th>
            <th>风险</th>
            <th>未知原因</th>
          </tr>
        </thead>
        <tbody>
          {codes.map((code) => {
            const row = byCode.get(code);
            const quality = row?.is_weak
              ? "vetoed"
              : (row?.data_quality?.status as string | undefined) ?? (row ? "unknown" : null);
            return (
              <tr key={code}>
                <td className="font-mono text-body text-ink">{code}</td>
                <td className="text-body text-ink">{row?.name ?? "—"}</td>
                <td className="text-body text-ink-2">{row?.collected ? "有" : "—"}</td>
                <td className="text-body text-ink-2">{row?.collected ? "有" : "—"}</td>
                <td className="text-body text-ink-2">{row ? "有" : "—"}</td>
                <td>
                  <Badge tone={row?.collection_status === "ok" ? "positive" : "outline"}>
                    {labelOf(COLLECTION_STATUS_LABELS, row?.collection_status ?? "missing")}
                  </Badge>
                </td>
                <td>
                  {quality ? (
                    <Badge tone={qualityTone(quality)}>{labelOf(QUALITY_LABELS, quality)}</Badge>
                  ) : (
                    <span className="text-ink-3">—</span>
                  )}
                </td>
                <td className="text-caption text-ink-2">
                  {row?.risk_events?.length ? `${row.risk_events.length} 项` : "—"}
                </td>
                <td className="text-caption text-ink-3">{row?.error ?? "—"}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// ─── 页面 ─────────────────────────────────────────────────────────────────

export function AuctionPage() {
  const search = useSearchParams();
  const urlDate = normalizeIso(search.get("date") ?? "");

  const [calendar, setCalendar] = useState<AuctionCalendar | null>(null);
  const [calendarLoading, setCalendarLoading] = useState(true);
  const [date, setDate] = useState(urlDate);
  const [day, setDay] = useState<AuctionDayView | null>(null);
  const [effect, setEffect] = useState<AuctionEffect | null>(null);
  const [effectLoading, setEffectLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    fetchAuctionCalendar(365)
      .then((payload) => {
        if (!active) return;
        setCalendar(payload);
        if (!urlDate) {
          const fallback = payload.latest_record_date ?? payload.end_date;
          if (fallback) {
            setDate(fallback);
            navigate(`/auction?date=${fallback}`, { replace: true });
          }
        }
      })
      .catch((exc: unknown) => {
        if (active) setError(messageOf(exc));
      })
      .finally(() => {
        if (active) setCalendarLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    if (urlDate && urlDate !== date) setDate(urlDate);
  }, [urlDate, date]);

  useEffect(() => {
    if (!date) return;
    let active = true;
    setError(null);
    setEffect(null);
    fetchAuctionDay(date)
      .then(async (payload) => {
        if (!active) return;
        setDay(payload);
        // 只在选中日 hash 快照时请求成效；空日期不触发无意义请求。
        if (!payload.available) return;
        setEffectLoading(true);
        const result = await fetchAuctionEffect(date).catch(() => null);
        if (!active) return;
        setEffect(result);
      })
      .catch((exc: unknown) => {
        if (!active) return;
        setDay(null);
        setError(messageOf(exc));
      })
      .finally(() => {
        if (active) setEffectLoading(false);
      });
    return () => {
      active = false;
    };
  }, [date]);

  const changeDate = (next: string) => {
    setDate(next);
    const query = `date=${encodeURIComponent(next)}`;
    if (window.location.search.replace(/^\?/, "") !== query) {
      navigate(`/auction?${query}`);
    }
  };

  return (
    <>
      <PageHeader
        eyebrow="AUCTION · 集合竞价"
        title="集合竞价"
        description="查看竞价强弱、后续表现与当日明细。数据只来自本地，不联网补行情；缺数据一律「待评估」。"
        actions={
          date ? (
            <span className="text-caption text-ink-2">
              当前交易日 <span className="num text-ink">{date}</span>
            </span>
          ) : null
        }
      />

      {error ? (
        <div className="alert-danger mb-3 flex items-center gap-2 rounded-lg px-3.5 py-2.5 text-body">
          <IconAlert size={16} />
          加载失败：{error}
        </div>
      ) : null}

      <div className="grid gap-3">
        <Section title="运行概况" description="快照/原始资产/损坏行/质量分布">
          {day ? <RunSection day={day} /> : <div className="text-body text-ink-3">正在读取该日竞价数据…</div>}
        </Section>

        <Section title="竞价结果" description="强度/方向/关键证据/质量；行展开 1/3/5 日后续">
          {day ? <ResultsSection day={day} effect={effect} /> : <div className="text-body text-ink-3">—</div>}
        </Section>

        <Section title="成效" description="按既有的 1/3/5 日净收益口径评估；只读本地日 K">
          <EffectSection effect={effect} loading={effectLoading} />
        </Section>

        <Section title="资产日历" description="近一年；所有日期可点击，空日期不请求成效">
          <AssetCalendar
            startDate={calendar?.start_date ?? date}
            endDate={calendar?.end_date ?? date}
            days={calendar?.days ?? []}
            selected={date}
            onSelect={changeDate}
            loading={calendarLoading}
          />
        </Section>

        <Section title="当日逐股明细" description="采集状态与决策质量分列；否决/数据不足不是采集失败">
          {day ? <DayDetailSection day={day} /> : <div className="text-body text-ink-3">—</div>}
        </Section>
      </div>
    </>
  );
}
