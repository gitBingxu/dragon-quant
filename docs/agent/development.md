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

源码 `web_ui/frontend/`（Vite+React+TS+Tailwind CSS v4，构建期依赖；运行期仅 react/react-dom），产物 `web_ui/dist/`（已入库并随 wheel 分发）。运行期只需 Python，**不需要 Node**；改前端时才需 `npm run build`（`tsc --noEmit && vite build`，`base: "/"` 支持 `/scans/<id>` 深链刷新）。

**三层结构**：`web_ui/backend/` 是 FastAPI 应用（`app.create_app` 工厂 / `serve.start_dashboard` 预绑定 socket + uvicorn / `routers/*` / `deps.py` 只读仓储适配 / `static.py` SPA + Cache-Control）；`web_ui/frontend/` 是源码；`web_ui/dist/` 是构建产物。`web_ui.start_dashboard` 是 **deprecated 薄壳**，内部委托 `start_dashboard`，保留旧签名供 `python -m web_ui.server` 与 `review --ui*` / `review-account --ui*` 过渡。

**公共入口**：`web_ui.start_dashboard(port=8765, open_browser=True, default_source="v2", default_page="review", *, settings=None, block=True)`，`port=0` 由 OS 分配且 URL 取真实端口；`block=False` 返回 `DashboardHandle`（`.port` / `.stop()`），**不接管信号**。SIGTERM 由本项目安装的处理器转成 `server.should_exit`，进程以非零码退出（E2E W8 冻结）。

**`dashboard` 命令（`commands/dashboard.py`）**：`--port` 未给时默认 8765，被占用则警告一次并 `port=0` 重试一次；显式端口被占用 → stderr 可操作错误 + `SystemExit(1)`；`EACCES` 等非占用错误原样抛出。首次调用若 `[web]`（fastapi+uvicorn）不可用：`--yes` 或 `DQ_WEB_AUTO_INSTALL=1` 直接安装；TTY 下询问（默认 Y）；非 TTY / 拒绝 → stderr 手动指令 + exit 1。安装命令固定 `sys.executable -m pip install "dragon-quant[web]"`，**绝不**使用 `--break-system-packages` / `--user`，失败（含受限环境拒绝子进程）降级为手动指令。`review --ui*` / `review-account --ui*` 走同一安装门禁并打印一次弃用提示。

**数据访问**：`web_ui` 内零 `sqlite3`、零自建连接、零建表写表；只经 `deps.py` 调用既有只读仓储（白名单见技术方案 §5.2）。`assets.db` 读函数一律 `create=False`，缺失返回空态；首次 `/api/dragons` 与今天一致幂等建 `dragon.db` schema（不违反 B1：只约束 import / `-h`）。

**前端 v2 与三态主题**：dashboard 前端固定 v2——旧 `?source=v1` 深链忽略 query、按 v2 渲染，页面与 `api/*` 不再产生 v1 请求（后端 v1 API / 表 / `--source v1` 完全保留）。主题为 system / light / dark 三态，偏好存 `localStorage["dq.theme"]`；`index.html` 的 head 内联同步脚本在首次绘制前设置 `html[data-theme]` / `color-scheme` / meta theme-color，避免闪烁。浅色只覆盖 `@theme` 语义 token（`[data-theme=light]`），不改布局、字体或红涨绿跌语义。**主题化颜色禁止使用 Tailwind 斜杠透明度**（如 `bg-white/5`、`border-critical/35`）：构建期会固化成字面量，不随 `[data-theme=light]` 变化；需要 alpha 时用显式 `-soft` / `-line` / `-focus` token 或 CSS `color-mix()`。`dragon_quant/tests/test_web_frontend_theme.py` 是零依赖构建契约测试，守护 dist 主题脚本、浅色 token、v1 文案清理与资产引用；改前端后必须 `npm run build`，让 `web_ui/dist` 与源码同批更新。发布时 `publish.sh` 会在写入版本号与任何 git add/commit/tag/push 之前先执行 `npm --prefix web_ui/frontend run build`，确保 dist 进入发布提交；发布机需有 Node/npm 且已安装前端 devDependencies（脚本不自动 npm install/ci）。

**依赖**：`[project.optional-dependencies]` 的 `web = fastapi>=0.162 / uvicorn>=0.54`（裸 uvicorn），`dev` 额外含 `httpx` 供 `TestClient`。基础安装仍只有 playwright/tmdx。`import web_ui` / `web_ui.backend` / `web_ui.backend.serve` 零 fastapi import、零目录副作用。

**入选后表现与展示精修（2026-10-31）**：新增只读 `GET /api/dragons/{trade_date}/{code}/performance`，路由注入 `deps.kline_provider()`（复用 `XueqiuProvider`，仅该端点）与 `deps.clock_module()`（仅解析缺省 `as_of`）；计算在 `dragon_quant/review/performance.py` 纯函数中完成，不写库、不改 CLI / 固定 5 日回测。白名单增量为 `dragon_quant.review.performance`（纯计算）与 `dragon_quant.infra.clock`（路由解析 `as_of`），`XueqiuProvider` 复用仅限绩效端点。前端新增 `HintIcon` / `CollapsibleSection`、`pages/prep/prepModel.ts` 投影与 `dragon_quant/tests/test_web_frontend_results.py` 零依赖契约测试；`trade-dates` 增 `items` 全量。改前端后仍必须 `npm --prefix web_ui/frontend run build`，让 `web_ui/dist` 与源码同批更新。

