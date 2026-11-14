# development — Agent Skill 包、Web UI 构建与测试/E2E 细则

> 触发：改动 Agent Skill 包（安装器 / manifest / 版本 / 发布）、Web UI 前端源码与构建、E2E harness 安全边界 / golden / 覆盖映射，或核对发布打包边界时读本文。
> 本文权威：Skill 包布局/安装/状态机/版本策略/内容边界/发布、Web UI 前端构建与服务器入口细节、测试与 E2E 细则、`docs/` 不进入发布物的打包边界。
> 上游权威：AGENTS.md（硬约束与测试门禁命令）；`skills/dragon-quant/SKILL.md` 是运行时路由层，`README.md` 是人类命令手册。

## Agent Skill 包（npx 安装）

仓库内置单主 Agent Skill 与零运行时依赖的 npm 安装器，让 agent 不必读完整 `AGENTS.md` 就能完成命令路由、前置自检、输出解读、异常分流与写操作确认。

- **定位**：Skill 是面向「交易日助手」的窄接口，只开放十一类稳定能力（个股行情 / 扫描与真龙 / 盘后复盘 / 集合竞价 / 盘前预案 / 龙头回测 / 账户回测 / 因子入库与读回 / 可视化面板 / 研究读回与对照实验 / 自定义策略与用户因子）。未开放能力统一走固定话术 + issue 链接，不以命令、路由、表格或否定示例出现在 Skill 正文。
- **布局**：`skills/dragon-quant/SKILL.md`（薄路由层）+ `references/{commands,outputs,reports,playbooks,operations,safety,premarket,evidence,research,workspace,datasets}.md`（按需加载）；根 `package.json`（`name=dragon-quant-skills`）+ `bin/dragon-quant-skills.mjs` + `lib/*.mjs`（安装器）；`test/*.test.mjs`（`node:test`，零依赖）；Python 侧 `dragon_quant/tests/test_skill_{package,content,routes}.py`。
- **唯一白名单**：`references/commands.md` 的 `<!-- routes:start/end -->` 表是 Skill 侧唯一命令真源；`references/safety.md` 的 `<!-- safety:start/end -->` 只登记同一白名单的 T0/T1。每条 route 的顶层命令必须属于 `{data, scan, dragons, review, prep, auction, premarket, factor, review-account, research, dashboard, workspace, strategy}`；`data` 只允许 `quote/kline/minute/batch-quote`，`factor` 只有 `build/query/list/status/export/eval/decision-review`（`fin` 不开放），`research` 只允许 `datasets/schema/query/scan-quality/account/signals/snapshots/strategy/experiments`，`workspace` 只允许 `init/check`，`strategy` 只允许 `list/current/validate/use`。被排除的顶层命令与 `data`/`factor` 子命令由测试从 CLI `-h` 动态枚举并断言不出现；`scan_v2` 与 `--source v1` 禁止出现。
- **路由纪律**：只对白名单叶子命令取 help（如 `data quote -h`、`factor build -h`），不要取父级 `data -h` / `factor -h`。默认安全分级只有 T0/T1；`dashboard` 是 T0 但长驻且可能初始化/校验本地 schema，默认只读、不生成 BS 派生数据；显式 `--bs-autogen` / `DQ_BS_AUTOGEN=1` 才生成，`--no-bs-autogen` / `DQ_BS_AUTOGEN=0` 始终优先，且禁止 `--yes` 自动安装。
- **因子入库**：`references/operations.md` 的 `<!-- factor-groups:start/end -->` 块固定 scan 7 / prep 23 / auction 17 三组名单，测试与 `FACTOR_SPECS.source` 逐组比较。`factor build` 默认构建全部注册因子（`--all` 为等价兼容参数）；skill 运维默认执行全量构建，再逐因子核对 `status` 并读回代表因子；只有需要补/核对某一组时才用 `--only` 精确名单。workspace 用户因子只需显式 `--workspace`（默认全量已包含），流程与契约由 `references/workspace.md` 拥有（init → 字面量 `SPECS` → 登记 → build/query → 策略消费 → validate/回测 → 可选持久切换/Web）。
- **报告与 workflow**：`references/reports.md` 拥有 prep 九段 / auction 七段骨架与缺失回退；`operations.md` 拥有单能力 vs 每日复合、UI 生命周期、因子入库核对；`outputs.md` 拥有字段语义与 `strategy_context` 六段（盘前字段由 `premarket.md` 拥有）。
- **安装目标与路径**：`bin/dragon-quant-skills.mjs` 消费 `lib/paths.mjs` 的 `AGENT_TARGETS` 唯一注册表；六个 harness 为 `claude-code` / `codex` / `openclaw` / `hermes` / `deepseek` / `pi`，`agents` 仅作 `.agents/skills` 兼容别名、不进 `all`。注册表字段：`projectBase`（`projectRoot` / `cwd` / `workspace`）、`projectRel`、`global`（`homeRel` 或 `env + defaultRel + rel`）、`hints`（按 scope）、`sameName`。`resolveSkillsRoot` 接受 `cwd` / `env` 注入：`--target-dir` 始终优先；Pi 用 cwd、OpenClaw 用 workspace（默认 cwd），不套用 git root；三个官方 env（`OPENCLAW_STATE_DIR` / `DSH_HOME` / `PI_CODING_AGENT_DIR`）只覆盖 global，空串回落默认、`~/` 展开 home、相对路径按 cwd。install 主循环、跨 scope 警告与 doctor `--all-scopes` 都必须传 `cwd` / `env`。
- **安装**：`npx dragon-quant-skills install` 默认写项目 `.claude/skills/dragon-quant`；`--agent a,b` 多选按输入顺序去重，`all` 原位展开六个 harness；`--global` 才写用户目录；`--target-dir DIR` 表示项目根（Pi / OpenClaw 表示工作目录 / workspace）；`--dry-run` 不写盘并打印最终绝对路径且不输出 reload 提示。install 的 `agents[]` 在原有键上新增 `hints: string[]`（dry-run 为空），顶层 JSON 键与 manifest schema 不变。多目标顺序执行、错误即停、不回滚。`doctor` 检查安装完整性、CLI 版本、多 scope 遮蔽与外来源 lockfile，并按选中目标聚合去重后的 reload/trust 提示；`uninstall` 只删 manifest 内文件。
- **交互式多选（`lib/agent-select.mjs`）**：仅当命令是 `install`、未显式给 `--agent`、非 `--json`，且 `stdin` / `stdout` / `stderr` 三流均为 TTY 时，在 stderr 渲染六 harness 多选（默认勾选 `claude-code`，不显示 `agents` 别名）。`shouldPromptInstall` 是唯一 TTY 判定入口；`createSelectionState` / `normalizeSelectionKey` / `reduceSelection` 为无 IO 纯函数，`selectAgents` 用 `readline.emitKeypressEvents` 做流 Adapter，`input` / `output` 可注入。键位：`↑`/`↓` 首尾环绕、空格切换、`a` 全选/清空、Enter 确认（空选择停留并提示，不退出）、`q` / `Ctrl+C` / 流 `end`/`close` 返回 `null`；CLI 取消时 exit 0 且零写入（含不创建 skills 根目录），交互异常转 `io` 错误 exit 1。`finally` 统一恢复交互前 raw 状态、移除自身 `keypress`/`error`/`end`/`close` 及交互期间新增的 `data` 监听并 `pause()`；选择结果 `join(",")` 后仍走 `expandAgents` 与既有安装状态机，JSON 结构不变。`--agent` / `--json` / 任一标准流非 TTY 完全跳过并沿用默认 `claude-code`；`doctor` / `uninstall` 永不弹菜单。`main(argv, io)` 支持注入 `stdin`/`stdout`/`stderr`/`home`/`env` 供测试隔离。
- **自托管守卫**：OpenClaw 项目目标是 `<workspace>/skills`，在 dragon-quant 仓库根运行时会与包内 `skills/` 源目录重合。install / uninstall 在所有 roots 解析完成后、任何写操作前整体 exit 2；`--force` 不可绕过，doctor 只读不受影响。消费项目或 `--target-dir` 指向临时 workspace 可正常安装。
- **状态机**：`absent/foreign/corrupt/clean/modified/up-to-date` 由 `lib/manifest.mjs` 单一分类，install/uninstall/doctor 共用。manifest（`.dragon-quant-skill.json`）记录内容 SHA-256、包版本与 `min_cli_version`；默认拒绝覆盖用户修改，`--force` 先整目录备份再替换；卸载不删用户新增文件。
- **版本策略**：npm 包与 Skill 使用独立语义化版本，不跟随 Python 包；`skill-version == package version`（当前 `1.0.2`）。CLI 兼容性唯一契约是 `metadata.min-cli-version`（当前 `0.8.1`），它只声明所需 CLI 最低版本，与 npm / Skill / Python 版本号均不比较大小。
- **安装与 CLI 解耦**：安装器只读探测 Python CLI（`--python` > `DQ_PYTHON` > PATH > `python3 -m` > `python -m`），绝不执行 pip；CLI 缺失/过低时 install 仍 exit 0 并警告，doctor exit 1 并给修复命令。
- **安全边界**：skill 安全矩阵只覆盖白名单十一类能力的 T0/T1（含 research 读回、实验 run、workspace/strategy）；T0 只读（含长驻 UI 与显式 validate），T1 写本地数据库/workspace 文件/用户指定文件，按用户给出的任务范围持续有效。外发/破坏性/高风险操作不开放，走通用未开放话术。workspace 源码与 `workspace.json` 编辑属于用户已授权的开发任务，不逐文件确认；研究/实验成功不自动切换活动策略。
- **内容边界**：skill 只做路由/预检/解读/报告模板/异常/门禁，**不复制评分权重、策略参数数值、SQLite 表结构**；`AGENTS.md` 与 `review_account/strategy/STRATEGY.md` 是权威。`dragon_quant/tests/test_skill_content.py` 用禁用模式与 `StrategyConfig` 字段表强制校验；`references/reports.md` 固定 prep 九段 / auction 七段、事实/解读/待验证分栏与缺失回退；`references/premarket.md` 拥有盘前作战卡八段模板（一页结论 / 剧本 / 角色 / 作战卡 / 时间表 / 持仓 / 不做什么 / 归因），`references/evidence.md` 拥有外部信源/时点/去重/冲突/降级，`references/workspace.md` 拥有面向消费项目的自定义策略/用户因子渐进指南（含可运行最小示例与 wheel 资源读取方式）。
- **未开放话术**：Skill 正文统一使用固定话术「当前功能还不稳定，暂未对 Agent 开放。你可以到 https://github.com/gitBingxu/dragon-quant/issues 反馈需求或问题。」，不列被排除命令名；行为场景与 P0 判定由 main 编排。
- **兼容通道**：`npx skills add gitBingxu/dragon-quant` 与手工复制均不校验 CLI 版本、无 manifest；安装器会把无 manifest 的目录判为 `foreign`，不静默覆盖。远端通用安装与 Windows 路径本期未实测。
- **测试**：`npm test` 跑 Node 全量；`.venv/bin/python -m pytest dragon_quant/tests/test_skill_package.py dragon_quant/tests/test_skill_content.py dragon_quant/tests/test_skill_routes.py` 跑内容/路由对齐。`test/paths.test.mjs` 锁六目标 project/global 路径、env 覆盖与 `all` 集合；`test/agent-select.test.mjs` 锁 TTY 条件矩阵、键盘 reducer、假流确认/取消/异常与清理（raw 恢复、监听移除、pause、output 隔离）；`test/cli.test.mjs` 锁多选、env、cwd/workspace、目标提示、dry-run、部分失败与自托管守卫（run helper 默认清空三个官方 env），并用 `main(argv, io)` 假 TTY 覆盖默认 Enter、双目标真实安装、取消零写入与 `--agent`/`--json`/非 TTY 跳过；`test/doctor.test.mjs` 锁按目标提示与同名说明；`test/packaging.test.mjs` 做 `npm pack` 白名单（含 `lib/agent-select.mjs`）与本地 tarball `npm exec` 双目标 E2E（隔离 HOME/目标目录）。
- **发布**：维护者运行 `scripts/publish-skill.sh [--dry-run] [--tag TAG] [--access public|restricted]`（2FA 用 `NPM_OTP` 环境变量）。脚本先跑版本契约（package == skill-version、min-cli-version == Python 版本）、Python Skill 测试、Node 测试、`npm pack` 白名单与本地安装冒烟；同一 tarball 用于发布。dry-run 不需要 token；真发布检查命名分支/干净工作区/版本不存在，再隐藏读取 token，用权限 0600 的临时 npmrc（异常/信号路径清理）。`test/publish-skill.test.mjs` 用 fake npm/python/git 验证参数白名单（tag/access/OTP）、dry-run 与真发布同参数、全链 `npm_config_offline`、0600 配置与 SIGTERM 清理、main 分支发布、同一 tarball 与空 token 拒绝，不触网。PyPI 侧 `scripts/publish.sh` 是薄壳，编排在 `scripts/release/publish_pypi.py`：版本只经 argv、隔离准备与校验先于 git 变更、精确文件提交、原子推送、dry-run 零远端写入、`--resume` 校验工件 SHA256、base/release commit、branch、upstream（固定 `origin/<branch>`）、origin push URL 指纹、本地/远端 tag 与远端 branch 身份；旧状态缺少 upstream/指纹时 fail-closed，不补写当前值；上传恢复按注册表文件名+sha256 只补缺失；gh 缺失保持 pending。发布凭据错误收敛为干净报错：解密失败打印「密码错误」并 exit 1（不裸 traceback；解密按字节捕获先判 returncode、无效 token 按 `pypi-` 前缀拒绝），顶层未预期异常同样打印阶段与 `--resume` 提示。dry-run 使用独立状态目录、Python 构建 `--no-isolation`；staging 内先跑必要测试，失败前零 git 变更。npm 包 `dragon-quant-skills` 已发布，可通过 `npx dragon-quant-skills install` 安装；未配置 provenance/Trusted Publishing，不自动提交 git。方案与残余风险见 `docs/solutions/2026-10-24-agent-skills-design.md`。

