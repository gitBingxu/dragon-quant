# development — Agent Skill 包、Web UI 构建与测试/E2E 细则

> 触发：改动 Agent Skill 包（安装器 / manifest / 版本 / 发布）、Web UI 前端源码与构建、E2E harness 安全边界 / golden / 覆盖映射，或核对发布打包边界时读本文。
> 本文权威：Skill 包布局/安装/状态机/版本策略/内容边界/发布、Web UI 前端构建与服务器入口细节、测试与 E2E 细则、`docs/` 不进入发布物的打包边界。
> 上游权威：AGENTS.md（硬约束与测试门禁命令）；`skills/dragon-quant/SKILL.md` 是运行时路由层，`README.md` 是人类命令手册。

## Agent Skill 包（npx 安装）

仓库内置单主 Agent Skill 与零运行时依赖的 npm 安装器，让 agent 不必读完整 `AGENTS.md` 就能完成命令路由、前置自检、输出解读、异常分流与写操作确认。

- **定位**：Skill 是面向「交易日助手」的窄接口，只开放八类稳定能力（个股行情 / 扫描与真龙 / 盘后复盘 / 集合竞价 / 盘前预案 / 龙头回测 / 账户回测 / 因子入库与读回）。未开放能力统一走固定话术 + issue 链接，不以命令、路由、表格或否定示例出现在 Skill 正文。
- **布局**：`skills/dragon-quant/SKILL.md`（薄路由层）+ `references/{commands,outputs,reports,playbooks,operations,safety,premarket,evidence}.md`（按需加载）；根 `package.json`（`name=dragon-quant-skills`）+ `bin/dragon-quant-skills.mjs` + `lib/*.mjs`（安装器）；`test/*.test.mjs`（`node:test`，零依赖）；Python 侧 `dragon_quant/tests/test_skill_{package,content,routes}.py`。
- **唯一白名单**：`references/commands.md` 的 `<!-- routes:start/end -->` 表是 Skill 侧唯一命令真源；`references/safety.md` 的 `<!-- safety:start/end -->` 只登记同一白名单的 T0/T1。每条 route 的顶层命令必须属于 `{data, scan, dragons, review, prep, auction, premarket, factor, review-account}`；`data` 只允许 `quote/kline/minute/batch-quote`，`factor` 只允许 `build/query`。被排除的顶层命令与 `data`/`factor` 子命令由测试从 CLI `-h` 动态枚举并断言不出现；`scan_v2` 与 `--source v1` 禁止出现。
- **路由纪律**：只对白名单叶子命令取 help（如 `data quote -h`、`factor build -h`），不要取父级 `data -h` / `factor -h`。默认安全分级只有 T0/T1；UI-only 是 T0 但长驻且可能初始化/校验本地 schema。
- **因子入库**：`references/operations.md` 的 `<!-- factor-groups:start/end -->` 块固定 scan 7 / prep 23 / auction 17 三组名单，测试与 `FACTOR_SPECS.source` 逐组比较；构建必须显式 `--only`，禁止默认 build 与 `--all`，逐因子核对 `status` 并读回代表因子。
- **报告与 workflow**：`references/reports.md` 拥有 prep 九段 / auction 七段骨架与缺失回退；`operations.md` 拥有单能力 vs 每日复合、UI 生命周期、因子入库核对；`outputs.md` 拥有字段语义与 `strategy_context` 六段（盘前字段由 `premarket.md` 拥有）。
- **安装目标与路径**：`bin/dragon-quant-skills.mjs` 消费 `lib/paths.mjs` 的 `AGENT_TARGETS` 唯一注册表；六个 harness 为 `claude-code` / `codex` / `openclaw` / `hermes` / `deepseek` / `pi`，`agents` 仅作 `.agents/skills` 兼容别名、不进 `all`。注册表字段：`projectBase`（`projectRoot` / `cwd` / `workspace`）、`projectRel`、`global`（`homeRel` 或 `env + defaultRel + rel`）、`hints`（按 scope）、`sameName`。`resolveSkillsRoot` 接受 `cwd` / `env` 注入：`--target-dir` 始终优先；Pi 用 cwd、OpenClaw 用 workspace（默认 cwd），不套用 git root；三个官方 env（`OPENCLAW_STATE_DIR` / `DSH_HOME` / `PI_CODING_AGENT_DIR`）只覆盖 global，空串回落默认、`~/` 展开 home、相对路径按 cwd。install 主循环、跨 scope 警告与 doctor `--all-scopes` 都必须传 `cwd` / `env`。
- **安装**：`npx dragon-quant-skills install` 默认写项目 `.claude/skills/dragon-quant`；`--agent a,b` 多选按输入顺序去重，`all` 原位展开六个 harness；`--global` 才写用户目录；`--target-dir DIR` 表示项目根（Pi / OpenClaw 表示工作目录 / workspace）；`--dry-run` 不写盘并打印最终绝对路径且不输出 reload 提示。install 的 `agents[]` 在原有键上新增 `hints: string[]`（dry-run 为空），顶层 JSON 键与 manifest schema 不变。多目标顺序执行、错误即停、不回滚。`doctor` 检查安装完整性、CLI 版本、多 scope 遮蔽与外来源 lockfile，并按选中目标聚合去重后的 reload/trust 提示；`uninstall` 只删 manifest 内文件。
- **交互式多选（`lib/agent-select.mjs`）**：仅当命令是 `install`、未显式给 `--agent`、非 `--json`，且 `stdin` / `stdout` / `stderr` 三流均为 TTY 时，在 stderr 渲染六 harness 多选（默认勾选 `claude-code`，不显示 `agents` 别名）。`shouldPromptInstall` 是唯一 TTY 判定入口；`createSelectionState` / `normalizeSelectionKey` / `reduceSelection` 为无 IO 纯函数，`selectAgents` 用 `readline.emitKeypressEvents` 做流 Adapter，`input` / `output` 可注入。键位：`↑`/`↓` 首尾环绕、空格切换、`a` 全选/清空、Enter 确认（空选择停留并提示，不退出）、`q` / `Ctrl+C` / 流 `end`/`close` 返回 `null`；CLI 取消时 exit 0 且零写入（含不创建 skills 根目录），交互异常转 `io` 错误 exit 1。`finally` 统一恢复交互前 raw 状态、移除自身 `keypress`/`error`/`end`/`close` 及交互期间新增的 `data` 监听并 `pause()`；选择结果 `join(",")` 后仍走 `expandAgents` 与既有安装状态机，JSON 结构不变。`--agent` / `--json` / 任一标准流非 TTY 完全跳过并沿用默认 `claude-code`；`doctor` / `uninstall` 永不弹菜单。`main(argv, io)` 支持注入 `stdin`/`stdout`/`stderr`/`home`/`env` 供测试隔离。
- **自托管守卫**：OpenClaw 项目目标是 `<workspace>/skills`，在 dragon-quant 仓库根运行时会与包内 `skills/` 源目录重合。install / uninstall 在所有 roots 解析完成后、任何写操作前整体 exit 2；`--force` 不可绕过，doctor 只读不受影响。消费项目或 `--target-dir` 指向临时 workspace 可正常安装。
- **状态机**：`absent/foreign/corrupt/clean/modified/up-to-date` 由 `lib/manifest.mjs` 单一分类，install/uninstall/doctor 共用。manifest（`.dragon-quant-skill.json`）记录内容 SHA-256、包版本与 `min_cli_version`；默认拒绝覆盖用户修改，`--force` 先整目录备份再替换；卸载不删用户新增文件。
- **版本策略**：npm 包与 Skill 使用独立语义化版本，不跟随 Python 包；`skill-version == package version`（当前 `1.3.0`）。CLI 兼容性唯一契约是 `metadata.min-cli-version`（当前 `0.8.0`），它只声明所需 CLI 最低版本，与 npm / Skill / Python 版本号均不比较大小。
- **安装与 CLI 解耦**：安装器只读探测 Python CLI（`--python` > `DQ_PYTHON` > PATH > `python3 -m` > `python -m`），绝不执行 pip；CLI 缺失/过低时 install 仍 exit 0 并警告，doctor exit 1 并给修复命令。
- **安全边界**：skill 安全矩阵只覆盖白名单八类能力的 T0/T1；T0 只读（含长驻 UI），T1 写本地数据库/用户指定文件需本轮确认。外发/破坏性/高风险操作不开放，走通用未开放话术。
- **内容边界**：skill 只做路由/预检/解读/报告模板/异常/门禁，**不复制评分权重、策略参数数值、SQLite 表结构**；`AGENTS.md` 与 `review_account/strategy/STRATEGY.md` 是权威。`dragon_quant/tests/test_skill_content.py` 用禁用模式与 `StrategyConfig` 字段表强制校验；`references/reports.md` 固定 prep 九段 / auction 七段、事实/解读/待验证分栏与缺失回退；`references/premarket.md` 拥有盘前八段模板，`references/evidence.md` 拥有外部信源/时点/去重/冲突/降级。
- **未开放话术**：Skill 正文统一使用固定话术「当前功能还不稳定，暂未对 Agent 开放。你可以到 https://github.com/gitBingxu/dragon-quant/issues 反馈需求或问题。」，不列被排除命令名；行为场景与 P0 判定由 main 编排。
- **兼容通道**：`npx skills add gitBingxu/dragon-quant` 与手工复制均不校验 CLI 版本、无 manifest；安装器会把无 manifest 的目录判为 `foreign`，不静默覆盖。远端通用安装与 Windows 路径本期未实测。
- **测试**：`npm test` 跑 Node 全量；`.venv/bin/python -m pytest dragon_quant/tests/test_skill_package.py dragon_quant/tests/test_skill_content.py dragon_quant/tests/test_skill_routes.py` 跑内容/路由对齐。`test/paths.test.mjs` 锁六目标 project/global 路径、env 覆盖与 `all` 集合；`test/agent-select.test.mjs` 锁 TTY 条件矩阵、键盘 reducer、假流确认/取消/异常与清理（raw 恢复、监听移除、pause、output 隔离）；`test/cli.test.mjs` 锁多选、env、cwd/workspace、目标提示、dry-run、部分失败与自托管守卫（run helper 默认清空三个官方 env），并用 `main(argv, io)` 假 TTY 覆盖默认 Enter、双目标真实安装、取消零写入与 `--agent`/`--json`/非 TTY 跳过；`test/doctor.test.mjs` 锁按目标提示与同名说明；`test/packaging.test.mjs` 做 `npm pack` 白名单（含 `lib/agent-select.mjs`）与本地 tarball `npm exec` 双目标 E2E（隔离 HOME/目标目录）。
- **发布**：维护者运行 `scripts/publish-skill.sh [--dry-run]` 发布当前 `package.json` 版本；脚本自动定位仓库根目录，隐藏输入 access token，用权限 0600 的临时 npmrc 并在退出时清理，不写长期 npm 配置。token 需具备发布权限；Automation token 可避免交互 OTP。`test/publish-skill.test.mjs` 用 fake npm 验证任意目录调用、参数、空 token 拒绝与临时配置清理，不触网。npm 包 `dragon-quant-skills` 已发布，可通过 `npx dragon-quant-skills install` 安装；未配置 provenance/Trusted Publishing，不自动提交 git。方案与残余风险见 `docs/solutions/2026-10-24-agent-skills-design.md`。