**近几日卡片原始扫描榜（2026-11-01 增量）**：`routers/dragons.py` 的 `trade-dates` 改从 `deps.scan_repository()` 的 `list_scan_dates` / `list_scan_stock_contributions_by_date` 组装卡片（日期与 `list_dragon_trade_dates` 取并集），明细/绩效在 `dragons_v2` 缺行时回退当日扫描贡献。只新增只读组合，不改 `scan/storage`、`review`、DB schema、CLI 与前端源码，因此 `web_ui/dist/**` 与 `coverage-map.json` 的 parser 计数均不变；E2E D7 覆盖该口径（`seed_builders.scan_db_seed` 新增可选 `stocks` / `top_n`，默认行为不变）。

**运维/复盘成效投影（2026-11-01 增量）**：`web_ui/backend/presenters/` 新增纯投影层（`scan_view` / `prep_view` / `auction_view` / `auction_effect`），路由只做参数与响应；`deps.py` 白名单增量：`dragon_quant.infra.board_height`、`dragon_quant.infra.factor.decision_review(+_store)`、`dragon_quant.scan.storage.logs`、`dragon_quant.infra.assets.db`（`prep_calendar_rows` / `auction_calendar_rows` / `list_auction_asset_names` / `list_stock_daily_bars` / `first_prep_record_date` / `first_prep_snapshot_date`）与无 I/O 的 `review_assets_enabled()`。新端点：`/api/prep/calendar`、`/api/prep/{date}/review-context`、`/api/auction/calendar`、`/api/auction/{date}/effect`、`/api/scans/{id}/logs`。前端新增 `AssetCalendar` 与 `/auction` 页；排版统一 `html{font-size:14px}` + 六个 `--text-*` 令牌（所有可见文字 ≥1rem），语义色收敛为 `danger/warning/normal/positive`（+soft/line/text），删除 `warn/critical/info`，A 股 `up/down` 独立。零依赖契约测试为 `dragon_quant/tests/test_web_frontend_ops.py`；改前端后必须 `npm --prefix web_ui/frontend run build` 让 dist 同批更新。

**核心问题直答（2026-11-01 增量）**：新增只读 `GET /api/prep/{date}/plan-followup`（评估日 `T`，**无 `source` 参数**、固定 v2）：样本锚**计划日 `D`**（`plan_review.plan_date`，缺失回退上一快照日期并给 `plan_date_fallback` 告警，与上一快照不一致给 `plan_date_mismatch`），复用 `infra.factor.decision_review` + `decision_review_store.build_store("prep")` 与本地 `stock_daily`（窗口 `[T-120, T+30]`），输出逐股 T+1/3/5 净收益与 recommended / not_recommended / data_insufficient 组均值（只暴露 `valid / excluded / mean / median`，不暴露命中率与质量标签）。空态一律 200：`snapshot_missing` / `no_plan` / `plan_snapshot_missing|corrupt|unsupported` / `plan_samples_missing` / `local_bars_missing` / `insufficient_samples`。`presenters/local_market.py` 抽出共享 `LocalStockDailyMarket` / `LOCAL_CAVEATS` / `shift_day`（`auction_effect` 继续再导出，既有 import 不破），新投影在 `presenters/prep_effect.py`。`review-context` 的 `plan_effect.market` 增 `verification`：空仓验证结论只在后端 `prep_view._classify_verification` 产生，前端只渲染。前端新增 `MiniBar`、`pages/scans/ScanHealthBand`、`pages/prep/PrepSummaryBand`，`DataTable` 增可选 `rowClassName`；扫描详情错误置顶 + 真龙行置顶高亮，竞价页删逐股折叠块、T+1/3/5 并入主表、强弱差升为 KPI。零 sqlite3 / 零自建连接 / 零写库，只经既有 `assets_repository` / `decision_review_store` / `local_bars_loader` 三个依赖（`deps.py` 不新增函数）。测试增量：`test_dashboard_plan_followup.py`（pure presenter / 验证规则表）、`test_web_backend.py`（端点与 verification）、`test_web_frontend_ops.py`（新契约）、E2E D11；改前端后仍必须 `npm --prefix web_ui/frontend run build`。

## 测试与 E2E 细则

- **安全边界**：`dragon_quant/tests/e2e` 永不打开真实 `dragon.db` / `assets.db` / `prep.db`，只对目录/文件做 `os.stat` 哨兵比对；每个业务命令在独立临时根下运行，隔离 `DQ_DATA_DIR` / `HOME` / `TMPDIR`，注入 fake provider/backend/Playwright/固定时钟与 socket/DNS 拦截，禁止真实网络、Cookie、R2/B2。真实目录元数据变化即失败，不得重跑淡化。
- **覆盖映射**：`dragon_quant/tests/e2e/coverage-map.json` 固定 54 个 parser 节点 / 16 个顶层命令 / 隐藏别名 / 无参数实际帮助 / 独立入口，`test_coverage_map.py` 强制每个节点映射到真实测试，新增命令必须同步更新。
- **golden 纪律**：E2E golden 只在批准的行为变更时更新；结构重构不得改 golden。

## 打包边界

- `docs/agent/*.md` 只服务 agent 与维护者，**不进入任何发布物**：npm 安装器白名单显式禁止 `docs/`（`test/packaging.test.mjs`），Python wheel 的 `packages.find` 只含 `dragon_quant` / `web_ui`。新增或修改本文档不会影响 `npm test` 与打包 E2E。
