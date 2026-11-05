# dashboard 复盘 / 竞价页改版技术调研（2026-11-02）

## 1. 调研问题与判定标准

目标是在不改 prep/auction 决策算法、CLI、快照与表结构的前提下，完成年度吸顶日历、核心结果重排、单日效果验证与全历史逐股准确率。

判定标准：

1. 所有结果可由既有本地资产只读计算；不自动联网补历史、不写库。
2. 严格最大收益/回撤与现有 review 严格口径完全一致；准确率使用用户确认的独立开盘→收盘窗口，不混用现有 open→open 净收益。
3. 全历史不设置任意样本上限；缺快照、缺日 K、窗口未走完均显式排除并保留计数。
4. 不新增重型依赖；保持 Web 三层只读依赖边界、深浅主题、深链和 E2E 安全约束。

## 2. 已核实事实

### 2.1 主线次日板块涨跌幅：可实现，但需补齐代码契约

- 采集器原样保存 `ths_block_top.rows`；代码注释与 E2E 固定样本只定义 `name / limit_up_num / high / high_num / continuous_plate_num / stock_list`，未声明板块涨跌幅与代码（`dragon_quant/review_account/prep/collectors.py:328-331`，`dragon_quant/infra/provider/ths.py:519-536`）。
- main 只读核验本机真实 `assets.db` 最近 5 个 `ths_block_top` 资产（2026-10-23～2026-10-30）：每行实际均有 `code` 与 `change`，例如机器人概念 `code=885517`、`change=3.1382`。因此无需新增采集、provider、表字段或历史回填。
- `review.mainlines` 只保留 `board_name`，没有板块代码（`prep/review/board_rank.py:158-206`）。单日效果可用 T 日原始资产按名称找到代码，再以代码匹配 T+1 原始资产并读取 `change`；名称只作降级匹配。名称变更、同名、缺 T+1 资产时必须显示缺失/待评估，不用成分股均值冒充板块指数。
- 实现时应把真实上游 `code/change` 补充到 provider/owner 文档与测试夹具，避免继续依赖未声明字段。

### 2.2 严格最大收益/回撤：有唯一既有公式，宜共享而非复制

- 严格入场：入选后首个交易日开盘，不顺延（`dragon_quant/review/main.py:135-149`）。
- 收益窗口：买入后第 2～5 个交易日；最大收益为窗口最高价相对买价，最大回撤为买入日至峰值日最低价相对买价（`review/main.py:12-14,36-38,103-132`）。
- `_window_metrics` 是纯计算，但 `review.main` 模块同时 import provider、存储和 CLI 运行逻辑；Web 直接 import 私有函数会扩大依赖面。调研结论：阶段 2 应优先考虑把纯严格指标核抽成无 I/O 的共享模块，由现有 review 与 Web 共同调用，并以 parity 测试证明数值不变。不能把 `review.performance.compute_performance` 当严格口径，它固定宽松入场且语义不同。

### 2.3 全历史逐股准确率：本地数据能力足够，但公式必须新建

- 快照日期可全量枚举：`list_prep_snapshot_dates` 与 auction 对应接口支持无边界全历史；快照本身按日读取（`infra/assets/db.py:780-823`，`infra/factor/decision_review_store.py:24-49`）。
- 日 K 可批量读取：`list_stock_daily_bars(codes,date_from,date_to)` 返回按 code 分组的升序 bars、bar_dates 和 truncated；默认 200,000 bars 上限（`infra/assets/db.py:975-1059`）。全历史实现应按 codes 分批并检查 truncated，不得直接截断样本。
- 既有 `decision_review` 是 available_at 后首个可成交开盘→第 N 日开盘、扣 13bp、含可成交剔除（`decision_review.py:10-12,34-39`）。它不符合本需求的复盘 T+1 开盘→T+3 收盘、竞价 T 开盘→T+2 收盘且不扣费用，因此不能直接复用其收益标签或命中率。
- 新准确率是纯价格方向计算：正/负/零，零计未命中但保留在有效分母；缺数据/未成熟/信号不完整排除并计数。prep 还需按当日 sentiment 是否建仓动态选择推荐或不推荐组；auction 等级弱优先于方向，非弱偏空预期下跌，非弱偏多/中性预期上涨。
- 本机当前规模较小：prep 快照 8 日、auction 快照 7 日/48 行、stock_daily 资产 3 日（2026-10-28～30）。历史早期样本很可能缺日 K，页面必须展示覆盖与排除数，不能把少量样本包装成稳定策略结论。

### 2.4 默认最近交易日：离线环境只有“最新已确认交易日”

- 当前日历 end_date 是上海当天，latest_record_date 是最近记录日；前端默认 `latest_record_date ?? end_date`，周末/节假日或漏跑时不符合需求（`web_ui/backend/routers/assets.py:40-63`，两个 Page 组件的日历 effect）。
- 本地 `stock_daily.bar_dates` 可提供已有日 K 的交易日并集，但 `LocalStockDailyMarket` 明确它是 bars_union、不是交易所日历（`presenters/local_market.py:27-42`）。独立 `build_trade_calendar` / `tdx_trade_dates` 都依赖 provider，不符合不联网约束。
- 推荐离线口径：用本地日 K 最新 bar 日期作为“最近已确认交易日”，auction 直接选它；prep 在该日期等于上海当天时选前一已确认交易日，否则直接选最新已确认交易日（周末/节假日/当日尚未写日 K 时都符合“交易日选 T-1、休息日选最近交易日”）。结合快照/资产记录日作缺失时回退并显式标注来源。它能在正常数据覆盖下正确处理周末和节假日，但若最近多个交易日完全无本地行情，系统不能凭空知道实际开市序列。此为数据覆盖限制，不引入新的在线日历或重型交易日历依赖。

