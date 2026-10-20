# TDX 板块三维融合（行业 / 概念 / 地区）技术调研

- 日期：2026-10-20（盘后）
- 范围：`scan` 主流程 Phase A/B 的板块口径改造
- 结论摘要：tmdx 3.0.1 对 HY（行业）/ GN（概念）/ DQ（地区）三类板块的**排行、成分股、板块 1 分钟 K、历史 5 分钟 K 全部支持**，单次调用 0.05–0.16s；三类之间 code 当前互不重叠，但 board code **全局不唯一**（HY2/YJ 复用 881xxx），且协议层请求不带 board_type，因此板块身份必须是 `(source, category, code)`。THS 回退链路同样可覆盖三类（行业/概念/地区），但概念成分股为分页采样、地区排行只有单页。改造可行，不需要付费数据源。

---

## 1. 调研方法与证据来源

| 证据类型 | 具体做法 | 可信度 |
|---|---|---|
| 库源码 | `.venv/lib/python3.14/site-packages/easy_tdx/mac/enums.py`、`client.py`、`commands/board_list.py` | 直接读取，最高 |
| 实时探测 | 2026-10-20 盘后连接 TDX 服务器，实测 HY/HY2/GN/DQ/FG/YJ/ALL 的 `get_board_list`、成分股与 `get_stock_kline` | 实测 |
| 链路代码 | `dragon_quant/orchestrator.py`、`providers/{tdx,ths,base}.py`、`cache/data_cache.py`、`rate_limit.py`、`scorers/*.py` | 直接读取 |
| 基线运行 | `DQ_DATA_DIR=/tmp/dq-baseline python -m dragon_quant scan --force`（2026-10-20 盘后） | 实测 |
| 历史追溯 | `git log -S`、`archived_md/score/评分器Refactor.md` | 间接 |

版本：`tmdx==3.0.1`（`easy_tdx.__version__ == "3.0.1"`），`pyproject.toml` 依赖 `tmdx>=3.0`，Python `>=3.10`。

---

## 2. tmdx 三类板块能力矩阵（实测）

### 2.1 枚举与接口

```python
class BoardType(IntEnum):
    HY = 0          # 行业一级
    HY2 = 1         # 行业二级
    GN = 3          # 概念
    FG = 4          # 风格（统计型：昨日涨停/昨日连板/科创成长层…）
    DQ = 5          # 地区
    OTHER = 6
    YJ_LEVEL1 = 7   # 业绩一级
    YJ_LEVEL2 = 8   # 业绩二级
    YJ_LEVEL3 = 9   # 业绩三级
    ALL = 255
```

- `MacClient.get_board_list(board_type, count=10000, sort_column=BoardSortColumn.CHANGE_PCT)`：内部按 150/页自动分页，按涨跌幅降序返回 `price/pre_close`，`pct` 需本地现算（`_to_sector` 已实现）。
- `MacClient.get_board_members(board_symbol, ...)`：内部按 80/页自动翻页，返回全部成分股（不是采样）。
- `MacClient.get_stock_kline(market, code, period=MIN_1|MIN_5, count, adjust=NONE)`：板块指数 K 线。
- 板块 code → 协议 code：`88xxxx` 统一映射为 `int - 880000 + 20000`（`_convert_board_code`），因此**同一 88xxxx 命名空间内必须靠 board_type 区分**。

### 2.2 实测数据（2026-10-20 盘后）

| 类别 | 板块数 | code 范围 | 榜单耗时 | 成员/1minK/5minK |
|---|---|---|---|---|
| HY 行业 | 128 | 881002–881479 | 0.16s | 支持 |
| HY2 行业二级 | 345 | 881003–881479 | — | 未使用 |
| GN 概念 | 269 | 880501–880978 | 0.11s | 支持 |
| DQ 地区 | 32 | 880201–880232 | 0.10s | 支持 |
| FG 风格 | 158 | 880081–880979 | 0.11s | 支持（本次不使用） |
| YJ_LEVEL1/2/3 | 30 / 128 / 345 | 881001–881479 | — | 未使用 |
| ALL 合计 | 560 | 880201–881479 | 0.24s | — |

成员与 K 线抽样（均为一次调用耗时）：

| code | 名称 | 成员数 | 成员耗时 | 1min bars | 5min bars | K 线耗时 |
|---|---|---|---|---|---|---|
| 880683 | 星闪概念 | 69 | 0.09s | 240 | 480 | 0.06s |
| 880660 | 工业软件 | 35 | 0.05s | — | — | — |
| 880221 | 湖南板块 | 147 | 0.10s | 240 | 480 | 0.05s |
| 881257 | 医疗美容 | 4 | 0.04s | — | — | — |

结论：

