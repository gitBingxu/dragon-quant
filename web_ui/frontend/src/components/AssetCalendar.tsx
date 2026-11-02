/**
 * 近一年数据资产日历（GitHub Contributions 风格，prep / auction 共用）。
 *
 * - 列 = 周，行 = 周一…周日；顶部月份标签，左侧星期标签（一/三/五）；
 * - 方块只表达状态色阶，不保留格内图标：
 *   无记录 `panel-3` / 完备 `positive` / 不完备 `warning` / 资产缺失 `danger`；
 * - **窗口内所有日期都可点击**（含完全缺失日/周末）：空日期只切换选中日，
 *   由当日区块展示缺失，不产生 404；
 * - 每格是原生 `button`，可 Tab 聚焦、Enter/Space 选中，`aria-label` 含
 *   日期 / 状态 / 选中态，`aria-current="date"` 标记选中日；
 * - 容器内部横向滚动，页面本身不横向滚动；只用语义 token，随主题切换。
 */
import { useMemo } from "react";

export type CalendarStatus = "complete" | "incomplete" | "missing";

export interface CalendarDayLike {
  trade_date: string;
  status: CalendarStatus;
}

const STATUS_META: Record<CalendarStatus, { label: string; className: string }> = {
  complete: {
    label: "完备",
    className: "border-positive-line bg-positive-soft",
  },
  incomplete: {
    label: "不完备",
    className: "border-warning-line bg-warning-soft",
  },
  missing: {
    label: "资产缺失",
    className: "border-danger-line bg-danger-soft",
  },
};

const NO_RECORD_META = {
  label: "无记录",
  className: "border-transparent bg-panel-3",
};

const LEGEND = [NO_RECORD_META, ...Object.values(STATUS_META)];

/** 只显示周一 / 周三 / 周五（GitHub 同款留白）。 */
const WEEKDAY_LABELS = ["一", "", "三", "", "五", "", ""];

const CELL = 14;
const GAP = 3;
const MONTH_ROW = "1.35rem";

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
  const { weeks, monthLabels } = useMemo(() => {
    const start = parseIso(startDate);
    const end = parseIso(endDate);
    if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime()) || start > end) {
      return { weeks: [] as Array<Array<string | null>>, monthLabels: [] as string[] };
    }
    // 网格从「包含 start 的那一周的周一」开始，列=周、行=周一..周日。
    const cursor = new Date(start);
    const offset = (cursor.getDay() + 6) % 7;
    cursor.setDate(cursor.getDate() - offset);

    const weeks: Array<Array<string | null>> = [];
    const monthLabels: string[] = [];
    let lastMonth = -1;
    while (cursor <= end) {
      const week: Array<string | null> = [];
      for (let row = 0; row < 7; row += 1) {
        const iso = toIso(cursor);
        week.push(cursor >= start && cursor <= end ? iso : null);
        cursor.setDate(cursor.getDate() + 1);
      }
      const isFirstWeek = weeks.length === 0;
      weeks.push(week);
      // 月份标签落在包含该月 1 日的周；首周无 1 日时标首周月份。
      const monthStartIso = week.find(
        (iso) => iso != null && parseIso(iso).getDate() === 1
      );
      const firstIso = week.find((iso): iso is string => iso != null);
      const labelIso = monthStartIso ?? (isFirstWeek ? firstIso : null);
      if (labelIso) {
        const month = parseIso(labelIso).getMonth() + 1;
        if (month !== lastMonth) {
          monthLabels.push(`${month}月`);
          lastMonth = month;
        } else {
          monthLabels.push("");
        }
      } else {
        monthLabels.push("");
      }
    }
    return { weeks, monthLabels };
  }, [startDate, endDate]);

  const statusByDate = useMemo(
    () => new Map(days.map((day) => [day.trade_date, day.status])),
    [days]
  );

  return (
    <div className="grid gap-2">
      <div className="flex flex-wrap items-center justify-between gap-2 text-caption text-ink-2">
        <div
          className="flex flex-wrap items-center gap-1.5"
          aria-label="日历图例：无记录 / 完备 / 不完备 / 资产缺失"
        >
          <span>较少</span>
          {LEGEND.map((item) => (
            <span
              key={item.label}
              title={item.label}
              aria-label={item.label}
              className={`inline-block h-3 w-3 rounded-[2px] border ${item.className}`}
            />
          ))}
          <span>较多</span>
        </div>
        <span className="text-caption text-ink-3">
          {loading ? "正在读取日历…" : `${startDate} ~ ${endDate}`}
        </span>
      </div>

      <div className="overflow-x-auto pb-1">
        <div className="inline-flex items-start gap-1.5">
          <div className="grid gap-1">
            <div style={{ height: MONTH_ROW }} aria-hidden="true" />
            <div
              aria-hidden="true"
              className="grid text-caption text-ink-3"
              style={{ gridTemplateRows: `repeat(7, ${CELL}px)`, gap: `${GAP}px` }}
            >
              {WEEKDAY_LABELS.map((label, row) => (
                <span
                  key={`weekday-${row}`}
                  className="flex items-center justify-end pr-1 leading-none"
                  style={{ height: CELL }}
                >
                  {label}
                </span>
              ))}
            </div>
          </div>

          <div className="grid gap-1">
            <div
              aria-hidden="true"
              className="grid text-caption text-ink-3"
              style={{
                gridTemplateColumns: `repeat(${Math.max(weeks.length, 1)}, ${CELL}px)`,
                columnGap: `${GAP}px`,
                height: MONTH_ROW,
              }}
            >
              {monthLabels.map((label, index) => (
                <span key={`month-${index}`} className="whitespace-nowrap">
                  {label}
                </span>
              ))}
            </div>

            <div
              role="group"
              aria-label="数据资产日历"
              className="grid"
              style={{
                gridTemplateRows: `repeat(7, ${CELL}px)`,
                gridAutoFlow: "column",
                gridAutoColumns: `${CELL}px`,
                gap: `${GAP}px`,
              }}
            >
              {weeks.map((week, weekIndex) =>
                week.map((iso, row) => {
                  if (!iso) {
                    return (
                      <span
                        key={`blank-${weekIndex}-${row}`}
                        aria-hidden="true"
                      />
                    );
                  }
                  const status = statusByDate.get(iso);
                  const meta = status ? STATUS_META[status] : NO_RECORD_META;
                  const active = iso === selected;
                  return (
                    <button
                      key={iso}
                      type="button"
                      aria-label={`${iso} · ${meta.label}${active ? " · 当前选中" : ""}`}
                      aria-current={active ? "date" : undefined}
                      title={`${iso} · ${meta.label}`}
                      onClick={() => onSelect(iso)}
                      className={`cursor-pointer rounded-[2px] border p-0 transition-transform hover:scale-110 ${meta.className} ${
                        active
                          ? "ring-2 ring-brand ring-offset-1 ring-offset-panel"
                          : ""
                      }`}
                      style={{ height: CELL, width: CELL }}
                    />
                  );
                })
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