### 2.5 前端日历与页面重构：无数据障碍

- `AssetCalendar` 已接收连续一年 start/end 和稀疏三态 days，能生成周列×七行并补齐空日期，所有日期可点击（`web_ui/frontend/src/components/AssetCalendar.tsx:95-195`）。
- 当前样式是带图标的 20px 状态格、周一起始、无月份/星期标签，不是用户要求的 GitHub Contributions 外观；需要重做布局与视觉 token，但不需要新 API 字段。
- 复盘现有 `plan-followup` 锚定“上一计划”，不能冒充当前选中日 T 的效果；新单日效果必须直接锚 T 的 review_candidates。竞价表格现有 T+1/3/5 净收益列可保留，和新的准确率口径分开展示，不能混称。

## 3. 候选技术路径与方向建议（供阶段 2，不是详细设计）

### 3.1 三条路径对比

| 路径 | 做法 | 判定 |
|---|---|---|
| A. 纯本地只读投影（推荐） | 全历史快照 + 分批本地日 K + 纯函数计算；真实板块资产 `code/change`；本地已确认交易日 | 满足不联网、不写库、不改表；历史缺失如实降级，覆盖限制可解释 |
| B. 在线日历与历史补数 | 接入 provider 的交易日历、板块/个股历史 K，页面打开时补齐缺口 | 能得到更完整日历和历史，但违反已确认的只读本地/不自动联网补历史约束，并引入反爬、延迟与一致性问题 |
| C. 新增持久化衍生统计 | 在 prep/auction 运行时写板块效果与准确率派生表 | 查询快，但需要新表/写入/回填与版本治理，违反本需求非目标；早期历史仍缺数据，不解决根源 |

### 3.2 推荐方向的组成

1. 后端新增只读投影：当前日复盘效果、prep 全历史准确率、auction 全历史准确率；保留既有 API 兼容，前端可停止消费旧区块。
2. 严格指标核共享化；准确率独立纯函数核。路由只负责参数与响应，仓储/快照/日 K 均依赖注入。
3. 全历史采用“列日期→逐日快照→收集 codes→分批日 K→纯函数聚合”，不持久化缓存；先以简单实现和覆盖计数满足正确性，不为假设规模提前加缓存层。
4. 日历复用同一组件，两页只改默认日期策略与区块组合；GitHub 外观使用本地 CSS/token，无新前端依赖。

## 4. 风险与已知边界

- MAJOR：最近交易日只能由本地行情确认；完全缺最新行情时存在回退日期不是实际最近交易日的可能，必须显示日期来源，后续如需绝对交易所日历另立需求。同理，T-1/T+1 必须由已确认交易日序列定位，不能把“下一条有记录日期”冒充下一交易日。
- MAJOR：真实 `code/change` 尚未写入代码契约，必须同批补文档与夹具；历史资产若缺字段则该主线待评估。
- MINOR：名称匹配存在改名/同名风险，优先代码并记录降级原因。
- MINOR：全历史逐日读快照随日期数增长；当前规模小，不提前引入持久缓存。
- MINOR：准确率是描述性逐股方向命中，不证明策略有效；同日股票相关性、局部日 K 覆盖与一字板可成交性需在页面说明。

## 5. 自审留痕（research-self-review，round 1）

```yaml
review_type: research
round: 1
verdict: APPROVE
summary: "三条技术路径与关键公式均有代码或真实资产证据；无 CRITICAL，两个数据覆盖 MAJOR 作为已知边界进入方案。"
findings:
  - severity: MAJOR
    dimension: "需求覆盖 / 事实准确性"
    location: "§2.4 与 §4"
    finding: "离线本地日 K 不是完整交易所日历，最新交易日或整日缺行情时默认日期及 T±1 只能回退。"
    evidence: "presenters/local_market.py 明确 calendar_basis=bars_union；独立交易日历 helper 依赖 provider。"
    recommendation: "方案必须显式定义已确认交易日、回退和提示，不得把下一记录日当下一交易日。"
  - severity: MAJOR
    dimension: "事实准确性 / 可行动性"
    location: "§2.1 与 §4"
    finding: "板块 code/change 只在真实资产核验中出现，当前代码注释与固定夹具未声明。"
    evidence: "本机 2026-10-23～30 五行真实资产均有 code/change；ths.py/collectors.py 契约未列出。"
    recommendation: "实现同批补契约、文档和夹具，并对旧资产缺字段降级。"
  - severity: MINOR
    dimension: "风险识别"
    location: "§2.3 / §4"
    finding: "全历史样本当前很少且日 K 仅近 3 日，准确率覆盖可能偏低。"
    evidence: "只读统计 prep 8 日、auction 7 日、stock_daily 3 日。"
    recommendation: "返回有效/排除/未成熟计数与数据覆盖，不展示虚假稳定性。"
scorecard:
  critical: 0
  major: 2
  minor: 1
  dimensions_passed: "6/6"
```

已修正首审前发现的候选路径未显式展开问题（§3.1 已补三条同口径对比）；无新增外部依赖或未验证外部断言。

## 5. 调研结论

需求在既有本地资产与只读边界下总体可行，无需新增依赖、联网补数、表结构或决策算法变更。关键取舍是：严格指标共享、准确率独立计算、主线使用真实上游 `code/change`、最近交易日采用本地可确认口径。可进入阶段 2 技术方案。
