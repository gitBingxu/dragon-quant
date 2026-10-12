# TDX Provider 接入技术方案

> 目标：新增 `TdxProvider` 作为首选数据链路（通达信 TDX TCP 协议，经 `tmdx`/`easy_tdx` 包），失败时回退现有 `ths + xueqiu + tencent` 老链路。老链路 provider 代码**不改动**。
>
> 状态：待评审（2026-10-10 出稿）

---

## 1. 背景与结论

### 1.1 痛点

现有数据层依赖 3 个 HTTP 数据源，存在三类运维痛点：

| 数据源 | 用途 | 痛点 |
|---|---|---|
| 同花顺 `ths` | 板块排行 / 成分股 / 板块 1 分&5 分 K | 网关 403 频控，需退避重试 |
| 雪球 `xueqiu` | 个股日 K / 分时 | **Cookie 依赖**（有效期数天到数周，失效需重配） |
| 腾讯 `tencent` | 批量行情 + 收盘盘口 bid1/ask1 | 零认证但字段有限 |

### 1.2 调研结论（已核对 tmdx 源码 + 运行中 API）

`tmdx`（import 名 `easy_tdx`，v3.0.0）是通达信 TDX TCP 协议客户端，**无 Cookie、无反爬**，能力覆盖仓库全部数据需求：

- K 线 / 分时 / 实时五档行情 / 封单盘口（bid1/ask1）/ 换手率 / 量比 / 总市值 / 流通市值 / 市盈率 / 涨跌停价 / 均价
- 板块排行 / 板块成分股 / 个股所属板块 / 板块指数 K 线
- 资金流、除权除息、财务、离线读取（DuckDB warehouse）等（本次不启用）

> 唯一实质差异：板块分类口径。tmdx 是**通达信自己的行业/概念板块**（881xxx，行业约 124 个），与仓库现用的**同花顺行业板块**（881xxx，约 90 个）**不是同一套分类**。按你的决定，本方案**不处理口径统一**，只在 §7 风险里标注该差异的后果。

### 1.3 目标

1. 新增 `TdxProvider`，实现 `StockProvider` 全部接口。
2. 扫描主链路「首选 tdx、失败回退老链路」。
3. 老链路 `ths/xueqiu/tencent` 代码与行为不变。

---

## 2. 现状梳理（关键接入点）

### 2.1 provider 装配

`dragon_quant/providers/__init__.py` 的 `create_providers(logger)` 返回：

```python
{"ths": ..., "xueqiu": ..., "tencent": ...}
```

orchestrator 按名取用：`providers["ths"]` / `providers["xueqiu"]` / `providers["tencent"]`。

### 2.2 orchestrator 数据流（缓存键 → provider 方法）

| 阶段 | 缓存键 | 现有 provider 方法 | tdx 可替代 |
|---|---|---|---|
| A 板块排行 | `sector:ranking` | `ths.get_sector_ranking(asc)` | ✅ board-mac/list |
| B 成分股 | `sector:components:{code}` | `ths.get_sector_components(code, all_pages=True)` | ✅ board-mac/members |
| B 个股日 K | `kline:day:{code}` | `xq.get_kline(code, days=30)` | ✅ bars(DAY) |
| D 板块历史 5 分 K | `kline:5min:sector:{code}` | `ths.get_sector_5min_kline_history(code, days=10)` | ✅ bars/index(MIN_5) |
| D 板块 1 分 K | `kline:1min:sector:{code}` | `ths.get_sector_1min_kline(code)` | ✅ bars/index(MIN_1) |
| D 个股分时 1 分 K | `kline:1min:{code}` | `xq.get_minute_kline(code)` | ✅ minute |
| D 大盘分时 | `kline:1min:SH000001` | `xq.get_minute_kline("SH000001")` | ✅ 需代码映射（§4.4） |
| D 批量行情 | `quotes:batch` | `tx.batch_get_quotes(codes)` | ✅ MacClient.get_stock_quotes(fields=…) |

### 2.3 并发与缓存

