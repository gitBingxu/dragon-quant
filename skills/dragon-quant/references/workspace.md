# workspace — 自定义策略与用户因子（渐进指南）

**何时读我**：用户要在独立 workspace 里写策略或因子、把模块登记进 `workspace.json`、构建/读回用户因子、回测或切换用户策略，或打开 Web `/strategies` 页时。

**消费项目前提**：只装了 PyPI 包 + 本 Skill 时，以 `-h`、`strategy list`、`factor list` 与 JSON 输出为运行时契约；本指南给可执行流程与最小示例，不假设仓库文档存在。workspace 源码、`workspace.json`、`factors/*.py`、`strategies/**` 的编辑属于用户已授权的开发任务：在明确任务范围内连续完成，不逐文件、逐步重复询问；越出范围或用户撤回时才补确认。

## 1. 初始化与检查

```bash
dragon-quant -v
dragon-quant workspace init /path/to/my-workspace --json
dragon-quant workspace check --workspace /path/to/my-workspace --json
```

- `init` 创建 `workspace.json`（`schema_version=1`、路径派生的 `workspace_id=ws_<12hex>`、`revision`）、`strategies/`、`helpers/`、`factors/`、`snapshots/`、`experiments/` 与两份示例策略；**不覆盖已有文件**，并登记本机活动 workspace。
- `check` 会导入用户策略并检查全部 descriptor/因子，输出 `ok/errors`；属于显式验证路径。
- 路径优先级：`--workspace` > `DQ_WORKSPACE` > `DQ_DATA_DIR/workspace-selection.json`。数据目录由 `DQ_DATA_DIR` 控制，与 workspace 分离。
- `workspace_id` 与目录路径绑定；复制 workspace 到新路径后旧 `workspace.json` 会被拒绝。`init` 不覆盖已有文件：需先移走旧 `workspace.json` 再 `init`（会重建登记，策略/因子需重新登记），或手工把 ID 改成当前路径派生值。

## 2. 读 wheel 内的 SDK 指南与模板（可执行）

```bash
python -c "from dragon_quant.strategy_sdk import read_resource; print(read_resource('README.md'))"
python -c "from dragon_quant.strategy_sdk import resources_dir; print([p.name for p in resources_dir().iterdir()])"
python -c "import importlib.resources as r; print(r.files('dragon_quant.strategy_sdk.resources').joinpath('templates/example_momentum.strategy.py').read_text())"
```

项目内用 `.venv/bin/python`。`templates/` 下有两份可复制模板：`example_momentum.strategy.{json,py}` 与 `example_meanrev.strategy.{json,py}`（仅演示 API，无有效性宣称）。

## 3. 写用户因子 `factors/*.py`

文件必须定义 **字面量** `SPECS = [FactorSpec(...), ...]`（列表/元组 + 关键字字面量）。`factor list` 与策略冻结用 AST 读取这份元数据，**不导入**用户因子；`workspace check` 只做因子文件存在性与语法编译检查（策略部分会导入）。动态拼装的 `SPECS` 会被忽略，策略声明消费时会明确报「未注册」。

最小可运行示例（`source="prep"`，只读已存在的 prep 快照）：

```python
from dragon_quant.strategy_sdk import FactorFrame, FactorSpec


def compute(ctx):
    values = {}
    rows = ctx.source_rows.get("review_candidates") or []
    for row in rows:
        code = row.get("code")
        score = row.get("review_score")
        if not code or score is None:
            continue          # 缺失不写行，绝不补 0
        values[code] = float(score) / 100.0
    return FactorFrame(values=values, source="prep")


SPECS = [
    FactorSpec(
        name="prep_review_pct",
        label="复盘评分（百分之一）",
        frequency="1d",
        source="prep",
        available_phase="prep",
        pit_rule="derived",
        compute=compute,
        impl_key="user.prep_review_pct.v1",
    ),
]
```

契约要点：