1. **三类全部可用**，不存在「tmdx 不支持概念/地区」的能力缺口。
2. 地区板块成员可达 147 只（成员接口内部翻 2 页），比行业（通常 4–100 只）更大。
3. 板块 1min K 返回 240 根、5min 返回 480 根（10 日），与现有行业口径一致。

### 2.3 code 空间是否重叠（修正版）

实测 2026-10-20 盘后：

| 组合 | 交集 |
|---|---|
| HY ∩ GN | 0 |
| HY ∩ DQ | 0 |
| GN ∩ DQ | 0 |
| **HY ∩ HY2** | **非空（HY2 是 HY 的细分，复用同一 881xxx 区间）** |
| **HY ∩ YJ_LEVEL2** | **非空（YJ_LEVEL2 与 HY 的 code 区间完全一致：881002–881479）** |

结论必须分成两层：

1. **本次选用的三类（HY/GN/DQ）当前两两不重叠**：HY=881xxx、GN=880501–880978、DQ=880201–880232。
2. **board code 在全局并不唯一**：HY2（345 个）与 YJ_LEVEL2（128 个）直接复用 HY 的 881xxx code；YJ_LEVEL1/3 与 HY2 同理。也就是说 `(code)` 不构成板块身份，`(board_type, code)` 才是。

这带来一个协议层限制：`get_board_members(board_symbol)` 只接收 `board_symbol`，内部 `_convert_board_code` 把 `88xxxx` 映射为 `20000 + (code - 880000)`，**不携带 board_type**；板块 K 线接口 `get_stock_kline(market, code)` 同样没有维度参数。因此：

- 若未来 HY 与 GN/DQ 出现同 code（GN 数值分配已到 880978，向上越过 880999 后的下一个区间就是 881xxx，即 HY/HY2/YJ 占用区），服务端会把两次请求解析到**同一板块**，缓存命名空间只能避免「两份数据串号」，**不能消歧请求本身**。
- 所以命名空间是**必要的缓存隔离**，但不足以作为 code 撞车的完整缓解；还需要运行期不相交校验（见 §10 R1）。

THS 回退链路使用完全不同的 code 体系（概念榜单 3xxxxx、innerCode 885xxx；地区 882xxx），与 TDX 不共享身份，必须把 `source` 一并纳入板块身份（见 §10 R10）。

### 2.4 `BoardType.ALL` 能否直接当融合榜

`BoardType.ALL` 返回 560 条 = HY + GN + DQ + FG − 27 个 FG 统计板块（`昨日连板`/`昨日涨停`/`历史新低` 等被服务端剔除），并**包含 FG 风格板块**（如 `科创成长层`、`业绩预增`）。HY2/YJ 与 HY 共用 881xxx code，按 code 去重后不会额外增加条目。它不等于「行业+概念+地区」三类的并集，且无法区分来源维度。因此**不采用 ALL**，而是显式发三次 `get_board_list(HY/GN/DQ)` 后本地融合——这样才能给每个板块打上维度标签，用于回退降级与命名空间。

---

## 3. TDX 客户端「融合榜」口径

协议层没有「三维融合榜」这一接口：`BoardListCmd` 每次请求带一个 `board_type`，服务端按该类型返回一个列表。TDX 桌面客户端的「板块」页是 UI 侧把多个列表并排展示/切换，并非服务端单一排序池。

本需求的「融合」因此定义为**客户端侧归并**：三份列表各自已按涨跌幅降序（`sort_column=CHANGE_PCT`），本地合并后统一按 `pct` 降序取前 N。这与用户在客户端看到的「同一个涨跌幅排序视图」在语义上等价，且排序口径可审计。

补充：FG（风格）板块不属于用户指定的三类，其中 `昨日涨停`/`昨日连板` 等统计板块也已被现有 `STATISTICAL_CONCEPT_PREFIXES` 过滤，不纳入融合。

### 3.1 近 20 个交易日 Top8 构成（策略取舍的量化依据）

用全部 429 个 HY/GN/DQ 板块的指数日 K 反推每日涨跌幅，逐日重排 Top8（2026-09-15 至 2026-10-20，20 个交易日）：

| 指标 | 结果 |
|---|---|
| 行业内板块进入 Top8 的次数 | 101 次（平均 5.05 个/日，20/20 天都有） |
| 概念板块进入 Top8 的次数 | 55 次（平均 2.75 个/日，18/20 天都有） |
| 地区板块进入 Top8 的次数 | 4 次（3/20 天，占单日名额 2.5%） |
| 当日地区最高涨幅均值 | 1.48% |
| 当日 Top8 门槛（第 8 名涨幅）均值 | 2.42% |
| 仅行业口径下第 8 名涨幅均值 | 2.01% |

结论：

