import type { ReactNode } from "react";

/** 原始审计 JSON：默认折叠，不作为普通用户主视图。 */
export function BsAudit({
  label,
  value,
}: {
  label: string;
  value: unknown;
}): ReactNode {
  if (value == null) return null;
  return (
    <details className="mt-2 rounded-lg border border-hairline bg-base-200/40 px-3 py-2 text-caption">
      <summary className="cursor-pointer text-ink-3">
        {label}（原始审计 JSON，高级核对用）
      </summary>
      <pre className="mt-2 max-h-72 overflow-auto whitespace-pre-wrap break-all rounded bg-base-200 p-2 leading-5">
        {JSON.stringify(value, null, 2)}
      </pre>
    </details>
  );
}
