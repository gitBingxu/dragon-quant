# providers/CODEMAP.md — 数据源适配层

> 子目录代码地图。所有 provider 实现 `StockProvider`（`base.py:11`）抽象接口，
> 评分器/编排器只依赖接口。工厂 `create_providers()`（`__init__.py:14`）一次实例化全部 4 个。

## 生产环境实际分工（谁负责什么）

| 能力 | 实际 provider | 入口 |
|------|--------------|------|
| 行业板块排行 | **ths** | `ths.py:263 get_sector_ranking` |
| 行业成分股 | **ths** | `ths.py:326 get_sector_components` |
| 板块当日1分K | **ths** | `ths.py:407 get_sector_1min_kline` |
| 板块历史5分K(10日) | **ths** | `get_sector_5min_kline_history` |
| 个股日K | **xueqiu** | `xueqiu.py:110 get_kline` |
| 个股当日1分K | **xueqiu** | `xueqiu.py:160 get_minute_kline` |
| 批量行情+收盘盘口 | **tencent** | `tencent.py:114 batch_get_quotes` |
| 板块数据(备用) | eastmoney | `eastmoney.py:347` 默认不参与扫描 |

## 各 provider 要点

### THSProvider (`ths.py:255`) — 板块主数据源，无需 Cookie
- 关键 URL 常量：`RANKING_URL`(:43, `hyzjl/field/zdf`)、`DETAIL_URL`(:49, `/thshy/`)、`TIME_URL`(:52, 当日1分)、`LINE_URL`(:54, 历史5分,周期码30)
- 排行：curl + GBK 多页抓取(`:275`)，单页非严格有序→本地按 pct 排序；403 频控带退避重试
- innerCode：行业 clid 即 code(881xxx)，解析详情页 `<input id="clid">`，进程内缓存

### XueqiuProvider (`xueqiu.py:102`) — 个股，需 Cookie
- `get_minute_kline`(:160) 当日1分K；`get_kline`(:110) 日K；Referer `xueqiu.com/S/{SH/SZ}{code}`
- `get_quote`(:204) 存在但行情主用 tencent
- 注意：`pankou.json` 盘后空体已弃用，封单改走 tencent

### TencentProvider (`tencent.py:89`) — 零认证
- `batch_get_quotes`(:114) gtimg 批量行情；含**收盘盘口** `bid1_price`(f[9])/`bid1_volume`(f[10],手)/`ask1_volume`(f[20])
- 封单量 = `bid1_volume`，与成交量 `volume`(f[36]) 同为「手」

### EastMoneyProvider (`eastmoney.py:347`) — 保留备用
- curl + DoH 多 CDN 节点轮询，全节点失败抛 `EastMoneyAllNodesDown`(:55)；默认不参与扫描

### 基础设施
- `base.py:11 StockProvider`：抽象接口；新增板块 K 线方法用默认 `NotImplementedError`(`:51/:55`)，**非 `@abstractmethod`**（否则 4 个 provider 实例化即崩）
- `browser.py:41 BrowserSession`：Playwright 会话，雪球 Cookie 自动获取用
- `cookie.py`：Cookie 读写（`get_xq:74` 等）+ 自动获取（`fetch_xq:286`）

## 依赖关系

- 上游：`orchestrator.py` 各 Phase 调用；`create_providers()` 注入 logger。
- 下游契约：返回 `models/types.py` 的 `KBar`/`Quote`/`StockInfo`/`SectorPerformance`。
- 限流：所有外部调用经 `RateLimiter`，按 provider 名串行。

## 本层相关不变式

1. provider 基类新方法用默认 `NotImplementedError`（非 abstractmethod）。
2. 板块排行字段必须 `zdf`（涨跌幅），非 `tradezdf`（资金流，无视 order/page）。
3. 封单 `bid1_volume` 与成交量 `volume` 同为 gtimg「手」，禁与雪球(股)混用。
4. ths 排行单页非严格有序，必须本地排序；403 频控需退避重试。