## Web UI 架构与 `dashboard`

源码 `web_ui/frontend/`（Vite+React+TS+Tailwind CSS v4，构建期依赖；运行期仅 react/react-dom），产物 `web_ui/dist/`（已入库并随 wheel 分发）。运行期只需 Python，**不需要 Node**；改前端时才需 `npm run build`（`tsc --noEmit && vite build`，`base: "/"` 支持 `/scans/<id>` 深链刷新）。

**本地开发（双进程，HMR）**：终端 1 跑后端 `python -m dragon_quant dashboard --no-browser`（默认 8765，需已装 `[web]`）；终端 2 跑 `npm --prefix web_ui/frontend run dev`（Vite 默认 5173），`vite.config.ts` 的 `server.proxy` 已把 `/api` 转发到 `http://127.0.0.1:8765`，因此**开发时只访问 5173**，改 `src/**` 热更新、数据仍来自真实后端。两个坑：① macOS 上访问 `http://localhost:5173`，不要用 `127.0.0.1:5173`（Vite 监听 `localhost`，在 macOS 解析到 `::1`，`127.0.0.1` 直接连接被拒）；② **8765 只托管 `web_ui/dist` 构建产物**，想在 `dragon-quant dashboard` 里看到源码改动必须 `npm run build`（产物入库，与源码同批提交），否则「改了没生效」。首次或 `node_modules` 缺失时先 `npm --prefix web_ui/frontend install`；不想碰真实库时加 `DQ_DATA_DIR=<临时目录>`。