- `FactorSpec` 必填 `name / label / frequency / source / compute / impl_key`；`source` 只支持已接线源：`scan`、`vpa`、`fin`、`prep`、`auction`。其他值 build 返回 `missing_source`（未接入），不是 0。
- `compute(ctx: FactorContext) -> FactorFrame` 必须是纯函数：只读 `ctx.source_rows / ctx.codes / ctx.trade_date / ctx.as_of / ctx.calendar / ctx.params`，不触网、不写库、不改全局状态。
- `FactorFrame.values` 是 `code -> float`；没有值的 code **不出现**。`available_at` 是源数据实际可知时点：`recorded` 源填 `source_rows` 的 `created_at/as_of`；`derived` 可省略，由 `available_phase` 解析。
- 源数据先在本地生成：`scan` 需要该日 canonical 扫描记录，`prep`/`auction` 需要该日快照，`vpa` 需要 `vpa_analysis`，`fin` 需要本地财务数据。没有源就 `missing_source`；**不承诺任意数据源**。
- 作品名：本地名 `prep_review_pct`，注册名是 `ws_<短 workspace_id>.prep_review_pct`。真实名字以 `factor list --workspace ... --json` 输出为准，不要手算哈希。
- 版本：内容哈希（规范化 spec + 模块文件 + `helpers/`）；改模块或 helpers 自动变版本，`factor list` 会显示 `version`。

## 4. 在 `workspace.json` 登记因子模块

`workspace.json` 的 `factors` 是 **workspace 内相对路径**数组（拒绝绝对路径与 `..`）；下面是结构示意，**不要整份覆盖 init 生成的文件**：保留实际 `workspace_id`、已有策略/因子及保存选择，只向相应数组追加路径。通过 workspace 配置 API 写入时使用当前 `revision` 做 CAS；直接编辑 JSON 时先重读当前文件，避免与 Web/CLI 并发修改。

```json
{
  "schema_version": 1,
  "workspace_id": "ws_0123456789ab",
  "revision": 2,
  "selected_strategy_id": "builtin:dragon_pullback_daily",
  "selected_parameters": {},
  "strategies": ["strategies/example_momentum/strategy.json"],
  "factors": ["factors/prep_review.py"]
}
```

登记后：

```bash
dragon-quant factor list --workspace /path/to/my-workspace --json
```

从输出取真实 `ws_<short>.<local>` 名称、`version` 与 `available_phase`。`factor list --workspace` 只用 AST 元数据、不导入用户代码；`factor status` 只报告内建注册表/存储状态；`factor query / build / eval / export --workspace` 才导入并执行用户因子。

## 5. 构建与读回用户因子

```bash
# 默认构建当前注册表全部因子（内建 + 本 workspace 用户因子）
dragon-quant factor build --date 20261110 \
  --workspace /path/to/my-workspace --json

# 只补/核对单个用户因子时，用精确注册名收窄
dragon-quant factor build --date 20261110 \
  --only ws_0123456789ab.prep_review_pct \
  --workspace /path/to/my-workspace --json

dragon-quant factor query --date 20261110 \
  --name ws_0123456789ab.prep_review_pct \
  --workspace /path/to/my-workspace --json
```

核对（不能只看退出码）：

1. `reports[0].factors[<注册名>].status`：`ok` 有行；`empty` 源可用但当日无值；`missing_source` 源不可用/配置 fail-closed；`error` 必须报告。
2. `rows > 0`；`version` 与 `factor list` 一致；`available_at` 符合该源时点（如 prep 为 `T@16:40`）。
3. `factor query` 的 `values` 有该 code 的值，且 `resolved_versions` 含该因子与版本；缺失/未来不可用不补零。
4. 同一因子在多日使用时，对每个候选 `trade_date` 分别构建；不要假设一次构建覆盖任意日期。

`factor status` 目前只报告内建注册表/存储状态，不接受 `--workspace`；用户因子状态用 `factor list` + `build` + `query` 核对。

## 6. 写用户策略

目录 `strategies/<slug>/`，`strategy.json`：

```json
{
  "id": "user:factor_consumer",
  "label": "示例·消费用户因子",
  "sdk_version": 1,
  "entrypoint": "create_strategy",
  "parameters": {"min_factor": {"type": "number", "default": 0.55}},
  "required_factors": ["prep_review_pct"],
  "data_requirements": ["1d", "5m"]
}
```

