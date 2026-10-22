# prep 宏观扰动数据源调研（外围指数 / 美债 / 黄金）— v2

日期：2026-10-20 · 分支：feat/reviewa-account-op · 状态：待 research-reviewer 复审
v2 修订：吸收首轮 findings（1 CRITICAL / 4 MAJOR / 4 MINOR），逐条处理见 §12。
采样环境：本机 tmdx 3.0.0（editable，`/Users/bing/Repo/cus-tdx-server`），实际采样时间
北京时间 2026-10-21 00:04–00:20（纽约 2026-10-20 12:04–12:20 EDT，美股盘中）。

## 1. 调研目标与约束

为 `prep` 盘后复盘增加「宏观扰动」采集，需要三类数据：

| 类别 | 目标品种 | 要求 |
|------|----------|------|
| 外围股市 | 道琼斯、纳斯达克综合、标普 500（至少三者） | 免费、盘后（≥15:30 Asia/Shanghai）可得、尽量无 Cookie |
| 美债 | 美国 10 年期国债收益率（10Y 优先） | 同上，且能给出「变动」而非仅绝对值 |
| 黄金 | XAU / COMEX 黄金 / 沪金（任一可得口径） | 同上 |

硬约束（任务包 + AGENTS.md）：

- 不新增第三方依赖；只能用标准库 + 已有 `tmdx` / `playwright`。
- 排除东方财富。
- 新 provider 方法 transport 失败必须抛异常，由 prep 采集器记 `status=error`；单品种/整体失败不得中断 prep。
- 资产 payload 必须 JSON 原生（no NaN/inf），能落 `assets.db/prep_assets`。
- **prep 的快照 = 资产纯函数，`--recompute` 必须可重放**：同一 `trade_date` 的资产取值不能随运行时刻漂移。

## 2. 关键设计结论（先给结论）

1. **宏观取值改为「最后一根已完成日 K」而不是实时 quote**：
   `goods_kline(period=DAILY)` 取 `< trade_date` 的最后一根 bar（T-1 口径），
   与 quote 的「此刻最新价」解耦；同一 `trade_date` 无论 15:30 还是 23:00 采集，值都相同。
   这同时解决首轮 M2（15:30 未实测）与 M3（黄金盘中漂移）。
2. **首选 tmdx 扩展行情 `MacExClient`**，一次连接、6 次日 K，实测 connect 0.178s + klines 0.27s。
3. **回退**：美股指数 → 腾讯 quote（仅接受 `session_date < trade_date` 的已完成 session）；
   美债 → Yahoo chart API（best-effort，实测会 429）；黄金 → Yahoo `GC=F`（best-effort）；
   雪球仅作指数最后备选。
4. **美债变动只给近似值**：TDX `8_ATY` 为两位小数日频快照，与独立源同日残差 ≤1.4bp，
   两端点舍入会把 bp 差异放大；
   展示 `美债10Y 5.15%（较前值 +6bp，近似）`，不宣称精确 bp。
5. **降级状态已对齐 prep 四态**：整体失败抛异常→`error`；部分成功→`ok + degraded[]`；
   全缺但未抛→`empty`（并入 `_MUST_NONEMPTY` 强制降级）。

## 3. 候选源逐一评估

### 3.1 tmdx（easy_tdx）扩展行情 — 首选

`easy_tdx` 除 A 股 `MacClient` 外，还有扩展行情层：

- `easy_tdx.ex.mac_client.MacExClient`：端口 7727，MAC 协议扩展市场（期货/港股/美股）。
- `easy_tdx.mac.enums.ExMarket` 明确包含 `INTL_INDEX=12`、`COMEX_FUTURES=16`、
  `SH_GOLD=46`、`MACRO_INDICATOR=38`。
- PyPI 3.1.1 wheel 已由复审确认包含 `easy_tdx/ex/mac_client.py` 与上述枚举；
  本机 editable 3.0.0 同样可用。**实现需声明 tmdx ≥ 3.0.0（含 ex 模块）**；旧版 ImportError
  按「宏观源不可用」降级，不崩溃。

#### 3.1.1 日 K canonical 实测