- `RateLimiter`：**按 provider 名分组串行**（同 provider 排队，不同 provider 并发）。
- `_cached_fetch` / `_cached_fetch_sync`：命中交易日磁盘缓存则跳过请求，未命中提交任务并按需落盘。
- 缓存键无 provider 前缀（如 `sector:ranking`），跨 provider 共享。

---

## 3. 方案总览

```
create_providers()
  ├── tdx:     TdxProvider        ← 新增（首选）
  ├── ths/xueqiu/tencent ← 保留不动（回退）
  └── 路由逻辑（orchestrator 层）   ← 新增：板块「链级」、个股「endpoint 级」fallback

scan() 数据加载处：
  首选 tdx 方法 → 失败/空 → 回退对应老 provider 方法
```

**不新增中间 provider 包装类的理由**：现有 orchestrator 已按名取用 provider，且板块与个股的 fallback 粒度不同（见 §4.1）。直接在 orchestrator 取数处加一个 `_try_primary_fallback(primary_fn, fallback_fn)` 辅助函数，改动最小、行为最直观。

---

## 4. 核心设计

### 4.1 fallback 粒度 —— 板块「链级」、个股「endpoint 级」【关键决策】

**个股数据（日 K / 分时 / 行情）可以逐接口独立 fallback**，因为 6 位股票代码跨数据源通用（`000001` 在 tdx / 雪球 / 腾讯都是平安银行）。

**板块数据必须「链级」一致**，不能逐接口混用：TDX 板块代码与同花顺板块代码虽同为 881xxx，但**编号→板块的映射完全不同**（TDX `881376=数字媒体`，同花顺 `881376` 可能不存在或指别的行业）。若 Phase A 从 tdx 拿到 TDX 板块代码 X，Phase B 却用 ths 去取「X 的成分股」，会静默取错或取空。

因此：

- **板块链以 `get_sector_ranking` 为锚点**：Phase A 先试 `tdx.get_sector_ranking()`，成功（非空）→ 本轮板块链（成分股 / 板块 1 分 / 5 分历史）全部走 tdx；失败 → 全部走 ths。本轮内不混用。
- **个股链按 endpoint 独立**：`get_kline` / `get_minute_kline` / `batch_get_quotes` 各自「tdx 失败→雪球/腾讯」。

> 注：这也意味着「老链路完整回退」时行为与现在完全一致（板块=ths、个股=雪球、行情=腾讯），零回归风险。

### 4.2 连接模型

- tmdx 提供同步客户端 `TdxClient`（标准五档 + K 线 + 板块文件）与 `MacClient`（拓展行情 + 板块排行/成分股 + MAC K 线）。另有 `UnifiedClient` 自动路由二者。
- 采用**同步客户端 + 现有 RateLimiter 串行**：tdx 是单 TCP 连接，天然不能并发；`RateLimiter` 已按 provider 名串行，正好匹配。
- **线程安全（已核实源码）**：`TdxConnection.execute()` 内置 `threading.Lock` 串行化单连接请求；但 `MacClient._reconnect()` 会**整体替换 `self._conn` 对象**，若多线程并发调用会竞态。因此「同一时刻仅一个线程调 tdx」是**正确性要求**，不只是性能——`RateLimiter` 的 key=`provider` 串行恰好保证。首版「一个 `tdx` 名、一条串行队列、单个共享 `MacClient`」。
- 后续优化（非首版）：多主机/多连接并发，把 provider 名拆成 `tdx:kline` / `tdx:quote` 等，**每个队列各自持有独立 `MacClient`**，避免共享连接在 `_reconnect` 时的竞态。

### 4.3 数据转换层（DataFrame → dataclass）

tmdx 返回 pandas DataFrame（或 dataclass 模型），需转换为仓库 `KBar` / `Quote` / `StockInfo` / `SectorPerformance`。集中在 `TdxProvider` 内部实现 `_to_kbar` / `_to_quote` / `_to_stockinfo` / `_to_sector` 四个转换函数。

关键字段换算（已核对 tmdx 字段）：

