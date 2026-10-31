# development — Agent Skill 包、Web UI 构建与测试/E2E 细则

> 触发：改动 Agent Skill 包（安装器 / manifest / 版本 / 发布）、Web UI 前端源码与构建、E2E harness 安全边界 / golden / 覆盖映射，或核对发布打包边界时读本文。
> 本文权威：Skill 包布局/安装/状态机/版本策略/内容边界/发布、Web UI 前端构建与服务器入口细节、测试与 E2E 细则、`docs/` 不进入发布物的打包边界。
> 上游权威：AGENTS.md（硬约束与测试门禁命令）；`skills/dragon-quant/SKILL.md` 是运行时路由层，`README.md` 是人类命令手册。

## Agent Skill 包（npx 安装）

仓库内置单主 Agent Skill 与零运行时依赖的 npm 安装器，让 agent 不必读完整 `AGENTS.md` 就能完成命令路由、前置自检、输出解读、异常分流与写操作确认。

- **布局**：`skills/dragon-quant/SKILL.md`（薄路由层）+ `references/{commands,outputs,playbooks,operations,safety}.md`（按需加载）；根 `package.json`（`name=dragon-quant-skills`）+ `bin/dragon-quant-skills.mjs` + `lib/*.mjs`（安装器）；`test/*.test.mjs`（`node:test`，零依赖）；Python 侧 `dragon_quant/tests/test_skill_{package,content,routes}.py`。
- **安装**：`npx dragon-quant-skills install` 默认写项目 `.claude/skills/dragon-quant`；`--global` 才写用户目录；`--target-dir DIR` 表示项目根；`--dry-run` 不写盘并打印最终绝对路径。`doctor` 检查安装完整性、CLI 版本、多 scope 遮蔽与外来源 lockfile；`uninstall` 只删 manifest 内文件。
- **状态机**：`absent/foreign/corrupt/clean/modified/up-to-date` 由 `lib/manifest.mjs` 单一分类，install/uninstall/doctor 共用。manifest（`.dragon-quant-skill.json`）记录内容 SHA-256、包版本与 `min_cli_version`；默认拒绝覆盖用户修改，`--force` 先整目录备份再替换；卸载不删用户新增文件。
- **版本策略**：npm `MAJOR.MINOR` 跟随 Python 包，`PATCH` 可独立；兼容性唯一契约是 `metadata.min-cli-version`，不是版本相等。测试校验 npm major.minor == pyproject major.minor、skill-version == package version。
- **安装与 CLI 解耦**：安装器只读探测 Python CLI（`--python` > `DQ_PYTHON` > PATH > `python3 -m` > `python -m`），绝不执行 pip；CLI 缺失/过低时 install 仍 exit 0 并警告，doctor exit 1 并给修复命令。
- **安全边界**：skill 安全矩阵覆盖全部 16 个顶层命令；T0 只读、T1 写本地/远端探针需确认、T2 外发/破坏性需 dry-run + 二次确认。`assets doctor` 会远端写探针（T1），`storage clear` 不删 `.db` 但属破坏性（T2）。
- **内容边界**：skill 只做路由/预检/解读/异常/门禁，**不复制评分权重、策略参数数值、SQLite 表结构**；`AGENTS.md` 与 `review_account/strategy/STRATEGY.md` 是权威。`dragon_quant/tests/test_skill_content.py` 用禁用模式与 `StrategyConfig` 字段表强制校验。
- **兼容通道**：`npx skills add gitBingxu/dragon-quant` 与手工复制均不校验 CLI 版本、无 manifest；安装器会把无 manifest 的目录判为 `foreign`，不静默覆盖。远端通用安装与 Windows 路径本期未实测。
- **测试**：`npm test` 跑 Node 全量；`.venv/bin/python -m pytest dragon_quant/tests/test_skill_package.py dragon_quant/tests/test_skill_content.py dragon_quant/tests/test_skill_routes.py` 跑内容/路由对齐。`test/packaging.test.mjs` 做 `npm pack` 白名单与本地 tarball `npm exec` E2E（隔离 HOME/目标目录）。
- **发布**：npm 包 `dragon-quant-skills` 已发布，可通过 `npx dragon-quant-skills install` 安装；官方 registry 与 npmmirror 的仓库外 dry-run 已实测。未配置 provenance/Trusted Publishing，不自动提交 git。方案与残余风险见 `docs/solutions/2026-10-24-agent-skills-design.md`。

