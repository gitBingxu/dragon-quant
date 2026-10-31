import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { fetchDragonDetail, type DragonDetail, type ReviewFields } from "../../api/dragons";
import type { ReviewCaliber } from "../../api/dragons";
import { Badge } from "../../components/Badge";
import { Drawer } from "../../components/Drawer";
import { IconAlert } from "../../components/icons-extra";
import { fmtNum, fmtPct, pnlClass } from "../../lib/format";
import { DimBars } from "./DimBars";

export function DragonDetailDrawer({
  selected,
  caliber,
  onClose,
}: {
  selected: { date: string; code: string } | null;
  caliber: ReviewCaliber;
  onClose: () => void;
}) {
  const [detail, setDetail] = useState<DragonDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!selected) return;
    let active = true;
    setLoading(true);
    setDetail(null);
    setError(null);
    fetchDragonDetail(selected.date, selected.code, caliber)
      .then((payload) => {
        if (active) setDetail(payload);
      })
      .catch((exc: unknown) => {
        if (active) setError(exc instanceof Error ? exc.message : String(exc));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [selected, caliber]);

  return (
    <Drawer
      open={selected !== null}
      onClose={onClose}
      title={
        detail ? (
          <span className="flex items-center gap-2">
            <span className="num text-[13px] text-ink-2">{detail.code}</span>
            <span>{detail.name}</span>
          </span>
        ) : (
          "龙头明细"
        )
      }
      subtitle={
        detail
          ? `${detail.trade_date} · 排名 ${detail.rank ?? "—"} · 综合分 ${fmtNum(detail.composite_score, 1)}`
          : selected
            ? `${selected.date} · ${selected.code}`
            : undefined
      }
    >
      {error ? (
        <div className="alert-critical mb-4 flex items-center gap-2 rounded-lg px-3 py-2 text-[12.5px]">
          <IconAlert size={15} />
          {error}
        </div>
      ) : null}
      {loading ? <div className="py-10 text-center text-[12.5px] text-ink-2">正在加载…</div> : null}
      {detail ? <DetailBody detail={detail} /> : null}
    </Drawer>
  );
}

function DetailBody({ detail }: { detail: DragonDetail }) {
  return (
    <div className="grid gap-5">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={detail.is_true_dragon ? "brand" : "neutral"} dot>
          {detail.is_true_dragon ? "真龙" : "非真龙"}
        </Badge>
        <Badge tone="outline">{detail.caliber === "strict" ? "严格口径" : "宽松口径"}</Badge>
        {detail.is_true_dragon === false && detail.reject_reason ? (
          <Badge tone="warn">已否决</Badge>
        ) : null}
      </div>

      <div className="grid grid-cols-2 gap-x-4 gap-y-2.5 rounded-lg border border-hairline bg-raise px-3.5 py-3 sm:grid-cols-4">
        <Meta label="入选日" value={detail.trade_date} mono />
        <Meta label="排名" value={detail.rank ?? "—"} mono />
        <Meta label="连板" value={detail.board_count ?? "—"} mono />
        <Meta label="综合分" value={fmtNum(detail.composite_score, 1)} mono />
      </div>

      {detail.concepts.length ? (
        <div>
          <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-[0.09em] text-ink-2">
            概念
          </h3>
          <div className="flex flex-wrap gap-1.5">
            {detail.concepts.map((concept) => (
              <Badge key={concept} tone="neutral">
                {concept}
              </Badge>
            ))}
          </div>
        </div>
      ) : null}

      <div>
        <div className="mb-2.5 flex items-baseline justify-between">
          <h3 className="text-[11px] font-semibold uppercase tracking-[0.09em] text-ink-2">
            五维评分
          </h3>
          <span className="font-mono text-[10.5px] text-ink-3">
            dims · {detail.dims_source}
          </span>
        </div>
        <DimBars dims={detail.dims} />
      </div>

      {detail.reject_reason ? (
        <div className="rounded-lg border border-warn-line bg-warn-soft px-3.5 py-2.5">
          <div className="mb-0.5 text-[11px] font-semibold uppercase tracking-[0.08em] text-warn">
            否决原因
          </div>
          <div className="text-[12.5px] text-ink">{detail.reject_reason}</div>
        </div>
      ) : null}

      <div>
        <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-[0.09em] text-ink-2">
          严格 / 宽松口径对照
        </h3>
        <div className="grid gap-3 sm:grid-cols-2">
          <ReviewCard title="宽松口径" fields={detail.review_loose} />
          {detail.review_strict ? (
            <ReviewCard title="严格口径" fields={detail.review_strict} />
          ) : (
            <div className="rounded-lg border border-dashed border-hairline px-3.5 py-6 text-center text-[12px] text-ink-3">
              无严格回测行
            </div>
          )}
        </div>
      </div>

      {detail.report_text ? (
        <details className="rounded-lg border border-hairline bg-raise">
          <summary className="cursor-pointer list-none px-3.5 py-2.5 text-[12px] font-medium text-ink-2 transition-colors hover:text-ink">
            扫描报告原文
          </summary>
          <pre className="max-h-64 overflow-auto whitespace-pre-wrap border-t border-hairline px-3.5 py-3 font-mono text-[11.5px] leading-relaxed text-ink-2">
            {detail.report_text}
          </pre>
        </details>
      ) : null}
    </div>
  );
}

function Meta({ label, value, mono }: { label: string; value: ReactNode; mono?: boolean }) {
  return (
    <div>
      <div className="text-[10.5px] uppercase tracking-[0.08em] text-ink-3">{label}</div>
      <div className={`mt-0.5 text-[13px] text-ink ${mono ? "num" : ""}`}>{value}</div>
    </div>
  );
}

function ReviewCard({ title, fields }: { title: string; fields: ReviewFields }) {
  return (
    <div className="overflow-hidden rounded-lg border border-hairline">
      <div className="border-b border-hairline bg-panel-2 px-3.5 py-2 text-[11.5px] font-semibold text-ink-2">
        {title}
      </div>
      <div className="grid gap-0">
        <ReviewRow label="买入日" value={fields.buy_date ?? "—"} mono />
        <ReviewRow label="买入价" value={fmtNum(fields.buy_price)} mono />
        <ReviewRow
          label="5日收益"
          value={fmtPct(fields.max_return_5d)}
          className={pnlClass(fields.max_return_5d)}
          mono
        />
        <ReviewRow
          label="最大回撤"
          value={fmtPct(fields.max_drawdown_5d)}
          className={pnlClass(fields.max_drawdown_5d)}
          mono
        />
        <ReviewRow label="持有天" value={fields.max_return_hold_days ?? "—"} mono />
        <ReviewRow label="状态" value={fields.review_status ?? "—"} />
      </div>
    </div>
  );
}

function ReviewRow({
  label,
  value,
  className = "",
  mono,
}: {
  label: string;
  value: ReactNode;
  className?: string;
  mono?: boolean;
}) {
  return (
    <div className="flex items-center justify-between gap-3 border-b border-hairline px-3.5 py-1.5 last:border-b-0">
      <span className="text-[11.5px] text-ink-2">{label}</span>
      <span className={`text-[12.5px] text-ink ${mono ? "num" : ""} ${className}`}>{value}</span>
    </div>
  );
}