| 仓库字段 | tmdx 来源 | 备注 |
|---|---|---|
| `KBar.volume` | bars `vol` | 个股=股；**指数/板块分钟线 vol=null**（§7.3） |
| `KBar.amount` | bars `amount` | 元 |
| `KBar.pct` | 由 close/prev_close 或 bar 衍生 `change_pct` 计算 | tmdx bars 已附 `pre_close/change/change_pct` |
| `Quote.bid1_price/bid1_volume/ask1_volume` | `SecurityQuote.bid1/bid_vol1/ask_vol1`，或 MAC 自定义字段 | 封单判定必需 |
| `Quote.turnover_rate/volume_ratio/market_cap/float_market_cap/pe/limit_up/limit_down/avg_price` | MAC bitmap 字段（见 §4.5） | 腾讯 gtimg 的等价物 |

### 4.4 指数代码映射

- 仓库 `R.MARKET_SYMBOL = "SH000001"`（上证指数）。
- TDX 上证指数 = `999999`（SH），`000001` 在 TDX=平安银行（SZ，与仓库约定一致）。
- `TdxProvider` 内部维护映射：`SH000001 → (SH, 999999)`；`get_minute_kline("SH000001")` 内部改请求 `999999`。

### 4.5 Quote 字段拼装（MAC 自定义字段）

仓库 `Quote` 的 24 字段无法由单个 tmdx 端点默认集一次拿全，但 **`MacClient.get_stock_quotes(stocks, fields=[...])` 接受自定义 bitmap 字段集**，可一次取齐。实现时显式请求以下字段：

- 价格/量额：`CLOSE/OPEN/HIGH/LOW/VOL/AMOUNT`（0x04/01/02/03/05/07）
- 封单盘口：`BID_PRICE/BID_VOLUME/ASK_PRICE/ASK_VOLUME`（0x11/18/12/19，买一/卖一价量）
- 换手/量比：`TURNOVER`（0x1B）、`VOL_RATIO`（0x06）
- 市值：`TOTAL_MARKET_CAP_AB`（0x0F）、`FLOAT_SHARES`（0x0B）
- 估值/涨跌停/均价：`PE_DYNAMIC`（0x10）、`BUY_PRICE_LIMIT/SELL_PRICE_LIMIT`（0x20/21）、`AVG_PRICE`（0x26）

> 若 MAC 路径取不到五档盘口（当前 web `/quotes` 注释：MAC 默认字段集不含五档），则回退标准 `TdxClient.get_security_quotes` 补 bid1/ask1。这是实现期需验证的第一件事。

### 4.6 日志接入与失败归因

`ScanLogger.api(provider, endpoint, ok, ...)` 产生 `api:{provider}:{endpoint}` 分类，`ok=False` 记 `error` 级；orchestrator 的 `_report_api_failures` 用 `query(category="api", level="error")` 聚合失败，`api_stats()` 按 `api` 分类统计。TdxProvider 需：

1. **每个方法落 `self._logger.api("tdx", endpoint, ok=..., elapsed_ms=..., error=...)`**，`endpoint` 复用现有 `ENDPOINT_CN` 键（`sector_ranking` / `sector_components` / `sector_5min_kline` / `sector_5min_history` / `sector_1min_kline` / `kline` / `minute_kline` / `batch_quotes`），不新增映射。
2. **`PROVIDER_CN["tdx"] = "通达信"`**（§6.2），控制台中文名与 `api_stats` 归属即正确。
3. **fallback 噪声（易被忽略）**：tdx 失败但老链路成功时，`api:tdx:*` 的 error 仍会被 `_report_api_failures` 打成「❌ 通达信·…失败 N 次」，造成「扫描成功却报错」假象。对策：fallback 成功时由路由层记 `logger.warn("fallback", f"{endpoint}: tdx→{fallback}")`，并让 `_report_api_failures` 按 endpoint 关联，把「已回退」降级为「⚠️ 主链路失败（已回退）」，「主备皆失败」才用 ❌。
4. **主链路健康度**：`api_stats()` 或新增计数暴露「tdx 失败率 / 回退率」，供观察期（§9 Phase 5）评估是否值得长期用 tdx 主链路。

