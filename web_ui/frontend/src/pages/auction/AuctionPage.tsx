import { useEffect, useMemo, useState } from "react";
import {
  fetchAuctionAccuracy,
  fetchAuctionCalendar,
  fetchAuctionDay,
  fetchAuctionEffect,
  type AccuracyResponse,
  type AuctionCalendar,
  type AuctionDayView,
  type AuctionEffect,
  type AuctionSnapshotRow,
} from "../../api/assets";
import { AccuracyBoard } from "../../components/AccuracyBoard";
import { AssetCalendar } from "../../components/AssetCalendar";
import { Badge } from "../../components/Badge";
import { DataTable, type Column } from "../../components/DataTable";
import { EmptyState } from "../../components/EmptyState";
import { PageHeader } from "../../components/PageHeader";
import { Section } from "../../components/Section";
import { IconAlert } from "../../components/icons-extra";
import { fmtNum, pnlClass } from "../../lib/format";
import {
  COLLECTION_STATUS_LABELS,
  DEFAULT_DATE_SOURCE_LABELS,
  DIRECTION_LABELS,
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

// ─── 2. 竞价结果（按验收只保留必要列） ────────────────────────────────────

const OUTCOME_HORIZONS = [1, 3, 5] as const;

function outcomeMap(effect: AuctionEffect | null) {
  const map = new Map<
    string,
    Array<{ horizon: number; pct: number | null; reason: string | null }>
  >();
  for (const item of effect?.selected_day ?? []) {
    const list = map.get(item.code) ?? [];
    list.push({
      horizon: item.horizon,
      pct: item.net_return_pct,
      reason: item.exclusion_reason,
    });
    map.set(item.code, list);
  }
  return map;
}

type OutcomeItems = ReturnType<typeof outcomeMap> extends Map<string, infer T>
  ? T
  : never;

function OutcomeCell({
  items,
  horizon,
}: {
  items: OutcomeItems | undefined;
  horizon: number;
}) {
  const item = items?.find((entry) => entry.horizon === horizon);
  if (!item || item.pct == null) {
    return (
      <span
        className="text-caption text-ink-3"
        title={item?.reason ? reasonLabel(item.reason) : undefined}
      >
        待评估
      </span>
    );
  }
  return (
    <span className={`num text-body ${pnlClass(item.pct)}`}>
      {fmtNum(item.pct, 2)}%
    </span>
  );
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
        render: (row) => (
          <span className="num text-body text-ink">{fmtNum(row.strength, 1)}</span>
        ),
      },
      {
        key: "level",
        header: "等级",
        render: (row) =>
          row.level ? (
            <Badge tone="normal">{row.level}</Badge>
          ) : (
            <span className="text-ink-3">—</span>
          ),
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
        key: "collection",
        header: "采集状态",
        render: (row) => (
          <Badge tone={row.collection_status === "ok" ? "positive" : "outline"}>
            {labelOf(COLLECTION_STATUS_LABELS, row.collection_status)}
          </Badge>
        ),
      },
      ...OUTCOME_HORIZONS.map<Column<AuctionSnapshotRow>>((horizon) => ({
        key: `t${horizon}`,
        header: `T+${horizon}`,
        align: "right",
        width: 72,
        render: (row) => (
          <OutcomeCell items={outcomes.get(row.code)} horizon={horizon} />
        ),
      })),
    ],
    [outcomes]
  );

  if (day.count === 0) {
    return <EmptyState title="该日无竞价快照" description="无快照则无评估结果，不伪造候选。" />;
  }

  return (
    <div>
      <div className="overflow-hidden rounded-lg border border-hairline">
        <DataTable
          columns={columns}
          rows={day.snapshots}
          rowKey={(row) => row.code}
          minWidth={920}
        />
      </div>
      <div className="mt-1.5 text-caption text-ink-3">
        T+1、T+3、T+5 为本地日 K 净收益（已扣 13bp 成本）；缺数据或未来交易日未到一律「待评估」。
      </div>
    </div>
  );
}

