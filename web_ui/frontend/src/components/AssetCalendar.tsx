/**
 * 近一年数据资产日历（GitHub Contributions 风格，prep / auction 共用）。
 *
 * - 列 = 周，行 = 周一…周日；**按月份分块**：每月一组（组标题 = 月份），
 *   组间加大间距，月份边界一眼可分；
 * - 方块只表达状态色阶，不保留格内图标：无 `panel-3` / 完整 `positive` /
 *   缺失 `warning`（后端 `missing` 状态视觉并入「缺失」，不再单独标红）；
 * - 图例为「色块 + 文字」逐项标签（无 / 缺失 / 完整，按完备度升序）；
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
    label: "完整",
    className: "border-positive-line bg-positive-soft",
  },
  incomplete: {
    label: "缺失",
    className: "border-warning-line bg-warning-soft",
  },
  // 后端 missing（资产缺失）视觉并入「缺失」：无=灰已表达「无资产」，黄色表达「资产不全」。
  missing: {
    label: "缺失",
    className: "border-warning-line bg-warning-soft",
  },
};

const NO_RECORD_META = {
  label: "无",
  className: "border-transparent bg-panel-3",
};

/** 图例按完备度从低到高排列：无 → 缺失 → 完整。 */
const LEGEND = [
  NO_RECORD_META,
  STATUS_META.incomplete,
  STATUS_META.complete,
];

/** 只显示周一 / 周三 / 周五（GitHub 同款留白）。 */
const WEEKDAY_LABELS = ["一", "", "三", "", "五", "", ""];

const CELL = 14;
const GAP = 3;
const MONTH_GAP = 16;
const MONTH_ROW = "1.35rem";

/** 一个月份分组：组标题（如「3月」）+ 该月包含的周列。 */
interface MonthGroup {
  key: string;
  label: string;
  weeks: Array<Array<string | null>>;
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
  const { months } = useMemo(() => {
    const start = parseIso(startDate);
    const end = parseIso(endDate);
    if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime()) || start > end) {
      return { months: [] as MonthGroup[] };
    }
    // 网格从「包含 start 的那一周的周一」开始，列=周、行=周一..周日。
    const cursor = new Date(start);
    const offset = (cursor.getDay() + 6) % 7;
    cursor.setDate(cursor.getDate() - offset);

    // 周列按月份分组：首周开组；此后包含某月 1 日的周开启新组。
    // 组标题 = 该月（含 1 日的周取其月份；首周无 1 日时取首周月份）。
    const months: MonthGroup[] = [];
    while (cursor <= end) {
      const week: Array<string | null> = [];
      for (let row = 0; row < 7; row += 1) {
        const iso = toIso(cursor);
        week.push(cursor >= start && cursor <= end ? iso : null);
        cursor.setDate(cursor.getDate() + 1);
      }
      const monthStartIso = week.find(
        (iso) => iso != null && parseIso(iso).getDate() === 1
      );
      const firstIso = week.find((iso): iso is string => iso != null);
      if (monthStartIso || months.length === 0) {
        const labelIso = monthStartIso ?? firstIso;
        months.push({
          key: labelIso ?? `group-${months.length}`,
          label: labelIso ? `${parseIso(labelIso).getMonth() + 1}月` : "",
          weeks: [week],
        });
      } else {
        months[months.length - 1].weeks.push(week);
      }
    }
    return { months };
  }, [startDate, endDate]);

  const statusByDate = useMemo(
    () => new Map(days.map((day) => [day.trade_date, day.status])),
    [days]
  );

  return (
    <div className="grid gap-2">
      <div className="flex flex-wrap items-center justify-between gap-2 text-caption text-ink-2">
        <div
          className="flex flex-wrap items-center gap-3"
          aria-label="日历图例：无 / 缺失 / 完整"
        >
          {LEGEND.map((item) => (
            <span key={item.label} className="flex items-center gap-1">
              <span
                aria-hidden="true"
                className={`inline-block h-3 w-3 rounded-[2px] border ${item.className}`}
              />
              <span>{item.label}</span>
            </span>
          ))}
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

          <div
            role="group"
            aria-label="数据资产日历"
            className="inline-flex items-start"
            style={{ gap: `${MONTH_GAP}px` }}
          >
            {months.map((month) => (
              <div key={month.key} className="grid" style={{ gap: `${GAP}px` }}>
                <div
                  aria-hidden="true"
                  className="flex items-end whitespace-nowrap text-caption text-ink-3"
                  style={{ height: MONTH_ROW }}
                >
                  {month.label}
                </div>
                <div
                  className="grid"
                  style={{
                    gridTemplateRows: `repeat(7, ${CELL}px)`,
                    gridAutoFlow: "column",
                    gridAutoColumns: `${CELL}px`,
                    gap: `${GAP}px`,
                  }}
                >
                  {month.weeks.map((week, weekIndex) =>
                    week.map((iso, row) => {
                      if (!iso) {
                        return (
                          <span
                            key={`blank-${month.key}-${weekIndex}-${row}`}
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
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