## Web UI 架构与 `dashboard`

源码 `web_ui/frontend/`（Vite+React+TS+Mantine），产物 `web_ui/dist/`（已入库并随 wheel 分发）。运行期只需 Python，**不需要 Node**；改前端时才需 `npm run build`（`tsc --noEmit && vite build`，`base: "/"` 支持 `/scans/<id>` 深链刷新）。

**三层结构**：`web_ui/backend/` 是 FastAPI 应用（`app.create_app` 工厂 / `serve.start_dashboard` 预绑定 socket + uvicorn / `routers/*` / `deps.py` 只读仓储适配 / `static.py` SPA + Cache-Control）；`web_ui/frontend/` 是源码；`web_ui/dist/` 是构建产物。`web_ui.start_dashboard` 是 **deprecated 薄壳**，内部委托 `start_dashboard`，保留旧签名供 `python -m web_ui.server` 与 `review --ui*` / `review-account --ui*` 过渡。

**公共入口**：`web_ui.start_dashboard(port=8765, open_browser=True, default_source="v2", default_page="review", *, settings=None, block=True)`，`port=0` 由 OS 分配且 URL 取真实端口；`block=False` 返回 `DashboardHandle`（`.port` / `.stop()`），**不接管信号**。SIGTERM 由本项目安装的处理器转成 `server.should_exit`，进程以非零码退出（E2E W8 冻结）。

**`dashboard` 命令（`commands/dashboard.py`）**：`--port` 未给时默认 8765，被占用则警告一次并 `port=0` 重试一次；显式端口被占用 → stderr 可操作错误 + `SystemExit(1)`；`EACCES` 等非占用错误原样抛出。首次调用若 `[web]`（fastapi+uvicorn）不可用：`--yes` 或 `DQ_WEB_AUTO_INSTALL=1` 直接安装；TTY 下询问（默认 Y）；非 TTY / 拒绝 → stderr 手动指令 + exit 1。安装命令固定 `sys.executable -m pip install "dragon-quant[web]"`，**绝不**使用 `--break-system-packages` / `--user`，失败（含受限环境拒绝子进程）降级为手动指令。`review --ui*` / `review-account --ui*` 走同一安装门禁并打印一次弃用提示。

**数据访问**：`web_ui` 内零 `sqlite3`、零自建连接、零建表写表；只经 `deps.py` 调用既有只读仓储（白名单见技术方案 §5.2）。`assets.db` 读函数一律 `create=False`，缺失返回空态；首次 `/api/dragons` 与今天一致幂等建 `dragon.db` schema（不违反 B1：只约束 import / `-h`）。

**依赖**：`[project.optional-dependencies]` 的 `web = fastapi>=0.162 / uvicorn>=0.54`（裸 uvicorn），`dev` 额外含 `httpx` 供 `TestClient`。基础安装仍只有 playwright/tmdx。`import web_ui` / `web_ui.backend` / `web_ui.backend.serve` 零 fastapi import、零目录副作用。

## 测试与 E2E 细则

- **安全边界**：`dragon_quant/tests/e2e` 永不打开真实 `dragon.db` / `assets.db` / `prep.db`，只对目录/文件做 `os.stat` 哨兵比对；每个业务命令在独立临时根下运行，隔离 `DQ_DATA_DIR` / `HOME` / `TMPDIR`，注入 fake provider/backend/Playwright/固定时钟与 socket/DNS 拦截，禁止真实网络、Cookie、R2/B2。真实目录元数据变化即失败，不得重跑淡化。
- **覆盖映射**：`dragon_quant/tests/e2e/coverage-map.json` 固定 54 个 parser 节点 / 16 个顶层命令 / 隐藏别名 / 无参数实际帮助 / 独立入口，`test_coverage_map.py` 强制每个节点映射到真实测试，新增命令必须同步更新。
- **golden 纪律**：E2E golden 只在批准的行为变更时更新；结构重构不得改 golden。

## 打包边界

- `docs/agent/*.md` 只服务 agent 与维护者，**不进入任何发布物**：npm 安装器白名单显式禁止 `docs/`（`test/packaging.test.mjs`），Python wheel 的 `packages.find` 只含 `dragon_quant` / `web_ui`。新增或修改本文档不会影响 `npm test` 与打包 E2E。
