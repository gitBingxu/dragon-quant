import { Section } from "../../components/Section";
import { IconPlus } from "../../components/icons";
import { IconTrendUp } from "../../components/icons-extra";

export function EmptyRuns({ onCreate }: { onCreate: () => void }) {
  return (
    <Section className="overflow-hidden">
      <div className="flex flex-col items-center gap-3 px-6 py-16 text-center">
        <div className="grid h-12 w-12 place-items-center rounded-2xl border border-hairline bg-brand-soft text-brand-bright">
          <IconTrendUp size={22} />
        </div>
        <h2 className="text-[16px] font-semibold text-ink">还没有账户回测记录</h2>
        <p className="max-w-[440px] text-[12.5px] leading-relaxed text-ink-2">
          生成第一条账户级回测批次：选择日期范围与初始资金，系统将按策略逐日撮合并输出权益曲线、
          交割单、单票收益与决策时间线。
        </p>
        <button type="button" className="btn btn-primary mt-1" onClick={onCreate}>
          <IconPlus size={15} />
          生成回测记录
        </button>
      </div>
    </Section>
  );
}
