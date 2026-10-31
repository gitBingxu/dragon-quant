import type { KeyboardEvent } from "react";

/** 图表键盘十字线：Tab 聚焦，←/↓ 与 →/↑ 逐点，PgUp/PgDn 快移，Home/End 首尾。 */
export function useEquityKeyboard({
  length,
  hoverIndex,
  setHoverIndex,
}: {
  length: number;
  hoverIndex: number | null;
  setHoverIndex: (index: number | null) => void;
}) {
  const lastIndex = length - 1;
  const moveTo = (index: number) =>
    setHoverIndex(Math.max(0, Math.min(lastIndex, index)));

  const onKeyDown = (event: KeyboardEvent<HTMLElement>) => {
    if (!length) return;
    const current = hoverIndex ?? lastIndex;
    switch (event.key) {
      case "ArrowLeft":
      case "ArrowDown":
        event.preventDefault();
        moveTo(current - 1);
        break;
      case "ArrowRight":
      case "ArrowUp":
        event.preventDefault();
        moveTo(current + 1);
        break;
      case "PageDown":
        event.preventDefault();
        moveTo(current - 5);
        break;
      case "PageUp":
        event.preventDefault();
        moveTo(current + 5);
        break;
      case "Home":
        event.preventDefault();
        moveTo(0);
        break;
      case "End":
        event.preventDefault();
        moveTo(lastIndex);
        break;
      case "Escape":
        setHoverIndex(null);
        break;
      default:
        break;
    }
  };

  const onFocus = () => {
    if (hoverIndex == null && length) setHoverIndex(lastIndex);
  };

  return { lastIndex, onKeyDown, onFocus };
}