// ─── 3. 当日资产采集与异常 ────────────────────────────────────────────────

type CollectionStatus = "ok" | "asset_only" | "snapshot_only" | "missing";

type CollectionRow = {
  code: string;
  name: string | null;
  collected: boolean;
  snapshot: boolean;
  status: CollectionStatus;
  error: string | null;
};

const COLLECTION_COLUMNS: Column<CollectionRow>[] = [
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
    key: "original",
    header: "原始资产",
    render: (row) => (
      <Badge tone={row.collected ? "positive" : "outline"}>
        {row.collected ? "有" : "无"}
      </Badge>
    ),
  },
  {
    key: "snapshot",
    header: "快照",
    render: (row) => (
      <Badge tone={row.snapshot ? "positive" : "outline"}>
        {row.snapshot ? "有" : "无"}
      </Badge>
    ),
  },
  {
    key: "status",
    header: "采集状态",
    render: (row) => (
      <Badge tone={row.status === "ok" ? "positive" : "outline"}>
        {labelOf(COLLECTION_STATUS_LABELS, row.status)}
      </Badge>
    ),
  },
  {
    key: "reason",
    header: "未知原因",
    render: (row) => (
      <span className="text-caption text-ink-3">{row.error ?? "—"}</span>
    ),
  },
];

function AssetCollectionSection({ day }: { day: AuctionDayView }) {
  const rows = useMemo<CollectionRow[]>(() => {
    const byCode = new Map(day.snapshots.map((row) => [row.code, row]));
    const assetCodes = new Set(day.asset_codes);
    const codes = Array.from(
      new Set([...day.asset_codes, ...day.snapshots.map((row) => row.code)])
    ).sort();
    return codes.map((code) => {
      const snapshot = byCode.get(code);
      const inAssets = assetCodes.has(code);
      const hasSnapshot = Boolean(snapshot);
      const status: CollectionStatus =
        hasSnapshot && inAssets
          ? "ok"
          : hasSnapshot
            ? "snapshot_only"
            : inAssets
              ? "asset_only"
              : "missing";
      return {
        code,
        name: snapshot?.name ?? day.names[code] ?? null,
        collected: inAssets,
        snapshot: hasSnapshot,
        status,
        error: snapshot?.error ?? null,
      };
    });
  }, [day]);

  const run = day.run;
  const anomalyCodes = [
    ...run.snapshot_only_codes,
    ...run.asset_only_codes,
  ];

  return (
    <div className="grid gap-2">
      <div className="flex flex-wrap items-center gap-2 text-caption text-ink-2">
        <Badge tone={run.error_rows ? "danger" : "muted"}>
          损坏快照行 <span className="num">{run.error_rows}</span>
        </Badge>
        <Badge tone="outline">
          仅快照 <span className="num">{run.snapshot_only_codes.length}</span>
        </Badge>
        <Badge tone="outline">
          仅资产 <span className="num">{run.asset_only_codes.length}</span>
        </Badge>
        {anomalyCodes.length ? (
          <span className="text-caption text-ink-3">
            异常代码：{anomalyCodes.slice(0, 6).join("、")}
            {anomalyCodes.length > 6 ? " …" : ""}
          </span>
        ) : (
          <span className="text-caption text-ink-3">无快照/资产错位</span>
        )}
      </div>

      {rows.length === 0 ? (
        <EmptyState
          title="该日无原始资产与快照"
          description="空日期也可点击：只展示缺失，不请求成效。"
        />
      ) : (
        <div className="overflow-hidden rounded-lg border border-hairline">
          <DataTable
            columns={COLLECTION_COLUMNS}
            rows={rows}
            rowKey={(row) => row.code}
            minWidth={720}
          />
        </div>
      )}
    </div>
  );
}

// ─── 页面（四区块） ───────────────────────────────────────────────────────

