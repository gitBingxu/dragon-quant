import { useState } from "react";
import type { ReactNode } from "react";

/**
 * 原生 `<details>/<summary>` 折叠区（自带键盘与屏幕阅读器语义）。
 * `defaultOpen` 用受控 state 固定初始值，避免父级重渲染时被强制展开/收起。
 */
export function CollapsibleSection({
  title,
  children,
  defaultOpen = false,
  right,
  className = "",
}: {
  title: ReactNode;
  children: ReactNode;
  defaultOpen?: boolean;
  right?: ReactNode;
  className?: string;
}) {
  const [open, setOpen] = useState(defaultOpen);

  return (
    <details
      open={open}
      onToggle={(event) => setOpen(event.currentTarget.open)}
      className={`overflow-hidden rounded-lg border border-hairline bg-raise ${className}`}
    >
      <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-3.5 py-2.5">
        <span className="text-body font-medium text-ink-2">{title}</span>
        {right ? <span className="shrink-0">{right}</span> : null}
      </summary>
      {open ? (
        <div className="border-t border-hairline px-3.5 py-3">{children}</div>
      ) : null}
    </details>
  );
}
