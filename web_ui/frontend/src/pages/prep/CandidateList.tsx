import { Badge } from "../../components/Badge";
import { fmtNum } from "../../lib/format";
import { display, eligibilityLabel, positionLabel, roleLabel, type CandidateView } from "./prepModel";

function asNumber(value: unknown): number | null {
  return typeof value === "number" && !Number.isNaN(value) ? value : null;
}

function asString(value: unknown): string | null {
  return typeof value === "string" && value !== "" ? value : null;
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-caption uppercase tracking-[0.08em] text-ink-3">{label}</div>
      <div className="mt-0.5 text-body text-ink">{value}</div>
    </div>
  );
}

function ContributionList({ title, items }: { title: string; items: CandidateView["topDrivers"] }) {
  if (!items.length) return null;
  return (
    <div>
      <div className="mb-0.5 text-caption uppercase tracking-[0.08em] text-ink-3">{title}</div>
      <ul className="grid gap-0.5 text-body text-ink-2">
        {items.map((item, index) => (
          <li key={`${title}-${index}`}>
            <span className="font-mono text-ink-3">{asString(item.dim) ?? "—"}</span>{" "}
            {asString(item.reason) ?? "—"}
          </li>
        ))}
      </ul>
    </div>
  );
}

/** 候选（主角）：全宽列表 + 行展开预期 / 贡献。 */
export function CandidateList({ candidates }: { candidates: CandidateView[] }) {
  if (candidates.length === 0) {
    return <div className="text-body text-ink-3">该日无候选。</div>;
  }
  return (
    <div className="grid gap-1.5">
      {candidates.map((item, index) => (
        <details key={`${item.code}-${index}`} className="rounded-md border border-hairline bg-raise">
          <summary className="flex cursor-pointer list-none flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2.5">
            <span className="font-mono text-body text-ink-3">{item.code}</span>
            <span className="text-body font-medium text-ink">{display(item.name)}</span>
            <span className="num text-body text-ink-2">
              分 {item.score != null ? fmtNum(item.score, 1) : "—"}
            </span>
            <span className="text-body text-ink-2">档 {display(item.grade)}</span>
            <span className="text-body text-ink-2">主线 {display(item.mainline)}</span>
            <span className="text-body text-ink-2">{roleLabel(item.role)}</span>
            <span className="text-body text-ink-2">{positionLabel(item.positionPct)}</span>
            <Badge tone={item.eligibility === "eligible" ? "positive" : "neutral"}>
              {eligibilityLabel(item.eligibility)}
            </Badge>
            {item.reason ? (
              <span className="text-caption text-warning">否决：{item.reason}</span>
            ) : null}
          </summary>
          <div className="grid gap-2 border-t border-hairline px-3 py-2.5">
            {item.expectations ? (
              <div className="grid grid-cols-2 gap-x-4 gap-y-1 sm:grid-cols-4">
                <Field label="高开 ≤ %" value={display(asNumber(item.expectations.gap_high))} />
                <Field label="低开 ≥ %" value={display(asNumber(item.expectations.gap_low))} />
                <Field label="量比 ≥" value={display(asNumber(item.expectations.ratio_low))} />
                <Field label="封板时限" value={display(asString(item.expectations.seal_by))} />
              </div>
            ) : (
              <div className="text-body text-ink-3">无预期数据。</div>
            )}
            <ContributionList title="主要贡献" items={item.topDrivers} />
            <ContributionList title="主要拖累" items={item.topDrags} />
          </div>
        </details>
      ))}
    </div>
  );
}