**三层结构**：`web_ui/backend/` 是 FastAPI 应用（`app.create_app` 工厂 / `serve.start_dashboard` 预绑定 socket + uvicorn / `routers/*` / `deps.py` 只读仓储适配 / `static.py` SPA + Cache-Control）；`web_ui/frontend/` 是源码；`web_ui/dist/` 是构建产物。`web_ui.start_dashboard` 是 **deprecated 薄壳**，内部委托 `start_dashboard`，保留旧签名供 `python -m web_ui.server` 与 `review --ui*` / `review-account --ui*` 过渡。

**公共入口**：`web_ui.start_dashboard(port=8765, open_browser=True, default_source="v2", default_page="review", *, settings=None, block=True)`，`port=0` 由 OS 分配且 URL 取真实端口；`block=False` 返回 `DashboardHandle`（`.port` / `.stop()`），**不接管信号**。SIGTERM 由本项目安装的处理器转成 `server.should_exit`，进程以非零码退出（E2E W8 冻结）。

**`dashboard` 命令（`commands/dashboard.py`）**：`--port` 未给时默认 8765，被占用则警告一次并 `port=0` 重试一次；显式端口被占用 → stderr 可操作错误 + `SystemExit(1)`；`EACCES` 等非占用错误原样抛出。首次调用若 `[web]`（fastapi+uvicorn）不可用：`--yes` 或 `DQ_WEB_AUTO_INSTALL=1` 直接安装；TTY 下询问（默认 Y）；非 TTY / 拒绝 → stderr 手动指令 + exit 1。安装命令固定 `sys.executable -m pip install "dragon-quant[web]"`，**绝不**使用 `--break-system-packages` / `--user`，失败（含受限环境拒绝子进程）降级为手动指令。`review --ui*` / `review-account --ui*` 走同一安装门禁并打印一次弃用提示。

