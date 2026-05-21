# Changelog

All notable changes to dragon-quant will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.8] - 2026-05-21

### Added
- dragons 表 `version` 字段，记录每条 dragon 入库时的 dragon_quant 版本号
- `dragon_quant/_version.py` 版本号集中管理模块
- `encrypt_token.sh` 加密脚本 + `publish.sh` 支持 `--passwd` 解密发布
- 发布脚本自动同步版本号到 `pyproject.toml` 和 `_version.py`
- review 模块 14 个单元测试（覆盖一字板跳过、日期筛选、批量回测）
- 全量测试覆盖 154 个用例（+14 新增）

### Changed
- **review 可介入判定**：从「非涨停日(pct≥9.9%)」改为「非一字板(high!=low)」，以最低价买入
- **review 自动筛选**：默认只回测入选日距今 >5 且 <20 交易日的 pending 记录
- **东财请求**：urllib 优先 + curl 兜底 + TLSv1.2 强制 + DNS 多 IP 轮询，绕过 CDN 坏节点导致的 rc=52 / 空响应
- 错误报告改进：urllib 失败不再静默，打印具体 IP 和错误原因

### Fixed
- `_get_ut_token()` 中 `html` 变量在 curl 失败时 `UnboundLocalError`，已初始化为空字符串
- `_curl_request()` 在 curl rc=0 但输出为空时不打印任何信息，已修复为始终输出失败原因
- `run_review` 的 `force` 分支与实际行为等价（待完善）
- 发布脚本不再要求手动预改 `pyproject.toml` 版本号

### Added
- Phase B 候选股按 5 日累计涨幅排序筛选（替换原日涨跌幅排序）
- 候选滤镜新增北交所（8/92 开头）过滤
- Phase C 排序改为连板优先、概念数次之
- 140 个单元测试覆盖 13 个模块（scorers / orchestrator / cache / providers / logging / storage）
- `StockInfo` 新增 `five_day_return` 字段

### Fixed
- `_find_limit_up_dates` 连板截断：3-entry 窗口过早 `break` 导致 `max_cons` 偏低

## [0.1.5] - 2026-05-14

### Added
- publish.sh 一键发布脚本（版本校验 + 构建 + twine 上传）

### Changed
- 东财 push2/push2his 跳过 urllib 直接走 Playwright，省去 TLS 指纹拦截的无效重试

### Docs
- 评分器技术方案合并入技术方案.md

## [0.1.4] - 2026-05-13

### Fixed
- CLI `cookie-status` / `cookie-fetch` / `batch-quote` 因 hyphen-underscore 不匹配导致 handler 静默不执行

## [0.1.3] - 2026-05-13

### Changed
- `top_n` 仅控制输出范围，固定对前 25 只候选做四维评分

## [0.1.2] - 2026-05-12

### Fixed
- 领涨性 deviation 使用东财数据（与排名一致），避免 Tencent 数据不一致导致假跑输

## [0.1.1] - 2026-05-11

### Fixed
- `_lead_lag_score` 中 `_bar_return` 多传参数导致领涨性评分全部异常兜底为 50 分
- license 改为 SPDX 字符串格式

## [0.1.0] - 2026-05-10

### Added
- 核心架构：东财 / 雪球 / 腾讯 3 个 Provider + RateLimiter 并发调度 + DataCache 双重缓存
- Orchestrator 7 阶段扫描流程（A→F）
- 四维评分器：带动性(35%) / 抗跌性(15%) / 领涨性(25%) / 资金承接(25%)
- 结构化日志模块 (ScanLogger) + 自然语言报告生成器 (ReportBuilder)
- 持久化存储管理 (paths / manager)
- CLI 入口 + Programmtic API + 子进程打分
- Cookie 管理 CLI（手动设置 + Playwright 自动获取）
- Agent 集成指南 + 8 个场景示例

### Fixed
- 雪球 K 线 type=before → type=after 修复数据截断
- 封板时间检测：改为找当天最早接近涨停价的 bar
- 报告模板优化 + CDF 修复 + anti_drop 函数补回
- 过滤统计类概念板块（昨日涨停/连板/炸板等）
- Playwright 线程安全改造