---

## 5. 逐接口映射表（tdx 端点 → 仓库方法）

| 仓库方法 | tmdx 能力（sync 客户端） | 备注 |
|---|---|---|
| `get_sector_ranking(asc)` | `MacClient.get_board_list(board_type=HY, sort_column=CHANGE_PCT)` | 直接返回排序列表；涨跌幅用 `price/pre_close-1` 现算。**勿用 `get_board_ranking`**（内部逐个板块拉成分股，124 行业串行很慢） |
| `get_sector_components(code, all_pages)` | `MacClient.get_board_members(board_symbol)` | 返回成分股 code/name/pct |
| `get_sector_5min_kline_history(code, days=10)` | `get_index_bars(code, MIN_5)` 分页拉取 | 板块分钟线 vol=null |
| `get_sector_1min_kline(code)` | `get_index_bars(code, MIN_1)` | 板块分钟线 vol=null |
| `get_kline(code, days)` | `MacClient.get_stock_kline(code, DAY)`（支持 QFQ/HFQ 复权） | 算连板 / 5 日涨幅；复权对「涨幅百分比」类计算无影响（等比缩放，见 §7.9） |
| `get_minute_kline(code)` | `get_security_bars(code, MIN_1)` | 需 OHLC；`get_minute_time_data`（分时）只有 price+vol，不满足 `KBar` |
| `get_quote` / `batch_get_quotes(codes)` | `MacClient.get_stock_quotes(stocks, fields=[...])` | **80 只/次上限，需 80 分片**（腾讯是 200） |

---

## 6. 改动范围

### 6.1 新增文件

| 文件 | 内容 |
|---|---|
| `dragon_quant/providers/tdx.py` | `TdxProvider(StockProvider)`：连接管理 + 数据转换 + 代码映射 + 全接口实现 |
| `tests/test_provider_tdx.py` | 字段转换单测 + fallback 路由单测 |

### 6.2 修改文件

| 文件 | 改动 |
|---|---|
| `dragon_quant/providers/__init__.py` | `create_providers()` 注册 `tdx` |
| `dragon_quant/orchestrator.py` | ① 引入 `tdx` 实例；② 新增 `_try_primary_fallback` 辅助；③ Phase A 板块链锚点判源；④ Phase B/D 取数处接 fallback；⑤ `PROVIDER_CN` 增 `tdx: 通达信`；⑥ `_report_api_failures` 区分「已回退/真失败」（§4.6） |
| `dragon_quant/data.py`（可选） | `get_*` 系列 `source="tdx"` 支持（供 CLI 单查验证） |
| `pyproject.toml` | `dependencies` 增 `tmdx>=3.0` |

### 6.3 依赖影响

- tmdx 依赖 `pandas>=2.0` / `click` / `tzdata`，并 **要求 Python >=3.10**（仓库当前声明 `>=3.8`）。需把 `requires-python` 提到 `>=3.10`，或把 tmdx 设为 optional extra 以保留 3.8 兼容（推荐后者，见 §7.5）。

---

## 7. 风险与对策