```text
connect 0.178s；6 个品种 goods_kline(DAILY, count=10) 总 0.27s
A_DJI   最后 3 根：2026-10-16 收 52002.410 / 2026-10-19 收 51966.031 / 2026-10-20（盘中）52432.551
A_IXIC  最后 3 根：2026-10-16 收 26140.033 / 2026-10-19 收 26430.887 / 2026-10-20（盘中）26475.994
A_SPX   最后 3 根：2026-10-16 收 7659.440 / 2026-10-19 收 7690.730 / 2026-10-20（盘中）7719.460
GC00W   最后 3 根：2026-10-16 收 4186.700 / 2026-10-19 收 4160.300 / 2026-10-20（盘中）4149.700
Au99.99 最后 3 根：2026-10-16 收 901.570 / 2026-10-19 收 900.890 / 2026-10-20 收 889.780
8_ATY   最后 5 根：2026-10-14 5.19 / 10-15 5.10 / 10-16 5.09 / 10-19 5.15
```

- 取 `< trade_date` 的最后一根：
  - 15:30（美股休市）：最后一根就是 T-1 已完成收盘；
  - 23:00（美股盘中）：最后一根是 T 日盘中 bar，`< trade_date` 会**排除它**，仍取 T-1；
  - 因此两种时刻得到同一值，`--recompute` 可重放。
- 黄金：COMEX `GC00W` 主连有效；`GC00Y`（连续）实测全 0，不可用；`SH_GOLD` 的
  `Au99.99` / `Au(T+D)` 可作美元金价之外的备选口径（元/克，不可与 COMEX 混为一个数）。
- 美债：`MACRO_INDICATOR` 下 `8_ATY`（10Y）、`8_ATFY`（5Y）、`8_ATSY`（2Y）、
  `8_ATLY`（30Y）、`8_EFFR`。`8_ATY` 的日 K 为 `open=high=low=close`、`vol=0`，
  是**日频收益率快照**，不是连续交易行情。

#### 3.1.2 8_ATY 口径对齐（回应 CRITICAL C1）

独立源 Yahoo `^TNX`（复审验证，America/New_York 日线）与 TDX `8_ATY` **同日对齐**：

| 日期 | Yahoo ^TNX | TDX 8_ATY | 同日残差 |
|---|---|---|---|
| 2026-10-13 | 5.217 | 5.21 | -0.7bp |
| 2026-10-14 | 5.190 | 5.19 | 0.0bp |
| 2026-10-15 | 5.089 | 5.10 | +1.1bp |
| 2026-10-16 | 5.098 | 5.09 | -0.8bp |
| 2026-10-19 | 5.136 | 5.15 | +1.4bp |

结论：

- **同日对齐成立**：残差全部 ≤1.4bp；10-14 的 5.190 vs 5.19 是判别点。
  （v2 初审曾误判为「日期标签一天错位」；若按错位检验，10-15 与 10-19 会分别差 9bp / 5.2bp，
  假设立即崩掉。该错误表述已删除。）
- TDX 是**两位小数的日频快照**（`open=high=low=close`、`vol=0`），与 `^TNX` 存在 0–1.4bp 源差
  （大概率 CMT 定盘 vs ^TNX 指数，不全是舍入）。两端点舍入会把连续两日的 bp 差异放大：
  TDX 10-16→10-19 得 +6bp，Yahoo 同日历得约 +3.8bp，差 2.2bp。
- 处置：美债仍用 TDX 作主源（唯一稳定可得的免费源），但：
  1. 展示 `美债10Y 5.15%（较前值 +6bp，近似）`；
  2. 落库 `chg_bp_approx: true` + `precision_note: "TDX 两位小数日频快照，bp 为近似值"`；
  3. `chg_bp` 标记为 **display-only**，不进入任何公式；
  4. 若 Yahoo 回退可用，则用 Yahoo 自己的连续日 K 计算，`chg_bp_approx: false`；
  5. 美债只作观察，不进 `position_advice` 公式。

#### 3.1.3 连接与落点

- 落点：`TdxProvider.get_macro_quotes(specs, trade_date)`，惰性 `MacExClient` 单例，
  复用 `TdxProvider._lock` 串行化；`timeout=8s`、`ping_timeout=2s`；
  prep 单线程一次批量调用，不经过 `RateLimiter`。