## Web UI 前端构建

源码 `web_ui/frontend/`（Vite+React+TS+Mantine），产物 `web_ui/dist/`（已入库随包分发）。运行期仅靠 Python stdlib 托管，**不需要 Node**；改前端时才需 `npm run build`。

**服务器入口（B2）**：`web_ui.start_server(port=8765, open_browser=True, default_source="v2", default_page="review", *, block=True) -> HTTPServer`（`web_ui.server.start_server` 的包级重导出）是唯一公共入口。`python -m web_ui.server`、`review --ui*`、`review-account --ui*` 保持既有阻塞行为（默认 `block=True`）；`--port 0` 由操作系统分配端口，日志与浏览器 URL 取 `server.server_address` 的真实端口（永不为 0），浏览器仅在明确开启且真实 URL 已知后打开。测试/嵌入传 `block=False` 立即拿到已绑定的 `HTTPServer`，自行 `serve_forever` / `shutdown` / `server_close`；启动失败、键盘中断与 serve 循环异常都会关闭 server 并释放端口。

**端口冲突（仅 `review` CLI）**：`review` 的 `--port` 默认值为 `None` 哨兵，`commands/review._start_ui` 解析为 `8765`；未显式给 `--port` 时遇 `EADDRINUSE`（兼容 Windows `winerror=10048`）在 stderr 警告一次并仅重试一次 `port=0`，不另写 URL 逻辑。显式 `--port N` 被占用时不换端口，stderr 输出可操作错误并 `SystemExit(1)`（无 traceback）；非端口占用错误（如 `EACCES`）原样抛出。`web_ui.server.start_server` 的公共签名与「占用时抛 `OSError`」契约不变；`review-account` 端口行为/默认值完全不变。E2E 用测试自持 ephemeral blocker 验证显式冲突，不依赖共享 8765。

