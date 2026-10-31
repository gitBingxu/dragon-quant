import type { ReactNode } from "react";
import { IconLayers } from "./icons-extra";

export function EmptyState({
  title,
  description,
  action,
  icon,
}: {
  title: string;
  description?: ReactNode;
  action?: ReactNode;
  icon?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 px-6 py-12 text-center">
      <div className="grid h-11 w-11 place-items-center rounded-xl border border-hairline bg-raise-strong text-ink-2">
        {icon ?? <IconLayers size={20} />}
      </div>
      <div>
        <div className="text-[14px] font-medium text-ink">{title}</div>
        {description ? (
          <div className="mx-auto mt-1 max-w-md text-[12.5px] leading-relaxed text-ink-2">
            {description}
          </div>
        ) : null}
      </div>
      {action}
    </div>
  );
}