- 失败分层（遵循 provider 契约，任何 transport/解析/写盘失败都**在 provider 内 raise**）：
  - `TdxProvider.get_macro_quotes`：连接失败、`goods_kline` 抛错、`~/.easy_tdx` 写盘失败、
    `easy_tdx.ex` 缺失/导入失败，一律 `raise RuntimeError`（不吞异常）；
  - 逐源回退与 `source_errors` 汇总放在 prep 侧宏观 facade（`collect_macro`）：
    捕获各源异常并继续下一个源；全部源失败才 `raise RuntimeError`，由 collector 记 `status=error`。
  - 采集器（`_collect_macro`）不再二次吞异常；`_collect_all` 统一隔离并写 `collection_report`。

### 3.2 腾讯 gtimg — 指数回退（quote，限已完成 session）

实测 `https://qt.gtimg.cn/q=usDJI,usIXIC,usINX,hf_GC`（GBK）：

```text
v_usDJI="200~道琼斯~.DJI~52441.91~51966.03~51959.21~149450324~...~2026-10-20 12:04:20~475.88~0.92~..."
v_usIXIC="200~纳斯达克~.IXIC~26476.10~26430.89~26332.92~...~2026-10-20 12:04:20~45.21~0.17~..."
v_usINX="200~标普500~.INX~7719.34~7690.73~7679.01~...~2026-10-20 12:04:20~28.61~0.37~..."
v_hf_GC="4146.93,-0.32,4148.60,4148.80,4174.90,4130.10,00:04:06,4160.10,4162.40,0,1,2,2026-10-21,纽约黄金";
```

- 美股字段：`[1]=名称 [2]=代码 [3]=现价 [4]=昨收 [5]=开盘 [30]=源时间 [31]=涨跌
  [32]=涨跌幅 [33]=最高 [34]=最低`。`[30]` 是**纽约当地时间**，不是北京时间。
- **不能复用 `_parse_gtimg_quote`**：实测该函数对 `usDJI` 返回 `None`，因为美股行含空字段
  （如 `[45]`）触发 `float("")`。宏观需专用轻量解析，只取上述价格字段。
- 回退规则：只接受 `session_date = [30][:10] < trade_date` 的 quote。
  15:30 时拿到的是上一美股 session（如 10-19），可用；23:00 时 session_date=10-20，
  与 `trade_date` 相同 → 拒绝，宁可降级也不把盘中值写进 T 日资产。
- 日 K 接口 `fqkline/get` 实测对 `usDJI` 只返回 1 根（当前 session），不能作为 canonical 回退。
- 美债：`usTNX` / `usTYX` / `usFVX` / `us10y` / `hf_US10Y` / `hf_ZN` / `hf_ZB` 均返回空；
  外汇前缀 `wh_USDCNH` / `wh_USDCNY` 也返回空。**腾讯不提供美债/外汇宏观源**。
- 黄金 `hf_GC` 是逗号格式（`0=现价 1=涨跌幅% 7=昨收 12=日期`），日期是北京日期；
  为避免口径漂移，**不作为黄金 canonical 回退**（黄金回退走 Yahoo 日 K）。

### 3.3 同花顺 THS — 不采用

- `https://q.10jqka.com.cn/global/` 实测 302/JS 跳回 `http://q.10jqka.com.cn/index/`（A 股首页），
  返回 A 股涨幅榜，不是全球指数；无 `hexin-v` 等令牌时拿不到全球指数表。
- `https://q.10jqka.com.cn/usa/` 可访问，但内容是美股个股涨跌榜，不含指数点位/美债/黄金。
- 现有 `data.10jqka.com.cn/dataapi` 只有 A 股涨停池/题材榜语义；页面内未发现宏观 dataapi 引用。
- 结论：THS 沿用现有 GBK + 403 退避也无法补齐三类宏观数据，**不作为宏观源**。

### 3.4 雪球 — 指数最后备选（需 Cookie）

带现有 Cookie 实测：

```text
.DJI  -> 道琼斯指数        52461.28  +0.95%
.IXIC -> 纳斯达克综合指数   26494.599 +0.24%
.INX  -> 标普500指数       7723.13   +0.42%
GC00Y / US10Y / XAU -> data.quote 为空
GLD   -> 黄金ETF-SPRD（ETF，不是金价，不采用）
```

- 符号是 `.DJI` / `.IXIC` / `.INX`（裸 `DJI` 返回 HTTP 400）。
- quote 的 `timestamp` 是毫秒；转 `America/New_York` 得 session date，可用于
  `session_date < trade_date` 判定。