## 测试与 E2E 细则

- **安全边界**：`dragon_quant/tests/e2e` 永不打开真实 `dragon.db` / `assets.db` / `prep.db`，只对目录/文件做 `os.stat` 哨兵比对；每个业务命令在独立临时根下运行，隔离 `DQ_DATA_DIR` / `HOME` / `TMPDIR`，注入 fake provider/backend/Playwright/固定时钟与 socket/DNS 拦截，禁止真实网络、Cookie、R2/B2。真实目录元数据变化即失败，不得重跑淡化。
- **覆盖映射**：`dragon_quant/tests/e2e/coverage-map.json` 固定 54 个 parser 节点 / 16 个顶层命令 / 隐藏别名 / 无参数实际帮助 / 独立入口，`test_coverage_map.py` 强制每个节点映射到真实测试，新增命令必须同步更新。
- **golden 纪律**：E2E golden 只在批准的行为变更时更新；结构重构不得改 golden。

## 打包边界

- `docs/agent/*.md` 只服务 agent 与维护者，**不进入任何发布物**：npm 安装器白名单显式禁止 `docs/`（`test/packaging.test.mjs`），Python wheel 的 `packages.find` 只含 `dragon_quant` / `web_ui`。新增或修改本文档不会影响 `npm test` 与打包 E2E。
