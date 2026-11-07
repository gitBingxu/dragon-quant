import { useId, useState } from "react";
import type { ReactNode } from "react";
import { IconInfo } from "./icons";

/**
 * 可访问的小提示：hover / focus 打开，mouseleave / blur / Escape 关闭。
 * 只用现有 token，不引入 UI 库或全局状态。
 *
 * ``as="span"`` 供「提示图标放在按钮内部」的场景使用：不能嵌套 ``<button>``，
 * 显隐改由外层按钮的 ``group`` hover / focus-visible 驱动（纯 CSS，无状态）。
 */
export function HintIcon({
  label,
  children,
  side = "bottom",
  align = "center",
  as = "button",
}: {
  label: string;
  children: ReactNode;
  side?: "bottom" | "top";
  align?: "center" | "start" | "end";
  /** span：非交互触发器，要求最近的祖先元素带 ``group`` 类。 */
  as?: "button" | "span";
}) {
  const id = useId();
  const [open, setOpen] = useState(false);

  // 居中在窄视口/边缘卡片会溢出；start/end 把 tooltip 锚到图标左右边缘。
  const horizontal =
    align === "start"
      ? "left-0"
      : align === "end"
        ? "right-0"
        : "left-1/2 -translate-x-1/2";
  const placement = `absolute z-30 w-56 ${horizontal} rounded-md border border-hairline bg-panel-2 px-2.5 py-1.5 text-left text-caption font-normal leading-relaxed text-ink-2 ${
    side === "top" ? "bottom-full mb-1.5" : "top-full mt-1.5"
  }`;

  if (as === "span") {
    return (
      <span
        className="relative inline-flex items-center"
        role="img"
        aria-label={label}
      >
        <IconInfo size={13} className="text-ink-3" />
        <span
          role="tooltip"
          className={`${placement} pointer-events-none invisible group-hover:visible group-focus-visible:visible`}
          style={{ boxShadow: "var(--shadow-overlay)" }}
        >
          {children}
        </span>
      </span>
    );
  }

  return (
    <span className="relative inline-flex">
      <button
        type="button"
        aria-label={label}
        aria-describedby={open ? id : undefined}
        className="inline-flex cursor-help items-center border-0 bg-transparent p-0 text-ink-3 transition-colors hover:text-ink-2 focus-visible:text-ink-2"
        onMouseEnter={() => setOpen(true)}
        onMouseLeave={() => setOpen(false)}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onKeyDown={(event) => {
          if (event.key === "Escape") setOpen(false);
        }}
      >
        <IconInfo size={12} />
      </button>
      {open ? (
        <span
          id={id}
          role="tooltip"
          className={placement}
          style={{ boxShadow: "var(--shadow-overlay)" }}
        >
          {children}
        </span>
      ) : null}
    </span>
  );
}
