import type { ReactNode } from "react";

/** 指标表：值/单位/定义/样本 N/分母/成熟截止；null 显示「待证据」。 */
export function BsMetricTable({
  title,
  rows,
}: {
  title: string;
  rows: Record<string, any>[];
}): ReactNode {
  if (!rows.length) return null;
  return (
    <div className="rounded-lg border border-hairline">
      <div className="border-b border-hairline px-3 py-2 text-caption font-semibold text-ink-2">
        {title}
      </div>
      <div className="max-h-80 overflow-auto">
        <table className="table table-sm w-full">
          <thead>
            <tr>
              {["指标", "值", "单位", "样本 N", "分母", "样本口径", "成熟截止", "说明"].map((label) => (
                <th key={label} className="text-caption text-ink-3">{label}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.key}>
                <td className="text-caption font-medium">{row.label}</td>
                <td className={`num text-caption ${row.value == null ? "text-ink-3" : "text-ink"}`}>
                  {row.display}
                </td>
                <td className="text-caption text-ink-3">{row.unit}</td>
                <td className="num text-caption">{row.sample_n ?? "—"}</td>
                <td className="text-caption text-ink-3">{row.denominator ?? "—"}</td>
                <td className="text-caption text-ink-3">{row.sample_basis ?? "—"}</td>
                <td className="text-caption text-ink-3">{row.maturity_cutoff ?? "—"}</td>
                <td className="text-caption text-ink-3">{row.explain ?? "后端未提供定义"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
