# workspace —— 用户策略工作区（M1–M4 契约）

> 触发：改 `workspace init/check`、`strategy list/current/use/validate`、策略 SDK、`--workspace/--strategy`、策略冻结/归属，或 Web `/strategies` 页。
> 本文权威：workspace 契约 / 选择持久化 / 统一运行时 / 快照与因子。
> 上游权威：[`docs/solutions/2026-11-11-workspace-bs-strategies.md`](../solutions/2026-11-11-workspace-bs-strategies.md) 第 13 节（执行契约优先）；根 `AGENTS.md` 硬约束。

## 边界与目录

workspace 独立于 site-packages，**显式 `workspace init` 才创建**；`list` / `-h` / 普通 import 不导入用户代码、不创建存储。

```
workspace.json            # schema_version=1 / workspace_id / revision / 策略清单 / 当前选择
strategies/<slug>/        # strategy.json + strategy.py（导出 create_strategy(params)）
helpers/                  # 共享代码（修改使快照版本变化）
factors/                  # 用户因子模块（SPECS = [FactorSpec, ...]）
snapshots/                # 运行时冻结快照（<sha256>/manifest.json + 源码）
```

- 稳定 ID：内建 `builtin:dragon_pullback_daily`；用户 `user:<slug>`（与目录名一致）。
- 路径优先级：显式 `--workspace` > `DQ_WORKSPACE` > 本机登记 `DQ_DATA_DIR/workspace-selection.json`。
- `workspace.json` 修改用内容 `revision` CAS；冲突保留原文件（Web 409，CLI 非零退出）。

## 选择与参数

- `strategy use ID` 持久切换；`--strategy` 只覆盖单次命令，不改保存默认。
- 参数优先级：descriptor 默认 < workspace 保存值 < 本次显式参数。
- 每次任务启动时解析并冻结一次；运行中切换不影响本次任务。

## 执行接入（统一运行时）

- `TradingEngine` / `SignalEngine` 通过 `review_account/strategy/runtime.py` 拿决策：
  - 内建：Adapter 逐位复用 `evaluate_buy` / `evaluate_sell`，默认行为不变；
  - 用户：深度只读 `StrategyContext`，`StrategyIntent` 做有限校验（方向/NaN/越界/结构），非法输出**显式失败**不退内建。
- `position_pct` 是建议上限，仍受共享仓位/现金/风险预算约束；`exit_fraction` 仅 0.5 或 1。
- 系统 T+1、下一可成交事件、手数、费用、涨跌停与同 code 幂等不可被返回值绕过。
- 持仓 `strategy_ref` 冻结开仓策略；退出按归属路由。无 ref 的旧持仓走 legacy 内建；切换策略不接管旧仓。
- `sell --strategy ID` 只筛选该归属持仓，不替换退出规则。

## 冻结与增量

- 首次执行前冻结 descriptor、有效参数、策略源码/helpers/用户因子到 `snapshots/<id>/`，原子发布并回读校验。
- `strategy_ref` schema_version=1：strategy_id / workspace_id / snapshot_id / params / params_hash / factor_versions / sdk_version / toolkit_version / python_version。
- 增量续算沿用基准冻结身份；快照缺失、SDK/工具版本不兼容、内容哈希不一致明确拒绝，不换用当前源码。
- 查看历史只验证 manifest，不导入历史代码。

## 因子

- 用户因子注册名 `ws_<短 workspace_id>.<local>`；组合注册表检测重复/悬空/循环；版本纳入 spec、模块与 helpers 哈希。
- `infra.factor` 不 import workspace；装配在命令层（`commands/factor.py:_workspace_specs`）。
- `FactorReader` 强制 `available_at <= 决策时点`；缺失与未来不可用返回明确状态，不补零。

## Web

- `GET /api/strategies` / `GET /api/strategies/current` 只读 descriptor；`POST /api/strategies/selection` 变更选择（同源 + `workspace_writes_enabled`，冲突 409、非法 400）。
- 不从请求接受宿主任意绝对路径或 Python 源码；workspace 由服务端设置/CLI 解析决定。
- 账户回测表单可选策略并真正发送 `strategy_id`；省略时消费同一保存选择。

## 冻结运行时与 Web 执行（main 验收修订）

