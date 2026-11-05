import { useEffect, useState } from "react";
import {
  fetchPrepAccuracy,
  fetchPrepCalendar,
  fetchPrepEffect,
  fetchPrepSnapshot,
  fetchPrepWatermark,
  type AccuracyResponse,
  type PrepCalendar,
  type PrepEffect,
  type PrepSnapshotView,
  type PrepWatermark,
} from "../../api/assets";
import { AccuracyBoard } from "../../components/AccuracyBoard";
import { AssetCalendar } from "../../components/AssetCalendar";
import { EmptyState } from "../../components/EmptyState";
import { PageHeader } from "../../components/PageHeader";
import { Section } from "../../components/Section";
import { IconAlert } from "../../components/icons-extra";
import { CALENDAR_BASIS_LABELS, DEFAULT_DATE_SOURCE_LABELS, labelOf } from "../../lib/labels";
import { navigate, useSearchParams } from "../../router";
import { AssetDayTable } from "./AssetDayTable";
import { PrepCoreResult } from "./PrepCoreResult";
import { PrepDayEffectSection } from "./PrepDayEffectSection";

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
  const [effect, setEffect] = useState<PrepEffect | null>(null);
  const [effectLoading, setEffectLoading] = useState(false);
  const [effectError, setEffectError] = useState<string | null>(null);
  const [accuracy, setAccuracy] = useState<AccuracyResponse | null>(null);
  const [accuracyLoading, setAccuracyLoading] = useState(true);
  const [accuracyError, setAccuracyError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  // 日历：一次请求覆盖近一年；默认日期由后端按本地日 K 计算，URL date 优先。
  useEffect(() => {
    let active = true;
    fetchPrepCalendar(365)
      .then((payload) => {
        if (!active) return;
        setCalendar(payload);
        if (!urlDate) {
          const fallback =
            payload.default_date ?? payload.latest_record_date ?? payload.end_date;
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

  // 全历史准确率固定请求，不随选中日变化；失败不阻塞其他区块。
  useEffect(() => {
    let active = true;
    setAccuracyLoading(true);
    setAccuracyError(null);
    fetchPrepAccuracy()
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

  // URL → 状态：直链与浏览器前进/后退。
  useEffect(() => {
    if (urlDate && urlDate !== date) setDate(urlDate);
  }, [urlDate, date]);

  // 当日请求：资产表 + 快照 + 单日效果；任何一项失败不拖垮其他区块。
  useEffect(() => {
    if (!date) return;
    let active = true;
    setError(null);
    setLoading(true);
    setWatermark(null);
    setSnapshot(null);
    setEffect(null);
    setEffectError(null);
    setEffectLoading(true);

    fetchPrepWatermark(date)
      .then(async (water) => {
        if (!active) return;
        setWatermark(water);
        if (!water.has_snapshot) return;
        const snap = await fetchPrepSnapshot(date).catch(() => null);
        if (!active) return;
        setSnapshot(snap);
      })
      .catch((exc: unknown) => {
        if (!active) return;
        setWatermark(null);
        setError(messageOf(exc));
      })
      .finally(() => {
        if (active) setLoading(false);
      });

    fetchPrepEffect(date)
      .then((payload) => {
        if (active) setEffect(payload);
      })
      .catch((exc: unknown) => {
        if (!active) return;
        setEffect(null);
        setEffectError(messageOf(exc));
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
      navigate(`/prep?${query}`);
    }
  };

  const defaultSource = calendar
    ? labelOf(
        DEFAULT_DATE_SOURCE_LABELS,
        calendar.default_date_source,
        calendar.default_date_source
      )
    : null;
  const basis = calendar
    ? labelOf(CALENDAR_BASIS_LABELS, calendar.calendar_basis, calendar.calendar_basis)
    : null;

  return (
    <>
      <PageHeader
        eyebrow="PREP · 盘后复盘"
        title="盘后复盘"
        description="查看复盘核心结果、当前效果、资产明细与全历史准确率。所有日期都可点击，缺数据如实展示缺失。"
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
        {/* 1. 年度日历：GitHub Contributions 风格，sticky 于 56px 顶栏下方。 */}
        <Section
          title="数据资产日历"
          description={
            calendar
              ? `近一年；默认日期：${defaultSource ?? "本地交易日"}。${basis ?? ""}，按本地日 K 交易日并集推算，不是交易所日历。`
              : "近一年数据资产日历；点任意日期切换。"
          }
          className="sticky top-14 z-20 order-4"
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
              默认日期已降级：{labelOf(
                { local_bars_missing: "本地日 K 缺失", no_previous_trading_day: "无前一交易日" },
                calendar.degraded_reason,
                calendar.degraded_reason ?? "未知原因"
              )}
              。
            </div>
          ) : null}
        </Section>

        {/* 2. 核心结果优先展示：日历等诊断信息在视觉顺序中后置。 */}
        <Section
          className="order-1"
          title="核心结果"
          description="市场情绪、主线、是否建仓、推荐建仓标的与资产异常摘要。"
        >
          {watermark || snapshot ? (
            <PrepCoreResult snapshot={snapshot} watermark={watermark} />
          ) : (
            <EmptyState
              title={loading ? "正在读取该日复盘…" : "该日无复盘记录"}
              description="请先运行 dragon-quant prep 生成复盘资产。"
            />
          )}
        </Section>

        {/* 3. 当前 T 日效果：主线次日涨跌 + 严格逐股收益/回撤。 */}
        <Section
          className="order-2"
          title="当前 T 日效果"
          description="真主线次日涨跌；推荐 / 不推荐逐股严格最大收益与回撤。只读本地日 K。"
        >
          <PrepDayEffectSection
            effect={effect}
            loading={effectLoading}
            error={effectError}
          />
        </Section>

        {/* 4. 当日资产明细与缺席/异常原因。 */}
        <Section
          className="order-5"
          title="当日资产明细"
          description="当日应有资产、缺失与合法缺席原因；采集记录是已知最新状态，不代表本次运行成功。"
        >
          {watermark ? (
            <AssetDayTable watermark={watermark} />
          ) : (
            <EmptyState
              title={loading ? "正在读取该日资产…" : "该日无资产记录"}
              description="空日期只展示缺失，不补算、不编造。"
            />
          )}
        </Section>

        {/* 5. 全历史逐股判断准确率：固定截至最新可验证日。 */}
        <div className="order-3">
          <AccuracyBoard
            scope="prep"
            data={accuracy}
            loading={accuracyLoading}
            error={accuracyError}
          />
        </div>
      </div>
    </>
  );
}
