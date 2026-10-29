# architecture — 仓库结构、公共入口与持久化清单

> 触发：定位模块/文件、确认公共入口与命令导航链、判断刻意保留的兼容例外、改动并发模型（RateLimiter）、新增或调整 SQLite 表时读本文。
> 本文权威：目录树、命令导航链、模块公共入口、兼容例外清单、并发模型、SQLite 表清单（表名 / 一句话用途 / 详细契约 owner）。
> 上游权威：AGENTS.md（硬约束与项目路由）；表级详细契约在各自的 owner 模块文档，本文只做 inventory。

## 目录树

```
dragon_quant/
├── __init__.py / __main__.py / _version.py / cli.py / app.py   # 入口 + Composition Root（app 不 import cli/commands）
├── commands/                    # 每个顶层命令一个薄 handler（COMMANDS + dispatch）
│   ├── __init__.py              # lazy `COMMANDS` 注册表 + `dispatch(name, args)` 唯一分发入口
│   ├── _shared.py               # CLI 纯 helper（策略 JSON / 日期 / 序列化）单一实现
│   ├── scan.py / logs.py / data.py / blacklist.py / review.py / review_account.py
│   └── dragons.py / prep.py / auction.py / assets.py / factor.py / buy_sell.py / vpa.py / storage.py
├── scan/                        # 扫描 feature（可调用包）
│   ├── __init__.py              # 可调用包：scan(...) → scan.orchestrator.main.scan
│   ├── sectors.py               # 板块身份 (source, category, code) + cache 键构造
│   ├── scores/                  # 五维「识别真龙」评分器
│   │   ├── __init__.py / main.py / aggregator.py / base.py / registry.py
│   │   └── drive.py / leadership.py / anti_drop.py / liquidity.py / absorption.py
│   ├── orchestrator/            # Phase A→F 编排
│   │   ├── __init__.py
│   │   └── main.py
│   ├── storage/                 # 扫描持久化仓储 + Phase F 收口
│   │   ├── __init__.py / main.py / scans.py / dragons.py / review_strict.py / logs.py
│   │   └── blacklist.py / results.py
│   ├── cache/                   # ScanCache（通用 DataCache 在 infra.storage）
│   │   ├── __init__.py
│   │   └── main.py
│   └── logger/                  # 结构化日志 + 报告 + 查询
│       ├── __init__.py / main.py / events.py / report.py
│       └── query.py
├── review/                      # 龙头回测
│   ├── __init__.py
│   └── main.py                  # run_review / review_dragon
├── review_account/              # 账户级回测 + 策略 + 盘后复盘 + 竞价 + 实盘辅助
│   ├── __init__.py / main.py / service.py / simulator.py / engine.py / data.py
│   ├── market.py / indicators.py / evaluation.py / models.py / advice.py / verdicts.py
│   ├── storage.py               # review_account_* 五表仓储
│   ├── prep/                    # 盘后复盘
│   │   ├── __init__.py / main.py / service.py / collectors.py / compute.py
│   │   ├── rebuild.py
│   │   └── review/              # 复盘分纯函数（配置/评分/市场/主线/角色/池/闸门/仓位/反馈/编排）
│   │       ├── __init__.py / config.py / scoring.py / market.py / mainline.py
│   │       ├── roles.py / pool.py / gates.py / sizing.py / context.py
│   │       └── feedback.py / board_rank.py / orchestrator.py
│   ├── auction/                 # 集合竞价（v2 纯函数流水线）
│   │   ├── __init__.py / main.py / collect.py / recompute.py / scoring.py / dates.py
│   │   ├── normalize.py / features.py / events.py / dimensions.py / context.py
│   │   └── registry.py / strength.py / serialize.py / report.py / types.py
│   ├── strategy/                # 策略纯拆分（main.py 只门面重导出）
│   │   ├── __init__.py / main.py / common.py / buy.py / checks.py / sell.py
│   │   ├── execution.py / priority.py / risk.py
│   │   └── STRATEGY.md
│   └── live_trade/              # 实盘辅助 buy/sell（纯信号记账）
│       ├── __init__.py / main.py / service.py / trader.py / signal_engine.py / shadow.py
│       └── storage.py
├── vpa/                         # 量价分析
│   ├── __init__.py / main.py / engine.py / types.py / report.py / storage.py
│   └── factors/                 # vol_amount / trend_verify / breakout / divergence
│       ├── __init__.py / base.py / vol_amount.py / trend_verify.py
│       └── breakout.py / divergence.py
├── infra/                       # 叶子层：唯一连接/schema/路径/缓存/provider/资产/因子
│   ├── __init__.py / clock.py / models.py / market_calendar.py / module_proxy.py
│   ├── provider/                # 数据源适配
│   │   ├── __init__.py / main.py / base.py / bundle.py / data.py / rate_limit.py
│   │   ├── tdx.py / ths.py / xueqiu.py / tencent.py / macro.py
│   │   └── cookie.py
│   ├── storage/                 # dragon.db 唯一连接 + 通用设施
│   │   ├── __init__.py / main.py / paths.py / identifiers.py / connection.py / schema.py
│   │   └── _base.py / dragons_rows.py / cache.py / manager.py
│   ├── factor/                  # 因子层（13 批准出口 + builtin）
│   │   ├── __init__.py / main.py / types.py / registry.py / engine.py / storage.py
│   │   ├── decision.py / evaluation.py / matrix.py / stats.py / service.py
│   │   ├── review_shadow.py / finance.py / sql.py / finance_store.py
│   │   ├── decision_review.py / decision_review_store.py
│   │   └── builtin/             # five_dim / vpa / financial / prep / auction / prep_review
│   │       ├── __init__.py / five_dim.py / vpa.py / financial.py
│   │       └── prep.py / auction.py / prep_review.py
│   └── assets/                  # assets.db 独立库 + 云端同步
│       ├── __init__.py / main.py / db.py / schema.py / dragons_mirror.py
│       ├── ASSETS_ARCHITECTURE.md
│       └── sync/
│           ├── __init__.py / cli_service.py / service.py / sources.py / tables.py
│           ├── manifest.py / state.py / codec.py / models.py / credentials.py
│           ├── doctor.py / errors.py
│           └── backends/
│               ├── __init__.py / base.py / local.py / s3.py / signer.py
├── tests/                        # 测试树（source-only，不入 wheel/sdist；命名空间 dragon_quant.tests）

# 同级仓库包（不是 dragon_quant 的子包）
web_ui/                          # 回测结果 Web UI（Vite+React+TS+Mantine / stdlib HTTPServer；web_ui.start_server）
```

