<p align="center">
  <img src="https://raw.githubusercontent.com/gitBingxu/dragon-quant/main/assets/icon.png" width="120" alt="dragon-quant icon">
</p>

<h1 align="center">dragon-quant 缅A龙头识别工具</h1>

<p align="center">
  <a href="https://pypi.org/project/dragon-quant/"><img src="https://img.shields.io/pypi/v/dragon-quant" alt="PyPI version"></a>
  <a href="https://pypi.org/project/dragon-quant/"><img src="https://img.shields.io/badge/python-%3E%3D3.10-blue" alt="Python >= 3.10"></a>
  <a href="https://github.com/gitBingxu/dragon-quant/blob/main/LICENSE"><img src="https://img.shields.io/pypi/l/dragon-quant" alt="License"></a>
  <a href="https://pypi.org/project/dragon-quant/"><img src="https://img.shields.io/pypi/dm/dragon-quant" alt="Downloads"></a>
  <a href="https://github.com/gitBingxu/dragon-quant"><img src="https://img.shields.io/github/stars/gitBingxu/dragon-quant" alt="GitHub stars"></a>
  <img src="https://img.shields.io/badge/stable-scan%20%7C%20review-brightgreen" alt="Stability: scan and review">
</p>

一款通过多维量化评分识别市场龙头的工具。基于 [TMDX](https://pypi.org/project/tmdx/) 获取股票行情，使用 SQLite 持久化数据，并具备完备的选股与选股回测能力。

## 目录

- [稳定性说明](#稳定性说明)
- [快速使用](#快速使用)
- [Agent Skill](#agent-skill)
- [龙头扫描](#龙头扫描)
- [龙头回测](#龙头回测)
- [设计哲学](#设计哲学)
- [目录结构](#目录结构)
- [免责声明](#免责声明)
- [欢迎共建](#欢迎共建)
- [License](#license)

## 稳定性说明

选股与选股回测能力已经稳定，暂时不再迭代。当前重点是搭建模拟账户交易与回测系统，打通盘后复盘、集合竞价和账户交易链路。

| 功能 | 状态 | 预期稳定时间 |
|------|------|--------------|
| `scan` 龙头扫描 | 稳定 | — |
| `review` 龙头回测 | 稳定 | — |
| `prep` 盘后复盘 | 快速迭代中 | 2026 年 12 月初 |
| `auction` 集合竞价强度判断 | 快速迭代中 | 2026 年 12 月初 |
| `review-account` 账户模拟交易与回测 | 快速迭代中 | 2026 年 12 月初 |
| `agent-skill` Agent 交易日助手 | 快速迭代中 | 2026 年 12 月中 |

## 快速使用

要求 **Python ≥ 3.10**，推荐使用 `pipx` 安装：

```bash
pipx install dragon-quant

# 当日收盘后扫描龙头
dragon-quant scan --top 5

# 回测扫描结果并打开面板
dragon-quant review --ui
```

只需查看已有回测结果时，运行 `dragon-quant review --ui-only`。

## Agent Skill

仓库内置单主 Agent Skill，让 Claude Code 等 Agent 按「交易日助手」定位使用 dragon-quant。安装器支持六个真实 harness 的项目级与用户级目标，可逗号多选或用 `all`；在真正的终端里裸跑 `install` 会弹出多选菜单：

```bash
npx dragon-quant-skills install                    # 终端里多选；直接 Enter 只装 claude-code
npx dragon-quant-skills install --agent hermes     # 单个目标，跳过菜单
npx dragon-quant-skills install --agent claude-code,hermes   # 多选，一次写两个目录
npx dragon-quant-skills install --agent all        # 六个 harness
npx dragon-quant-skills install --global --agent pi            # 用户级
npx dragon-quant-skills doctor                     # 检查安装完整性、CLI 版本与遮蔽
```

交互菜单只列出六个真实 harness（不含 `agents` 别名），默认勾选 `claude-code`：

- `↑` / `↓` 移动（首尾环绕），`空格` 切换当前项，`a` 全选 / 清空，`Enter` 确认；空选择时 Enter 不离场并提示「至少选择一个 agent」。
- `q` 或 `Ctrl+C` 取消：进程 exit 0，不写任何文件（skills 根目录也不会创建）。
- 仅在 `install`、未显式给 `--agent`、未加 `--json`，且 `stdin` / `stdout` / `stderr` **全部是 TTY** 时弹菜单；菜单与提示只写 stderr，stdout 仍只保留安装摘要。任一标准流被重定向（CI、管道到 `jq`、`2>log`）都会跳过交互并按默认 `claude-code` 安装。
- 由 Makefile、`npm run`、任务运行器等非人类调用但仍继承 TTY 的脚本，请显式传 `--agent` 或 `--json`，避免等待按键。`doctor` 与 `uninstall` 永不弹菜单。

| agent | 项目级目录（project scope） | 用户级目录（--global） |
|---|---|---|
| `claude-code`（默认） | `<projectRoot>/.claude/skills` | `~/.claude/skills` |
| `codex` | `<projectRoot>/.agents/skills` | `~/.agents/skills` |
| `openclaw` | `<workspace>/skills` | `${OPENCLAW_STATE_DIR:-~/.openclaw}/skills` |
| `hermes` | `<projectRoot>/.hermes/skills` | `~/.hermes/skills` |
| `deepseek`（developer preview） | `<projectRoot>/.dsh/skills` | `${DSH_HOME:-~/.dsh}/skills` |
| `pi` | `<cwd>/.pi/skills` | `${PI_CODING_AGENT_DIR:-~/.pi/agent}/skills` |
| `agents`（兼容别名） | `<projectRoot>/.agents/skills` | `~/.agents/skills` |

- `all` 展开为六个真实 harness（`claude-code,codex,openclaw,hermes,deepseek,pi`），不包含兼容别名 `agents`。
- Pi 与 OpenClaw 以当前工作目录 / workspace 为基准，不向上寻找 git root；`--target-dir` 对它们始终优先，表示工作目录。
- 三个官方环境变量只影响 `--global`：空串视为未设置，`~/` 展开为 home，相对路径按 cwd 解析。
- 安装成功后按目标输出提示：Hermes 项目运行 `hermes skills trust <projectRoot>`，Pi 编辑后 `/reload`，DeepSeek 自动发现 skills 根的直接子项，其余按客户端重载。安装器不自动修改任何 trust 或客户端配置，也不宣称客户端已完成真实加载验证。
- 在 dragon-quant 仓库根使用 `--agent openclaw` 会命中包内 `skills/` 源目录，install / uninstall 会直接拒绝（`--force` 也不能绕过）；请到消费项目运行，或用 `--target-dir` 指向临时 workspace。`all` 会在项目根创建 OpenClaw 的 `skills/`，不希望时请显式列出目标。

Agent 可用的能力限定为七类：个股行情、扫描与真龙、盘后复盘、集合竞价、龙头回测、账户回测、因子入库与读回。Skill 只做路由、预检、报告模板与写操作门禁，不复制评分权重或策略参数；报告默认在对话中交付，写文件需确认。

仓库内 Skill 版本可能高于 npm registry 已发布版本；安装后用 `npx dragon-quant-skills doctor` 核对实际安装的 Skill 版本、来源与最低 CLI 版本，不要假定 `npx` 拉到的就是仓库中的最新内容。

不在白名单内的能力统一回复：

> 当前功能还不稳定，暂未对 Agent 开放。你可以到 https://github.com/gitBingxu/dragon-quant/issues 反馈需求或问题。

## 龙头扫描

在**当日收盘后**运行，扫描涨停候选股，通过多维量化评分识别市场龙头：

```bash
dragon-quant scan --top 5
```

![龙头扫描示例](https://raw.githubusercontent.com/gitBingxu/dragon-quant/main/assets/scan.png)

## 龙头回测

这里的回测结果采用**宽松口径**：入选后首个非一字板交易日按最低价买入，统计后续最多 5 个交易日的最大收益与回撤。

```bash
# 回测已到观察期的扫描结果
dragon-quant review

# 打开龙头回测面板
dragon-quant review --ui-only
```

![龙头回测面板](https://raw.githubusercontent.com/gitBingxu/dragon-quant/main/assets/review.png)

## 设计哲学

**龙头不是预判出来的，而是根据盘面证据识别出来的。** 评分器从带动性、领涨性、抗跌性、流动性和资金承接五个维度评估候选股：四项核心特征设置门槛，资金承接作为补充证据，避免单一高分掩盖明显短板。

模拟交易与回测的目标，是把盘后复盘、集合竞价和账户交易串成可回放、可验证的决策链，用统一的候选池、买卖规则、费用与成交假设检验策略，而不只展示选股收益。

详细设计见[评分器规范](archived_md/score/评分器Refactor.md)与[模拟交易和回测方案](archived_md/review_account/2026-10-20-live-trade-alignment.md)。

## 目录结构

```text
dragon_quant/
├── commands/        # CLI 命令入口
├── scan/            # 龙头扫描、五维评分与结果持久化
├── review/          # 龙头回测
├── review_account/  # 盘后复盘、集合竞价、模拟账户与交易策略
├── vpa/             # 量价分析
├── infra/           # 行情数据源、SQLite、缓存、因子与资产同步
└── tests/           # 单元、集成与离线 E2E 测试
web_ui/              # 回测与账户可视化面板
assets/              # 图标与 README 截图
docs/                # 模块与开发文档
archived_md/         # 已落地的调研与技术方案
```

## 免责声明

本工具仅供学习交流，不提供任何个股买卖建议。评分、回测与模拟交易的所有输出均为技术研究结果，不构成投资建议；据此操作产生的盈亏由使用者自行承担。

## 欢迎共建

欢迎通过 Issues 分享使用反馈、讨论策略与提出改进，也欢迎提交 PR 参与共建。

## License

[MIT](LICENSE)