- 仅作指数最后备选；无 Cookie 时静默跳过（返回空，不算 transport 失败）。

### 3.5 Yahoo chart API — best-effort 回退（回应 MAJOR M1）

复审验证：`GET https://query1.finance.yahoo.com/v8/finance/chart/%5ETNX?range=7d&interval=1d`
返回 HTTP 200 JSON（`regularMarketPrice=5.115` + 7 根日线），无需 Cookie/crumb；
同一接口 `^DJI` 正常。

本次追加实测（同一网络，连续调用）：

```text
第 1–2 次（复审）：200
本次 query1 ^TNX：429 Too Many Requests
本次 query2 ^TNX：429 Too Many Requests
本次 v7 quote?symbols=^TNX：429
本次 v8 chart?range=5d：429
```

- **限频明显**：短时间连续调用会被 429。FRED CSV（`fredgraph.csv?id=DGS10`）本次两次
  均 `TimeoutError`；Stooq CSV 返回 JS 挑战页；CNBC 页面可用但 2.2MB 且需解析新闻页 JSON。
- 处置：Yahoo 作为 **best-effort 最后回退**，单品种单次尝试、5s 超时；
  429/超时 → 记 `source_errors.yahoo`，不重试、不阻断其它源。
  它覆盖 `^TNX`（美债）、`^DJI/^IXIC/^GSPC`（指数）、`GC=F`（黄金日 K），
  从而消除「美债绝对单源」；但不作为默认主链，避免把限频风险带进每日任务。
- 矩阵与推荐链中明确标注「best-effort，可能 429/不可达」。

### 3.6 新浪 hq.sinajs.cn — 已验证但不采用

实测（需 Referer，无 Cookie）：`gb_$dji` / `gb_ixic` / `gb_inx` / `hf_GC` 均 200，
`globalbd_US10Y` / `gb_US10Y` / `US10Y` 为空。

不采用原因：美股 quote 的时间 `2026-10-21 00:02:16` 是**北京日期**，15:30 时上一 session
的时间戳会是 `trade_date 04:00`，无法用 `session_date < trade_date` 区分「已完成」与「盘中」；
作为 canonical 回退会引入与 M3 同类的漂移风险。现有 TDX → 腾讯 → Yahoo/雪球已足够。

## 4. 来源矩阵

| 品种 | tmdx 日 K | 腾讯 quote | Yahoo 日 K | 雪球 quote | 新浪 quote | THS | 结论 |
|------|-----------|------------|------------|------------|------------|-----|------|
| 道琼斯 | ✅ `A_DJI` | ✅ `usDJI`（需 session<T） | ✅ `^DJI`（best-effort） | ✅ `.DJI`（Cookie） | ✅（日期口径不合） | ❌ | TDX 首选，腾讯回退 |
| 纳斯达克综合 | ✅ `A_IXIC` | ✅ `usIXIC` | ✅ `^IXIC` | ✅ `.IXIC` | ✅ | ❌ | 同上 |
| 标普 500 | ✅ `A_SPX` | ✅ `usINX` | ✅ `^GSPC` | ✅ `.INX` | ✅ | ❌ | 同上 |
| 美债 10Y | ✅ `8_ATY`（2 位小数，近似） | ❌ | ✅ `^TNX`（best-effort） | ❌ | ❌ | ❌ | TDX 首选，Yahoo best-effort |
| COMEX 黄金 | ✅ `GC00W` | ⚠️ `hf_GC`（日期口径不合） | ✅ `GC=F`（best-effort） | ❌ | ⚠️ | ❌ | TDX 首选，Yahoo 回退 |
| 沪金 | ✅ `Au99.99` | ❌ | ❌ | ❌ | ❌ | ❌ | TDX 内备选（元/克） |

认证要求：

- tmdx 扩展行情：无 Cookie、无显式登录；TCP 7727。
- 腾讯：无 Cookie，UA + Referer。
- Yahoo：无 Cookie；有 429 限频。
- 雪球：需 Cookie。
- THS：全球指数页需令牌，现有 A 股 dataapi 不覆盖宏观。

## 5. 时点语义（v2 核心修订）

### 5.1 prep 的可达窗口