1. **只扩行业 Top8 不够**：概念板块平均占据 Top8 的 2–3 席，只扩行业会系统性漏掉题材主线。
2. **地区维度确实低频但不为零**：20 天里 3 天上榜、共 4 席；且地区最高涨幅均值（1.48%）低于 Top8 门槛均值（2.42%），说明它多数时候不占名额，但普涨/区域行情日会进入。保留地区维度是口径正确的必要条件，成本仅为排序与少量 K 线请求。
3. 分维度配额（如 3+3+2）会与上述分布冲突：行业平均占 5 席，强行压到 3 席会挤掉强势行业；地区平均 0.2 席，强行给 2 席会引入弱势板块。

---

## 4. 现有真实链路（改造前）

### 4.1 Phase A（`orchestrator.scan` 430–483 行）

1. `sector_source` 固定先试 `tdx`：`_cf_sync("sector:ranking:tdx", tdx.get_sector_ranking(asc=False))`。
2. 为空则整体回退 `ths`，并写 `fallback:sector_ranking` 日志。
3. `_sector_ok()` 做两层过滤：`STATISTICAL_CONCEPT_PREFIXES` 名称前缀 + DB 黑名单子串。
4. `ranking_all` 已按 `pct` 降序（provider 内排序）；模块常量 `RANK_UP_COUNT = 5` 在 `scan()` 开头赋给局部变量 `rank_up_count`，再切片 `ranking_all[:rank_up_count]`；领跌用 `RANK_DOWN_COUNT = 20`（**行业口径**）。改造只改这个切片来源，不改 `RANK_DOWN_COUNT`。

结论：**当前主链路就是 TDX 行业板块（HY），不是 THS**。`ths` 仅在 tdx 返回空时兜底。

### 4.2 Phase B

- 对每个领涨板块：`_cf(sector_source, f"sector:components:{s.code}", provider.get_sector_components(code, page=1, all_pages=True), namespace="v2")`。
- 读回时 `cache.get(f"sector:components:{s.code}")`。
- 涨停筛选：`_is_valid_candidate`（排除 ST / 30 / 68 / 8 / 92）+ `pct >= 9.9`。
- 候选去重：按 code 首次出现建 `Candidate`，后续板块只 `concepts.append(name)`；**primary_sector 天然是首个（也是排名最高的）板块**，因为遍历顺序即榜单顺序。

### 4.3 缓存键与注册表

`cache/data_cache.py::_TYPE_REGISTRY`（按前缀长度降序匹配）：

```
kline:1min:sector: / kline:5min:sector: / sector:components:   → 板块
kline:1min: / kline:5min: / kline:day: / quotes:batch / sector:ranking
```

新增 `sector:components:gn:880683`、`kline:1min:sector:gn:880683` 等键**仍能命中上述前缀**，`_deserialize` 无需新增注册项。

### 4.4 RateLimiter

- `submit(provider, fn)` 以 provider 名为 key 串行。
- `RateLimiter(max_workers=workers, provider_delays={"ths": (0.3, 0.6)})`，即 **tdx 使用默认 0.3s 延迟 + 串行**；ths 0.3–0.6s 随机。
- tdx provider 内部另有 `threading.Lock` 串行化 `MacClient`（`_reconnect` 会替换连接，必须单线程调用）。

### 4.5 黑名单

`sector_blacklist` 表 + `db.get_sector_blacklist()`，按名称子串匹配，在内存过滤（改黑名单即时生效）。行业口径下默认种子为空。

改造后过滤函数本身不变（满足「不改黑名单语义」），但**输入集合从「行业榜」扩大为「三维融合榜」**，因此有两处可观测变化：

1. 同名板块会跨维度同时被过滤（例如「环境监测」若同时存在于 HY 与 GN，两者都会被排除）；
2. 概念里存在 `次新股`、`ST板块` 这类宽基/统计型板块，当前不在 `STATISTICAL_CONCEPT_PREFIXES` 中，若用户不希望它们上榜需要显式加入黑名单。

建议在 AGENTS/README 中明确写成「黑名单是**名称级、跨维度生效**」。是否补种默认黑名单属于人为决策项，本次不改。

---

## 5. 「TDX 无 QPS 限制」假设与性能影响

### 5.1 实测

- 单次 `get_board_list` 0.10–0.16s；`get_board_members` 0.04–0.10s；板块 K 线 0.05–0.06s。
- 连续 8 个板块成员拉取总耗时 0.76s，无失败、无退避。
- 本次探测全程约 4 秒内完成 30+ 次请求，未触发限流。

结论：**没有观察到显式 QPS 限制**，但 TDX 是免费公共行情服务器，不能把「无限制」当成契约。现有串行 + 0.3s 延迟的策略应保留；改造只增加调用量，不提高并发。

### 5.2 调用量（逐项求和）