**数据访问**：`web_ui` 内零 `sqlite3`、零自建连接、零建表写表；只经 `deps.py` 调用既有只读仓储（白名单见技术方案 §5.2）。`assets.db` 读函数一律 `create=False`，缺失返回空态；首次 `/api/dragons` 与今天一致幂等建 `dragon.db` schema（不违反 B1：只约束 import / `-h`）。

**Web 写操作唯一例外（BS 派生数据，2026-11-03）**：`web_ui/backend/bs_runner.py` 是标准库-only 的
单飞状态机（`idle → running → succeeded | failed`），只拼 argv 并 `subprocess.Popen` 委派
`python -m dragon_quant bs-eval [--force]`；**不 import `bs_eval`、不建连接、不写库**，写入发生在子进程。
`create_app` 只装配 `app.state.bs_runner`；`serve._serve` 在 `create_app` 后、`serve` 前按
`DashboardSettings.bs_autogen` 触发 `start_incremental("auto")`（`lifespan="off"` 不变），`finally`
调 `terminate()`。开关链：`dashboard --no-bs-autogen` / `DQ_BS_AUTOGEN` → `commands.dashboard._resolve_bs_autogen`
→ `web_ui.server.start_server(bs_autogen=...)` → settings（字段默认 **False**，程序化/测试安全）。
`GET /api/bs/jobs/status` 与 `POST /api/bs/rebuild`（202/409/500/403 非本机 Origin）见
[bs_eval](bs_eval.md)。E2E `EnvSandbox.env()` 固定注入 `DQ_BS_AUTOGEN=0`；`procguard` 默认拒绝业务
子进程，真实生成只在单元层用 fake spawn 覆盖。v1 无超时、多实例无跨进程锁（按单实例使用）。

**前端 v2 与三态主题**：dashboard 前端固定 v2——旧 `?source=v1` 深链忽略 query、按 v2 渲染，页面与 `api/*` 不再产生 v1 请求（后端 v1 API / 表 / `--source v1` 完全保留）。主题为 system / light / dark 三态，偏好存 `localStorage["dq.theme"]`；`index.html` 的 head 内联同步脚本在首次绘制前设置 `html[data-theme]` / `color-scheme` / meta theme-color，避免闪烁。浅色只覆盖 `@theme` 语义 token（`[data-theme=light]`），不改布局、字体或红涨绿跌语义。**主题化颜色禁止使用 Tailwind 斜杠透明度**（如 `bg-white/5`、`border-critical/35`）：构建期会固化成字面量，不随 `[data-theme=light]` 变化；需要 alpha 时用显式 `-soft` / `-line` / `-focus` token 或 CSS `color-mix()`。`dragon_quant/tests/test_web_frontend_theme.py` 是零依赖构建契约测试，守护 dist 主题脚本、浅色 token、v1 文案清理与资产引用；改前端后必须 `npm run build`，让 `web_ui/dist` 与源码同批更新。发布时 `publish.sh` 会在写入版本号与任何 git add/commit/tag/push 之前先执行 `npm --prefix web_ui/frontend run build`，确保 dist 进入发布提交；发布机需有 Node/npm 且已安装前端 devDependencies（脚本不自动 npm install/ci）。

**依赖**：`[project.optional-dependencies]` 的 `web = fastapi>=0.162 / uvicorn>=0.54`（裸 uvicorn），`dev` 额外含 `httpx` 供 `TestClient`。基础安装仍只有 playwright/tmdx。`import web_ui` / `web_ui.backend` / `web_ui.backend.serve` 零 fastapi import、零目录副作用。