`prep.py` 的 `same_day_only` 门禁是 `trade_date < ctx.today` → `unavailable`。
宏观**不设** `same_day_only`：canonical 日 K 对同一 `trade_date` 是确定性历史数据，
补采历史日不会污染，午夜后运行也仍可补采（见 §5.2）。
因此宏观采集只在 `trade_date == today` 执行，典型窗口是 **15:30–21:29**（美股休市）；
21:30 之后美股开盘，若仍用 quote 会拿到盘中值。

### 5.2 canonical 取值：最后一根已完成日 K

实现取 `goods_kline(DAILY, count≥10)` 中 **日期 < trade_date 的最后一根**：

- 15:30：最后一根是 T-1 已完成收盘（美股 10-19）；
- 23:00：美股盘中 bar（10-20）存在，但 `< trade_date` 排除它，仍取 10-19；
- 00:04（本报告采样时刻）：同样取 10-19；
- 因此同一 `trade_date` 的资产值不随运行时刻漂移，`--recompute` 可重放。

`session_date` = bar 日期；`as_of` = 采集日；`caliber = "last_completed_daily_bar_before_trade_date"`。
`stale` 标记：`(trade_date - session_date).days > 4` → 展示「数据较旧（N 天前）」，
覆盖美股节假日导致 session_date 落后多日的情况。

### 5.3 各品种语义

- **美股**：T 日 15:30 取 T-1 美股收盘（对 A 股 T+1 而言是「隔夜美股」）。
- **美债**：T 日 15:30 取上一美债交易日收盘收益率；TDX 两位小数，bp 变动标「近似」。
- **黄金**：取 T-1 COMEX 日 K 收盘（或沪金日 K），不再使用 15:30 的盘中 quote；
  因此不会出现同一 trade_date 两次采集值不同的情况。

## 6. 推荐采集链

```text
美股三大指数：
  tmdx MacExClient INTL_INDEX 日 K(A_DJI/A_IXIC/A_SPX，< trade_date 最后一根)
    → 腾讯 quote（仅 session_date < trade_date）
    → Yahoo chart ^DJI/^IXIC/^GSPC（best-effort，单次）
    → 雪球 quote .DJI/.IXIC/.INX（仅 session_date < trade_date，Cookie 可选）

美债 10Y：
  tmdx MacExClient MACRO_INDICATOR 日 K(8_ATY，< trade_date 最后一根，chg_bp 标近似)
    → Yahoo chart ^TNX（best-effort，单次）
    → 无其它稳定免费源；失败则 ust10y 单品种降级

黄金：
  tmdx MacExClient COMEX_FUTURES 日 K(GC00W)
    → tmdx MacExClient SH_GOLD 日 K(Au99.99，元/克口径)
    → Yahoo chart GC=F（best-effort，单次）
```

与现有 TDX→THS→腾讯/雪球家族一致；Yahoo 仅补美债/黄金的最终回退。

## 7. 降级策略（对齐 prep 四态 status）

| 场景 | 采集器行为 | collection_report | compute 行为 |
|------|------------|-------------------|--------------|
| 所有源、所有品种都失败 | collector 抛 RuntimeError | `macro_global: error` | `compute_macro` 返回 `available=false, degraded=["macro_global"]`；`sentiment.degraded` 追加 `macro_global` |
| 至少一个品种成功 | 返回 payload | `macro_global: ok` | payload 内 `degraded=[缺失 key]`；`sentiment.degraded` 追加 `macro_partial` |
| payload 无任何品种（异常未抛） | — | `_EMPTY_KEYS["macro_global"]` → `empty`；`_MUST_NONEMPTY` 含 macro → `_prepare_assets` 降级 | 同整体缺失 |
| 部分品种缺失且需要显式警告 | `suspicious_check` 返回 note | `empty_suspicious`（仅当整体为空时；部分缺失用 payload.degraded） | 不使用该资产 |
| `as_of` | 直接写 `trade_date`（canonical bar 归属日） | 不做 `_AS_OF_CHECKED` 相等校验 | 历史补采不误伤 |
| 补跑历史日 | `same_day_only=False`，日 K canonical 可确定性重建 | `ok`（TDX/Yahoo 日 K）或 `degraded` | 不污染历史；新鲜度由 `session_date`/`stale` 表达 |
| TDX ex 缺失/旧版 | provider 抛 RuntimeError | 走回退；全失败则 error | 降级，不崩溃 |