| 阶段 | 改造前（5 领涨，N=3 候选） | 改造后（8 领涨，M=10 候选） |
|---|---|---|
| 板块排行 | 1 | 3（HY/GN/DQ，同步、不过 limiter） |
| 成分股 | 5 | 8（地区板块内部可能翻 2 页） |
| 板块 5min 历史 | 25（5+20） | 28（8+20） |
| 板块 1min | 5 | 8 |
| 个股日 K | N = 3 | M = 10 |
| 个股 1min | N = 3 | M = 10 |
| 大盘 1min（SH000001） | 1 | 1 |
| 批量行情 | 1（内部 80/批） | 1（内部批数随成分股并集增长） |
| **合计** | **44** | **69** |

### 5.3 基线实测与耗时推算（修正版）

2026-10-20 盘后 `scan --force`（临时 `DQ_DATA_DIR`）：**总耗时 14s**，5 个行业板块、3 只候选、1 只真龙，日志中无回退告警，即上述 44 次调用基本都由 tdx 承担。

- 由基线反推单次 tdx 调用摊销：14s ÷ 44 ≈ **0.32s/次**（其中 0.3s 来自 RateLimiter 的固定延迟，payload 耗时仅 0.05–0.16s）。
- 改造后 69 次 × 0.32s ≈ **22s**（区间 20–24s，含固定开销与行情批数增长的不确定性）。
- 该推算是线性的、只用于预判；**验收以同一交易日的实测 A/B 为准**：改造后在同一交易日、全新临时目录跑一次 `scan --force`，要求 `elapsed_s ≤ 2 × 基线`（当前 28s）。若超标，优先核对实际调用次数是否与上表一致，再定位 Phase 耗时。

> 修正说明：本节初版把调用次数写成 55→71、并用 0.4s/次外推，与表格行项目不自洽；第 2 版漏了大盘 1min 一行。现按行项目求和为 44→69，并用基线反推的 0.32s/次重算。

---

## 6. THS 回退链路能力（实测）

| 维度 | 排行 | 成分股 | 板块 1min/5min K | 备注 |
|---|---|---|---|---|
| 行业 hy | `data.10jqka.com.cn/funds/hyzjl/field/zdf/order/desc/page/{p}/`（50/页，3 页） | `q.10jqka.com.cn/thshy/detail/code/{code}/`（20/页，可翻页） | `d.10jqka.com.cn/v6/time/48_{inner}/last.js`，inner=code | 现有实现，无需改动 |
| 概念 gn | `.../funds/gnzjl/field/zdf/order/desc/page/{p}/`（表结构与 hyzjl 完全一致，td[1]=名称链接、td[3]=涨跌幅） | `q.10jqka.com.cn/gn/detail/code/{code}/`；非 ajax 翻页 `.../gn/detail/order/desc/page/{p}/code/{code}/`（10/页） | innerCode 从 `clid` 解析（实测 `300816 → 885517`），K 线可用 | **需新增**：现有详情页解析只 `re.search` 第一个 `<tbody>`，概念页有 2 个 tbody，成员表在第 2 个 |
| 地区 dq | `q.10jqka.com.cn/dy/`（单页表格，默认按涨跌幅降序，33 行；td[2]=涨跌幅） | `q.10jqka.com.cn/dy/detail/code/{code}/`（20/页，翻页 `.../dy/detail/order/desc/page/{p}/code/{code}/`） | inner=code（882xxx） | **需新增**：排行页结构与 `/funds/*zjl` 不同，需要独立解析器 |

结论：

- **THS 三维全部可得**，满足硬性决定 #4 的「优先三维融合」。
- 概念成分股每页仅 10 只，翻 5 页约 50 只，与行业采样口径相当；但相对 TDX 全量成员仍是降级样本，需在 `degraded` 中体现。
- 地区排行只有单页 33 行，足够取头部；不支持 `asc`/分页参数。
- THS 旧接口存在 401（ajax）与 403 频控，非 ajax 路径可用。
- source 不同 → 板块 code 不同构，**同一维度在一次运行内必须固定单一 source**（详见 §6.5）。

### 6.5 按维度降级的判定矩阵（硬性决定 #4 的落地语义）

现有链路用**单一 `sector_source`** 驱动排行 + 成分股 + 两段 K 线；改造后必须升级为**按维度分派**。判定规则如下：