**入选后表现与展示精修（2026-10-31）**：新增只读 `GET /api/dragons/{trade_date}/{code}/performance`，路由注入 `deps.kline_provider()`（复用 `XueqiuProvider`，仅该端点）与 `deps.clock_module()`（仅解析缺省 `as_of`）；计算在 `dragon_quant/review/performance.py` 纯函数中完成，不写库、不改 CLI / 固定 5 日回测。白名单增量为 `dragon_quant.review.performance`（纯计算）与 `dragon_quant.infra.clock`（路由解析 `as_of`），`XueqiuProvider` 复用仅限绩效端点。前端新增 `HintIcon` / `CollapsibleSection`、`pages/prep/prepModel.ts` 投影与 `dragon_quant/tests/test_web_frontend_results.py` 零依赖契约测试；`trade-dates` 增 `items` 全量。改前端后仍必须 `npm --prefix web_ui/frontend run build`，让 `web_ui/dist` 与源码同批更新。

**近几日卡片原始扫描榜（2026-11-01 增量）**：`routers/dragons.py` 的 `trade-dates` 改从 `deps.scan_repository()` 的 `list_scan_dates` / `list_scan_stock_contributions_by_date` 组装卡片（日期与 `list_dragon_trade_dates` 取并集），明细/绩效在 `dragons_v2` 缺行时回退当日扫描贡献。只新增只读组合，不改 `scan/storage`、`review`、DB schema、CLI 与前端源码，因此 `web_ui/dist/**` 与 `coverage-map.json` 的 parser 计数均不变；E2E D7 覆盖该口径（`seed_builders.scan_db_seed` 新增可选 `stocks` / `top_n`，默认行为不变）。

**运维/复盘成效投影（2026-11-01 增量）**：`web_ui/backend/presenters/` 新增纯投影层（`scan_view` / `prep_view` / `auction_view` / `auction_effect`），路由只做参数与响应；`deps.py` 白名单增量：`dragon_quant.infra.board_height`、`dragon_quant.infra.factor.decision_review(+_store)`、`dragon_quant.scan.storage.logs`、`dragon_quant.infra.assets.db`（`prep_calendar_rows` / `auction_calendar_rows` / `list_auction_asset_names` / `list_stock_daily_bars` / `first_prep_record_date` / `first_prep_snapshot_date`）与无 I/O 的 `review_assets_enabled()`。新端点：`/api/prep/calendar`、`/api/prep/{date}/review-context`、`/api/auction/calendar`、`/api/auction/{date}/effect`、`/api/scans/{id}/logs`。前端新增 `AssetCalendar` 与 `/auction` 页；排版统一 `html{font-size:14px}` + 六个 `--text-*` 令牌（所有可见文字 ≥1rem），语义色收敛为 `danger/warning/normal/positive`（+soft/line/text），删除 `warn/critical/info`，A 股 `up/down` 独立。零依赖契约测试为 `dragon_quant/tests/test_web_frontend_ops.py`；改前端后必须 `npm --prefix web_ui/frontend run build` 让 dist 同批更新。

**核心问题直答（2026-11-01 增量）**：新增只读 `GET /api/prep/{date}/plan-followup`（评估日 `T`，**无 `source` 参数**、固定 v2）：样本锚**计划日 `D`**（`plan_review.plan_date`，缺失回退上一快照日期并给 `plan_date_fallback` 告警，与上一快照不一致给 `plan_date_mismatch`），复用 `infra.factor.decision_review` + `decision_review_store.build_store("prep")` 与本地 `stock_daily`（窗口 `[T-120, T+30]`），输出逐股 T+1/3/5 净收益与 recommended / not_recommended / data_insufficient 组均值（只暴露 `valid / excluded / mean / median`，不暴露命中率与质量标签）。空态一律 200：`snapshot_missing` / `no_plan` / `plan_snapshot_missing|corrupt|unsupported` / `plan_samples_missing` / `local_bars_missing` / `insufficient_samples`。`presenters/local_market.py` 抽出共享 `LocalStockDailyMarket` / `LOCAL_CAVEATS` / `shift_day`（`auction_effect` 继续再导出，既有 import 不破），新投影在 `presenters/prep_effect.py`。`review-context` 的 `plan_effect.market` 增 `verification`：空仓验证结论只在后端 `prep_view._classify_verification` 产生，前端只渲染。前端新增 `MiniBar`、`pages/scans/ScanHealthBand`、`pages/prep/PrepSummaryBand`，`DataTable` 增可选 `rowClassName`；扫描详情错误置顶 + 真龙行置顶高亮，竞价页删逐股折叠块、T+1/3/5 并入主表、强弱差升为 KPI。零 sqlite3 / 零自建连接 / 零写库，只经既有 `assets_repository` / `decision_review_store` / `local_bars_loader` 三个依赖（`deps.py` 不新增函数）。测试增量：`test_dashboard_plan_followup.py`（pure presenter / 验证规则表）、`test_web_backend.py`（端点与 verification）、`test_web_frontend_ops.py`（新契约）、E2E D11；改前端后仍必须 `npm --prefix web_ui/frontend run build`。

**复盘/竞价 3A 后端增量（2026-11-02）**：新增无 I/O 共享严格核 `dragon_quant/review/strict_metrics.py`（review CLI 与 Web 共用，`review.main` 保留 `_is_valid_bar` / `_strict_review` 兼容别名）与 `presenters/{calendar_defaults,bars_loader,snapshot_io,accuracy,day_effect}.py`。`deps.py` 新增 `bar_calendar_loader`（只读 `assets_db.list_stock_daily_bar_dates`）；该仓储函数与 `list_stock_daily_bars` 一样 `create=False`。新增路由 `routers/accuracy.py`（`/api/prep/{date}/effect`、`/api/prep/accuracy`、`/api/auction/accuracy`），**必须先于** `assets.router` 注册（静态 accuracy 不能被动态 `{trade_date}` 吞掉）；日历响应追加 6 个默认日期字段。全历史日 K 走 `load_bars_resilient`（日期二分 → code 二分，单 code+单日仍截断则整体降级，不输出部分统计）。测试：`test_review_strict_metrics.py`、`test_dashboard_{calendar_defaults,prep_effect,accuracy}.py`、`test_assets_db_calendar.py`、`test_web_backend.py`、E2E D10/D12；3A 不含前端源码与 `dist` 改动。