**实现落点**：

- `_EMPTY_KEYS["macro_global"] = lambda p: not (p.get("instruments") or {})`
- `_MUST_NONEMPTY` 加入 `"macro_global"`
- **不加入** `_AS_OF_CHECKED`：`as_of` 直接写 `trade_date`（canonical 日 K 归属日），
  历史补采可确定性重建，不存在「今天数据污染历史」的问题。
- `_macro_suspicious(payload)`：无 instruments → 返回 note（兜底，正常由 collector 抛错）

## 8. 推荐落库字段

```json
{
  "key": "us_dji",
  "label": "道指",
  "kind": "index",
  "price_unit": "points",
  "chg_unit": "pct",
  "price": 51966.031,
  "prev_close": 52002.410,
  "chg": -36.379,
  "pct": -0.06995,
  "chg_bp": null,
  "chg_bp_approx": null,
  "source": "tdx_ex",
  "code": "A_DJI",
  "market": "INTL_INDEX",
  "session_date": "2026-10-19",
  "raw_date": "20261019",
  "raw_time": null,
  "stale": false
}
```

美债：

```json
{
  "key": "ust10y", "label": "美债10Y", "kind": "yield",
  "price_unit": "pct", "chg_unit": "bp",
  "price": 5.15, "prev_close": 5.09,
  "chg": 0.06, "pct": null, "chg_bp": 6.0, "chg_bp_approx": true,
  "precision_note": "TDX 两位小数日频快照，bp 为近似值",
  "source": "tdx_ex", "code": "8_ATY", "market": "MACRO_INDICATOR",
  "session_date": "2026-10-19", "raw_date": "20261019", "raw_time": null,
  "stale": false
}
```

- `price_unit` 词表：`points` / `pct` / `usd_per_oz` / `cny_per_g`。
- `chg_unit`：`pct`（指数/黄金）或 `bp`（美债）。
- 顶层 payload：`trade_date`、`as_of`、`caliber`、`instruments`、`degraded`、`sources`、`source_errors`。
- 所有数值 `float()` 归一，NaN/inf → None；不落 numpy 标量。

## 9. 风险与未决点

1. **TDX 扩展行情服务器稳定性**：公网 IP 列表可能变化；连接失败走回退；全失败降级不中断。
2. **美债精度**：TDX 两位小数日频快照，与独立源同日残差 ≤1.4bp；两端点舍入会放大 bp 差异，
   只展示近似 bp，不宣称精确。
   Yahoo 可提供更高精度，但实测 429 限频，仅 best-effort。
3. **Yahoo 限频/不可达**：单次尝试，失败记 `source_errors.yahoo`；不影响其它源。
4. **腾讯美股字段**：`[30]` 是纽约时间；不能用 `_parse_gtimg_quote`（空字段导致 None）；
   只用专用解析 + `session_date < trade_date` 判定。
5. **黄金口径**：COMEX（美元/盎司）与沪金（元/克）不混算；`label`/`market`/`code` 保留来源。
6. **`from_best_host` 写盘副作用**：写 `~/.easy_tdx`，只读/容器环境可能失败；失败按源不可用处理。
7. **节假日 stale**：`session_date` 落后 >4 天标「数据较旧」；不改变数值。
8. **未在 15:30 实时复跑**：本报告采样在 00:04；但 canonical 已改为日 K `< trade_date`，
   该值在 00:04 与 15:30 相同（日 K 盘中 bar 被排除），风险由设计消除；
   仍建议实现后在 15:30–16:00 做一次真实运行校验。
9. **tmdx 版本**：需 ≥3.0.0（含 `easy_tdx.ex`）；旧版 ImportError 按源不可用降级。

## 10. 复现命令

