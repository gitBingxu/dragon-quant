# 东方财富公开 API 参考

所有接口均为 JSONP 公开接口，从东方财富 Web/App 前端逆向而来。
无需登录，免费使用，稳定性高。

## 通用说明

- **Base URL**: `https://push2.eastmoney.com/api/qt`
- **历史K线**: `https://push2his.eastmoney.com/api/qt/stock/kline/get`
- **格式**: JSONP（`jQuery({...})` 包装），需去掉前缀
- **频率限制**: 建议 < 3 req/s，高峰期偶有超时
- **市场编码**: `0`=深圳, `1`=上海, `90`=板块指数

---

## 1. 涨停板列表

```
GET /api/qt/clist/get
```

| 参数 | 值 | 说明 |
|---|---|---|
| `pn` | 1 | 页码 |
| `pz` | 5000 | 每页条数 |
| `fs` | `m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23` | 市场筛选 |
| `fields` | `f12,f14,f3,f184,f186,f100,f72,f78` | 返回字段 |

### 返回字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `f12` | str | 股票代码 |
| `f14` | str | 股票名称 |
| `f3` | float | 涨跌幅% |
| `f184` | int | 连板数 |
| `f186` | str/null | 封板时间 HHMM（空=一字板） |
| `f100` | str | 行业名称（申万二级；注：f128/f87 在此接口返回 -/数字，不可用） |
| `f72` | float | 换手率% |
| `f78` | int | 成交额（元） |

### 示例

```bash
curl "https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=5&fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23&fields=f12,f14,f3,f184,f186,f87,f128,f72,f78"
```

---

## 2. 行业成分股列表

```
GET /api/qt/clist/get?fs=b:MK0{BKxxxx}
```

参数同上，仅 `fs` 不同：`fs=b:MK0BK0429`（半导体行业）。

### 示例

```bash
curl "https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=500&fs=b:MK0BK0429&fields=f12,f14,f3,f72,f78"
```

---

## 3. 个股实时行情

```
GET /api/qt/stock/get?secid=0.002xxx
```

| 参数 | 值 | 说明 |
|---|---|---|
| `secid` | `0.002xxx` 或 `1.600519` | 市场.代码 |
| `fields` | `f43,f44,f45,f46,f47,f48,f50,f57,f58,f60,f170` | 返回字段 |

### 字段说明

| 字段 | 说明 | 单位 |
|---|---|---|
| `f43` | 最新价 | 需/100 |
| `f44` | 开盘价 | 需/100 |
| `f45` | 最高价 | 需/100 |
| `f46` | 最低价 | 需/100 |
| `f47` | 成交量 | 手 |
| `f48` | 成交额 | 元 |
| `f57` | 股票代码 | - |
| `f58` | 股票名称 | - |
| `f170` | 涨跌幅 | 需/100 |
| `f50` | 量比 | - |

### 示例

```bash
curl "https://push2.eastmoney.com/api/qt/stock/get?secid=1.600519&fields=f43,f44,f45,f46,f47,f48,f57,f58,f170"
```

---

## 4. 日 K 线

```
GET https://push2his.eastmoney.com/api/qt/stock/kline/get
```

| 参数 | 值 | 说明 |
|---|---|---|
| `secid` | `0.002xxx` 或 `1.600519` | 市场.代码 |
| `klt` | `101` | 日K |
| `lmt` | `20` | 返回条数 |
| `fields1` | `f1,f2,f3,f4,f5,f6` | - |
| `fields2` | `f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61` | - |

### fields2 对应

`日期,开盘,收盘,最高,最低,成交量,成交额,振幅,涨跌幅,涨跌额,换手率`

### 示例

```bash
curl "https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=1.600519&klt=101&lmt=20&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"
```

---

## 5. 5 分钟 K 线（板块/个股）

```
GET https://push2his.eastmoney.com/api/qt/stock/kline/get
```

| 参数 | 值 | 说明 |
|---|---|---|
| `secid` | `90.BK0429` 或 `0.002192` | 板块指数 / 个股 |
| `klt` | `5` | 5分钟K |
| `lmt` | `48` | 一天 48 根 |

---

## 6. 大盘指数 K 线

| 指数 | secid |
|---|---|
| 上证指数 | `1.000001` |
| 深证成指 | `0.399001` |
| 创业板指 | `0.399006` |
| 科创50 | `1.000688` |

用 `klt=101` 获取日K，参数同「日K线」。