**复盘/竞价 3B 前端增量（2026-11-02）**：`AssetCalendar` 重做为 GitHub Contributions 风格（列=周、行=周一…周日、**按月份分块（组标题=月份、组间加大间距）**/星期标签、「色块+文字」图例（无/缺失/完整；后端 `missing` 视觉并入「缺失」）、**无格内图标**、`sticky top-14 z-20` 避开 56px 顶栏、容器 `overflow-x-auto`、原生 `button` + `aria-label` / `aria-current`），`/prep` 与 `/auction` 共用。新增共用 `AccuracyBoard`（固定请求 `/api/prep/accuracy` 或 `/api/auction/accuracy`，**不随选中日变化**；展示最新可验证日、正确/有效/排除/命中率、逐股聚合与 caveats；文案明确「历史样本描述性统计，不构成策略有效性结论」，auction 排除按「条数」）。`/prep` 固定五区块：日历 → 核心结果（市场情绪/主线/是否建仓/推荐建仓标的/资产完备与异常摘要）→ 当前 T 日效果（真主线次日涨跌 + 推荐/不推荐逐股严格收益/回撤 + 组内汇总）→ 当日资产明细 → 全历史准确率；删除旧「结论」「运行概况」「上次计划复盘」及页面对 `plan-followup` / `review-context` 的请求（后端端点保留）。`/auction` 固定四区块：日历 → 竞价结果（只删风险事件/决策质量/关键证据三列，保留 T+1/3/5，仍取 `auction/{date}/effect.selected_day`）→ 当日资产采集与异常 → 全历史准确率；删除运行概况与窗口成效聚合展示。默认日期改用后端 `calendar.default_date`（URL `?date=` 优先）。契约测试同步更新 `test_web_frontend_ops.py` / `test_web_frontend_results.py`；改前端后必须 `npm --prefix web_ui/frontend run build` 让 dist 同批更新。

**复盘/竞价表格分页（2026-11-02 增量）**：新增 `components/PaginatedDataTable.tsx`（装饰式组合 `DataTable` + `Pagination`：默认 10 条/页、10/25/50/100、仅 `total > 10` 渲染分页、`resetKey` 变化回第 1 页、行数缩短时钳制到末页）。页码计算唯一入口仍是 `lib/pagination.ts` 的 `usePagination`（新增可选 `resetKey` / `minTotalToShow` 与 `showPagination`，旧 4 个调用点零改动）。`/prep`、`/auction` 共 10 张表全部迁移（含两页共用 `AccuracyBoard`）；其他页面保持直接 `DataTable` / 旧 `usePagination` 接线（opt-out）。`DataTable` 仅导出 `DataTableProps<T>`，运行时行为不变；`Pagination` 改为 `<nav aria-label="表格分页">`，页大小 select 增 `aria-label="每页条数"`，摘要 `aria-live="polite"`，控件组窄屏 `flex-wrap`。纯前端、无后端 / DB / CLI 变化；零依赖契约测试为 `test_web_frontend_table_pagination.py`；改前端后必须 `npm --prefix web_ui/frontend run build` 让 dist 同批更新。方案见 `docs/solutions/2026-11-02-dashboard-table-pagination.md`。

**运行入口增量（2026-11-06）**：`/scans`、`/prep`、`/auction` 三页新增「快捷 + 自定义」命令入口（`RunActions` + `RunParamsModal`）。后端新增纯注册表 `web_ui/backend/run_specs.py`（job → 时间窗 / 参数白名单 / `build_argv`，未知参数一律 400，argv 以列表交给 `Popen`）与纯判定 `presenters/run_plan.py`；路由 `routers/runs.py` 提供 `GET /api/runs/{job}/spec|eligibility|status` 与 `POST /api/runs/{job}`（Origin 403 / 参数 400 / 复检不满足 409 `not_eligible` / 单飞 409）。三条命令共用 `RunJobRunner`（组合 `BsRunner` 的 `start_command(argv, job, label)` 扩展，`JobSnapshot` 增可选 `job` / `label`，BS / 回测响应键不变）；与 BS / 回测作业互不影响。可点击规则：交易日 + 时间窗（scan 15:00–23:55、prep 21:00–23:55、auction 09:25–23:55）+ 当日未完成 + 无运行中任务；**prep 额外要求当日已有扫描记录**。交易日由 `trade_calendar.TradeCalendarProbe` 复用 TDX 指数日K判定并进程内按自然日缓存，失败降级工作日口径；15:05 前不承认「不在日历 = 非交易日」（与 `build_trade_calendar` 同口径），避免 09:25 竞价窗口误判。完成探测全部 `create=False` 只读：`scans.list_scan_dates` / `assets_db.list_prep_snapshot_dates` / `list_auction_snapshot_dates`。前端禁用态用 `aria-disabled` + `HoverTip` 渲染式气泡（原生 `disabled` 会吞掉 hover，`title` 延迟长且样式不可控；说明文本以 `sr-only` 关联 `aria-describedby`），成功后自动刷新本页并切到当日。测试：`test_dashboard_run_specs.py`、`test_dashboard_run_plan.py`、`test_web_backend_runs.py`、`test_web_frontend_runs.py`；改前端后仍必须 `npm --prefix web_ui/frontend run build`。

