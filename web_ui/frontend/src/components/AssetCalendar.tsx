/**
 * 近一年数据资产日历（GitHub 风格网格 + 成功/警告/错误图标）。
 *
 * - 每个日期格与图例都带可访问图标：complete=成功(positive) /
 *   incomplete=警告(warning) / missing=错误(danger，明确表示「资产缺失」)；
 * - **窗口内所有日期都可点击**（含完全缺失日/周末）：空日期只切换选中日，
 *   由当日表格展示资产缺失与无快照，不产生 404，也不请求成效；
 * - 单元格是原生 `button`，可 Tab 聚焦，`aria-label` 带日期/状态；
 * - 容器内部横向滚动，页面本身不横向滚动；图标不是文字，文字仍 ≥1rem。
 */
import { useMemo } from "react";

export type CalendarStatus = "complete" | "incomplete" | "missing";

export interface CalendarDayLike {
  trade_date: string;
  status: CalendarStatus;
}

const STATUS_META: Record<
  CalendarStatus,
  { label: string; className: string }
> = {
  complete: {
    label: "完备",
    className: "border-positive-line bg-positive-soft text-positive-text",
  },
  incomplete: {
    label: "不完备",
    className: "border-warning-line bg-warning-soft text-warning",
  },
  missing: {
    label: "资产缺失",
    className: "border-danger-line bg-danger-soft text-danger-text",
  },
};

function StatusIcon({ status }: { status: CalendarStatus }) {
  if (status === "complete") {
    // 对勾 = 成功
    return (
      <svg width="12" height="12" viewBox="0 0 16 16" aria-hidden="true" focusable="false">
        <path
          d="M3.2 8.4l3.1 3.1 6.5-6.6"
          fill="none"
          stroke="currentColor"
          strokeWidth="2.2"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
    );
  }
  if (status === "incomplete") {
    // 三角感叹号 = 警告
    return (
      <svg width="12" height="12" viewBox="0 0 16 16" aria-hidden="true" focusable="false">
        <path
          d="M8 2.2l5.6 11H2.4L8 2.2z"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.5"
          strokeLinejoin="round"
        />
        <path d="M8 6.2v3.4" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
        <circle cx="8" cy="11.7" r="0.9" fill="currentColor" />
      </svg>
    );
  }
  // 叉号 = 错误/缺失
  return (
    <svg width="12" height="12" viewBox="0 0 16 16" aria-hidden="true" focusable="false">
      <circle cx="8" cy="8" r="6.2" fill="none" stroke="currentColor" strokeWidth="1.4" />
      <path
        d="M5.6 5.6l4.8 4.8M10.4 5.6l-4.8 4.8"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
      />
    </svg>
  );
}

function toIso(date: Date): string {
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, "0");
  const d = String(date.getDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}

function parseIso(value: string): Date {
  return new Date(`${value}T00:00:00`);
}

export function AssetCalendar({
  startDate,
  endDate,
  days,
  selected,
  onSelect,
  loading = false,
}: {
  startDate: string;
  endDate: string;
  days: CalendarDayLike[];
  selected: string;
  onSelect: (date: string) => void;
  loading?: boolean;
}) {
  const { cells, weeks } = useMemo(() => {
    const start = parseIso(startDate);
    const end = parseIso(endDate);
    if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime()) || start > end) {
      return { cells: [] as Array<string | null>, weeks: 0 };
    }
    // 网格从「包含 start 的那一周的周一」开始，列=周、行=周一..周日。
    const cursor = new Date(start);
    const offset = (cursor.getDay() + 6) % 7;
    cursor.setDate(cursor.getDate() - offset);

    const list: Array<string | null> = [];
    let columns = 0;
    while (cursor <= end) {
      columns += 1;
      for (let row = 0; row < 7; row += 1) {
        const iso = toIso(cursor);
        const inRange = cursor >= start && cursor <= end;
        list.push(inRange ? iso : null);
        cursor.setDate(cursor.getDate() + 1);
      }
    }
    return { cells: list, weeks: columns };
  }, [startDate, endDate, days]);

  const statusByDate = useMemo(
    () => new Map(days.map((day) => [day.trade_date, day.status])),
    [days]
  );

  return (
    <div className="grid gap-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-3 text-caption text-ink-2">
          {(["complete", "incomplete", "missing"] as CalendarStatus[]).map((status) => (
            <span key={status} className="inline-flex items-center gap-1.5">
              <span
                className={`inline-flex h-4 w-4 items-center justify-center rounded-[3px] border ${STATUS_META[status].className}`}
              >
                <StatusIcon status={status} />
              </span>
              {STATUS_META[status].label}
            </span>
          ))}
        </div>
        <span className="text-caption text-ink-3">
          {loading ? "正在读取日历…" : `${startDate} ~ ${endDate}`}
        </span>
      </div>

      <div className="overflow-x-auto pb-1">
        <div
          role="grid"
          aria-label="数据资产日历"
          className="grid gap-[3px]"
          style={{
            gridTemplateRows: "repeat(7, 1.25rem)",
            gridAutoFlow: "column",
            gridAutoColumns: "1.25rem",
            width: `${weeks * 23}px`,
          }}
        >
          {cells.map((iso, index) => {
            if (!iso) {
              return <span key={`blank-${index}`} aria-hidden="true" />;
            }
            const status = statusByDate.get(iso) ?? "missing";
            const active = iso === selected;
            const meta = STATUS_META[status];
            return (
              <button
                key={iso}
                type="button"
                role="gridcell"
                aria-label={`${iso} · ${meta.label}${active ? " · 当前选中" : ""}`}
                aria-current={active ? "date" : undefined}
                title={`${iso} · ${meta.label}`}
                onClick={() => onSelect(iso)}
                className={`flex h-5 w-5 cursor-pointer items-center justify-center rounded-[3px] border p-0 transition-transform hover:scale-110 ${meta.className} ${
                  active ? "ring-2 ring-brand ring-offset-1 ring-offset-panel" : ""
                }`}
              >
                <StatusIcon status={status} />
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}
