# 归档文档索引

本目录保存**开发期产物**（技术调研 / 技术方案 / 方法论 / 终验记录）。它们在对应 feat 落地后归档，只作历史追溯，不是长期维护文档。

- **维护规则**：已归档文档若对应 feat 再次修改，必须在**同一 commit** 内同步更新（CLAUDE.md §6「调研 / 方案文档归档」）。
- **2026-10-25 整仓结构重构（M0–M12）当前状态**：本目录历史方案中的 `dragon_quant/cli_commands.py`、`dragon_quant/review_account/prep.py` 等实现位置已迁移为 `dragon_quant/commands/<command>.py`、`dragon_quant/review_account/prep/` 包、`dragon_quant/review_account/auction/main.py`、`dragon_quant/infra/assets/sync/cli_service.py`。受影响文档已在标题下加同日期「当前状态」指引，历史正文保持原样；当前结构与命令以 `docs/agent/architecture.md` 为准。
- **日期**：文档对应 feat 的落地日期（取自首次提交），非移动归档日期。
- **原路径**：归档前的仓库路径；`—` 表示自创建起就在本目录。

## factor_framework/

| 日期 | 主题 | 文件 | 原路径 |
|------|------|------|--------|
| 2026-10-29 | prep / auction 决策结果回测（factor decision-review：分组 × 1/3/5 日净收益） | [2026-10-29-decision-outcome-review-technical-design.md](factor_framework/2026-10-29-decision-outcome-review-technical-design.md) | `docs/solutions/2026-10-29-decision-outcome-review-technical-design.md` |
| 2026-10-29 | prep / auction 决策回测调研（快照分组 / 可成交标签 / 覆盖与剔除偏差） | [2026-10-29-decision-outcome-review-research.md](factor_framework/2026-10-29-decision-outcome-review-research.md) | `docs/solutions/2026-10-29-decision-outcome-review-research.md` |
| 2026-10-22 | 量化因子框架技术方案（issue #28，P0-P2 全部落地；含财务 tmdx delta） | [2026-10-22-factor-framework-design.md](factor_framework/2026-10-22-factor-framework-design.md) | `docs/solutions/2026-10-22-factor-framework-design.md` |
| 2026-10-22 | 量化因子框架调研（开源候选否决 + 业界规范；结论：混合自研零新增依赖） | [2026-10-22-factor-framework-survey.md](factor_framework/2026-10-22-factor-framework-survey.md) | `docs/research/2026-10-22-factor-framework-survey.md` |

## assets_sync/

| 日期 | 主题 | 文件 | 原路径 |
|------|------|------|--------|
| 2026-10-23 | assets.db 全表同步（M6：分区 manifest + TableSpec 注册表 + volatile 重读 + 行级合并） | [2026-10-23-assets-db-full-sync-design.md](assets_sync/2026-10-23-assets-db-full-sync-design.md) | `docs/solutions/2026-10-23-assets-db-full-sync-design.md` |
| 2026-10-22 | 资产同步 M2-M4 现状核对（设计文档 vs 当前代码） | [2026-10-22-assets-sync-m2-m4-reality-check.md](assets_sync/2026-10-22-assets-sync-m2-m4-reality-check.md) | `docs/research/2026-10-22-assets-sync-m2-m4-reality-check.md` |
| 2026-10-22 | 资产同步 M2-M4 实现方案（push/pull/status/verify + manifest + 增量 state） | [2026-10-22-assets-sync-m2-m4-implementation.md](assets_sync/2026-10-22-assets-sync-m2-m4-implementation.md) | `docs/solutions/2026-10-22-assets-sync-m2-m4-implementation.md` |
| 2026-10-18 | 数据资产云端存储选型调研（R2/B2） | [2026-10-18-assets-cloud-storage.md](assets_sync/2026-10-18-assets-cloud-storage.md) | `docs/research/2026-10-18-assets-cloud-storage.md` |

> 注：主设计文档 `docs/solutions/2026-10-18-assets-cloud-sync-design.md` 因 M5（定时调度）尚未实现，**暂不归档**。