```bash
# tmdx 日 K canonical（指数/黄金/美债）
python3 - <<'PY'
from easy_tdx.ex.mac_client import MacExClient
from easy_tdx.mac.enums import ExMarket, Period
with MacExClient.from_best_host(ping_timeout=2) as c:
    for mk, code in [(ExMarket.INTL_INDEX,"A_DJI"),(ExMarket.INTL_INDEX,"A_IXIC"),
                     (ExMarket.INTL_INDEX,"A_SPX"),(ExMarket.COMEX_FUTURES,"GC00W"),
                     (ExMarket.SH_GOLD,"Au99.99"),(ExMarket.MACRO_INDICATOR,"8_ATY")]:
        df=c.goods_kline(mk, code, Period.DAILY, 0, 10)
        print(mk.name, code); print(df[["datetime","close"]].tail(4).to_string())
PY

# 腾讯（quote 回退；hf_GC 日期口径不合，仅指数）
curl -s 'https://qt.gtimg.cn/q=usDJI,usIXIC,usINX' | iconv -f gbk -t utf-8

# Yahoo best-effort（可能 429）
curl -s -H 'User-Agent: Mozilla/5.0' \
  'https://query1.finance.yahoo.com/v8/finance/chart/%5ETNX?range=10d&interval=1d'

# THS 全球指数重定向
curl -sI 'https://q.10jqka.com.cn/global/'
```

## 11. 结论

- **首选 tmdx 扩展行情日 K**：一套连接覆盖美股三大指数、美债 10Y、COMEX/沪金，
  免费、无 Cookie、约 0.45s，且日 K `< trade_date` 口径天然稳定、可重放。
- **回退**：腾讯 quote 仅用于美股指数且要求 `session_date < trade_date`；
  Yahoo 日 K best-effort 覆盖美债/黄金/指数（可能 429）；雪球仅指数最后备选。
- **美债**：TDX 两位小数，变动标「近似」；Yahoo 可提高精度但限频，不默认依赖。
- **THS/新浪不采用**：前者无法稳定取得宏观数据，后者时间口径与 canonical 不兼容。
- **时点语义**：T 日 15:30 取 T-1 已完成日 K（对 A 股 T+1 即隔夜）；23:00 也取 T-1，
  不随运行时刻漂移；`stale` 标记节假日滞后。

## 12. 首轮 findings 处理对照

| # | severity | 处理 |
|---|----------|------|
| C1 美债变动未验证 | CRITICAL | §3.1.2 **同日对齐**表（10-13..10-19，残差 ≤1.4bp；10-14 5.190 vs 5.19 为判别点），确认两位小数日频快照的源差；改为「近似 bp」+ `chg_bp_approx` + display-only，不再宣称精确 |
| M1 漏 Yahoo 回退 | MAJOR | §3.5 实测 Yahoo（首次 200、连续 429）、FRED 超时、Stooq JS 挑战；把 Yahoo 纳入 best-effort 回退与矩阵，美债不再绝对单源 |
| M2 15:30 未实测 | MAJOR | §5 改为日 K canonical `< trade_date`，00:04 与 15:30 取值相同；采样场景与 prep 门禁的矛盾由设计消除；保留 15:30 实跑为残余验证项 |
| M3 黄金漂移 | MAJOR | §5.2/§5.3 黄金改用 T-1 日 K 收盘，不再用盘中 quote；同日重跑覆盖写同一 bar，`--recompute` 可重放 |
| M4 降级未映射四态 | MAJOR | §7 状态映射表 + `_EMPTY_KEYS`/`_MUST_NONEMPTY`/`suspicious_check` 明确落点；`same_day_only=False`、不做 `_AS_OF_CHECKED` |
| m1 unit/字段语义 | MINOR | §8 拆 `price_unit`/`chg_unit`，补顶层 `as_of`/`caliber`、`chg_bp_approx`、`stale` |
| m2 tmdx 版本 | MINOR | §3.1/§9 声明 tmdx ≥3.0.0（含 ex），旧版 ImportError 降级 |
| m3 落点/生命周期/写盘 | MINOR | §3.1.3 固定 `TdxProvider.get_macro_quotes`、惰性单例、锁、超时；**明确 provider 内 raise、facade 逐源回退、collector 记 error 的三层分层**；写盘失败列入 §9 风险 |
| m4 节假日 stale | MINOR | §5.2/§9 增加 `stale`（`session_date` 落后 >4 天）展示「数据较旧」 |
| 复审 MAJOR（v2 修复引入） | MAJOR | §3.1.2 删除「日期标签一天错位」错误推断与坏表，改为同日对齐/残差四列表；10-14 判别点纳入 |
| 复审 MINOR（v2 修复引入） | MINOR | §3.1.3 明确 `get_macro_quotes` 内 raise、facade 逐源回退；不吞 transport 失败 |