- `id` 必须是 `user:<slug>` 且与目录名一致；`sdk_version` 固定 `1`；`label` 必填；`parameters` 的 `type` 只允许 `number/int/bool/string`。参数还可选写 `label/help/unit/step`（以及既有 `min/max/enum`）作为 Web 表单展示元数据，不影响保存数据形状。
- `required_factors` 写**本地因子名**（或已注册的内建名）；运行时映射为冻结的注册名。缺失/未注册在校验或运行期显式失败，不静默当无信号。
- `strategy.py` 必须导出 `create_strategy(params) -> Strategy`，实现 `evaluate_entry(ctx)`、`evaluate_exit(ctx)`，可选 `explain(ctx)`。

最小策略示例（消费上面的用户因子）：

```python
from dragon_quant.strategy_sdk import StrategyIntent


class FactorConsumer:
    descriptor = {"id": "user:factor_consumer", "sdk_version": 1}

    def __init__(self, params):
        self.min_factor = float((params or {}).get("min_factor", 0.55))

    def _factor_value(self, ctx):
        # ctx.factors 的键是冻结后的注册名 ws_<short>.<local>；按键名后缀匹配可跨路径。
        for name, fv in ctx.factors.items():
            if name.endswith(".prep_review_pct"):
                return fv.value
        return None

    def evaluate_entry(self, ctx):
        if ctx.phase != "open":
            return None
        value = self._factor_value(ctx)
        if value is None or value < self.min_factor:
            return None
        return StrategyIntent(
            action="buy",
            reason_code="factor_gate",
            reason_text=f"用户因子 {value:.3f} 达标",
            priority=60,
            position_pct=20.0,
            evidence={"factor": value},
        )

    def evaluate_exit(self, ctx):
        return None


def create_strategy(params):
    return FactorConsumer(params)
```

运行时语义：

- `ctx` 是每事件**深度只读**快照：`timestamp / trade_date / phase / candidate / row / position / account / parameters / factors`。晚于事件的日 K/分时不会进入 context。
- `phase` 常见 `open / bar / late / close`；开盘事件只公开可知字段（`open`、`prev_row`、`bar_*`、限制/事件字段），当日最终 OHLCV 与其派生指标（如当日 MA）不可见——需要均线读 `ctx.row["prev_row"]`。
- `ctx.factors[name]` 是 `FactorValue`：`value` 可为 `None`，`status` 为 `ok/missing/unavailable`；**缺失不是 0**。`required_factors` 缺失时该候选不产生新开仓，退出信号也会跳过并记原因。
- 返回 `StrategyIntent`：`action` 必须与评估方向一致；`priority` 有限数值；买入 `position_pct` 在 `(0, 100]`（建议上限）；卖出 `exit_fraction` 只能 `0.5` 或 `1`；`evidence` 必须 JSON 可序列化。非法输出**显式失败**，不退内建策略。
- 成交、费用、T+1、手数、涨跌停、同 code 幂等与共享仓位/现金约束仍由系统执行，策略不能绕过。

## 7. 登记、校验与回测

把 descriptor 路径加入 `workspace.json` 的 `strategies` 数组，然后：

```bash
dragon-quant strategy list --workspace /path/to/my-workspace --json
dragon-quant strategy validate user:factor_consumer --workspace /path/to/my-workspace --json
dragon-quant workspace check --workspace /path/to/my-workspace --json

# 回测前先为候选 trade_date 构建所需因子（见第 5 节）
dragon-quant review-account --from 20261106 --to 20261110 --capital 100000 \
  --strategy user:factor_consumer --workspace /path/to/my-workspace --json
```

- `--json` 回执含真实 `run_id` 与汇总；`strategy_ref` 记录 `snapshot_id / params_hash / required_factors / factor_versions / sdk_version / toolkit_version`。详细结果用 `research account --run-id N --view summary|snapshots|trades|positions|events` 读回。
- 省略 `--strategy` 时沿用保存选择（无 workspace 选择则用内建）；`--strategy` 只覆盖本次命令，不改保存默认。
- 首次运行先发布冻结快照，再从快照执行：descriptor、有效参数、策略源码、`helpers/`、用户因子都被冻结。之后改源码不影响已冻结运行与旧持仓；**切换只影响新开仓，旧持仓按开仓时冻结的引用退出**。
- 快照缺失、内容哈希不一致、SDK/工具版本不兼容时明确拒绝（不换用当前源码）；需新建回测。
- 回测需要本地行情/缓存与候选池（默认 prep 快照，缺失时按回退池并报告）。`DataCoverageError` 是数据覆盖问题，不是策略结论。
- 若日志/明细出现 `strategy_required_factor_missing`：先按候选 `trade_date` 构建该用户因子并核对注册名与版本，再重跑。