## auction/

| 日期 | 主题 | 文件 | 原路径 |
|------|------|------|--------|
| 2026-10-29 | auction 快照优先与 --force 强制重跑（缓存短路 / 只读投影 / 损坏回退） | [2026-10-29-auction-snapshot-short-circuit-design.md](auction/2026-10-29-auction-snapshot-short-circuit-design.md) | `docs/solutions/2026-10-29-auction-snapshot-short-circuit-design.md` |
| 2026-10-29 | auction 证据化重建技术方案（五维评分 / 四轴 / strategy_context / 8+9 因子） | [2026-10-29-auction-strength-technical-design.md](auction/2026-10-29-auction-strength-technical-design.md) | `docs/solutions/2026-10-29-auction-strength-technical-design.md` |
| 2026-10-29 | auction 字段语义与六篇参考资料调研（真实资产证据 / 因子映射） | [2026-10-29-auction-strength-research.md](auction/2026-10-29-auction-strength-research.md) | `docs/solutions/2026-10-29-auction-strength-research.md` |
| 2026-10-21 | auction 候选池改 prep 推荐 + 买入 P/A 三值优先级与否决 | [2026-10-21-auction-prep-buy-priority-design.md](auction/2026-10-21-auction-prep-buy-priority-design.md) | `docs/solutions/2026-10-21-auction-prep-buy-priority-design.md` |
| 2026-10-20 | 集合竞价强度快照接线（`quote_json` / `auction_snapshots` / `--recompute`） | [2026-10-20-auction-snapshot-wiring-design.md](auction/2026-10-20-auction-snapshot-wiring-design.md) | `docs/solutions/2026-10-20-auction-snapshot-wiring-design.md` |
| 2026-10-14 | 集合竞价强度分析技术方案 | [集合竞价强度分析_技术方案.md](auction/集合竞价强度分析_技术方案.md) | — |
| 2026-10-14 | 集合竞价强度判断方法论 | [集合竞价强度判断方法论.md](auction/集合竞价强度判断方法论.md) | — |

## cli/

| 日期 | 主题 | 文件 | 原路径 |
|------|------|------|--------|
| 2026-10-21 | CLI JSON 输出统一设计（auction/prep `--json`，移除 `--verbose`） | [2026-10-21-cli-json-output-design.md](cli/2026-10-21-cli-json-output-design.md) | `docs/solutions/2026-10-21-cli-json-output-design.md` |

## live_trade/

| 日期 | 主题 | 文件 | 原路径 |
|------|------|------|--------|
| 2026-10-21 | buy 四类买点诊断 + 龙回头默认开启 | [2026-10-21-buy-checks-and-dragon-comeback.md](live_trade/2026-10-21-buy-checks-and-dragon-comeback.md) | `docs/solutions/2026-10-21-buy-checks-and-dragon-comeback.md` |
| 2026-10-20 | buy/sell 执行层对齐 review-account（保本线/半仓/影子风控/数据分级） | [2026-10-20-live-trade-alignment.md](live_trade/2026-10-20-live-trade-alignment.md) | `docs/solutions/2026-10-20-live-trade-alignment.md` |

## prep/

