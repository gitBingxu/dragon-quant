# playbooks — 异常分流与 issue 闭环

**何时读我**：命令报错、结果降级、出现空池、因子缺失、UI 启动失败时。每节固定格式：症状 / 诊断 / 处理 / 停止条件。

## 1. CLI 缺失或版本漂移

- **症状**：`command not found`；或 PATH 上的版本低于 `min-cli-version`；结果与文档不一致。
- **诊断**：`dragon-quant -v`；项目内用 `.venv/bin/python -m dragon_quant -v`。
- **处理**：在项目虚拟环境安装/升级；确认调用的是 `.venv/bin/python`，不要混用 PATH 上的旧版本。
- **停止条件**：版本仍低于 `min-cli-version` 时停止执行新命令。

## 2. 个股行情失败或空输出

- **症状**：`quote` 打印“获取行情失败”；`kline`/`minute` 没有输出行；`batch-quote` 缺少个别代码。
- **诊断**：确认代码为 6 位数字；确认输出字符串与退出码；`batch-quote` 逐只核对。
- **处理**：`quote` 的失败字符串按失败处理（即使 exit 0）；`kline`/`minute` 改用 `--source tencent` 重试一次；仍失败则说明数据不可得。
- **停止条件**：换源后仍空输出时停止，不编造行情字段。

## 3. scan 盘中拒绝

- **症状**：同一交易日 09:00–15:00 运行 `scan` 被拒绝。
- **诊断**：本地时点与交易日判断；CLI 报错文本。
- **处理**：说明收盘后（15:00 之后）更准确，等待门禁；历史日改用 `dragons --date D --true-only` 或 `scan --date D`。
- **停止条件**：门禁未到时不执行，也不用 `--force`。

## 4. prep 门禁（16:40）

- **症状**：`prep` 报当日需 16:40 后运行。
- **诊断**：目标日是今天还是历史；历史日可用 `--date D`。
- **处理**：等待 16:40；历史复盘用 `--date`；`--recompute` 仅用于用已存资产重算。
- **停止条件**：门禁未到时不写快照，不用 `--force` 硬闯。

## 5. auction 09:25 与缓存

- **症状**：09:25 前运行无数据；重复运行只返回快照；09:30 后运行担心数据过期。
- **诊断**：确认当前时点、`from_cache`、`mode`、`pool_source`、`fallback_reason`。
- **处理**：09:25 前不运行，说明竞价数据尚未形成；命中快照时明确“来自缓存，未重新采集”；**09:30 后仍读取/采集 09:25 终态并正常出报告**，只有额外行情观察才归“开盘后验证”；需要重采时由用户明确要求后用 `--force`。
- **停止条件**：快照损坏且无法重算时停止，不伪造竞价强度。

## 6. 停牌与日K兜底

- **症状**：`data_quality=daily_fallback`；`fallback_days` 非空；`suspended_codes` 出现。
- **诊断**：看数据质量三态、停牌列表、不可用列表；确认是分时缺失还是当日日K缺失。
- **处理**：说明兜底成交价是保守近似；停牌持仓冻结、按最近可得收盘价估值；分时相关规则在兜底日自然跳过。
- **停止条件**：日K完全无历史或预热不足时停止，不持久化部分结果。

## 7. 空池、快照缺失与回退

- **症状**：`auction` 空池；`prep` 候选为空；`review-account` 候选池回退；输出出现 `fallback_reason`。
- **诊断**：看 `pool_source`、`fallback_reason`、`prep_snapshot_date`、`dragon_source.window_days`。
- **处理**：解释回退原因；回退池不是当日盘前推荐，解读要降权；历史缺口不要假装能补采。
- **停止条件**：候选池与回退池都为空时停止，明确说“无参与标的”。

## 8. 因子缺失或默认关闭

- **症状**：`factor query` 无行；`factor build` 报告 `missing_source`；代表因子读回 `rows=0`。
- **诊断**：看 `reports[0].factors[].status` 与 `warnings`；确认源（scan/prep/auction）是否已生成、日期是否一致；确认用的是三组固定 `--only` 名单。
- **处理**：显式 `--only` 构建对应组；`missing_source` 说明源不可用或配置 fail-closed，**不等于 0**；`empty` 说明源可用但当日无值；`no_scan_for_date` 对 prep/auction-only 组是预期。
- **停止条件**：源整体不可用且没有历史行时停止，不要用 0 或中性分填充。

## 9. UI 面板启动失败

- **症状**：`review --ui-only` / `review-account --ui-only` 启动报错或端口被占用；没有出现 URL。
- **诊断**：看启动日志错误；确认端口是否占用；确认是否在本机运行。
- **处理**：`review` 未显式给 `--port` 时 CLI 可能自动换端口，以日志真实 URL 为准；`review-account` 端口被占用时建议显式 `--port` 后重试。只报告真实 URL，不猜测端口。
- **停止条件**：仍无法启动时停止，说明错误与可操作建议，不声称已打开浏览器。

## 10. 策略上下文缺失或版本不认识

- **症状**：`prep --json` 没有 `strategy_context`；`context_version` 不是 `sc_v1`；`feedback_plans[]` 全为 `expectations_unavailable`。
- **诊断**：确认快照版本与 CLI 版本是否匹配；确认是否用 `--date` 读到旧快照；确认 `data_quality.missing` 是否覆盖全部标的。
- **处理**：版本不匹配时先说明，再由用户确认后用 `--recompute` 重算（T1，写快照）；旧快照只输出旧摘要（四行 + 逐只清单），并明确「没有结构化策略上下文，无法给出条件化明日策略」。
- **停止条件**：`strategy_context` 缺失且无法重算时停止，**不拼凑**阈值、仓位或动作。

## 11. 未知异常与 issue 信息模板

- **症状**：无法按上述分流解释的报错、缺功能、体验问题或数据异常。
- **处理**：使用固定话术「当前功能还不稳定，暂未对 Agent 开放。你可以到 https://github.com/gitBingxu/dragon-quant/issues 反馈需求或问题。」并按下面的最小信息模板收集信息；**不自动创建** issue。
- **停止条件**：无法定位时不反复重试放大问题。

```text
- dragon-quant CLI 版本（`dragon-quant -v`）：
- Skill 版本（frontmatter `skill-version`）：
- 操作系统 / Python 版本：
- 想做的事（自然语言；不要贴 Cookie/token）：
- 实际执行的命令（如有）：
- 期望结果 vs 实际结果（脱敏输出片段）：
- 时间与场景（交易日？盘中/盘后/竞价？）：
```