## 8. 持久切换与 Web 策略页

```bash
dragon-quant strategy use user:factor_consumer --workspace /path/to/my-workspace --json
dragon-quant strategy current --workspace /path/to/my-workspace --json
```

- `strategy use` 是**持久切换**（写 `workspace.json`，revision CAS；冲突非零退出并保留原文件）。只在用户明确要求切换活动策略时执行；研究/回测结果不得自动切换。
- Web：`dashboard --workspace-writes`（或 `DQ_DASHBOARD_WORKSPACE_WRITES=1`）显式启用后，`/strategies` 页可列出策略、查看当前选择并持久切换；默认只读，`--no-workspace-writes` 优先。
- 选择接口：`GET /api/strategies`、`GET /api/strategies/current` 只读 descriptor；`POST /api/strategies/selection` 需同源且已启用写开关（未启用 403、跨站/非法 400、revision 冲突 409）。
- Web 与 CLI 共读同一保存选择；账户回测表单可选策略并真正发送 `strategy_id`，省略时消费保存选择。启用写后用户代码在独立 worker 子进程执行，HTTP 进程不导入用户代码。
- 实盘交易信号不在本 Skill 白名单；研究/回测任务不隐含实盘授权，需要实盘操作时由用户自行执行或另行明确授权。

## 9. 研究闭环（与 research 配合）

1. 发现能力与数据/因子覆盖，缺失如实记录。
2. 冻结内建或用户基线，写可证伪假设与预期方向。
3. 在独立策略目录新增候选，不改 site-packages。
4. 先 `strategy validate`，再用同一输入 `review-account`；区分 `no_signal` 与执行失败。
5. 时间切分（样本外）+ 费用/滑点压力 + 因子/规则消融 + 交易归因。

```bash
dragon-quant research experiments list --run-id N --json
# T1：必须真实 run_id + --confirm；基准 run 需有 strategy_ref
dragon-quant research experiments run --run-id N --confirm --json
```

`list/show` 只读已存报告，不重算；`run` 单飞，使用冻结的 `strategy_ref` 在 60/20/20 split 真正执行；`--strategy-param k=v` 只对已有 `strategy_ref` 的基准 run 生效，并发布参数变体快照。证据不足（`insufficient_data`/`insufficient_evidence`）是有效结论；`no_change` 不是改进。**实验成功不会自动切换活动策略、不会写交易记账。** 详见 [research.md](research.md)。

## 10. 常见错误

| 现象 | 原因与处理 |
|---|---|
| `workspace 未初始化` | 先 `workspace init`；或用 `--workspace` / `DQ_WORKSPACE` 指向正确目录 |
| `workspace_id 与目录路径不一致` | 复制过 workspace；移走旧 `workspace.json` 后重新 `init` 并登记（或手工修正 ID） |
| `因子模块 ... 缺少 SPECS` / 注册表校验失败 | 用字面量 `SPECS = [FactorSpec(...)]`；检查重名、悬空依赖、循环 |
| `missing_source` | 该日源数据不存在（scan/prep/auction/vpa/fin）；先生成源或换日期；不是 0 |
| `策略声明的必要因子 ... 未注册` | 因子未登记到 `workspace.json`、名字拼错、或 `SPECS` 非字面量 |
| 回测有候选但不下单 | 看 `strategy_required_factor_missing` / `unavailable`；按候选 `trade_date` 构建因子 |
| `revision` 冲突 | 重新读取 `workspace.json` 后重试；不要覆盖原文件 |
| Web 403 / 409 / 400 | 未启用 `--workspace-writes` / revision 冲突 / 跨站或非法输入 |
| 旧持仓不跟随新策略 | 预期行为：旧持仓按开仓时冻结引用退出 |

安全门禁与写操作确认见 [safety.md](safety.md)；命令白名单见 [commands.md](commands.md)。
