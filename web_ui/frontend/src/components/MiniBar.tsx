/**
 * 五维细条（纯视觉，不承担读数）。
 *
 * 数值本身由调用方用 `fmtNum` 渲染（可见文字 ≥1rem）；条形只用品牌色与主题
 * 轨道 token，禁止 Tailwind 斜杠透明度（构建期固化，不随主题切换）。
 */
export function MiniBar({
  value,
  max = 100,
  label,
  width = 44,
}: {
  value: number | null;
  max?: number;
  label: string;
  width?: number;
}) {
  if (value == null || Number.isNaN(value)) return null;
  const ratio = Math.max(0, Math.min(1, value / max));
  return (
    <span
      aria-hidden="true"
      title={`${label} ${value} / ${max}`}
      className="relative inline-block h-1.5 overflow-hidden rounded-full bg-raise-strong align-middle"
      style={{ width }}
    >
      <span
        className="absolute inset-y-0 left-0 rounded-full bg-brand"
        style={{ width: `${ratio * 100}%` }}
      />
    </span>
  );
}