- **统一按冻结身份解析**：`(workspace_id, strategy_id, snapshot_id, params_hash, factor_versions)` 是运行时缓存键；即使 strategy_id 与活动选择相同，也只用快照执行。`FrozenRuntimeProvider` 从 `snapshots/<id>/` 加载源码与 descriptor，校验 manifest 身份、文件哈希、SDK 与工具版本。
- **缺失即拒绝**：已知归属的快照缺失/损坏/不兼容抛 `FrozenReferenceError`（`ValueError` 子类），本次写操作整体中止；只有真正无 `strategy_ref` 的旧仓走 legacy 内建。
- **内建旧仓冻结参数**：`BuiltinStrategyRuntime.controls` 使用持仓冻结的 `strategy_ref.params`，不用当前 cfg 的止损/退出；新内建运行（含无 workspace）同样冻结身份与有效参数，并写入每笔 `entry_signal`。
- **首次运行也从快照执行**：先发布快照，再由 provider 从快照构造运行时，消除 freeze 后读当前源码的竞态。
- **PIT Context**：日K兜底的开盘事件使用可知字段白名单，不公开当日最终 OHLCV 或其派生指标；历史均线等读取 `row.prev_row`。分时事件保留由已完成 K 线计算的可知累计值，`intraday_bars` 按事件时间切片；KBar 转纯 dict 并深度冻结（不共享可变引用）。内建引擎仍读原始 row，golden 不变。
- **用户因子**：必要因子名映射到 `ws_<short>.<local>`；任务开始冻结 `factor_versions`，`FactorReader` 显式按版本读取；候选因子用候选 `code` 与入选 `trade_date`，退出用持仓 `code` 与事件日。读取故障向上抛，不缓存成缺失。
- **用户因子元数据**：`factor list --workspace` 用 AST 提取 `SPECS` 常量元数据（`factor status` 仍只报告内建状态，不接受 `--workspace`），不导入用户 Python；`build/query/eval/export` 才导入并可在 `specs` 注入下评估用户因子。供策略冻结/消费的 `SPECS` 元数据须按模板用字面量声明；动态拼装的声明会明确拒绝，以保证 Web 装配阶段不执行用户因子模块。
- **Web 本地执行**：`DashboardSettings.workspace_writes_enabled` 默认 False；`dashboard --workspace-writes` 或 `DQ_DASHBOARD_WORKSPACE_WRITES=1` 显式启用，`--no-workspace-writes` 优先。账户生成/增量/策略选择在 Origin 校验之后才检查该开关；禁用或跨站时完全不进入用户代码执行。启用后用户策略经 `workspace/worker.py` 独立子进程（版本化 JSON、超时、坏输出/异常清理）执行，HTTP 进程不导入用户代码。
- **研究接入**：`review_account.experiments` 的 variant runner 可注入冻结 runtime；基准 run 有 `strategy_ref` 时 `research experiments run` 真正执行该策略，报告 `frozen.strategy_ref` 记录身份/参数/因子版本；无 ref 的旧 run 不冒用当前规则。

## D1–D4 深化修订（main delta）

- **D1 白名单裁剪**：`daily_fallback` 的 open 事件不再按黑名单删列，而是白名单只公开开盘价 + 上一交易日/历史 + `bar_*` + 限制/事件字段；ma5/ma10/ma20/volume_ma5/amount_ma5/return_*/drawdown/amplitude/atr 等含当日最终值的派生指标全部不进入 Context。需要均线读 `row["prev_row"]`（示例 `example_meanrev` 已改）。分时 `build_row` 的累计值由已完成 bars 计算，保留并切片。
- **D2 参数变体**：`snapshots.publish_parameter_variant` 基于已冻结快照生成 **新 params_hash/snapshot_id** 的引用；provider 缓存前校验 `params` 与 `params_hash` 一致，伪造引用直接拒绝。基线/变体工厂统一由 `runtime.make_frozen_runtime_factories(provider, ref)` 产出（研究编排与回归测试共用），每个 split/成本压力各建独立实例（`build(fresh=True)`），禁止复用可变策略对象。
- **D3 worker 闭环**：worker 生命周期内保留快照根以支持 lazy `import helpers`；用户 `print` 重定向到 stderr，协议 stdout 不被污染；CLI 用户策略与 Web 一样默认走 worker（内建不走）；父进程不永久改 sys.path；父进程在 run/live/experiment 的 finally 关闭 provider 与子进程。
- **D4 因子不在父进程执行**：`freeze_strategy` 的用户因子版本改为静态 AST 元数据 + 模块/helpers 哈希（与 build 时版本公式一致），不 import 用户因子；Web 账户/实验路径父进程不加载因子模块，因子 build 仍在显式 CLI 路径执行。