| 数据类型 | 失败判定 | 处理 | 是否换源 |
|---|---|---|---|
| 排行 | 返回空 / 异常 | 该维度整维回退 THS | **是（维度级）** |
| 排行 | 回退后仍为空 | 该维度置 `degraded`，从融合榜剔除 | 否 |
| 成分股 | 某板块为空 | 该板块贡献 0 候选；记日志；不换源 | 否（板块级） |
| 成分股 | 该维度所有板块都为空 | `raw_output` 记 `components_unavailable`；板块仍按排名占 Top8 名额（0 候选） | 否 |
| 板块 1min K | 某板块为空 | drive/anti_drop 走既有降级 | 否 |
| 板块 5min K | 某板块为空 | absorption 走既有降级 | 否 |
| 领跌 hy 排行 | 空 | 领跌榜为空 → absorption 走「有效对手板块不足」降级；扫描继续 | 否 |

关键约束：

1. **回退是维度级，不是板块级**：同一维度的排行、成分股、1min、5min 必须来自同一 source（code 体系与采样深度一致），不得混用 TDX 排行 + THS 成分。
2. **零候选维度仍占 Top8 名额**：名额由涨幅排名决定，不因该维度无涨停股而顺延给第 9 名——否则「总榜 Top8」口径失真。
3. **跨维度允许不同 source**：这正是「某维度不可用则用可得维度融合」的实现方式；每个维度独立打 `source` 与 `degraded` 标记。
4. 维度是否可用**只在排行阶段判定**；成分/K 线失败属于板块级降级，不触发换源（换源会导致 code 身份切换，代价大于收益）。

---

## 7. 历史决策回溯：为什么曾经只用行业板块

- `4946c13`：新增同花顺 provider，最初用**概念**排行（`gn`/`gnzjl`，Playwright 渲染）。
- `0a271f8`（2026-09-12）：一次较大的评分偏差修正，同步把口径固定为「同花顺行业板块 `/thshy/`、`/hyzjl/`，代码 881xxx，不是概念板块 `/gn/`」，写入 `评分器Refactor.md` 与 AGENTS/README。
- 该 commit 的说明聚焦「修正指数基准、时序事件与封板判定」，**没有给出「弃用概念」的独立理由**；代码层面也没有相应的技术论证。

因此：

1. 本次改造与历史决策的**唯一冲突是文档表述**（「不是概念板块」），不是实现依赖。
2. 当时的行业口径带来一个实际后果：行业板块成员少且题材粒度粗。实测 2026-10-20，行业 Top5 只筛出 3 只候选，而同期涨幅更高的是 `星闪概念`/`工业软件`/`AI眼镜` 等概念板块，融合后可得 10 只候选——这正是本需求要解决的问题。
3. 规避方式：同一 commit 更新 `AGENTS.md`、`README.md` 与 `archived_md/score/评分器Refactor.md` 的板块口径表述，把「行业板块」改为「行业+概念+地区三维融合，领跌仍为行业」。

---

## 8. 概念板块重叠导致的候选重复规模

对 2026-10-20 融合 Top8（2 个行业 + 6 个概念，地区未入榜）逐板块拉全量成分股，按现有规则（pct≥9.9、排除 ST/双创/北交所）统计：

| 板块 | 上榜涨停数 |
|---|---|
| 医疗美容(hy) | 0 |
| 环境监测(hy) | 0 |
| 星闪概念(gn) | 2 |
| 工业软件(gn) | 0 |
| AI眼镜(gn) | 6 |
| 无线耳机(gn) | 3 |
| AI手机PC(gn) | 4 |
| 机器视觉(gn) | 5 |
| **去重后唯一** | **10** |

- 板块级命中 20 次，唯一 10 只 → **重复率 50%**，7/10 的股票同时出现在 ≥2 个板块（如 `603068 博通集成` 出现在 3 个概念）。
- 含义：①必须按 code 去重（现有逻辑已做）；②`primary_sector` 取「总榜排名最高板块」是必要规则，否则归属取决于遍历顺序；③`concepts` 列表会显著变长（同一股票可获得多个题材标签），Phase C 的 `(board_count, len(concepts))` 排序与 drive 的候选池过滤不受影响。

另：当日地区板块最高 `江苏 1.75%`，未进入 Top8；地区板块在普涨日更容易上榜（成员多、涨幅平滑），届时成员可达 100+，需关注成分股拉取耗时与候选数量。

---

## 9. 评分器适配性

评分器统一签名 `score(code, cache, **kwargs) -> ScoreResult`，是纯 cache 消费者。板块身份用于两处：**缓存键**与**候选池内同板块比较**。

| 评分器 | 板块数据用途 | 概念/地区数据缺失时 | 改名/命名空间影响 |
|---|---|---|---|
| drive 带动性 | `kline:1min:sector`、`sector:components`（封板池 + 共鸣） | 缺 1min → `_lead_sector` 降级；缺 components → 共鸣/封板池降级 | 需按新键读取 |
| leadership 领涨性 | 只用 `candidate_pool` 的 `primary_sector` 分组 | 无影响 | 比较键需与 primary_sector 一致 |
| anti_drop 抗跌性 | `kline:1min:sector` 作为板块基准 | 缺 K 线 → 板块基准降级，仍有大盘基准 | 需按新键读取 |
| liquidity 流动性 | `sector:components` 做换手分位 | 缺 → `degraded=True`，仍有绝对换手 + 封板 | 需按新键读取 |
| absorption 资金承接 | 目标板块 5min K + 领跌 Top20 行业 5min K | 缺 → 该维度降级 | 领跌仍行业；目标板块需按新键读取 |