export function AuctionPage() {
  const search = useSearchParams();
  const urlDate = normalizeIso(search.get("date") ?? "");

  const [calendar, setCalendar] = useState<AuctionCalendar | null>(null);
  const [calendarLoading, setCalendarLoading] = useState(true);
  const [date, setDate] = useState(urlDate);
  const [day, setDay] = useState<AuctionDayView | null>(null);
  const [effect, setEffect] = useState<AuctionEffect | null>(null);
  const [effectLoading, setEffectLoading] = useState(false);
  const [accuracy, setAccuracy] = useState<AccuracyResponse | null>(null);
  const [accuracyLoading, setAccuracyLoading] = useState(true);
  const [accuracyError, setAccuracyError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    fetchAuctionCalendar(365)
      .then((payload) => {
        if (!active) return;
        setCalendar(payload);
        if (!urlDate) {
          const fallback =
            payload.default_date ?? payload.latest_record_date ?? payload.end_date;
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

  // 全历史准确率固定请求，不随选中日变化；失败不阻塞其他区块。
  useEffect(() => {
    let active = true;
    setAccuracyLoading(true);
    setAccuracyError(null);
    fetchAuctionAccuracy()
      .then((payload) => {
        if (active) setAccuracy(payload);
      })
      .catch((exc: unknown) => {
        if (!active) return;
        setAccuracy(null);
        setAccuracyError(messageOf(exc));
      })
      .finally(() => {
        if (active) setAccuracyLoading(false);
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
    setDay(null);
    setEffect(null);
    setEffectLoading(false);
    fetchAuctionDay(date)
      .then(async (payload) => {
        if (!active) return;
        setDay(payload);
        // T+1/3/5 仍取既有 selected_day 口径；空日期不触发无意义请求。
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

  const defaultSource = calendar
    ? labelOf(
        DEFAULT_DATE_SOURCE_LABELS,
        calendar.default_date_source,
        calendar.default_date_source
      )
    : null;

  return (
    <>
      <PageHeader
        eyebrow="AUCTION · 集合竞价"
        title="集合竞价"
        description="查看竞价强弱、候选结果与当日资产采集。数据只来自本地，不联网补行情；缺数据一律「待评估」。"
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
        {/* 1. 年度日历：默认永远最近确认交易日。 */}
        <Section
          title="数据资产日历"
          description={
            calendar
              ? `近一年；默认日期：${defaultSource ?? "本地最近交易日"}。点任意日期切换。`
              : "近一年数据资产日历；点任意日期切换。"
          }
          className="sticky top-14 z-20"
        >
          <AssetCalendar
            startDate={calendar?.start_date ?? date}
            endDate={calendar?.end_date ?? date}
            days={calendar?.days ?? []}
            selected={date}
            onSelect={changeDate}
            loading={calendarLoading}
          />
          {calendar?.degraded ? (
            <div className="mt-2 text-caption text-warning">
              默认日期已降级：{calendar.degraded_reason ?? "本地日 K 缺失"}。
            </div>
          ) : null}
        </Section>

        {/* 2. 所有候选竞价结果（含 T+1/3/5）。 */}
        <Section
          title="竞价结果"
          description="强度 / 等级 / 方向 / 排名 / 采集状态；T+1、T+3、T+5 净收益并入主表。"
          right={
            day && effectLoading ? (
              <Badge tone="outline">T+1/3/5 评估中…</Badge>
            ) : undefined
          }
        >
          {day ? (
            <ResultsSection day={day} effect={effect} />
          ) : (
            <div className="text-body text-ink-3">正在读取该日竞价数据…</div>
          )}
        </Section>

        {/* 3. 当日资产采集与异常。 */}
        <Section
          title="当日资产采集"
          description="原始资产 / 快照 / 采集状态 / 未知原因；运行异常按损坏行与错位代码统计。"
        >
          {day ? (
            <AssetCollectionSection day={day} />
          ) : (
            <div className="text-body text-ink-3">—</div>
          )}
        </Section>

        {/* 4. 全历史逐股判断准确率：固定截至最新可验证日。 */}
        <AccuracyBoard
          scope="auction"
          data={accuracy}
          loading={accuracyLoading}
          error={accuracyError}
        />
      </div>
    </>
  );
}
