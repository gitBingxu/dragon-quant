import { useEffect, useRef, useState } from "react";
import {
  fetchAuctionDates,
  fetchAuctionDay,
  fetchPrepDates,
  fetchPrepSnapshot,
  fetchPrepWatermark,
  type AuctionDayView,
  type PrepSnapshotView,
  type PrepWatermark,
} from "../../api/assets";
import { EmptyState } from "../../components/EmptyState";
import { PageHeader } from "../../components/PageHeader";
import { Section } from "../../components/Section";
import { Select } from "../../components/Select";
import { IconCalendar } from "../../components/icons";
import { IconAlert } from "../../components/icons-extra";
import { navigate, useSearchParams } from "../../router";
import { WatermarkSection } from "./WatermarkSection";
import { SnapshotSection } from "./SnapshotSection";
import { AuctionSection } from "./AuctionSection";

async function fetchAuctionDatesSafe(): Promise<string[]> {
  try {
    const payload = await fetchAuctionDates(60);
    return Array.from(new Set([...payload.snapshot_dates, ...payload.asset_dates]));
  } catch {
    return [];
  }
}

export function PrepPage() {
  const search = useSearchParams();
  const urlDate = search.get("date") ?? "";

  const [dates, setDates] = useState<string[]>([]);
  const [snapshotDates, setSnapshotDates] = useState<string[]>([]);
  const [datesLoaded, setDatesLoaded] = useState(false);
  const [date, setDate] = useState(urlDate);
  const [watermark, setWatermark] = useState<PrepWatermark | null>(null);
  const [snapshot, setSnapshot] = useState<PrepSnapshotView | null>(null);
  const [auction, setAuction] = useState<AuctionDayView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [dayLoading, setDayLoading] = useState(false);
  const [snapshotLoading, setSnapshotLoading] = useState(false);
  const selfWritten = useRef("");

  useEffect(() => {
    let active = true;
    Promise.all([fetchPrepDates(60), fetchAuctionDatesSafe()])
      .then(([prep, auctionDates]) => {
        if (!active) return;
        const union = Array.from(
          new Set([...prep.asset_dates, ...prep.snapshot_dates, ...auctionDates])
        ).sort();
        setDates(union);
        setSnapshotDates(prep.snapshot_dates);
        // 仅在没有 URL 日期时回落到最新交易日。
        if (!urlDate && union.length > 0) setDate(union[union.length - 1]);
      })
      .catch((exc: unknown) => {
        if (active) setError(exc instanceof Error ? exc.message : String(exc));
      })
      .finally(() => {
        if (active) setDatesLoaded(true);
      });
    return () => {
      active = false;
    };
  }, []);

  // URL → 状态：直链打开与浏览器后退/前进；本页写回 URL 时跳过。
  useEffect(() => {
    if (window.location.search === selfWritten.current) return;
    if (urlDate && urlDate !== date) setDate(urlDate);
  }, [urlDate, date]);

  // 资产水位与竞价：按所选日期请求。
  useEffect(() => {
    if (!date) return;
    let active = true;
    setError(null);
    setDayLoading(true);
    Promise.all([fetchPrepWatermark(date), fetchAuctionDay(date).catch(() => null)])
      .then(([water, auctionDay]) => {
        if (!active) return;
        setWatermark(water);
        setAuction(auctionDay);
      })
      .catch((exc: unknown) => {
        if (active) {
          setWatermark(null);
          setAuction(null);
          setError(exc instanceof Error ? exc.message : String(exc));
        }
      })
      .finally(() => {
        if (active) setDayLoading(false);
      });
    return () => {
      active = false;
    };
  }, [date]);

  // 快照详情仅在快照日期列表包含该日时请求，避免预期 404。
  const snapshotKey = snapshotDates.join("|");
  useEffect(() => {
    if (!date || !snapshotDates.includes(date)) {
      setSnapshot(null);
      setSnapshotLoading(false);
      return;
    }
    let active = true;
    setSnapshot(null);
    setSnapshotLoading(true);
    fetchPrepSnapshot(date)
      .then((payload) => {
        if (active) setSnapshot(payload);
      })
      .catch(() => {
        if (active) setSnapshot(null);
      })
      .finally(() => {
        if (active) setSnapshotLoading(false);
      });
    return () => {
      active = false;
    };
  }, [date, snapshotKey]);

  const changeDate = (next: string) => {
    setDate(next);
    const params = new URLSearchParams(window.location.search);
    params.set("date", next);
    const query = params.toString();
    selfWritten.current = `?${query}`;
    navigate(`${window.location.pathname}?${query}`);
  };

  const snapshotSettled =
    Boolean(date) && datesLoaded && !snapshotLoading && snapshot === null;

  return (
    <>
      <PageHeader
        eyebrow="PREP · 盘后复盘"
        title="盘后复盘 / 集合竞价"
        description="只读 assets.db（create=False，绝不建库）。资产水位、复盘快照块与竞价强度分布按交易日聚合；交易日写入 URL 可直链分享。"
        actions={
          <span className="inline-flex items-center gap-1.5 text-[12px] text-ink-2">
            <IconCalendar size={14} />
            <span className="num text-ink">{dates.length}</span> 个交易日
          </span>
        }
      />

      {error ? (
        <div className="alert-critical mb-3 flex items-center gap-2 rounded-lg px-3.5 py-2.5 text-[12.5px]">
          <IconAlert size={15} />
          加载失败：{error}
        </div>
      ) : null}

      <div className="grid gap-3">
        <Section
          title="选择交易日"
          description="日期来自 prep 资产、复盘快照与竞价快照的并集"
          bodyClassName="px-4 py-3.5 sm:px-[18px]"
        >
          {dates.length === 0 ? (
            <EmptyState
              title={datesLoaded ? "未找到 assets.db 数据" : "正在读取交易日…"}
              description={datesLoaded ? "请先运行 dragon-quant prep 生成复盘资产。" : undefined}
              icon={<IconCalendar size={20} />}
            />
          ) : (
            <div className="flex flex-wrap items-end gap-3">
              <Select
                label="交易日"
                value={date}
                options={dates.map((item) => ({ value: item, label: item }))}
                onChange={changeDate}
                width={220}
              />
              <div className="pb-1.5 font-mono text-[11px] text-ink-3">
                最新 {dates[dates.length - 1]} · 共 {dates.length} 个可查看交易日
              </div>
            </div>
          )}
        </Section>

        {watermark ? (
          <Section
            title={`资产水位 · ${watermark.trade_date}`}
            description="present / status / as_of / usable_display 来自 prep 资产元数据"
            bodyClassName="px-4 py-3.5 sm:px-[18px]"
          >
            <WatermarkSection watermark={watermark} />
          </Section>
        ) : null}

        {snapshot ? (
          <Section
            title="复盘快照"
            description={`version ${snapshot.version ?? "—"} · ${snapshot.slim ? "slim" : "full"}`}
            bodyClassName="px-4 py-3.5 sm:px-[18px]"
          >
            <SnapshotSection snapshot={snapshot} />
          </Section>
        ) : snapshotSettled ? (
          <Section title="复盘快照" description="该日快照不可用">
            <EmptyState
              title="该日无复盘快照"
              description="prep 未生成快照，或该日快照缺失；资产水位与竞价数据仍可查看。"
            />
          </Section>
        ) : null}

        {auction ? (
          <Section
            title={`竞价快照 · ${auction.trade_date}`}
            description={`共 ${auction.count} 只 · 强度分布与明细`}
            bodyClassName="px-4 py-3.5 sm:px-[18px]"
          >
            <AuctionSection auction={auction} />
          </Section>
        ) : null}

        {dayLoading && !watermark ? (
          <div className="text-[12px] text-ink-3">正在加载该日资产与竞价数据…</div>
        ) : null}
      </div>
    </>
  );
}