## 测试与 E2E 细则

- **浏览器交互验收（2026-11-05 起）**：dashboard 的真实交互（回测入口 / popover 持久已读 /
  单飞 / 日历折叠吸顶 / 百分点格式 / 旧账户 / 请求乱序与失败重试）用隔离 fixture server +
  Playwright 脚本验证；fixture 使用独立 `DQ_DATA_DIR`、种子脚本与离线 provider，不打开真实
  库、不触网。脚本与启动命令见 `docs/solutions/2026-11-05-dashboard-user-reported-fixes.md`。
- **安全边界**：`dragon_quant/tests/e2e` 永不打开真实 `dragon.db` / `assets.db` / `prep.db`，只对目录/文件做 `os.stat` 哨兵比对；每个业务命令在独立临时根下运行，隔离 `DQ_DATA_DIR` / `HOME` / `TMPDIR`，注入 fake provider/backend/Playwright/固定时钟与 socket/DNS 拦截，禁止真实网络、Cookie、R2/B2。真实目录元数据变化即失败，不得重跑淡化。
- **覆盖映射**：`dragon_quant/tests/e2e/coverage-map.json` 固定 78 个 parser 节点 / 21 个顶层命令 / 隐藏别名 / 无参数实际帮助 / 独立入口，`test_coverage_map.py` 强制每个节点映射到真实测试，新增命令必须同步更新。`bs-eval` 的 BS1–BS5 映射到 `test_bs_eval.py`，只读 Web API + SPA fallback（`/bs`）映射到 `cross_cutting.bs_eval_api` 与 `web_spa_fallback`；`dashboard` 节点 flags 含 `--no-bs-autogen`，D13 映射 BS 自动生成 fail-soft。计数（78/21）由 `dragon_quant/tests/e2e/test_coverage_map.py` 与 parser 快照断言。
- **golden 纪律**：E2E golden 只在批准的行为变更时更新；结构重构不得改 golden。

## 打包边界

- `docs/agent/*.md` 只服务 agent 与维护者，**不进入任何发布物**：npm 安装器白名单显式禁止 `docs/`（`test/packaging.test.mjs`），Python wheel 的 `packages.find` 只含 `dragon_quant` / `web_ui`。新增或修改本文档不会影响 `npm test` 与打包 E2E。

## 研究入口与 dashboard 只读启动（2026-11-05）

- 次级导航入口统一叫「扫描研究」（`/research`）；导航里的「研究 / 诊断」灰字伪入口已删除。
  研究页用中文说明通过 / 否决、评分 / rank、首次 / 重复入榜对 scan 质量的反馈。
- Web 新增 `routers/research.py`：`GET /api/research/scan-quality`（只读原始扫描候选 +
  本地 `stock_daily`）与 `POST /api/account/experiments`（用户显式 `confirm=true` 才运行，
  冻结 run 的期间/资金/策略/上游母集，复用 `AccountSimulator`；本地行情缺失返回
  `insufficient_data`，不触网）。`GET /api/account/experiments` 只读已存报告，不重算。
  scan-quality 的主 horizon 收益可能缺失：bootstrap 与主窗口统计只接收该窗口的有限有效
  样本（不把 `None` 当 0），其他 horizon 的有效样本保留；基础扫描日志读取失败返回
  `insufficient_data + reason="scan_read_error"`，不吞成「区间内无扫描」。
- Web 新增 `routers/review.py` + `review_runner.py`：龙头总览「待回测」卡片的增量回测经
  显式 POST 启动 `review --recompute-all` 子进程（单飞 / 403 / 中文错误 / shutdown 清理），
  只读状态端点与页面加载绝不触发回测。契约见 [review](review.md)。
- 实验报告落 `review_account_experiments`（幂等建表）；`review_account/experiments.py`
  不复制买卖/撮合算法，fixed 60/20/20 时间切分与双倍滑点压力，样本不足显式
  `insufficient_evidence`，不自动推广参数。
- `dashboard` **默认只读**：BS 启动自动生成改为显式 opt-in（`--bs-autogen` 或
  `DQ_BS_AUTOGEN=1`；`--no-bs-autogen`/`DQ_BS_AUTOGEN=0` 优先）。页面「全部重算」/「增量计算」
  仍是显式写操作，纯查看不触发。
- `doctor` 增加实际发现路径/版本来源（manifest vs frontmatter）、同名候选路径提示
  （不推断宿主实际加载）与 CLI 叶子能力探测。

## 实验输入冻结与单飞（2026-11-05 返工，R1/R2）

- `POST /api/account/experiments` 运行前先冻结：prep 快照、龙头池、竞价行、情绪、因子模型
  版本与本地行情内容，计算 `content_sha256`；variant 只消费冻结输入，不再读活动仓储。
- 增量 run 的实验继承期初状态/base_equity/峰值/累计已实现盈亏；缺期初状态时返回
  `incremental_seed_missing`，不用空账户冒充续算。