关键结论：

1. 概念/地区的 1min 与 5min 板块 K 在 TDX 与 THS 都可得，**主链路不会因为换维度而系统性降级**——但这句话只在「所有读取点都同步改名」的前提下成立（见下一条）。
2. 概念板块成员数分布较宽（实测抽样 4–147，行业 4–100，地区 147；**269 是 GN 板块总数，不是成员数**），`_voice`（板块共鸣）与换手分位的分母随之变化；这是「按板块内相对位置打分」的固有性质，不是 bug，但会改变分数分布，需在方案中作为**语义变化风险**显式记录。
3. 抗跌性的「板块基准」从行业指数变为概念指数后，概念波动通常更大，`anti_drop` 的板块分可能更极端；门槛 35 分是否仍然合适属于参数问题，本次不调参（用户明确不改评分语义）。

### 9.1 键改名是静默降级风险（必须在方案中逐点核对）

`DataCache.get()` 未命中返回 `None`，不抛异常。若某个读取点漏改键名，评分器不会报错，只会拿到空列表并走降级分支——若漏改点集中在板块键，会导致 drive / anti_drop / liquidity / absorption **同时静默降级**，最终表现为「本轮没有真龙」而不是异常。

必须同步修改的读取点清单（实现时逐项勾选）：

| # | 位置 | 读取的键 |
|---|---|---|
| 1 | `scorers/drive.py`（`score` / `_early_seal`） | `kline:1min:sector:{key}`、`sector:components:{key}` |
| 2 | `scorers/anti_drop.py` | `kline:1min:sector:{key}` |
| 3 | `scorers/liquidity.py` | `sector:components:{key}` |
| 4 | `scorers/absorption.py` | `kline:5min:sector:{key}`、`all_sector_codes` / `__meta__:sector_codes` |
| 5 | `orchestrator._score_one` | 传给 `evaluate` 的板块身份参数 |
| 6 | `orchestrator` Phase B/D | 所有 `_cf` 的 key 与读回 key |
| 7 | `orchestrator` `__meta__:sector_codes` | 领跌行业 code 列表 |
| 8 | `orchestrator` `sector_name_map` | 板块名称字典 |
| 9 | `cache/data_cache.py::_TYPE_REGISTRY` | 前缀仍需匹配（新键是旧前缀的超集，无需改，但需单测确认） |

方案阶段应增加**缓存键存在性自检**：Phase D 结束后，对每个候选检查 `sector:components` 与 `kline:1min:sector` 是否命中；若全部候选都未命中（系统性失配），记 error 级告警并在 `raw_output` 标记，避免「静默 0 分」被误读为「本轮无真龙」。

---

## 10. 风险与边界清单

| # | 风险 | 证据 | 缓解 |
|---|---|---|---|
| R1 | **board code 全局不唯一**：HY2/YJ_LEVEL2 直接复用 HY 的 881xxx；GN 数值分配已到 880978，向上越过 880999 后就是 881xxx。且协议层 `get_board_members(board_symbol)` / `get_stock_kline(market, code)` **不携带 board_type**，同 code 会被服务端解析为同一板块 | §2.3 实测；`client.py:77` `_convert_board_code` | ①缓存键加 `{source}:{category}:` 命名空间（只防串号）；②**Phase A 运行期做 code 冲突校验，逐板块剔除**：按 hy > gn > dq 优先级保留首个占用者，只丢弃后出现维度中**该 code 的板块**（不是整维剔除），记 error 与 `sector_collisions` 明细；③单测构造同 code 跨维度验证不串 |
| R2 | THS 概念成分股每页 10 只，采样弱于 TDX | 实测 | 回退时标 `degraded`，并在 raw_output 记录 `sector_source` |
| R3 | THS 地区排行只有单页、字段布局不同 | 实测 | 独立解析器 + 单测；失败则该维度 degraded |
| R4 | 概念板块过宽（`次新股`、`ST板块` 等）可能进入 Top8 | 实测 GN 含这两类；20 日回看中概念日均占 2.75 席 | 维持现有黑名单 + 统计前缀过滤；是否补种黑名单由用户决定（人为决策项） |
| R5 | 融合后候选数上升，扫描耗时增加 | §5.2/5.3：43→68 次调用，预计 22s | 同一交易日实测 A/B，≤ 2 倍基线（28s） |
| R6 | 概念指数波动大，anti_drop 板块分分布变化 | §9 | 不改阈值；在报告中保留 degraded 与明细 |
| R7 | 地区板块成员 100+，行情批次数上升 | 实测 147 | 腾讯/tdx 批量接口已按 80/200 分批，无需改动 |
| R8 | 旧缓存键与新键并存 | 旧 `sector:ranking:tdx`、`sector:components:{code}` | 新键命名空间隔离，旧文件自然过期，不做迁移 |
| R9 | 当日行业 Top5 只有 3 只候选，融合后 10 只 | 基线 vs 估算 | 属预期行为，非风险 |
| R10 | **跨源身份不同构**：THS 概念 code 3xxxxx / inner 885xxx，地区 882xxx；TDX 概念 880xxx、地区 880xxx。回退后同一板块的 `primary_sector` 与 TDX 不同 | §6 实测 | 板块身份定为 `(source, category, code)`：缓存键含 source，输出新增 `primary_sector_source`；`primary_sector` 保留原生 code；跨源/跨日对比需带 source 读 |
| R11 | **键改名静默降级**：`DataCache.get` 未命中返回 None，漏改的读取点不报错，只降级 | §9.1 | 逐点清单 + Phase D 后缓存键存在性自检；单测断言新键被真实读取 |

