# dragon-quant 🐉

**龙头战法量化筛选系统** — A 股涨停板龙头识别工具

基于通达信 TDX（首选）+ 同花顺、雪球、腾讯三大公开数据源（宏观回退 Yahoo），对涨停候选股进行多维量化评分，自动识别市场龙头；同时提供日志查询、SQLite 持久化、龙头回测与 Web UI 可视化能力。

当前主流程使用**五维「识别真龙」评分体系**：带动性 30% / 领涨性 25% / 抗跌性 15% / 流动性 20% / 资金承接 10%，采用**门槛 + 加权两段式聚合**（四大特征任一低于门槛即一票否决，资金承接不否决仅加权贡献）。设计哲学：龙头不是预判出来的，是「识别」出来的。完整规则见 [评分规范](dragon_quant/scorers/评分器Refactor.md)。

## 目录

- [龙头回测成绩单（历史样本）](#龙头回测成绩单历史样本)
- [安装](#安装)
- [快速开始](#快速开始)
- [CLI 命令大全](#cli-命令大全)
- [Programmatic API](#programmatic-api)
- [评分体系](#评分体系)
- [数据源](#数据源)
- [目录结构](#目录结构)
- [设计原则](#设计原则)
- [持久化](#持久化)
- [免责声明](#免责声明)
- [License](#license)

## 龙头回测成绩单（历史样本）

> 入选后第一个非一字板日以最低价买入；最大收益按收益观察窗口统计，最大回撤按「买入日至最大收益出现日」窗口统计。

| 排名 | 代码 | 名称 | 入选日 | 综合分 | 买入日 | 买入价 | 最大收益% | 最大回撤% |
|------|------|------|--------|--------|--------|--------|-----------|-----------|
| 1 | 002552 | 宝鼎科技 | 2026-05-22 | 77.8 | 2026-05-25 | 41.02 | +51.37 | +0.00 |
| 2 | 000636 | 风华高科 | 2026-05-22 | 83.0 | 2026-05-26 | 40.18 | +50.72 | +0.00 |
| 3 | 600172 | 黄河旋风 | 2026-05-22 | 75.1 | 2026-05-25 | 11.16 | +43.91 | +0.00 |
| 4 | 000725 | 京东方Ａ | 2026-05-21 | 77.5 | 2026-05-22 | 4.47 | +36.24 | +0.00 |
| 5 | 603989 | 艾华集团 | 2026-05-22 | 81.1 | 2026-05-25 | 29.00 | +32.48 | +0.00 |
| 6 | 002579 | 中京电子 | 2026-05-26 | 62.2 | 2026-05-27 | 15.99 | +29.83 | +0.00 |
| 7 | 002585 | 双星新材 | 2026-05-22 | 72.4 | 2026-05-25 | 9.62 | +22.66 | +0.00 |
| 8 | 002975 | 博杰股份 | 2026-05-25 | 79.4 | 2026-05-26 | 125.00 | +21.55 | -5.60 |
| 9 | 600707 | 彩虹股份 | 2026-05-21 | 82.8 | 2026-05-22 | 10.57 | +21.38 | +0.00 |
| 10 | 002952 | 亚世光电 | 2026-05-21 | 78.5 | 2026-05-22 | 28.80 | +17.15 | +0.00 |

## 安装

```bash
pip install dragon-quant

# 或从源码
git clone https://github.com/gitBingxu/dragon-quant.git
cd dragon-quant && pip install -e .
```

> 要求 Python ≥3.10；`tmdx`（通达信 TDX）为默认依赖，随 `pip install dragon-quant` 自动安装。

## 快速开始

```bash
# 首次使用：配置雪球 Cookie（个股数据依赖；自动获取需 playwright，首次自动下载 chromium 内核约 150MB，走国内镜像）
dragon-quant data cookie-fetch

# 查看 Linux 风格帮助提示
dragon-quant -h
dragon-quant scan -h

# 五维「识别真龙」扫榜 — 找 top5 龙头
dragon-quant scan --top 5

# 账户级模拟交易回测（按真实账户逐日推进，可 --ui 打开 /account 面板）
dragon-quant review-account --ui-only

# 龙头回测 + Web UI
dragon-quant review --ui
# 查看龙头回测面板（默认读取 dragons_v2）
dragon-quant review --ui-only

# 实盘辅助交易（复用 review-account 策略，纯信号记账，不维护真实账户）
dragon-quant buy                              # 盘中：候选池内触发买点的标的全部记入买入信号
dragon-quant sell                             # 盘中：对已买入未卖出的信号判定是否卖出
dragon-quant buy --date 20260907 --at 10:00   # 历史日期回放当日分时（必须指定 --at）
dragon-quant buy --force                      # 非交易时段强制跑当日回放（09:30 前仍拒绝）
dragon-quant sell --force                     # 同上，卖出建议
```

> 雪球 Cookie 也可手动设置：`dragon-quant data cookie-set --cookie "xq_a_token=...; u=..."`。Cookie 文件位于数据目录下的 `cookies/xueqiu`（macOS `~/Library/Application Support/dragon-quant/`，Windows `%APPDATA%\dragon-quant\`，可用 `DQ_DATA_DIR` 覆盖）。自动获取（`data cookie-fetch`）首次运行会自动下载 chromium 内核（约 150MB，走国内镜像，仅一次），无需手动 `playwright install`。

## CLI 命令大全

### `scan` — 扫榜

```bash
dragon-quant scan [--top 25] [--candidates 5] [--workers 2] [--force] [--no-cache]
```

| 参数 | 默认 | 说明 |
|---|---|---|
| `--top` | 25 | 最多展示和入选的真龙数量；否决票不占名额 |
| `--candidates` | 5 | 兼容参数；当前五维路径按每个入选板块当日全部涨停股入池 |
| `--workers` | 2 | 并发线程数 |
| `--force` | - | 跳过交易时段拦截与 DB 缓存 |
| `--no-cache` | - | 刷新 Provider 缓存；重新评分时与 `--force` 一起使用 |

`scan` 走五维「识别真龙」体系。输出包含：板块排行（领涨/领跌明细）、候选股列表、评分表格、自然语言详细报告，并自动持久化到数据目录的 `*_v2` 表（macOS `~/Library/Application Support/dragon-quant/`，Windows `%APPDATA%\dragon-quant\`，可用 `DQ_DATA_DIR` 覆盖）。`scan_v2` 仍可用于旧脚本兼容，但帮助文档不再展示。

### `blacklist` — 板块黑名单

拉取领涨/领跌板块时按名称子串过滤（**名称级、跨维度生效**：行业/概念/地区同名板块会被一并过滤；默认种子为空，按需维护）。

```bash
dragon-quant blacklist list
dragon-quant blacklist add "次新股"
dragon-quant blacklist remove "次新股"
```

### `dragons` — 导出龙头（JSON）

```bash
dragon-quant dragons                             # 最新有龙头数据的交易日，全部龙头
dragon-quant dragons --date 20260519             # 指定交易日
dragon-quant dragons --date 20260519 --true-only # 只输出真龙 (is_true_dragon=1)
```

| 参数 | 默认 | 说明 |
|---|---|---|
| `--date` | 最新有数据的交易日 | 指定交易日（`YYYYMMDD` / `YYYY-MM-DD` / `YYYY/MM/DD`） |
| `--true-only` | - | 仅保留 `is_true_dragon=1` 的记录，默认不区分 |

固定读取 `dragons_v2`（扫描物化的入选龙头）并以 JSON 输出：外层含 `trade_date` / `source` / `count`，`dragons` 为龙头明细数组。`--date` 缺省时取**最新有龙头数据的交易日**（非「今天」）。无数据时输出 `{"error": ...}` 并以非零码退出，便于脚本判断。

### `prep` — 盘后复盘（候选池 + 情绪温度快照）

```bash
dragon-quant prep                             # 最新有龙头数据的交易日
dragon-quant prep --date 20260519             # 指定复盘日
dragon-quant prep --date 20260519 --recompute # 用已存资产重算快照（算法升级重放）
dragon-quant prep --date 20260519 --force     # 强制重采全部可采资产（不绕过 16:40 门禁）
dragon-quant prep --json                      # 输出完整复盘 JSON（默认 4 行市场摘要 + 标的清单）
```

盘后执行复盘，分**两段式流水线**（固定 `source=v2`），默认**增量采集**：

1. **采集段**：先读存量资产 + `collection_report` 做健康判定——资产完好则**零采集**直接进入计算段；否则只补采缺失/不健康且可采的资产（`dragons` 重采且候选 code 集合变化时级联补采 `fund_flow`/`cninfo_announcements`）。payload 与本次 report 条目单事务写入 **`assets.db`** 的 `prep_assets`。
   - **`collection_report` 语义**：该日期资产的**当前最佳状态**，不是本次运行的遭遇。只按键合并本次实际执行的 collector 条目；零采集时完全不动；补采失败不覆盖既有健康状态（记 `last_attempt`）。
   - **窗口**：复盘范围为含 T 的最近 3 个交易日 `[T-2, T-1, T]`（按交易日历，采集段解析并写入 `dragons` 资产）。窗口内某日无龙头记录（未跑 scan/无真龙）→ 跳过该日并标 `dragons_partial` 降级，不中断；三日并集为空才视为 `empty`。T 日尚未 scan 时仍可用前两日候选。
   - tmdx：全 A 市场宽度（红盘率/涨停/炸板/跌停/成交额）、FG 板块（昨日涨停溢价/晋级交叉/龙虎榜名单）、并集候选股主力净额、cninfo 公告、**宏观扰动**（日经225期指/A50期指同日口径，TDX 扩展行情单源；美债 10Y/黄金 T-1 口径，TDX 主链路 + Yahoo 回退）
   - THS dataapi：涨停池（涨停原因/板型/炸板次数/连板高度）、题材主线榜（题材涨停家数/最高板）；仍按 T 日口径采集
2. **计算段**：只从 `prep_assets` 读回资产（快照 = 资产的纯函数）→ 计算 `sentiment` / `main_boards` / `macro` / `candidates` → 写入 `prep_snapshots`。
   - 候选池 = 三日真龙并集，同 code 去重保留综合分更优者，每只保留**真实入选日**（`trade_date`，下游 `candidate_trade_date` 血缘）。
   - 每只候选新增 `facts` 块（今日是否在池、今日涨幅/换手/成交额、炸板/连板/封板率、资金/公告可用性、风险公告）与 `verdict`（强烈建议/轻仓博弈/忽略/未评级）+ 2-3 行维度明细 + 忽略原因；`in_pool_today` 与入选日解耦，T-2 入选、T 日再涨停的龙回头也能进买入组。默认 `prep_verdict_filter_enabled=True`：`ignore` 出池，`strong/light/unrated` 保留（unrated 仅 P=False 排序垫底）；推荐集为空时回退 legacy 且不再应用 ignore 过滤。
   - THS 入选日归因字段（涨停原因/板型/炸板次数等）保持既有契约：跨日候选为 `null`；今日封板事实在 `facts` 内按 T 日池口径提供。
   - 快照 `version=0.5`，顶层 `macro`（日经/A50期指 + 美债/黄金；`available`/`session`/`instruments`/`degraded`/`sources`，口径血缘在逐品种 `caliber`）；`dragon_source` 含 `window_days` / `dates`（逐日 scan_id/version/count/status）/ `version_mixed`；只增 key，旧快照继续可读。

**默认输出**：4 行市场摘要（是否建议买入 / 建议仓位 / 建议等级 / 买入理由，理由内含外围/美债/黄金片段）+ 空行 + 逐只标的清单：

```text
是否建议买入：是
建议仓位：50%
建议等级：一般
买入理由：复盘日 2026-10-21；外围：日经225期指 +1.23%、富时A50期指 -0.87%（同日收盘）；美债10Y 5.15%（较前值 +6bp，近似）；COMEX金 -0.63%；……

✅ 科森科技-强烈建议
- 质量维度：五维综合 82.0（排名 1）；题材：低空经济（今日主线第 2）
- 涨停维度：换手板 3天3板，首封 09:32，炸板 0 次，封板成功率 100%
- 资金维度：主力净流入 1.23 亿（占成交额 4.1%）

风华高科-忽略：今日涨幅 +1.20%、主力净流出 1.20 亿（占成交额 -6.2%），资金离场
```

买入组（✅ strong/light）在前，组内综合分降序；忽略组在后、每只一句原因；未评级最后；候选为空显示「无参与复盘标的」。等级映射：0.8 强烈买入、0.5 一般、0.3 轻仓博弈、0.0 不建议买入、数据不足时「不建议买入（数据不足）」；降级信息并入理由。需要完整 JSON（含 `sentiment`/`macro`/`candidates`/`dragon_source`）时加 `--json`。

**宏观时点语义**：`macro_global` 的品种与口径由 `MACRO_SPECS` 声明。`jp_nikkei`（日经225期指）与 `cn_a50`（富时A50期指）取「日 K 中日期 `<= trade_date` 的最后一根」（**同日收盘**，TDX 单源、无回退）；`ust10y` 与 `gold` 保持 `< trade_date`（上一已完成 bar，TDX 主链路 + Yahoo 回退）。夜盘归属下一交易日 bar，T+1 bar 被排除，因此 16:41 与更晚运行得到同一值（T 日 bar 的 TDX 发布延迟若发生，会标 `macro_same_day_missing` 降级）。`session` 描述外围指数组（`same_day`/`previous_session`/`mixed`），口径血缘在逐品种 `caliber`；`session_date` 为源端交易日，落后 >4 天标 `stale`。期指连续存在换月 roll 跳变，换月日涨跌幅可能失真；美债为两位小数日频快照，bp 变动标「近似」且只展示、不进仓位公式。

`review-account` / `buy` / `sell` / `auction` 统一消费**严格 T-1** 快照作为候选池（`prep_snapshot_for` 单一实现）；快照缺失/读取异常/推荐集为空时回退到「前 3 日真龙并集」旧逻辑，并输出 `fallback_reason`。快照窗口与 `StrategyConfig.candidate_lookback_days` 无关（后者只作用于回退路径）；`auction --days N` 也只在 legacy/回退路径生效。

**时点约束**：FG 板块与实时行情类资产为「当日快照」（`same_day_only`），补跑历史日**不采集、不写 report**（不把当日数据写到历史日名下，compute 按缺失降级）；THS 涨停池/题材榜、cninfo 公告与 `macro_global`（日 K canonical）支持历史补采（THS 实测 ≥4.5 个月）。当日运行须在 **16:40** 之后（A50 期指日盘 16:30 收盘 + 缓冲）。

**增量与强制刷新**：默认增量意味着同一交易日盘后首次采集成功后，晚些时候重跑不会重新拉取数据（快照仍按存量资产重算，`collection_report` 不受影响）；需要最新数据时用 `--force` 重采全部可采资产——它**不绕过** 16:40 与交易日校验（与 `scan --force` 语义不同）。`--recompute` 零检测零采集，只用已存资产重算。采集决策提示（增量补采/修复项）输出到 stderr，stdout 仍是「4 行摘要 + 标的清单」。

### `auction` — 采集集合竞价盘口与强度快照

```bash
dragon-quant auction                        # 优先 T-1 prep 推荐（strong/light），空则回退
dragon-quant auction --days 5               # 仅 legacy/回退路径的回溯交易日数
dragon-quant auction --pool legacy          # 强制旧口径：最近 N 日 dragons 并集
dragon-quant auction --recompute 20261020   # 用已存原始资产重算（不触网）
dragon-quant auction --json                 # 输出完整 JSON（默认人读摘要）
```

候选池默认取**严格 T-1 盘后复盘快照**中 eligible（真龙 + `min_score`）且 `verdict != ignore` 的标的（strong/light/unrated/缺 verdict 保留；推荐集为空则回退），与 `buy` / `review-account` 共用同一池语义（`collect_candidates_ex`），保证「竞价采的 code = 次日策略可能买的 code」。T-1 快照缺失/读取异常/无资格/推荐集为空时，自动回退最近 N 个有龙头数据的交易日 `dragons_v2` 并集（`--days` 在该路径生效），并在输出中回显 `pool_source="legacy"` 与 `fallback_reason`（`no_snapshot` / `snapshot_read_error` / `empty_snapshot` / `no_eligible` / `all_ignored` / `calendar`）；回退池不再应用 `ignore` 出池，P 仍按快照 verdict 标注。只有 legacy 也为空时才正常结束（exit 0）且不发起任何行情/盘口请求。

确定候选后先**整批**获取行情（TDX 主、tencent 兜底，禁止逐股请求），再逐一拉取**当日**集合竞价盘口（TDX `get_auction`，仅当日 9:25 后可用、盘后仍可拉取、无历史回看），落库到 `assets.db/auction_assets`（`items_json` 盘口明细 + `quote_json` 行情快照，键 `trade_date+code`，同日重复采集幂等覆盖）。随后逐股评分、整批排名，并以单事务整日替换 `auction_snapshots`（`source="v2"`）。

默认输出人读摘要：标题（日期、参与只数、池来源；有回退时附 `fallback_reason`）+ 逐只「排名/代码/名称/等级/强度/方向」（未验证前缀 `?`、否决前缀 `-`，否决原因与未验证标记可叠加）+ 汇总（强/中/弱/其他计数、未验证数、采集失败数）。空池、采集层全失败、评分层全失败、候选全为空盘口分别给出单行提示。加 `--json` 输出完整 JSON：`trade_date`（竞价所属交易日 = 当日）/ `pool_source` / `fallback_reason` / `prep_snapshot_date` / `recommended_count` / `days_ignored` / `lookback_dates` / `candidate_codes` / `saved_count` / `saved`（含每只盘口条数）/ `empty`（当日无竞价数据的 code，如停牌）/ `failed`（逐 code 隔离，含 `stage`）/ `quote`（`source` 为实际生效来源 `tdx`/`tencent`/`none`，含 `matched`/`missing`/`degraded`/`reason`）/ `scored_count` / `snapshot_saved_count` / `snapshot_ok` / `snapshot_skipped` / `snapshot_error` / `snapshots`（每只判定摘要）。空池时额外输出 `pool_empty` / `ignore_reason_count` / `ignore_reason_samples` / `prep_degraded`。行情缺失时快照仍会写入（`unverified` 且大概率被保守否决），并在 `quote.degraded` 与 stderr 告警中显式标注。默认模式的错误写 stderr 并以非零码退出；`--json` 模式错误保持原 JSON error 到 stdout，退出码语义不变。

`--recompute DATE` 不触网：只读该日 `auction_assets`（`items` + `quote`）重算并覆盖 `auction_snapshots`；`DATE` 支持 `YYYYMMDD` / `YYYY-MM-DD` / `YYYY/MM/DD`，输出统一 `YYYY-MM-DD`。默认输出与采集模式相同的人读摘要（标题标注「重算」），加 `--json` 输出完整 JSON。无原始资产、全部 `items` 为空或全部评分失败时以非零码退出；未来日期拒绝。空结果不会删除既有快照。

适合每日 9:25 后（或盘后）定时跑一次，逐日积累龙头竞价盘口历史；算法升级后用 `--recompute` 重放历史日。

### `assets` — 数据资产云端同步

```bash
dragon-quant assets doctor --profile r2 --samples 50
```

把 `prep` / `auction` 产出的原始数据资产备份到 S3 兼容对象存储（Cloudflare R2 主选 / Backblaze B2 备选），本地始终是权威副本。当前版本提供 `doctor`（门槛 A：PUT / HEAD / GET / LIST / DELETE + 元数据回环）；`push` / `pull` / `status` / `verify` 在后续里程碑提供。

凭据文件：`<DQ_DATA_DIR>/secrets/assets_sync.json`（POSIX 0600），结构为 `default_profile + profiles{r2,b2}`；也可用 `DQ_ASSETS_*` 环境变量覆盖当前 profile。

云端布局：`blobs/<date>/<source>_<type>.<sha256>.json.gz` + `manifests/<date>.json`。设计依据见 `docs/solutions/2026-10-18-assets-cloud-sync-design.md`。

### `review` — 龙头回测

```bash
dragon-quant review                       # 自动筛 5~20 交易日内 pending 票全回测
dragon-quant review --date 20260519 --top 5
dragon-quant review --date 20260519
dragon-quant review --force --date 20260519
dragon-quant review --ui                  # 回测后启动 Web UI（默认展示 dragons_v2）
dragon-quant review --ui-only --port 8765 # 仅看结果（默认 dragons_v2）
```

`review` 默认读取/写回 `dragons_v2`；`--source v1` 仅用于查询和回测历史旧表。回测流程：从对应 `dragons_*` 表读 pending 龙头 → 找入选后第一个非一字板日（`high != low`）以最低价买入 → 算 `max_return_5d` / `max_return_hold_days` → 按买入日至峰值窗口算 `max_drawdown_5d` → 写回对应 DB 表。回测时对每只 pending 个股追加一段**量价分析**，结论写入独立的 `vpa_analysis` 表。

### `review-account` — 账户级模拟交易

```bash
dragon-quant review-account --from 20260501 --to 20260601
dragon-quant review-account --from 20260501 --to 20260601 --capital 200000 --ui
dragon-quant review-account --ui-only
```

`review-account` 与 `buy/sell` 共用 `review_account` 策略与成交函数（`evaluate_buy`/`evaluate_sell`/`fill_price`/`fee`），统一候选、买卖规则与费用，因此后续只改 `review_account` + 回测即可同步调整 `buy/sell`。回测按开盘及完整 5 分钟 K 顺序重放，不能用当天最终数据决定早盘交易。候选池优先读**盘后复盘快照**（`prep_snapshots`）；快照缺失/空/异常时回退为**前 3 个真实交易日的全部真龙**并集（共享层过滤否决/低于 50 分），按 code 去重保留综合分更高者，不再截断前 N 只；空池不向更早日期补票，回测首日会加载区间前的候选日期。

- 开盘 MA5 承接只用上日日 K 指标与量能；突破前高改为**首根 5 分钟 K 完成后确认**，需该根换手 ≥0.5% 带量（`turn_strong_bar_turnover_min`），日 K 兜底日不触发；分歧买龙等待完整 30 分钟承接窗口，不把瞬时触板当成保证成交。
- 多候选择优：**P/A 三值 tier → 五维综合分 → 买点得分 → 代码**（三值序 True < Unknown < False；不再参考真龙 rank）。`auction_ranking_enabled`（默认 False）控制 A 是否参与排序，`auction_veto_enabled`（默认 False）控制「verified ∧ is_weak 一票否决」；两者皆关时零竞价读取、零否决、A 恒 Unknown。排序键由 `review_account/priority.rank_key` 单一实现，`buy` 的 buy-all 语义不变。
- 信号与成交分离：历史信号在下一可交易事件撮合，实时信号等待更晚的有效报价；跳空不补填成本线或止损价，封死涨跌停不假定能买卖。
- T+1、当天卖出后不再买入；按事件顺序先卖后买，不用下午卖出结果阻止早上买入。止损、移动止盈、保本、高开窗口和尾盘规则共用一处实现；尾盘以 14:55 已知行情判断，15:00 只估值和更新峰值。
- 默认仍保留原仓位基线；`--config params.json` 可统一设置风险预算、单票仓位上限、MA5 距离/趋势过滤、ATR 移动止盈。最低佣金默认每笔 5 元，双边滑点 0.2%，佣金 0.03%，卖出印花税 0.05%。
- 择时与仓位改造（除龙回头默认开启外，其余 default-off）：情绪→仓位→禁买（消费盘后复盘快照 `sentiment.position_pct`）、组合风控（日级熔断/净值回撤降仓/连续亏损降仓）、`entry_veto`（无调用方的通用前置否决钩子；竞价否决在 `evaluate_buy` 信号后置闸门，`reason_code=auction_veto`）、首日止损减半 `first_day_stop`（默认关；开启后 T+1 必须至少运行一次 `sell`）、时间止损、分级移动止盈、龙回头买点（**默认开启**，显式 false 可复现旧基线），及验证基建（高原扫描/消融/回撤分解/试验账本）。分叉声明与规则链顺序见 [STRATEGY.md](dragon_quant/review_account/STRATEGY.md)。
- 数据分级成交：当日所有相关个股都有完整 5 分钟 K 时走严格盘中撮合；任一缺失则整日**日K兜底**（开盘价买入、当日 OHLC 判止损止盈，分歧买龙/高开窗口等分时规则自动跳过；分时可用但当日日K缺行的标的记为 `daily_unavailable`，当日不建行）。雪球 5 分钟历史约仅保留 14 天，故多月回测多为兜底模式，成交价为保守近似，结果标注 `data_quality`（strict_5min/mixed/daily_fallback）与 `fallback_days`。**停牌标的按 code 跳过并标记**：历史交易日「当日日K缺行」或「日K `volume`/`amount` 双零」且无分时 → 判停牌，不进当日 `rows`、不评估买卖、按最近可得收盘价估值（净值沿用上一交易日收盘），剔除停牌 code 后再判断是否整日兜底；结果新增 `suspended_days` / `suspended_codes` / `suspended_days_count` / `unavailable_days` / `data_degraded`，停牌候选仍保留在 `details` 并以 `reason_code=suspended` 说明。当日（盘中与收盘后）不做停牌分类（日K缓存按写入日命名、不校验新鲜度），沿用旧契约硬报错。日K完全无历史或预热不足仍报错、不保存部分回测；除权口径缺口仍待解决。

`/account` 继续支持生成/删除记录，`POST /api/account/runs` 可传 `strategy_params` JSON；快照附带完整 `positions`，旧记录仍可读取。完整规则见 [STRATEGY.md](dragon_quant/review_account/STRATEGY.md)。`review_account.evaluation.compare_strategies` 使用时间顺序训练/验证/独立测试和双倍滑点压力测试；数据不足时返回原因，不自动推广参数，不保证收益改善。

- **买点统计**：`/account` 新增「买点统计」卡片，按四类买点（开盘回踩MA5 / 弱转强 / 分歧买龙 / 龙回头）+ 未知 code + 合计，展示笔数、盈利笔数、胜率、平均每笔收益、累计贡献收益；胜率与顶部 KPI 同源。基于已落库的 `entry_reason_code` / `realized_return` 实时聚合，旧 run 也可用；累计贡献按 `Σ已实现盈亏 / 初始资金` 估算（忽略买入佣金）。
- **清仓后窗口指标**：「单票收益汇总」新增「清仓5日内最高盈利」「清仓5日内最大回撤」两列。窗口为卖出日之后的 5 根有效日K（停牌不计入、不足按实际、无 bar 显示 `—`），可越出回测区间（事后评估，不影响交易）；旧 run 不回溯，显示 `—`。最高盈利 =（窗口最高−卖出）/卖出×100，最大回撤 = min(0,（窗口最低−卖出）/卖出×100)。`GET /api/account/positions` 同时返回 `stats` 买点聚合（只增键）。

### `buy` / `sell` — 实盘辅助交易

```bash
dragon-quant buy --date 20260907 --at 10:00   # 历史日期回放当日分时（必须指定 --at）
dragon-quant buy                              # 实时：回放截至当前已完成5分钟K
dragon-quant buy --json                       # 完整 JSON（details[].checks 含四类买点诊断）
dragon-quant sell --date 20260908 --at 14:55  # 对已买入未卖出的信号判定卖出
dragon-quant buy --force                      # 午休/收盘后强制跑当日回放（09:30 前仍拒绝）
```

`buy`/`sell` 底层与 `review-account` 完全复用同一 `review_account` 策略与成交函数，只做信号记账、**不维护真实账户**：只要候选池标的触发买点就记一条买入（buy-all，不再择优取第一只）；`sell` 从信号表筛出「买入日之前且 as-of 未平仓」的信号，回放当日行情判定卖出。策略调参只需改 `review_account` + 回测，`buy/sell` 自动跟随。

- 与 `review-account` 共用 P/A 三值排序与否决；`buy` 输出 `ranking_mode`（`auction` / `auction_unverified` / `prep_only` / `legacy`）与 `fallback_reason`，被否决但本可买入的标的在 `details` 中带 `vetoed_signal`（含买点类型），便于影子对照。
- **四类买点诊断**：`buy` 对每个候选（买入与未买入）逐条输出 ① 开盘回踩MA5 / ② 弱转强 / ③ 分歧买龙 / ④ 龙回头 的状态与带数值原因；同一 code 只输出一次，取最后一条带 `checks` 的 detail。状态：`passed`（通过）、`failed`（未通过）、`pending`（待确认，09:30 的 ②③）、`window_closed`（窗口已过未通过）、`unavailable`（日K兜底/数据缺失）、`skipped`（情绪禁买/综合分不足/非真龙等前置闸门，不伪造失败）。决策与诊断共用 `review_account/strategy.py` 的 `_rule_*`，阈值只有一份。`buy --json` 的 `details[].checks` 每项含 `rule`/`label`/`window`/`status`/`selected`/`reason`，detail 另带 `observed_at`/`phase`；`checks` 只进 JSON，不写 `buy_sell_signals`。
- **龙回头默认开启**：`StrategyConfig.dragon_comeback_enabled=True`（`buy` / `review-account` 共享策略同时生效）；显式 `--config '{"dragon_comeback_enabled": false}'` 可复现旧基线。默认开启后历史回测结果不可直接与旧基线比较，历史快照不重算。
- `SignalEngine` 复刻 `TradingEngine.step` 的「决策 → 下一可执行事件成交」两段式与峰值跟踪，去掉资金/仓位/次数限制；卖出数量用 `notional_quantity`（`initial_cash × max_position_pct`，与回测首仓同口径）落库冻结，保证 `break_even_price` 与回测精确一致；半仓规则只减半不平仓，`half_close_signal` 冻结半仓腿后剩余半仓继续跟踪。
- 组合风控经 `live_trade/shadow.py` 从信号表按回放时点重建影子状态，逐事件调用同一个 `risk_governor`：日级熔断、连亏熔断与 `equity<=0` 破产闸跟随；delevel 降仓在无 sizing 场景不适用（详见 `review_account/STRATEGY.md` §5）。
- 数据分级：`buy`/`sell` 的行情完整性判定都覆盖「候选 ∪ 持仓」，任一分时缺失即整日走日K兜底，输出带 `data_quality` 提示。
- 历史回放必须显式指定 `--date YYYYMMDD --at HH:MM`，仅用历史数据；实时运行不指定 `--at`，回放截至当前已完成的5分钟K。
- `--force`（`buy`/`sell` 均支持）：放行午间休市（11:30–13:00）与已收盘（≥15:06）的时段闸门，**09:30 前仍拒绝**；15:00–15:06 属既有收盘宽限窗口（本来就不拦截），`--force` 在该窗口不生效、日K兜底仍按收盘价成交。不解除任何数据/日期校验（非交易日、历史缺 `--at`、无完整5分钟K 照常报错），与 `--at` 同给时 `--at` 优先。强制模式仍用最后一个已完成5分钟K回放（午休=11:30、收盘=15:00），不伪造收盘价成交；信号照常落库并在 `signal_json` 中带 `"forced": true`，人读输出打印一行强制模式提示。
- 信号表 `buy_sell_signals`：同一未平仓 code 幂等去重，峰值跨日写回供移动止盈/保本判定；历史重放可能命中既有行，输出标注「未落库」。
- `buy/sell` 仍不接券商。数据齐全、参数和事件一致时，两条路径的买点/卖点/成交价与回测一致，不代表真实市场必然成交。
- `buy` 输出首行显示**建议仓位**（盘后复盘快照 `sentiment.position_pct`，缺失显示 `—`），**仅供人工参考、不代下单**（`SignalEngine` 不维护真实账户）；该值另外用于禁买判定。


### `vpa` — 量价分析

```bash
dragon-quant vpa --code 600519 [--source xueqiu] [--days 60] [--no-save]
```

独立于评分体系的量价健康度验证模块，基于「多空博弈 + 量能验证」，内置 4 个插件式因子：量额灵敏度 / 趋势量价验证 / 突破放量验证 / 量价背离。输出健康度（0-100）+ 偏多/中性/偏空信号 + 判断依据，定位「验证器」而非买卖指令。

### `data` — 原子数据查询

```bash
dragon-quant data sector [--asc]                       # 行业板块涨/跌幅榜
dragon-quant data components --sector 881167           # 行业成分股（同花顺 6 位代码）
dragon-quant data kline --code 600172 [--days 20]      # 个股日 K
dragon-quant data minute --code 600172                 # 个股 1 分 K（分时）
dragon-quant data quote --code 600172                  # 实时行情
dragon-quant data batch-quote --codes 600172,000001    # 批量行情
dragon-quant data cookie-status                        # Cookie 状态
```

### `logs` / `storage` — 日志与数据管理

```bash
dragon-quant logs tail [-n 20]
dragon-quant logs --source v2 query [--date 20260513] [--category scorer:drive] [--level error] [--code 600172]
dragon-quant logs --source v2 summary
dragon-quant logs clear --days 7

dragon-quant storage status      # 存储状态
dragon-quant storage size        # 磁盘占用
dragon-quant storage clear --all # 清理全部
```

## Programmatic API

```python
import dragon_quant

result = dragon_quant.scan(top_n=5, candidates_n=5, workers=2)

# 返回 dict：
# {
#   "timestamp": "...", "elapsed_s": 38.2,
#   "sectors": {"up": [...], "down": [...]},
#   "ranking": [
#     {"code": "...", "name": "...", "concepts": [...], "board_count": 3,
#      "composite_score": 73.5,
#      "is_true_dragon": true, "rank": 1, "reject_reason": null,
#      "dimensions": {"drive": {...}, "leadership": {...}, "anti_drop": {...},
#                     "liquidity": {...}, "absorption": {...}}}
#   ],
#   "report_text": "..."
# }
```

原子数据查询：

```python
from dragon_quant.data import (
    get_sector_ranking, get_sector_components,
    get_kline, get_minute_kline, get_quote, batch_get_quotes,
    cookie_status, fetch_cookies,
)

sectors = get_sector_ranking(asc=False)        # 行业涨幅榜
stocks = get_sector_components("881167")       # 行业成分股
kline = get_kline("600172", days=30)
quote = get_quote("600172")
```

## 评分体系

### 五维「识别真龙」

| 维度 | 权重 | 门槛 | 衡量 |
|------|------|------|------|
| 带动性 | 30% | 40 | 封板最早 + 带动板块（脉冲-跟随因果检测）+ 板块共鸣 |
| 领涨性 | 25% | 40 | 连板最多 + 5日涨幅在板块内分位 |
| 抗跌性 | 15% | 35 | 大盘 + 板块**双基准**横盘稳住 + 率先起飞 |
| 流动性 | 20% | 35 | 换手充沛度 + 封板质量（封单/开板次数，一字不罚）|
| 资金承接 | 10% | — | 跨板块虹吸（出逃规模越大 + 拉升越高 → 分越高）|

聚合：四大特征任一低于门槛或执行异常即否决；资金承接不否决，无数据、无信号或异常均降级为 50 分。全部候选保留诊断综合分，但只有通过者按综合分降序、同分代码升序获得真龙排名（否决者 `rank=null`）。`raw_output.ranking` 和 `scan_stocks_v2` 保存全部明细；报告及 `dragons_v2` 只使用 Top N 真龙，不用否决票补位。无真龙仍保存扫描和原因，五日去重后保留原排名。

关键口径：稳定封板取最后一次开板后持续封至最后有效分钟的起点；带动与跟风按动作先后互斥判定，同步启动不判方向，相关性奖励只看首次触板前；抗跌性使用 **SH000001 上证指数**，反弹需基准和个股均实际回升，横盘不获反弹分；非涨停买一量不算封单，从未触板的完整分时稳定性为 0；资金承接以扫描日领跌 Top20 行业为对手盘，六根五分钟线不跨午休或隔夜，逐板块验证先跌后拉且间隔不超十分钟。阈值与降级分集中于 `scorers/registry.py`。

资金承接单事件按强度/广度/持续性 **60%/20%/20%** 评分，拉升满分参考为 **3%**、出逃规模（绝对均跌百分比×有效板块数）满分参考为 **10**。同交易时段的重叠窗口合并为独立事件，取消次数加分；事件分按三交易日半衰期向中性 50 衰减，再取衰减后最强的最多三个独立事件均值。报告展示原始窗口数、独立事件数、代表事件原始分和衰减结果；不调整维度总权重 10%，不新增否决条件。

成分股按板块采样（TDX 全量；THS 每维度最多 3 页），属于采样而非全市场覆盖；腾讯行情按代码排序分批获取，不再截断总量为 200 只。领涨性使用日 K 派生的连板与五日涨幅，带动/抗跌/流动性看当日，承接回看十日。分钟和盘口只能提供采样证据，不能证明逐笔封单或实际资金流向。

历史数据不会自动重算或清理；盘后需要重新取数评分时执行 `dragon-quant scan --force --no-cache`，旧扫描记录仍代表当时规则。

## 数据源

| 数据源 | 用途 | Cookie |
|---|---|---|
| 通达信 TDX（首选）| 板块排行 / 成分股 / K线 / 分时 / 五档行情 | 无需 |
| 同花顺（回退）| 行业/概念/地区板块排行 / 成分股 / 板块当日1分K / 历史5分K | 无需 |
| 雪球（回退）| 个股日 K / 当日 1 分 K | 需要 |
| 腾讯（回退）| 批量实时行情 + 收盘盘口（买一封单量）；宏观 quote 方法保留（当前规格未路由） | 无需 |
| Yahoo chart（宏观 best-effort）| 美债 ^TNX / 黄金 GC=F 日 K 回退（新指数不路由） | 无需（可能 429 限频） |

> 封单数据首选 TDX 五档盘口，回退腾讯 gtimg 收盘盘口（盘后仍保留收盘瞬间状态）。

## 目录结构

```
dragon_quant/
├── cli.py                # CLI 门面（解析树 + main() + 共享 helper）
├── cli_commands.py       # 各子命令 _cmd_* 处理函数
├── orchestrator.py       # 编排器（Phase A→F，固定五维评分）
├── data.py               # 原子数据查询 API
├── rate_limit.py         # 并发限流器
├── providers/            # 数据源适配（tdx/ths/xueqiu/tencent/macro/cookie）
├── scorers/           # 五维评分器 + registry + aggregator
├── vpa/                  # 量价分析（插件式因子）
├── cache/                # 内存+本地双缓存
├── logging/              # ScanLogger + ReportBuilder + query
├── storage/              # paths / db（SQLite 门面 + 领域模块）/ manager
├── utils/trading.py     # 交易日历工具
├── review.py             # 龙头回测
├── review_account/       # 账户级模拟交易回测（strategy/simulator/models/indicators/service）
├── live_trade/           # 实盘辅助交易 buy/sell（signal_engine/shadow/trader/service，复用 review_account 策略）
├── assets_sync/          # 数据资产云端同步（codec/models/credentials/doctor/backends）
├── web_ui/               # 回测 Web UI（Vite+React+TS / stdlib HTTPServer）
└── models/types.py      # 数据模型
```

## 设计原则

1. **Provider 抽象**：所有数据源实现 `StockProvider` 接口，评分器只依赖接口，可无缝切换/新增数据源。
2. **评分器是 cache 消费者**：统一签名 `score(code, cache, **kwargs) -> ScoreResult`，只读缓存不发请求；编排器 Phase A→D 预填，Phase E 打分。
3. **并发与限流**：`RateLimiter` 按 provider 串行排队 + 随机延迟，不同 provider 并发。
4. **结构化日志**：`ScanLogger` 全链路打点，支持按类别/级别/代码查询。
5. **历史兼容**：主流程固定写 `*_v2` 表；旧 `*_v1` 表保留显式查询能力，不参与新扫描。

## 持久化

SQLite 表分为三类：

- 当前主流程：`scans_v2` / `scan_stocks_v2` / `scan_logs_v2` / `dragons_v2`
- 历史旧表：`scans_v1` / `scan_stocks_v1` / `scan_logs_v1` / `dragons_v1`（仅显式 `--source v1` 查询）
- 共享表：`vpa_analysis` / `sector_blacklist`
- 账户级 review 表：`review_account_runs` / `review_account_snapshots` / `review_account_trades` / `review_account_positions` / `review_account_events`
- 实盘辅助买卖信号表：`buy_sell_signals`（`buy` / `sell` 命令使用，未平仓 code 唯一）
- 数据资产独立库 `assets.db`：`prep_assets` / `prep_snapshots`（盘后复盘原始资产/结果）、`auction_assets` / `auction_snapshots`（竞价原始资产/结果；采集与 `--recompute` 已接线）

运行时不创建旧无后缀 `scans` / `scan_stocks` / `scan_logs` / `dragons` 表；新扫描固定写 `source="v2"` 和 `*_v2` 表，以兼容已存在的 v2 历史数据。

`dragons_v2` 表关键字段：
- `source`：固定为 `v2`；旧 `dragons_v1` 仅用于历史记录。
- `version`：入库时的包版本号。
- review 字段：`buy_date` / `buy_price` / `max_return_5d` / `max_drawdown_5d` / `max_return_hold_days` / `review_status`，按 source 独立维护。

`scan_stocks_v2` 填充 `dim_liquidity` / `is_true_dragon` / `reject_reason` 等五维识别字段。

`review_account_*` 表用于账户级模拟交易：`runs` 保存 UI 记录名称、策略参数与汇总，`snapshots` 保存每日权益/现金/持仓快照，`trades` 保存交割单及买卖逻辑，`positions` 保存已平仓持仓的收益、退出原因与清仓后 5 日窗口指标（`post_exit_max_return` / `post_exit_max_drawdown` / `post_exit_window_days`；事后评估、不参与决策，旧记录为 NULL 不回溯），`events` 保存买入、卖出、持仓和空仓原因时间线。

`buy_sell_signals` 表用于实盘辅助交易（`buy` / `sell`）：记录买入（code/name/entry_date/entry_price/`qty`/买入原因与信号）与卖出（exit_date/exit_price/卖出原因/持有天数），半仓规则通过 `took_profit_half` + `half_exit_date/price/qty/reason_code/reason_text/signal_json` 冻结半仓腿（`status` 仍为 `open`），并保存最高浮盈/价（`highest_return`/`highest_price`）供跨日移动止盈/保本判定；未平仓 code 由部分唯一索引 `idx_buy_sell_open` 保证幂等去重。

### 数据资产独立库 `assets.db`

`prep` 与 `auction` 的数据落在**独立 SQLite 库** `assets.db`（与 `dragon.db` 同目录，随 `DQ_DATA_DIR` 覆盖），与可重算的扫描产物物理隔离——原始数据资产不可再生，避免被 `storage clear --all` 连带清理：

- **`prep_assets`**：盘后复盘原始数据资产，`UNIQUE(trade_date, asset_type)`。每日采集一次即永久留档（时间换历史），支持 `--recompute` 重放历史口径。资产类型：`dragons` / `ths_limit_up_pool` / `ths_block_top` / `market_breadth` / `fg_boards` / `fund_flow` / `cninfo_announcements` / `macro_global` / `collection_report`；`collection_report` 按条合并，语义为该日期资产的当前最佳状态（未执行的键沿用存量，失败补采不覆盖健康状态）。
- **`prep_snapshots`**：复盘计算结果，单列 `result_json`（`sentiment` / `main_boards` / `macro` / `candidates`（含 `facts`/`verdict`/`dimensions`/`ignore_reason`） / `dragon_source`），`UNIQUE(trade_date, source)`，`source` 固定写 `v2`；后续新增字段直接加 JSON key，无需改表。当前版本 `version=0.5`（三日窗口 + 日经/A50 宏观口径 + 逐只判定）；`dragon_source.window_days`：`0` 表示 dragons 资产不可用（血缘未知）、`1` 表示旧版单日口径重算、`3` 表示三日窗口。
- **`auction_assets`**：集合竞价原始资产（`auction` 命令），`items_json` 存 `time`/`price`/`matched`/`unmatched` 数组，`quote_json`（可空）存 `Quote` 行情快照（`prev_close` / `limit_down` / `float_market_cap` 等评分所需字段，非有限浮点清洗为 `null`；旧行为 `NULL`），`UNIQUE(trade_date, code)` 幂等覆盖；同日重采时 `items_json` 以本次为准，`quote_json` 若本次缺失则保留上次成功快照（`COALESCE`），避免瞬时行情故障抹掉不可再生上下文。`trade_date` 为竞价所属交易日（= 当日），因 TDX `get_auction` 无日期参数、仅返回当日数据，故只能逐日积累、无法事后回补。旧库首次**写入**时自动补 `quote_json` 列；读路径不执行列迁移（不 ALTER 既有表，缺列按 NULL 读取）。
- **`auction_snapshots`**：竞价强度计算结果，单列 `analysis_json` 存 `AuctionVerdict` 序列化结果（`version` / `code` / `is_weak` / `strength` / `direction` / `level` / `dims` / `reject_reason` / `unverified` / `rank`），`UNIQUE(trade_date, code, source)`。由 `auction` 采集与 `auction --recompute DATE` 写入（`source="v2"`）；同日重跑整日原子替换，空结果（无可用资产/全部评分失败）不会删除既有快照。写入者必须遵循该契约，不得另立字段。读接口为 `assets_db.list_auction_snapshots` / `get_auction_snapshot`（逐行 JSON 损坏隔离），供 `buy` / `review-account` 的 A 维度消费。

`storage status` 会展示 `dragon.db` / `assets.db` 的存在性与体积；旧 `prep.db` 若仍存在，会以 `legacy_prep_db` 单独标注（不再读写，确认后可删除）。`storage clear --all` 只清理 cache/results/logs/shared，数据库文件列入 `protected_databases`，永不删除。

#### 从旧库迁移（可选，代码不自动执行）

存储层重构不自动迁移历史数据。旧 `prep.db` 与 dragon.db 的遗留 `auction_snapshots` 表保留但不再读写：

- **首次运行新版本前**：`mv "<DQ_DATA_DIR>/prep.db" "<DQ_DATA_DIR>/assets.db"`（如有 `-wal`/`-shm` 一并移动），即可保留历史复盘资产与快照。
- **执行迁移 SQL 前先建表**：`assets.db` 的 4 张表在首次读写时惰性创建。刚完成 `mv` 或新建库后，先运行一次会写库的命令，例如 `python -m dragon_quant prep --recompute --date <已存日期>`（无旧资产时用盘后 `python -m dragon_quant prep`，或当日 9:25 后 `python -m dragon_quant auction`）。旧库缺 `auction_assets.quote_json` 时，首次 `auction` **写入**会自动补列（读路径不执行列迁移）；`auction --recompute` 只读资产，不会补列。
- **导入竞价原始明细**（幂等；旧表无行情快照，`quote_json` 留 `NULL`）：
```bash
sqlite3 "<DQ_DATA_DIR>/assets.db"
```
```sql
ATTACH DATABASE '<DQ_DATA_DIR>/dragon.db' AS legacy;
INSERT OR IGNORE INTO auction_assets(trade_date, code, name, items_json)
SELECT trade_date, code, name, items_json FROM legacy.auction_snapshots;
DETACH DATABASE legacy;
```
  旧 `prep.db` 同理，可用 `ATTACH` + `INSERT OR IGNORE` 将 `prep_assets` / `prep_snapshots` 拷入 `assets.db`。旧竞价资产可先补跑一次 `auction` 采集（当日）获得 `quote_json`，或用 `--recompute` 接受行情缺失降级。
- **未迁移时**：历史日 `prep --recompute` 会报「无可重算资产」；`auction --recompute` 会报「无竞价原始资产」。review-account 候选池回退「前 3 日真龙并集」、情绪仓位降级（既有回退路径）。

## 免责声明

本工具仅供学习交流，不提供任何个股买卖建议，开发者不承担任何个股买卖亏损。评分、回测与实盘辅助交易的所有输出均为技术研究结果，不构成投资建议；据此操作产生的盈亏由使用者自行承担。

## License

MIT