## 命令导航与入口事实

> **命令导航（M11B 起）**：`cli.py` 只做 argparse 解析，随后调用 `commands.dispatch(name, args)`；
> 每个顶层命令的实现在 `commands/<name>.py` 的 `handle(args, services)`（buy/sell 共用
> `commands/buy_sell.py` 的 `handle_buy`/`handle_sell`）。测试与脚本应直接走
> `commands.dispatch` 或命令模块的公共 helper，不存在第二个转发层。真实导航链是
> `cli → commands → feature 门面 → feature 用例 → 实现`；`app.py`（Composition Root）只是
> commands 按需调用的装配支路，不在链上多一跳。

> **入口事实（实测）**：`python -m dragon_quant`（不带子命令）与 `-h` 一样**打印帮助并 exit 0**，
> 不会执行扫描；实时扫描必须显式 `scan`。`scan --date YYYYMMDD` 只读取历史扫描记录，不触网。
> 旧的「无参数即 scan」说法与当前 parser 不符，不得据此调用。

## 模块公共入口

> **模块公共入口**：`scan`（可调用包）；`review.main.run_review/review_dragon`；
> `review_account.main`（`run_review_account` + `StrategyConfig` / `MarketData`）；
> `review_account.prep.main`（`run_prep` + `format_advice_lines` / `format_candidate_lines`）；
> `review_account.live_trade.main.run_buy/run_sell`；
> `review_account.auction.main.run_auction/run_auction_recompute`；
> `infra.factor.main` 13 个批准出口（`build/list_factors/query/status/export_matrix/evaluate_factor/
> fin_sync/fin_calibrate/fin_status/parse_only/FactorModel/annotate_pool/CleanConfig`）；
> `infra.assets.main.run`；`vpa.main`（`analyze` + `render` / `upsert_vpa`）；
> `infra.storage.main`（`init_db`/`DataCache`/`StorageManager`）与 `scan.storage.main`
> （扫描/龙头/日志/黑名单持久化）为文档化公共契约，`infra/storage`、`scan/storage` 的
> `__init__.py` 不做急切重导出；`web_ui.start_server`。

