import { Card, Group, SimpleGrid, Text, Tooltip } from "@mantine/core";
import { IconInfoCircle } from "@tabler/icons-react";
import type { Summary } from "../api";
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

export function SummaryCards({ summary }: { summary: Summary | null }) {
  const s = summary;
  return (
    <SimpleGrid cols={{ base: 2, sm: 3, lg: 6 }} spacing="sm" mb="md">
      <StatCard label="总记录">
        <Text span c="dimmed" inherit>
          {s?.total ?? 0}
        </Text>
      </StatCard>
      <StatCard label="已完成">{s?.completed ?? 0}</StatCard>
      <StatCard label="待回测">{s?.pending ?? 0}</StatCard>
      <StatCard label="平均收益">
        <Text span c={pnlClass(s?.avg_return)} inherit>
          {fmtPct(s?.avg_return)}
        </Text>
      </StatCard>
      <StatCard
        label="胜率"
        hint="胜率 = 已完成回测中 (最大收益 > 0 且 最大回撤 > -5%) 的占比"
        sub="收益>0 且回撤>-5% 占比"
      >
        <Text span c="red" inherit>
          {s?.win_rate != null ? s.win_rate.toFixed(1) + "%" : "—"}
        </Text>
      </StatCard>
      <StatCard
        label="最佳"
        sub={
          s?.best_stock_code
            ? `${s.best_stock_code} ${
                s.best_return != null ? fmtPct(s.best_return) : ""
              }`
            : ""
        }
      >
        <Text span c="red" inherit>
          {s?.best_stock_name || "—"}
        </Text>
      </StatCard>
    </SimpleGrid>
  );
}