| 日期 | 主题 | 文件 | 原路径 |
|------|------|------|--------|
| 2026-10-30 | prep 新旧算法统一与派生清理（六维唯一决策 / prev / purge-derived） | [2026-10-30-prep-unified-review-technical-design.md](prep/2026-10-30-prep-unified-review-technical-design.md) | `docs/solutions/2026-10-30-prep-unified-review-technical-design.md` |
| 2026-10-30 | prep 算法统一调研（双头推荐 / 兼容投影 / 精确清理边界） | [2026-10-30-prep-unified-review-research.md](prep/2026-10-30-prep-unified-review-research.md) | `docs/solutions/2026-10-30-prep-unified-review-research.md` |
| 2026-10-21 | prep 宏观改日经225期指 + 富时A50期指（同日口径，16:40 门禁） | [2026-10-21-prep-macro-jp-a50-design.md](prep/2026-10-21-prep-macro-jp-a50-design.md) | `docs/solutions/2026-10-21-prep-macro-jp-a50-design.md` |
| 2026-10-21 | prep 重复运行增量语义（健康零采集 / 补采 diff / report 逐条合并） | [2026-10-21-prep-incremental-rerun-design.md](prep/2026-10-21-prep-incremental-rerun-design.md) | `docs/solutions/2026-10-21-prep-incremental-rerun-design.md` |
| 2026-10-20 | prep 宏观扰动采集 + 近三日龙头逐只参与判定 | [2026-10-20-prep-macro-candidate-verdict-design.md](prep/2026-10-20-prep-macro-candidate-verdict-design.md) | `docs/solutions/2026-10-20-prep-macro-candidate-verdict-design.md` |
| 2026-10-20 | prep 复盘窗口扩为近三日龙头并集 | [2026-10-20-prep-3day-window-design.md](prep/2026-10-20-prep-3day-window-design.md) | `docs/solutions/2026-10-20-prep-3day-window-design.md` |
| 2026-10-20 | prep 宏观数据源调研 | [2026-10-20-prep-macro-sources.md](prep/2026-10-20-prep-macro-sources.md) | `docs/research/2026-10-20-prep-macro-sources.md` |

## provider/

| 日期 | 主题 | 文件 | 原路径 |
|------|------|------|--------|
| 2026-10-10 | 通达信 TDX 首选数据链路技术方案 | [TDX_PROVIDER_技术方案.md](provider/TDX_PROVIDER_技术方案.md) | `dragon_quant/providers/TDX_PROVIDER_技术方案.md` |

## scan/

| 日期 | 主题 | 文件 | 原路径 |
|------|------|------|--------|
| 2026-10-20 | scan 板块口径改行业+概念+地区三维融合 Top8（设计） | [2026-10-20-tdx-sector-merge-design.md](scan/2026-10-20-tdx-sector-merge-design.md) | `docs/solutions/2026-10-20-tdx-sector-merge-design.md` |
| 2026-10-20 | 板块三维融合调研 | [2026-10-20-tdx-sector-merge-research.md](scan/2026-10-20-tdx-sector-merge-research.md) | `docs/research/2026-10-20-tdx-sector-merge-research.md` |

## score/

| 日期 | 主题 | 文件 | 原路径 |
|------|------|------|--------|
| 2026-09-12 | 五维评分器 Refactor | [评分器Refactor.md](score/评分器Refactor.md) | `dragon_quant/scorers/评分器Refactor.md` |

## review/

| 日期 | 主题 | 文件 | 原路径 |
|------|------|------|--------|
| 2026-10-30 | review 双口径筛选与展示（严格 / 宽松查询上下文 + Web UI 联动） | [2026-10-30-review-dual-caliber-filter-design.md](review/2026-10-30-review-dual-caliber-filter-design.md) | `docs/solutions/2026-10-30-review-dual-caliber-filter-design.md` |

## review_account/

