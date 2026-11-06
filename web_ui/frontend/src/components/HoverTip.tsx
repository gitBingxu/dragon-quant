// 悬浮提示：鼠标悬停 / 键盘聚焦时渲染一段说明（用于禁用按钮的原因）。
//
// 为什么不用 `title`：原生 title 延迟长、样式不可控，且 disabled 按钮在部分
// 浏览器不触发 hover；这里用普通包裹层 + 绝对定位气泡，按钮本身只需
// `aria-disabled`（保持可 hover / 可聚焦）。
//
// 可访问性：说明文本始终以 `sr-only` 存在并由调用方通过 `aria-describedby`
// 关联；视觉气泡设 `aria-hidden`，避免读屏重复朗读。

import { useId, useState, type ReactNode } from "react";

export function HoverTip({
  tip,
  labelId,
  children,
}: {
  /** 说明文本；为空时不渲染任何包裹层。 */
  tip: string | null;
  /** 供按钮 `aria-describedby` 使用的稳定 id（可选）。 */
  labelId?: string;
  children: ReactNode;
}) {
  const generatedId = useId();
  const id = labelId ?? generatedId;
  const [open, setOpen] = useState(false);

  if (!tip) return <>{children}</>;

  return (
    <span
      className="relative inline-flex"
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
      onFocus={() => setOpen(true)}
      onBlur={() => setOpen(false)}
    >
      {children}
      <span id={id} className="sr-only">
        {tip}
      </span>
      {open ? (
        <span
          role="tooltip"
          aria-hidden="true"
          style={{ boxShadow: "var(--shadow-overlay)" }}
          className="pointer-events-none absolute right-0 top-full z-50 mt-1.5 w-max max-w-[280px] rounded-lg border border-hairline bg-panel px-3 py-2 text-caption leading-relaxed text-ink-2"
        >
          {tip}
        </span>
      ) : null}
    </span>
  );
}
