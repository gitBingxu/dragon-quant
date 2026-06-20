# CODEMAP.generated.md — 结构骨架（自动生成，勿手改）

> 由 `scripts/gen_codemap.py` 静态扫描生成。语义层（执行路径/设计意图/
> 不变式）见手工维护的 `CODEMAP.md`。修改代码后运行 `python scripts/gen_codemap.py` 重生成。

## 一、DataCache 键读写契约

| cache 键 | 写入方 (set) | 读取方 (get) |
|----------|-------------|-------------|
| `__meta__:candidates` | dragon_quant/orchestrator.py | dragon_quant/analyze.py |
| `__meta__:sector_codes` | dragon_quant/orchestrator.py | dragon_quant/analyze.py |
| `__meta__:sector_name_map` | dragon_quant/orchestrator.py | dragon_quant/analyze.py |
| `kline:1min:000001` | dragon_quant/orchestrator.py | dragon_quant/scorers_v2/anti_drop.py |
| `kline:1min:sector:{}` | dragon_quant/orchestrator.py | dragon_quant/scorers_v2/anti_drop.py, dragon_quant/scorers_v2/drive.py |
| `kline:1min:{}` | dragon_quant/orchestrator.py | dragon_quant/scorers/anti_drop.py, dragon_quant/scorers/drive.py, dragon_quant/scorers/leadership.py, dragon_quant/scorers_v2/anti_drop.py, dragon_quant/scorers_v2/drive.py, dragon_quant/scorers_v2/liquidity.py |
| `kline:5min:sector:{}` | dragon_quant/orchestrator.py | dragon_quant/scorers/absorption.py, dragon_quant/scorers/leadership.py, dragon_quant/scorers_v2/absorption.py |
| `kline:day:000001` | — | dragon_quant/scorers/anti_drop.py |
| `kline:day:{}` | dragon_quant/orchestrator.py | dragon_quant/orchestrator.py, dragon_quant/scorers/anti_drop.py, dragon_quant/scorers/drive.py, dragon_quant/scorers/leadership.py |
| `quotes:batch` | dragon_quant/orchestrator.py | dragon_quant/orchestrator.py, dragon_quant/scorers/drive.py, dragon_quant/scorers/leadership.py, dragon_quant/scorers_v2/drive.py, dragon_quant/scorers_v2/liquidity.py |
| `sector:components:{}` | dragon_quant/orchestrator.py | dragon_quant/orchestrator.py, dragon_quant/scorers/drive.py, dragon_quant/scorers/leadership.py, dragon_quant/scorers_v2/drive.py, dragon_quant/scorers_v2/liquidity.py |

## 二、评分器入口签名

### `scorers/`
- `dragon_quant/scorers/absorption.py` → `score(code, cache, primary_sector, all_sector_codes, sector_name_map)`
- `dragon_quant/scorers/anti_drop.py` → `score(code, cache)`
- `dragon_quant/scorers/drive.py` → `score(code, cache, candidate_pool, primary_sector)`
- `dragon_quant/scorers/leadership.py` → `score(code, cache, primary_sector)`

### `scorers_v2/`
- `dragon_quant/scorers_v2/absorption.py` → `score(code, cache, primary_sector, all_sector_codes, sector_name_map, **kwargs)`
- `dragon_quant/scorers_v2/aggregator.py` → `evaluate(code, cache)`
- `dragon_quant/scorers_v2/aggregator.py` → `rank_verdicts(verdicts)`
- `dragon_quant/scorers_v2/anti_drop.py` → `score(code, cache, primary_sector, **kwargs)`
- `dragon_quant/scorers_v2/drive.py` → `score(code, cache, primary_sector, candidate_pool, **kwargs)`
- `dragon_quant/scorers_v2/leadership.py` → `score(code, cache, primary_sector, candidate_pool, **kwargs)`
- `dragon_quant/scorers_v2/liquidity.py` → `score(code, cache, primary_sector, **kwargs)`

## 三、Provider 公共方法

### StockProvider (`dragon_quant/providers/base.py`)
- `set_logger(logger)`
- `name()`
- `get_sector_ranking(asc)`
- `get_sector_components(sector_code, page, all_pages, page_size)`
- `get_sector_5min_kline(sector_code, bars)`
- `get_sector_1min_kline(sector_code, bars)`
- `get_sector_5min_kline_history(sector_code, days)`
- `get_kline(code, days)`
- `get_5min_kline(code, bars)`
- `get_quote(code)`

### EastMoneyProvider (`dragon_quant/providers/eastmoney.py`)
- `name()`
- `get_sector_ranking(asc)`
- `get_sector_components(sector_code, page)`
- `get_sector_5min_kline(sector_code, bars)`
- `get_kline(code, days)`
- `get_5min_kline(code, bars)`
- `get_quote(code)`

### TencentProvider (`dragon_quant/providers/tencent.py`)
- `name()`
- `get_quote(code)`
- `batch_get_quotes(codes)`
- `get_kline(code, days)`
- `get_5min_kline(code, bars)`
- `get_sector_ranking(asc)`
- `get_sector_components(sector_code, page, all_pages, page_size)`
- `get_sector_5min_kline(sector_code, bars)`

### THSProvider (`dragon_quant/providers/ths.py`)
- `name()`
- `get_sector_ranking(asc)`
- `get_sector_components(sector_code, page, all_pages, page_size)`
- `get_sector_5min_kline(sector_code, bars)`
- `get_sector_1min_kline(sector_code, bars)`
- `get_sector_5min_kline_history(sector_code, days)`
- `get_kline(code, days)`
- `get_5min_kline(code, bars)`
- `get_quote(code)`

### XueqiuProvider (`dragon_quant/providers/xueqiu.py`)
- `name()`
- `get_kline(code, days, fq_type)`
- `get_5min_kline(code, bars)`
- `get_5min_kline_for(code, target_ts, bars_before, bars_after)`
- `get_minute_kline(code)`
- `get_sector_ranking(asc)`
- `get_sector_components(sector_code, page, all_pages, page_size)`
- `get_sector_5min_kline(sector_code, bars)`
- `get_quote(code)`