## 刻意保留的兼容例外

> **刻意保留的兼容例外**（不随 CLI 兼容层清理移除）：
> - `infra/factor/main.py` 的 25 个 lazy 旧名：一轮迁移兼容，经 lazy 映射按需解析，**不 eager 绑定**；
> - `infra/storage/_base.py` 转发包装：保留 `connection._connect` 运行期 patch 语义（测试与既有脚本依赖）；
> - `infra/module_proxy.py`：`review` / `prep` 包门面的历史属性代理（批准设计 §4 的包 `__init__` 策略）；仅 stdlib 通用模块属性转发、无业务 import，与已删除的迁移期 `infra/compat.py`（含 `alias_module` 与旧路径别名）职责不同；
> - `review_account/prep` 的 `legacy()` 与活动导出：子模块在调用期经它反查 patch 目标；
> - `review_account/live_trade/trader.datetime` 模块级导入：测试按模块属性 patch 交易时钟；
> - `COMMANDS["scan"]` 的 provider 例外（`providers=None`）：保持 `api_stats` 与 M1 golden 口径。

## 并发模型

`RateLimiter` 核心规则：
```
同一 provider（按 provider 名分组）→ 串行排队 + 随机延迟（防封 IP）
不同 provider 之间 → 自由并发
```
用法：`limiter.submit("ths", "ths", fn)` 之后 `limiter.wait_all()`。

## SQLite 表清单

表级契约按 owner 拆分：本文只列「表 / 一句话用途 / 详细契约」，列定义只在 owner 文档维护，禁止在此重复。

| 表 | 一句话用途 | 详细契约（owner） |
|----|-----------|------------------|
| `scans_v2` | 每轮扫描元信息（含 `raw_output` 完整结果 JSON） | [scan](scan.md) |
| `scan_stocks_v2` | 每轮全部评分结果（含否决诊断） | [scan](scan.md) |
| `dragons_v2` | 入选龙头最终物化（权威源） | [scan](scan.md) |
| `scan_logs_v2` | 结构化日志（`logs` 默认查询 v2） | [scan](scan.md) |
| `*_v1` | 历史旧表，仅 `--source v1` 只读查询 | [scan](scan.md) |
| `vpa_analysis` | 量价分析结果（独立表） | [scan](scan.md) |
| `sector_blacklist` | 板块黑名单 | [scan](scan.md) |
| `dragon_review_strict` | 严格口径 review 结果（侧表） | [review](review.md) |
| `review_account_runs` / `_snapshots` / `_trades` / `_positions` / `_events` | 账户回测批次、每日快照、交割单、已平仓持仓、决策时间线 | [trading](trading.md) |
| `buy_sell_signals` | 实盘辅助买卖信号（含半仓腿） | [trading](trading.md) |
| `prep_assets` / `prep_snapshots` | 复盘原始资产与计算结果 | [prep-auction](prep-auction.md) |
| `auction_assets` / `auction_snapshots` | 竞价原始资产与强度快照 | [prep-auction](prep-auction.md) |
| `dragons`（assets.db） | `dragons_v2` 全量镜像 | [assets](assets.md) |
| `factor_values` / `factor_registry` / `factor_build_runs` / `factor_sector_snapshots` / `factor_model_snapshots` | 因子长表、注册表、逐日审计、主板块快照、模型快照 | [factor](factor.md) |
| `fin_*` 五表（assets.db） | 财务观测、报告文件、vintage 记录、字段校准、除权记录 | [factor](factor.md) |

### `assets.db` 独立库

- **`assets.db`（独立库）**：数据资产仓（prep + auction + factor + fin + dragons 镜像），与 `dragon.db` 物理隔离，防止不可再生资产被 `storage clear --all` 连带清理；`storage status` 展示 dragon.db / assets.db / 旧 prep.db 体积。

> **存储层重构（2026-10-20）**：旧 `prep.db`（`prep_assets`/`prep_snapshots`）与 dragon.db 的遗留 `auction_snapshots` 表保留但**不再读写、不自动迁移、不 DROP**。需要保留历史时按 README「持久化」段的手工迁移片段操作。
