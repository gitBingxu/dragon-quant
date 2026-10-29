import { Card, Group, SimpleGrid, Text, Tooltip } from "@mantine/core";
import { IconInfoCircle } from "@tabler/icons-react";
import type { ReviewCaliber, Summary } from "../api";
import { fmtPct } from "../utils";

function StatCard({
  label,
  children,
  sub,
  hint,
}: {
  label: string;
  children: React.ReactNode;
  sub?: React.ReactNode;
  hint?: string;
}) {
  return (
    <Card withBorder radius="md" padding="md" ta="center">
      <Group justify="center" gap={6}>
        <Text size="xs" c="dimmed" tt="uppercase" fw={600}>
          {label}
        </Text>
        {hint && (
          <Tooltip label={hint} multiline w={240} withArrow>
            <IconInfoCircle size={14} style={{ color: "var(--mantine-color-dimmed)" }} />
          </Tooltip>
        )}
      </Group>
      <Text fz={26} fw={700} mt={4}>
        {children}
      </Text>
      {sub && (
        <Text size="xs" c="dimmed" mt={2}>
          {sub}
        </Text>
      )}
    </Card>
  );
}

function pnlClass(v: number | null | undefined) {
  if (v == null || v === 0) return "dimmed";
  return v > 0 ? "red" : "teal";
}

export function SummaryCards({
  summary,
  caliber,
}: {
  summary: Summary | null;
  caliber: ReviewCaliber;
}) {
  const s = summary;
  const isV1 = s?.source === "v1";
  const isStrict = caliber === "strict";

  // 平均收益 / 最佳随所选口径；v1 生效口径恒为 loose，等同旧口径。
  const avgReturn = isStrict ? s?.avg_return_strict : s?.avg_return;
  // 两胜率卡始终同时展示（对比用）。
  const looseRate = isV1 ? s?.win_rate : s?.win_rate_loose;
  const looseN = isV1 ? s?.completed : s?.win_rate_loose_n;
  const strictRate = isV1 ? null : s?.win_rate_strict;
  const strictN = isV1 ? 0 : s?.win_rate_strict_n;
  const bestName = isStrict ? s?.best_stock_name_strict : s?.best_stock_name;
  const bestCode = isStrict ? s?.best_stock_code_strict : s?.best_stock_code;
  const bestReturn = isStrict ? s?.best_return_strict : s?.best_return;

  // 「待回测」副文案来自严格侧重算进度，仅 v2 + strict 有意义。
  const pendingSub = isV1 || !s || !isStrict
    ? undefined
    : `待重算 ${s.pending_recompute} · 失败 ${s.failed_recompute}`;

  const caliberLabel = isV1 ? "旧口径" : isStrict ? "严格口径" : "宽松口径";

  return (
    <SimpleGrid cols={{ base: 2, sm: 3, lg: 7 }} spacing="sm" mb="md">
      <StatCard label="总记录">
        <Text span c="dimmed" inherit>
          {s?.total ?? 0}
        </Text>
      </StatCard>
      <StatCard label="已完成">{s?.derived_completed ?? s?.completed ?? 0}</StatCard>
      <StatCard label="待回测" sub={pendingSub}>
        {s?.derived_pending ?? s?.pending ?? 0}
      </StatCard>
      <StatCard label="平均收益" sub={caliberLabel}>
        <Text span c={pnlClass(avgReturn)} inherit>
          {fmtPct(avgReturn)}
        </Text>
      </StatCard>
      <StatCard
        label="宽松胜率"
        hint="入选次日按照最低价买入"
        sub={`n=${looseN ?? 0}`}
      >
        <Text span c="red" inherit>
          {looseRate != null ? looseRate.toFixed(1) + "%" : "—"}
        </Text>
      </StatCard>
      <StatCard
        label="严格胜率"
        hint="入选次日按照开盘价买入"
        sub={
          isV1
            ? "v1 无严格口径"
            : `n=${strictN}` +
              ((s?.cannot_evaluate ?? 0) > 0
                ? ` · 无法评估 ${s?.cannot_evaluate}`
                : "")
        }
      >
        <Text span c="red" inherit>
          {strictRate != null ? strictRate.toFixed(1) + "%" : "—"}
        </Text>
      </StatCard>
      <StatCard
        label="最佳"
        sub={bestCode ? `${bestCode} ${fmtPct(bestReturn)}` : ""}
      >
        <Text span c="red" inherit>
          {bestName || "—"}
        </Text>
      </StatCard>
    </SimpleGrid>
  );
}