| # | 风险 | 影响 | 对策 |
|---|---|---|---|
| 7.1 | **板块口径/代码不一致**（TDX vs 同花顺，本次不处理） | 评分基准（板块内分位）随数据源切换而漂移；tdx 成功与 ths 回退时产出的板块集合不同 | 已按你的决定接受；板块链「链级」一致（§4.1）保证单轮扫描内部不自相矛盾。历史 `dragons_v2` 跨源对比会存在口径差异，仅用于观察 |
| 7.2 | **板块链 fallback 时缓存键污染**：`sector:ranking` 无 provider 前缀，可能命中上一轮 ths 的缓存而跳过 tdx | 首轮后 tdx 形同虚设 | `sector:ranking` 加 source 命名空间（`sector:ranking:tdx` / `:ths`），或板块链判源在缓存命中前完成 |
| 7.3 | **板块指数分钟线无成交量**（vol=null） | 板块分时量能不可用 | 已确认现评分器对板块分时只用价格曲线（gain_curve 基于 pct），不消费 `.volume`，**不阻断**；若未来要用板块量能，改用 `amount` 替代 |
| 7.4 | **tdx 多主机数据覆盖差异**：个别板块/个股在部分主机取不到数据 | 局部取空触发 fallback | 取空视为「失败」走回退；tdx 自带多主机选优 + 重连 |
| 7.5 | **Python 版本下限冲突**（tmdx 需 3.10+） | 3.8/3.9 环境不可用 | tmdx 设为 optional extra，扫描时若未安装则自动降级走老链路；`requires-python` 是否上提另行决策 |
| 7.6 | **非纯 stdlib 依赖** | 违反 AGENTS.md「仅 stdlib + playwright」约束 | 随本次改动同步更新 AGENTS.md（§9） |
| 7.7 | **历史分时深度** | 板块历史 5 分 K（10 日）依赖 tmdx 分钟数据深度 | 实测验证；不足则板块历史 5 分 K 保留 ths 回退 |
| 7.8 | **MAC 字段单位换算**：`FLOAT_SHARES`/`TOTAL_SHARES` 单位万股、`TOTAL_MARKET_CAP_AB` 单位待确认（亿/元）、`amount` 元 | 市值/股本量纲错位（如市值差 1e8） | 实现期逐字段对表核对并换算对齐腾讯口径（`market_cap` 用元）；单测用已知市值个股断言 |
| 7.9 | **共享连接竞态**：`MacClient._reconnect()` 整体替换 `self._conn` | 并发调用偶发错乱 | 首版靠 `RateLimiter` 串行规避（§4.2）；拆多队列时每队列独立 `MacClient` |

---

## 8. 验证方案

1. **字段级单测**（`tests/test_provider_tdx.py`）：用 tmdx 离线/回放数据验证 `_to_quote` / `_to_kbar` 字段映射（重点：bid1/ask1 封单、turnover/量比/市值/PE、指数代码映射 000001→999999）。
2. **fallback 路由单测**：mock `tdx` 抛异常/返回空 → 断言回退到老 provider；tdx 成功 → 断言不调老 provider。
3. **端到端对比**：同一交易日 `scan` 跑「tdx 主链路」与「--force 强制老链路」两份结果，对比候选池、板块数、综合分分布，确认口径差异范围（记录但不作为阻塞项）。
4. **故障演练**：断网 / 停 tmdx 服务 / 传非法代码，确认自动回退且结果可用。

---

## 9. 实施步骤（建议顺序）

1. **Phase 1 — 基础设施**：加 tmdx 依赖（optional extra）；`create_providers` 注册 `tdx`；`TdxProvider` 骨架 + 连接管理。
2. **Phase 2 — 个股链**：实现 `get_kline` / `get_minute_kline` / `batch_get_quotes`（含 MAC 字段拼装 + 指数映射）+ orchestrator 个股 endpoint 级 fallback。跑通 `scan` 个股数据。
3. **Phase 3 — 板块链**：实现板块四方法 + orchestrator 板块链锚点判源 + 缓存命名空间隔离。跑通完整 `scan`。
4. **Phase 4 — 测试与文档**：单测 + 端到端对比 + 故障演练；同步 AGENTS.md / README.md（依赖、板块口径说明、数据源架构、指数代码映射）。
5. **Phase 5 — 观察期**：tdx 主链路灰度跑若干交易日，收集失败率/覆盖差异，再决定是否调整 fallback 策略或默认开关。

---

## 10. 文档同步清单（强制，随代码同 commit）

- **AGENTS.md**：
  - 架构总览 `providers/` 增 `tdx.py`；
  - 「数据源」说明改为「TDX（首选）+ 同花顺/雪球/腾讯（回退）」；
  - 「必须遵守的约束」中「仅 stdlib + playwright」改为「stdlib + playwright + tmdx（optional）」；
  - 反爬要点增「通达信 TDX TCP」小节；
  - 板块口径注明「TDX 与同花顺行业板块非同一分类，主/备链路口径不同」。
- **README.md**：同步上述数据源与依赖说明。