- 账户生成与实验共用 `web_ui/backend/locks.py::ACCOUNT_HEAVY_LOCK` 单飞锁；同进程不得并行。
- 训练/验证选型后才运行 test，且 test 与双倍滑点压力只给 baseline 与选中项；
  与 baseline 同参数的 variant 标 `no_change`，不伪造消融差异。
- 本地行情 `LocalAccountMarket` 返回真实 `KBar`（15:00 Asia/Shanghai），缺日K 抛
  `DataCoverageError`；无分时走日K兜底。

## 研究 Facade 契约（2026-11-06）

- `dragon_quant/research/` 是 CLI 与 Web 共享的只读用例层：`catalog.py`（17 个数据集、字段/过滤器/排序契约）、`query.py`（注册表校验 + 分页 cursor + 可解释空态）、`scan_quality.py`（从 Web presenter 迁入的纯计算与共享编排）、`account.py`、`snapshots.py`、`strategy.py`、`experiments.py`、`bars_loader.py`、`stats.py`、`local_account_market.py`。Web presenter 只做兼容转发。
- SQL 与字段映射留在 owner 的 `research_reads.py`（`scan/storage`、`review_account`、`infra/assets`、`infra/factor`）；通用严格只读连接归 `infra/storage/readonly.py`。`research` 不新建影子业务表，不 import Web/FastAPI/provider。
- CLI `research` 固定 `source=v2`，`--json` stdout 只有一个 `research_v1` envelope；缺库/缺表/空集 exit 0 且 `empty_reason` 可解释，参数/cursor 错误 exit 2，只读不可用等真实故障 exit 1。实验 `run` 是唯一写动作，必须 `--run-id N --confirm`，未知 sell-rule 报参数错误。
- Web `GET /api/research/scan-quality` 与 CLI 走同一 `run_scan_quality`；`POST /api/account/experiments` 与 CLI 走同一 `run_account_experiment`，进程内锁 + 数据目录 `locks/account_experiment.lock` 进程级互斥。`list/show` 与 query 不建锁文件。
- 大 payload（`result` / `analysis` / `report`）默认不返回，需 `--fields` 显式选择；`experiments show` 显式返回完整报告。跨库读取标 `meta.non_atomic_read=true`，只有实验报告声称输入冻结。
- 收益/回撤/胜率等字段是百分比点（1.0=1%）；scan/账户/信号/prep/auction 固定 v2，账户子表经父 run、实验经 base run；factor `source` 是业务来源，不按 v2 过滤。prep `--code` 只认结构化股票键，未知旧结构返回 `code_filter_unsupported`。
- 只读连接 sidecar 策略：非空 WAL 无 `-shm` → `readonly_unavailable`；有 `-shm` → `mode=ro`；无 WAL/SHM → `immutable=1`。损坏 JSON 行标 `data_quality=degraded` + `corrupt_json_fields`。

## 实验期初状态与因子冻结（2026-11-05 D1/D3）

- `seed_from_run` 只读批次自身冻结的 `start_equity/start_cash/start_positions/
  start_peak_equity/start_consecutive_losses/start_realized_pnl`；**禁止**用
  `run.date_to` 期末快照倒放期初。旧增量 run 缺 `start_positions_json` 记录时按
  不可复现处理，不按空期初伪造。
- `review_account_runs` 幂等新增 `start_cash/start_peak_equity/
  start_consecutive_losses/start_marks_json`；新 run 完整记录期初现金、盯市、
  峰值与连亏。
- train/validation/test 均从本批期初状态独立重启，报告显式
  `split_seed_policy=independent_restart_from_period_start`、
  `split_historical_continuity=false`，不宣称跨区间连续持仓复现。
- 因子 variant 一次性冻结模型版本与所需因子值/PIT 读取结果；所有 split/压力
  共用同一 `factor_loader` + 模型。运行中出现 `factor_mode=degraded` 即
  `insufficient_data`，不把降级排序当有效实验。
- `research_prep_bypass` 仅内部实验使用；普通账户生成入口传入即 400。

## workspace / skill / 真实交互边界（2026-11-11）

- 用户策略的 Web 与 CLI 执行固定走 `dragon_quant/workspace/worker.py` 子进程（版本化 JSON 协议）；父进程只读 descriptor、因子与快照校验，不导入用户代码（内建策略仍在进程内）。E2E 中 `procguard` 默认拒绝一切业务子进程；仅 `DQ_E2E_ALLOW_WORKER=1` 时放行唯一 argv `[python, "-m", "dragon_quant.workspace.worker"]`，worker 继承同一沙箱注入（netguard/procguard 仍生效），WS6 用例覆盖该路径。
- Web 策略选择/执行默认只读，需 `dashboard --workspace-writes`（或 `DQ_DASHBOARD_WORKSPACE_WRITES=1`）显式启用；跨站 Origin 一律拒绝。
- 真实浏览器交互用隔离 `DQ_DATA_DIR`/workspace + `PLAYWRIGHT_BROWSERS_PATH` 指向本机已装 chromium 的脚本验证（列表→选择→保存→CLI 读取→CLI 切换→Web 刷新→账户表单下拉），截图留证；E2E pytest 因 conftest 隔离浏览器目录而不跑真实浏览器。
- wheel 消费测试构建本地 wheel，在临时 venv `--no-deps` 安装后执行用户策略与用户因子，并重复安装验证用户代码不被覆盖；构建失败即失败，不跳过。