| 日期 | 主题 | 文件 | 原路径 |
|------|------|------|--------|
| 2026-10-22 | 清仓后窗口指标 + 买点统计 | [2026-10-22-post-exit-and-entry-reason-stats.md](review_account/2026-10-22-post-exit-and-entry-reason-stats.md) | `docs/solutions/2026-10-22-post-exit-and-entry-reason-stats.md` |
| 2026-10-22 | 停牌标的处理（缺日K/双零量冻结估值） | [2026-10-22-suspended-stock-handling.md](review_account/2026-10-22-suspended-stock-handling.md) | `docs/solutions/2026-10-22-suspended-stock-handling.md` |
| 2026-10-22 | 首日止损改造技术方案（D3 首日浮亏减半 `first_day_stop`，默认关） | [2026-10-22-first-day-stoploss-redesign.md](review_account/2026-10-22-first-day-stoploss-redesign.md) | `docs/solutions/2026-10-22-first-day-stoploss-redesign.md` |
| 2026-10-22 | 仓位分散回测验证（结论：维持满仓 100/1，分散配置收益归零） | [2026-10-22-position-sizing-backtest.md](review_account/2026-10-22-position-sizing-backtest.md) | `docs/research/2026-10-22-position-sizing-backtest.md` |
| 2026-10-18 | 盘后复盘因子扩展技术方案（独立资产库 + THS 涨停原因/题材主线） | [复盘因子扩展技术方案.md](review_account/复盘因子扩展技术方案.md) | — |
| 2026-10-14 | 择时与仓位调研 | [择时与仓位调研.md](review_account/择时与仓位调研.md) | — |
| 2026-10-14 | 择时与仓位技术方案 | [择时与仓位技术方案.md](review_account/择时与仓位技术方案.md) | — |
| 2026-10-14 | 现状策略基线 | [现状策略基线.md](review_account/现状策略基线.md) | — |
| 2026-10-14 | 终验追溯（择时与仓位 feat） | [终验追溯.md](review_account/终验追溯.md) | — |
| 2026-10-12 | 盘后复盘方法论 | [复盘方法论.md](review_account/复盘方法论.md) | `dragon_quant/review_account/复盘方法论.md` |
| 2026-10-12 | 复盘候选池技术方案 | [候选池技术方案.md](review_account/候选池技术方案.md) | `dragon_quant/review_account/候选池技术方案.md` |
| 2026-09-06 | 操盘策略（旧版 `STRATEGY.md`，现行版本见 `review_account/STRATEGY.md`） | [操盘策略.md](review_account/操盘策略.md) | `dragon_quant/review_account/STRATEGY.md` |

> 注：`现状策略基线.md` / `择时与仓位技术方案.md` 中的「9 条 SellRule、首日 -3.5% 盘中止损」为 2026-10-14 的历史快照；现行规则链为 10 条并新增 `first_day_stop`（默认关），以 `review_account/STRATEGY.md` 为准。

## agents/

| 日期 | 主题 | 文件 | 原路径 |
|------|------|------|--------|
| 2026-10-27 | AGENTS.md 渐进式披露拆分（根宪法 + 8 份模块文档 + 体量门禁） | [2026-10-27-agents-progressive-disclosure-design.md](agents/2026-10-27-agents-progressive-disclosure-design.md) | `docs/solutions/2026-10-27-agents-progressive-disclosure-design.md` |
| 2026-10-27 | 需求落地工作流单一 owner（父级 AGENTS.md 唯一正文 + 两处指针） | [2026-10-27-workflow-single-owner-design.md](agents/2026-10-27-workflow-single-owner-design.md) | `docs/solutions/2026-10-27-workflow-single-owner-design.md` |

## type_quality/

| 日期 | 主题 | 文件 | 原路径 |
|------|------|------|--------|
| 2026-10-27 | 现有检查范围类型错误清零（Pyright 627→0，TypedDict / overload / TypeGuard） | [2026-10-27-type-error-remediation-design.md](type_quality/2026-10-27-type-error-remediation-design.md) | `docs/solutions/2026-10-27-type-error-remediation-design.md` |

## web_ui/

| 日期 | 主题 | 文件 | 原路径 |
|------|------|------|--------|
| 2026-10-31 | web_ui FastAPI 重构技术方案（issue #24，dashboard 命令 / `[web]` extra / P0 五页 / Kraken 终端风前端） | [2026-10-31-web-ui-dashboard-design.md](web_ui/2026-10-31-web-ui-dashboard-design.md) | `docs/superpowers/specs/2026-10-31-web-ui-dashboard-design.md` |
| 2026-10-31 | FastAPI + uvicorn + 运行期自装调研（版本选型 / SPA 托管 / 端口与 SIGTERM 契约 / pip extra 三环境行为） | [2026-10-31-fastapi-web-ui-research.md](web_ui/2026-10-31-fastapi-web-ui-research.md) | `docs/superpowers/specs/2026-10-31-fastapi-web-ui-research.md` |

## 根目录

| 日期 | 主题 | 文件 | 原路径 |
|------|------|------|--------|
| 2026-05-10 | 结构化报告少样本示例 | [fewshot.md](fewshot.md) | `fewshot.md` |
