---
name: codemap
description: 生成或更新本仓库的代码地图（CODEMAP.md）。当用户要求「生成/更新 codemap」「建代码地图」「梳理某目录结构」，或改动了模块结构/调用链/数据流后需要刷新导航文档时使用。为目录生成层级式 CODEMAP.md：含结构摘要、关键导出（带源码行号标注）、执行路径、数据流契约与不变式。
allowed-tools: Glob, Grep, Read, LS, Edit, Write, Bash
---

## 目标

为 dragon-quant 仓库生成 / 增量更新**语义化代码地图**，供 AI agent 与人类快速导航。
本 skill 移植自开源 `code-map`（Claude Code skill）的层级式 CODEMAP 规范，适配 TraeCli。

代码地图回答的是「**改某功能要动哪些文件、调用链怎么走、数据怎么流、有哪些不可破的约束**」，
是 `AGENTS.md`（操作手册）与 `README.md`（对外说明）之外的**第三类文档：导航/语义层**。

## 产出物

- **根 `CODEMAP.md`**：全局执行路径地图 + 任务导航表 + 关键不变式。
- **按目录 `<dir>/CODEMAP.md`**（可选，对 `scorers_v2/`、`providers/` 等核心目录）：
  该目录的职责摘要 + 关键导出（函数/类，**带 `文件:行号`**）+ 与其它模块的依赖关系。

## 执行流程（每次调用按此办）

1. **判断范围**：用户指定目录则只更新该目录；否则全量刷新根 `CODEMAP.md`。
2. **读真相，不臆测**：
   - 用 `Grep` 扫 `cache.get` / `cache.set` 得到 DataCache 键的读写分布（数据流契约）。
   - 用 `Grep`/`Read` 取评分器 `score()`/`evaluate()` 签名、provider 公共方法签名。
   - 用 `Read` 跟读 `orchestrator.py` 的 Phase A→F，还原调用链与行号。
3. **写语义，不止罗列**：每个关键导出要写「**它做什么 / 何时被调用 / 依赖什么**」，
   而不是仅列签名（仅列签名是机器能做的事，本 skill 的价值在语义）。
4. **标注行号**：所有文件引用用 `path:line` 格式，便于跳转；行号要核对当前代码。
5. **校验一致性**：对照 §「关键不变式」检查地图描述与代码是否吻合，发现背离要在地图中标注或修正代码。
6. **更新规范**：若本次同时改了代码，遵循 `AGENTS.md` 的「文档同步」条款，与 `AGENTS.md`/`README.md` 同 commit。

## 根 CODEMAP.md 必含章节

1. **执行路径地图**：`scan` / `scan_v2` 的 cli → orchestrator Phase A→F 调用链（带行号）；v2 聚合内部 `aggregator.evaluate` 的五维调用。
2. **任务导航表**：`| 想做什么 | 主改文件 | 关联/注意 |`，覆盖调权重、改算法、加数据源、改表结构、加 CLI 等高频任务。
3. **数据流 / cache 键契约**：每个 `kline:*` / `sector:*` / `quotes:*` / `__meta__:*` 键的「写入方 → 读取方」。
4. **关键不变式**：见下方清单（破坏即出 bug，必须原样保留并随代码演进更新）。

## 关键不变式（地图必须收录，且需与代码核验）

1. 评分器是 cache 消费者：`score()` 只读 `cache.get`，绝不发网络请求；新数据须先在 orchestrator Phase D 预填。
2. v1/v2 隔离：旧 `scorers/` 与 v1 编排路径零改动；v2 全在 `scorers_v2/` + `use_v2` 分支。
3. RateLimiter 按 provider 串行防封；同花顺排行有 403 频控，带退避重试。
4. 封单单位铁律：封单强度 = `Quote.bid1_volume` ÷ `Quote.volume`，二者同为腾讯 gtimg「手」，禁与雪球成交量(股)混用。
5. 粒度铁律：当日盘中时序对比一律 1分K；资金承接回看用 5分K 历史。
6. 板块排行字段铁律：必须 `field=zdf`（涨跌幅），`tradezdf`(资金流) 无视 order/page；单页非严格有序须本地排序。
7. dragons 版本合并：同 `(trade_date, code)` 多次写入 `scorer_version` 并集去重（v1 在前）；5日去重排除同日。
8. provider 基类新方法用默认 `NotImplementedError`（非 `@abstractmethod`），否则 `create_providers()` 实例化即崩。

## 风格约束

- 用表格和短调用链表达，避免大段散文；每条信息可定位到 `path:line`。
- 只写「自动工具表达不了的语义」——纯签名罗列没价值，重点是意图、顺序、依赖、约束。
- 中文撰写，与仓库其它文档一致。
- 生成后自检：随机抽 3 处 `path:line` 用 `Read` 核对是否准确。
