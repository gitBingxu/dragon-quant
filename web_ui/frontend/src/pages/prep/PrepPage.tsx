import { useEffect, useState } from "react";
import {
  fetchPrepCalendar,
  fetchPrepSnapshot,
  fetchPrepWatermark,
  fetchReviewContext,
  type PrepCalendar,
  type PrepSnapshotView,
  type PrepWatermark,
  type ReviewContext,
} from "../../api/assets";
import { AssetCalendar } from "../../components/AssetCalendar";
import { EmptyState } from "../../components/EmptyState";
import { PageHeader } from "../../components/PageHeader";
import { Section } from "../../components/Section";
import { IconAlert } from "../../components/icons-extra";
import { navigate, useSearchParams } from "../../router";
import { AssetDayTable } from "./AssetDayTable";
import { CandidateList } from "./CandidateList";
import { MainlineTable } from "./MainlineTable";
import { MarketStrip } from "./MarketStrip";
import { PlanReviewSection } from "./PlanReviewSection";
import { RunOverview } from "./RunOverview";
import { buildCandidateViews, buildMainlineRows, buildMarketView, buildRunInfo } from "./prepModel";

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

export function PrepPage() {
  const search = useSearchParams();
  const urlDate = normalizeIso(search.get("date") ?? "");

  const [calendar, setCalendar] = useState<PrepCalendar | null>(null);
  const [calendarLoading, setCalendarLoading] = useState(true);
  const [date, setDate] = useState(urlDate);
  const [watermark, setWatermark] = useState<PrepWatermark | null>(null);
  const [snapshot, setSnapshot] = useState<PrepSnapshotView | null>(null);
  const [context, setContext] = useState<ReviewContext | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  // 日历：一次请求覆盖近一年，不逐日请求。
  useEffect(() => {
    let active = true;
    fetchPrepCalendar(365)
      .then((payload) => {
        if (!active) return;
        setCalendar(payload);
        if (!urlDate) {
          const fallback = payload.latest_record_date ?? payload.end_date;
          if (fallback) {
            setDate(fallback);
            navigate(`/prep?date=${fallback}`, { replace: true });
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

  // URL → 状态：直链与浏览器前进/后退。
  useEffect(() => {
    if (urlDate && urlDate !== date) setDate(urlDate);
  }, [urlDate, date]);

  // 当日请求：资产表始终请求；快照/成效仅在该日 has_snapshot 时请求。
  useEffect(() => {
    if (!date) return;
    let active = true;
    setError(null);
    setLoading(true);
    setSnapshot(null);
    setContext(null);
    fetchPrepWatermark(date)
      .then(async (water) => {
        if (!active) return;
        setWatermark(water);
        if (!water.has_snapshot) return;
        const [snap, ctx] = await Promise.all([
          fetchPrepSnapshot(date).catch(() => null),
          fetchReviewContext(date).catch(() => null),
        ]);
        if (!active) return;
        setSnapshot(snap);
        setContext(ctx);
      })
      .catch((exc: unknown) => {
        if (!active) return;
        setWatermark(null);
        setError(messageOf(exc));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [date]);

  const changeDate = (next: string) => {
    setDate(next);
    const query = `date=${encodeURIComponent(next)}`;
    if (window.location.search.replace(/^\?/, "") !== query) {
      navigate(`/prep?${query}`);
    }
  };

  const market = snapshot ? buildMarketView(snapshot) : null;
  const mainlines = snapshot ? buildMainlineRows(snapshot, context) : [];
  const candidates = snapshot ? buildCandidateViews(snapshot) : [];
  const runInfo = snapshot ? buildRunInfo(snapshot) : null;

  return (
    <>
      <PageHeader
        eyebrow="PREP · 盘后复盘"
        title="盘后复盘"
        description="查看复盘结果、计划成效与数据资产。所有日期都可点击，没有资产的日子也会如实展示缺失。"
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
        <Section title="运行概况" description="当日应有资产与采集记录；异常折叠在下方">
          {watermark ? (
            <RunOverview
              watermark={watermark}
              snapshot={snapshot}
              runInfo={runInfo}
              context={context}
            />
          ) : (
            <EmptyState
              title={loading ? "正在读取该日资产…" : calendarLoading ? "正在读取日历…" : "该日无资产记录"}
              description="请先运行 dragon-quant prep 生成复盘资产。"
            />
          )}
        </Section>

        <Section title="上次计划复盘 / 成效" description="回看上一交易日的计划与次日的实际表现">
          {snapshot ? (
            <PlanReviewSection context={context} snapshot={snapshot} candidates={candidates} />
          ) : (
            <EmptyState title="该日无复盘快照" description="无快照则不评估计划成效；不补算、不编造。" />
          )}
        </Section>

        <Section title="市场" description="涨停 / 跌停 / 溢价 / 晋级率 / 最高板 / 炸板率 / 成交额；390px 在容器内横向滚动">
          {snapshot ? (
            <MarketStrip market={market} />
          ) : (
            <div className="text-body text-ink-3">该日无复盘快照。</div>
          )}
        </Section>

        <Section title="主线" description="状态中文化；排名/分数变化用中性箭头，不是涨跌" bodyClassName="px-3 py-3 sm:px-4">
          {snapshot ? (
            <MainlineTable rows={mainlines} />
          ) : (
            <div className="text-body text-ink-3">该日无复盘快照。</div>
          )}
        </Section>

        <Section title="候选" description="主角：全宽列表，行展开预期与贡献">
          {snapshot ? (
            <CandidateList candidates={candidates} />
          ) : (
            <div className="text-body text-ink-3">该日无复盘快照。</div>
          )}
        </Section>

        <Section title="数据资产" description="近一年日历 + 当日明细；图标表示完备 / 不完备 / 资产缺失">
          <AssetCalendar
            startDate={calendar?.start_date ?? date}
            endDate={calendar?.end_date ?? date}
            days={calendar?.days ?? []}
            selected={date}
            onSelect={changeDate}
            loading={calendarLoading}
          />
          <div className="mt-3">
            {watermark ? (
              <AssetDayTable watermark={watermark} />
            ) : (
              <EmptyState title="该日无资产记录" />
            )}
          </div>
        </Section>
      </div>
    </>
  );
}
