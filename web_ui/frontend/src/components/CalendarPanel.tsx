/**
 * 复盘 / 竞价共用「可折叠日历面板」（单一状态实现，两页只组合不复制）。
 *
 * - 默认展开；展开时切换控件固定在**面板下沿水平居中**（chevron 上 + 收起）；
 * - 收起后控件在**紧凑面板上沿水平居中**（chevron 下 + 展开），只占一行；
 * - 收起只隐藏日历内容，日期摘要留在面板头，选中日 / URL / 已加载数据与
 *   页面请求都不受影响（不重新请求日历或统计）；
 * - 原生 button + `aria-expanded` / `aria-controls`，Enter / Space 可操作；
 * - 只用语义 token，明暗主题与 390 / 1440 宽度共用同一布局。
 */
import { useId, useState, type CSSProperties, type ReactNode } from "react";
import { IconChevronDown, IconChevronUp } from "./icons";

export function CalendarPanel({
  title,
  description,
  summary,
  children,
  className = "",
  style,
  defaultOpen = true,
}: {
  title: ReactNode;
  description?: ReactNode;
  /** 收起后仍保留的日期摘要（如「当前交易日 2026-11-05」）。 */
  summary?: ReactNode;
  children: ReactNode;
  className?: string;
  style?: CSSProperties;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const bodyId = useId();

  const control = (
    <button
      type="button"
      onClick={() => setOpen((value) => !value)}
      aria-expanded={open}
      aria-controls={bodyId}
      className="inline-flex items-center gap-1 rounded-md px-3 py-1 text-caption text-ink-2 transition-colors hover:bg-hover hover:text-ink"
    >
      {open ? <IconChevronUp size={15} /> : <IconChevronDown size={15} />}
      <span>{open ? "收起" : "展开"}</span>
    </button>
  );

  return (
    <section className={`panel ${className}`} style={style}>
      <header className="panel-head">
        <div className="min-w-0">
          <h2 className="panel-title">{title}</h2>
          {description ? <p className="panel-desc">{description}</p> : null}
        </div>
        {summary ? (
          <div className="shrink-0 text-caption text-ink-2">{summary}</div>
        ) : null}
      </header>
      <div id={bodyId} className={open ? "panel-body" : "hidden"}>
        {children}
      </div>
      <div
        className={`flex justify-center py-1 ${
          open ? "border-t border-hairline" : ""
        }`}
      >
        {control}
      </div>
    </section>
  );
}