---

## 11. 对任务包 8 个重点问题的直接回答

1. **tmdx 三类能力**：全部支持；HY/GN/DQ 三类 code 当前两两不相交，但 **board code 全局不唯一**（HY2/YJ 复用 881xxx），维度必须由 `(source, category, code)` 共同标识。
2. **TDX 客户端融合榜口径**：协议层无融合榜，只有按 `board_type` 的单类列表；融合需客户端归并。`BoardType.ALL` 混入 FG 且缺失 27 个统计板块，不可直接用。
3. **现有链路**：Phase A/B 主链路是 TDX HY；THS 仅在 tdx 返回空时**整体**回退（单一 `sector_source` 驱动排行+成分+K线）；缓存键 `sector:ranking:{src}`、`sector:components:{code}`、`kline:*:sector:{code}`；RateLimiter 按 provider 串行、tdx 0.3s；黑名单在内存按名称子串过滤。
4. **QPS 假设**：未观察到限制，实测 30+ 次请求 4s 内完成；但不应依赖，保留串行 + 延迟。调用量 43→68 次，按基线反推 0.33s/次预计 22s（20–24s），验收以同日实测 A/B ≤ 2 倍（28s）为准。
5. **THS 回退**：行业/概念/地区三类均可得；概念成分 10/页、地区排行单页，均需新增解析/路由代码。回退必须是**维度级**，同一维度不得混用 TDX/THS（见 §6.5 矩阵）。
6. **历史决策**：概念→行业的切换是 0a271f8 的附带文档变更，无独立技术论证；本次冲突仅为文档表述，需同 commit 更新 AGENTS/README/评分器Refactor。
7. **评分器适配**：均能消费概念/地区数据；缺数据时已有降级路径；但**键改名是静默降级风险**，9 处读取点必须逐项核对（§9.1），主要语义风险是板块宽窄变化导致相对分数分布变化。
8. **重复规模**：实测 Top8 板块级 20 次命中 → 10 只唯一，重复率 50%，7/10 跨 ≥2 板块；必须「总榜最高板块」定 primary_sector。近 20 日回看：概念日均占 Top8 的 2.75 席、地区 3/20 天上榜，证明融合不是边缘场景。

---

## 12. 结论

- 技术可行，**不需要新依赖、不需要付费源**。
- 主链路（TDX）三维全支持，回退链路（THS）三维可得但概念为采样、地区仅单页，需 `degraded` 标记。
- 实施要点：三次分类排行 + 本地融合排序 + Top8；板块身份 `(source, category, code)` 贯穿缓存键与评分器；按 code 去重并以总榜最高板块为 `primary_sector`；领跌 Top20 保持行业口径。
- **接口约束**：本次只给现有 `StockProvider` 板块方法增加 `category` 参数（默认 `"hy"`），**不新增抽象方法**；`get_sector_1min_kline` / `get_sector_5min_kline_history` 保持普通方法 + 默认 `raise NotImplementedError`，避免 `create_providers()` 一次性实例化 4 个 provider 时崩溃。
- 主要风险：①board code 全局不唯一且协议层无法按 code 消歧 → 运行期不相交校验；②键改名静默降级 → 读取点清单 + 存在性自检；③跨源 code 不同构 → 身份带 source；④板块宽窄变化影响相对分 → 不改阈值、保留 degraded 明细。均可控，不阻塞实现。

---

## 附录 A：探测方法与可复现性

