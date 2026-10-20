# 归档文档索引

本目录保存**开发期产物**（技术调研 / 技术方案 / 方法论 / 终验记录）。它们在对应 feat 落地后归档，只作历史追溯，不是长期维护文档。

- **维护规则**：已归档文档若对应 feat 再次修改，必须在**同一 commit** 内同步更新（CLAUDE.md §6「调研 / 方案文档归档」）。
- **日期**：文档对应 feat 的落地日期（取自首次提交），非移动归档日期。
- **原路径**：归档前的仓库路径；`—` 表示自创建起就在本目录。

## auction/

| 日期 | 主题 | 文件 | 原路径 |
|------|------|------|--------|
| 2026-10-20 | 集合竞价强度快照接线（`quote_json` / `auction_snapshots` / `--recompute`） | [2026-10-20-auction-snapshot-wiring-design.md](auction/2026-10-20-auction-snapshot-wiring-design.md) | `docs/solutions/2026-10-20-auction-snapshot-wiring-design.md` |
| 2026-10-14 | 集合竞价强度分析技术方案 | [集合竞价强度分析_技术方案.md](auction/集合竞价强度分析_技术方案.md) | — |
| 2026-10-14 | 集合竞价强度判断方法论 | [集合竞价强度判断方法论.md](auction/集合竞价强度判断方法论.md) | — |

## provider/

| 日期 | 主题 | 文件 | 原路径 |
|------|------|------|--------|
| 2026-10-10 | 通达信 TDX 首选数据链路技术方案 | [TDX_PROVIDER_技术方案.md](provider/TDX_PROVIDER_技术方案.md) | `dragon_quant/providers/TDX_PROVIDER_技术方案.md` |

## score/

| 日期 | 主题 | 文件 | 原路径 |
|------|------|------|--------|
| 2026-09-12 | 五维评分器 Refactor | [评分器Refactor.md](score/评分器Refactor.md) | `dragon_quant/scorers/评分器Refactor.md` |

## review_account/

| 日期 | 主题 | 文件 | 原路径 |
|------|------|------|--------|
| 2026-10-18 | 盘后复盘因子扩展技术方案（独立资产库 + THS 涨停原因/题材主线） | [复盘因子扩展技术方案.md](review_account/复盘因子扩展技术方案.md) | — |
| 2026-10-14 | 择时与仓位调研 | [择时与仓位调研.md](review_account/择时与仓位调研.md) | — |
| 2026-10-14 | 择时与仓位技术方案 | [择时与仓位技术方案.md](review_account/择时与仓位技术方案.md) | — |
| 2026-10-14 | 现状策略基线 | [现状策略基线.md](review_account/现状策略基线.md) | — |
| 2026-10-14 | 终验追溯（择时与仓位 feat） | [终验追溯.md](review_account/终验追溯.md) | — |
| 2026-10-12 | 盘后复盘方法论 | [复盘方法论.md](review_account/复盘方法论.md) | `dragon_quant/review_account/复盘方法论.md` |
| 2026-10-12 | 复盘候选池技术方案 | [候选池技术方案.md](review_account/候选池技术方案.md) | `dragon_quant/review_account/候选池技术方案.md` |
| 2026-09-06 | 操盘策略（旧版 `STRATEGY.md`，现行版本见 `review_account/STRATEGY.md`） | [操盘策略.md](review_account/操盘策略.md) | `dragon_quant/review_account/STRATEGY.md` |

## 根目录

| 日期 | 主题 | 文件 | 原路径 |
|------|------|------|--------|
| 2026-05-10 | 结构化报告少样本示例 | [fewshot.md](fewshot.md) | `fewshot.md` |
