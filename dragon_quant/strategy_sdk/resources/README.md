# dragon-quant Strategy SDK（用户 workspace 指南）

本目录随 `dragon-quant` wheel 一起安装，只装 PyPI 包即可使用，无需 clone 仓库。

## 目录约定

一个 workspace 的典型结构：

```
my-workspace/
  workspace.json          # 元信息：schema_version / workspace_id / 策略清单 / 当前选择
  strategies/<slug>/
    strategy.json         # descriptor（id / label / sdk_version / entrypoint / parameters ...）
    strategy.py           # Python 实现，导出 create_strategy(params) -> Strategy
  helpers/                # 用户共享代码（可被策略 import）
  factors/                # 用户自定义因子（SPECS = [FactorSpec, ...]）
  snapshots/              # 运行时自动生成的冻结快照
  experiments/            # 实验记录
  AGENTS.md               # 面向 agent 的入口说明（init 自动生成）
```

## 策略契约

`strategy.json` 字段：

| 字段 | 必填 | 说明 |
|------|------|------|
| `id` | 是 | 必须为 `user:<slug>`，且与所在目录名一致 |
| `label` | 是 | 展示名 |
| `description` | 否 | 说明 |
| `sdk_version` | 是 | 固定 `1` |
| `entrypoint` | 否 | 工厂符号，默认 `create_strategy` |
| `parameters` | 否 | 参数 schema：`{name: {type, default, min, max, ...}}` |
| `required_factors` | 否 | 必要因子名列表；缺失时该候选默认不产生新开仓 |
| `data_requirements` | 否 | 数据级别声明（如 `1d` / `5m`） |

`strategy.py` 必须导出：

```python
def create_strategy(params: Mapping[str, object]) -> Strategy: ...
```

`Strategy` 协议（见 `dragon_quant.strategy_sdk`）：

- `evaluate_entry(ctx: StrategyContext) -> StrategyIntent | None`
- `evaluate_exit(ctx: StrategyContext) -> StrategyIntent | None`
- `explain(ctx) -> list[Mapping]`（可选）

`StrategyContext` 是**深度只读**的每事件快照：`timestamp / trade_date / phase /
candidate / row / position / account / parameters / factors`。晚于当前事件的
日K/分时不会进入 context。`factors` 是 `{name: FactorValue}`；缺失值
`FactorValue.value is None`，不得当作 0。

`StrategyIntent` 字段：`action`（buy/sell）、`reason_code`、`reason_text`、
`priority`（有限数值）、`position_pct`（买入，0<值<=100，建议上限）、
`exit_fraction`（卖出，仅 0.5 或 1）、`evidence`（JSON 可序列化）。

返回 `None` 表示无信号。方向错误、NaN/Infinity、越界、未知结构会**显式失败**，
不会被当作无信号吞掉。策略不得写库、调用数据源或修改引擎状态。

## 命令

```bash
dragon-quant workspace init /path/to/my-workspace
dragon-quant workspace check --workspace /path/to/my-workspace
dragon-quant strategy list --workspace /path/to/my-workspace --json
dragon-quant strategy validate user:example_momentum --workspace /path/to/my-workspace
dragon-quant strategy use user:example_momentum --workspace /path/to/my-workspace
dragon-quant buy --strategy user:example_momentum
dragon-quant sell
dragon-quant review-account --from 20260101 --to 20260201 --strategy user:example_momentum
```

`strategy use` 是**持久切换**；`--strategy` 只覆盖单次命令，不改保存的默认。
切换只影响新开仓，旧持仓按开仓时冻结的版本退出。

## 模板

`templates/` 下有两份示例，仅演示 API，无有效性宣称：

- `example_momentum`：高开承接入场 + 自定义止损/止盈退出。
- `example_meanrev`：回踩均线承接入场 + 移动保护退出。

## 研究闭环（agent）

发现能力 → 检查数据/因子覆盖 → 冻结基线 → 写可证伪假设 → 新增独立策略候选
→ validate → 同输入回测 → 时间切分与成本压力 → 因子/规则消融 → 保存代码、参数、
数据引用与试用次数 → 报告收益/回撤/交易数/费用滑点/覆盖/样本外/相对基线。
证据不足时如实返回，不得把不足样本当成结论。**实验成功不会自动切换活动策略。**