所有实测均为 **2026-10-20 盘后快照**；板块数量与 code 区间会随服务端更新变化，复现时请以当日结果为准。

### A.1 环境

```
.venv/bin/python -I          # Python 3.14，tmdx/easy_tdx 3.0.1
DQ_DATA_DIR=/tmp/dq-baseline # 基线扫描使用独立临时数据目录
```

### A.2 板块清单与 code 空间

```python
from easy_tdx import MacClient
from easy_tdx.mac.enums import BoardType, BoardSortColumn
c = MacClient.from_best_host(); c.connect()
for bt in (BoardType.HY, BoardType.HY2, BoardType.GN, BoardType.DQ,
           BoardType.FG, BoardType.YJ_LEVEL1, BoardType.YJ_LEVEL2,
           BoardType.YJ_LEVEL3, BoardType.ALL):
    df = c.get_board_list(board_type=bt, count=10000,
                          sort_column=BoardSortColumn.CHANGE_PCT)
    rows = df.to_dict("records")
    codes = [str(r["code"]) for r in rows]
    print(bt.name, len(rows), min(codes), max(codes))
```

实测输出（摘要）：HY 128 / 881002–881479；HY2 345 / 881003–881479；GN 269 / 880501–880978；DQ 32 / 880201–880232；FG 158 / 880081–880979；YJ1 30、YJ2 128、YJ3 345（均 881xxx）；ALL 560 / 880201–881479。

### A.3 Top8 当日构成与重复率

```python
# 三类榜单合并、过滤统计前缀后按 pct 降序取 8，再逐板块拉成员
merged = sorted([(pct, cat, code, name) for ...], reverse=True)
for pct, cat, code, name in merged[:8]:
    md = c.get_board_members(board_symbol=code).to_dict("records")
    limits = [(str(r["code"]), r["name"]) for r in md
              if r.get("pre_close") and (r["close"]/r["pre_close"]-1)*100 >= 9.9
              and not r["name"].upper().count("ST") and not str(r["code"]).startswith(("30","68","8","92"))]
```

实测：Top8 = 医疗美容(hy)/环境监测(hy)/星闪概念(gn)/工业软件(gn)/AI眼镜(gn)/无线耳机(gn)/AI手机PC(gn)/机器视觉(gn)；
逐板块涨停（仅列非零）：星闪概念 2、AI眼镜 6、无线耳机 3、AI手机PC 4、机器视觉 5；板块级 20 次命中 → 唯一 10 只。
去重后 10 只：`603068 博通集成`、`603236 移远通信`、`603341 龙旗科技`、`603893 瑞芯微`、`002881 美格智能`、`001283 豪鹏科技`、`603626 科森科技`、`603683 晶华新材`、`002347 泰尔股份`、`002403 爱仕达`。

### A.4 近 20 交易日 Top8 构成

```python
# 对 429 个 HY/GN/DQ 板块逐个取日 K（count=26, adjust=NONE），用相邻收盘价算每日 pct，
# 再按日合并三类、排序取 Top8，统计各维度占席与门槛（见 §3.1）
```

实测：20 个交易日；HY 101 席（20/20 天）、GN 55 席（18/20 天）、DQ 4 席（3/20 天）；地区最高涨幅均值 1.48%，Top8 门槛均值 2.42%，行业第 8 名均值 2.01%。

### A.5 基线扫描

```bash
DQ_DATA_DIR=/tmp/dq-baseline .venv/bin/python -m dragon_quant scan --force --top 25
```

实测：14s；领涨 5 行业、候选 3 只（润建股份 / 科森科技 / 美格智能）、真龙 1 只（润建股份）；日志无回退告警。

### A.6 THS 回退端点

| 用途 | URL | 实测 |
|---|---|---|
| 概念排行 | `http://data.10jqka.com.cn/funds/gnzjl/field/zdf/order/desc/page/1/` | 200，50 行，表结构与 hyzjl 一致（td[1]=名称、td[3]=涨跌幅） |
| 概念成分 | `http://q.10jqka.com.cn/gn/detail/code/300816/` | 200，2 个 tbody，成员表在第 2 个（10 行） |
| 概念翻页 | `http://q.10jqka.com.cn/gn/detail/order/desc/page/2/code/300816/` | 200，10 行 |
| 地区排行 | `http://q.10jqka.com.cn/dy/` | 200，单页 33 行，td[2]=涨跌幅 |
| 地区成分 | `http://q.10jqka.com.cn/dy/detail/code/882001/` | 200，20 行 |
| 地区翻页 | `http://q.10jqka.com.cn/dy/detail/order/desc/page/2/code/882001/` | 200，20 行 |
| 概念/地区 K 线 | `https://d.10jqka.com.cn/v6/time/48_{inner}/last.js` | 200；300816→885517、882001→882001，各 241 点 |
