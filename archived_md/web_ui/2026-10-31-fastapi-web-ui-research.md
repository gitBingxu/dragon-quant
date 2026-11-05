# 调研报告：web_ui 后端 FastAPI 重写 + `[web]` extra 可选安装（2026-10-31）

> 阶段 1 产物。调研问题：用 FastAPI 重写 web_ui 后端并新增 `dashboard` 命令是否可行、版本怎么 pin、SPA 托管与端口契约怎么保留、运行期自装 extra 怎么落地。
> 判定标准：MIT（或 MIT 兼容）许可、活跃维护、Python≥3.10、不触碰 `pip --break-system-packages` 红线、E2E 既有契约（SIGTERM 端口释放 / port=0 / 启动 URL 行）不破坏。

## 候选方向决策记录（阶段 0 已由用户拍板，此处留痕）

| 候选 | 概要 | 否决/入选理由 |
|---|---|---|
| A. 保持 stdlib，仅目录化 + 路由表重构 | 零新依赖 | 端点将翻倍到 ~25 个，手写解析/校验/序列化线性增长；`HTTPServer` 单线程，慢端点阻塞整个 UI 的问题仍在 |
| B. Flask + werkzeug | 成熟同步框架 | 同步模型不解决阻塞；参数校验要另配扩展，生态收益不如 FastAPI 内建 |
| C. **FastAPI + uvicorn（入选）** | pydantic 声明式校验、官方 `app.frontend()` SPA 托管、普通 def 端点跑线程池解决阻塞、自带 /docs | 用户在阶段 0 明确选择；调研结论 1–5 全部成立 |

## 结论（先读这节）

1. **选型可行**：`fastapi>=0.162.0` + `uvicorn>=0.54.0`（裸包，不带 `[standard]`），pydantic 由 fastapi 传递安装。全部 MIT/BSD-3/MIT/MIT，均要求 Python≥3.10，活跃维护（取证于 PyPI，2026-10-31）。只写下界、不写上界，与仓库现有依赖风格（`playwright>=1.30`、`tmdx>=3.0`）一致。
2. **Python 3.10 无需特殊处理**：starlette 1.8.0 起要求 ≥3.11，pip 会自动把 3.10 环境解析到 1.7.0；fastapi 官方 pin 无上界，组合无已知 breaking。
3. **SPA 托管用官方 `app.frontend("/", directory, fallback="index.html")`**（0.138.0 引入，比手工 mount + 回退更贴契约）；但 starlette StaticFiles **不设 Cache-Control**，immutable/no-cache 策略需自实现（等价复刻现 stdlib 行为：`/assets/*` immutable 1 年、其余 no-cache）。
4. **端口与生命周期契约可完整保留**：uvicorn `Server` 支持 SIGINT+SIGTERM 捕获与优雅关闭（W8 端口释放断言可过）；真实端口取回用官方模式 `server.servers[0].sockets[0].getsockname()[1]` 或预绑定 socket 方案，port=0 与 EADDRINUSE 重试策略可复刻。
5. **阻塞端点无需 async 改造**：普通 `def` 端点自动跑在 anyio 线程池（默认 40 并发），`/api/account/benchmark` 现场拉雪球 K 线声明为普通 def 即不阻塞事件循环。
6. **运行期自装可行但无主流先例**：venv 正常；pipx 下直接 pip 注入技术可行但绕过 pipx 元数据；PEP 668 环境直接 pip 会失败（`externally-managed-environment`），失败时降级为 stderr 打印手动指令 + exit 1，绝不用 `--break-system-packages`。**pipx 具体策略**：不做环境探测，统一走 `sys.executable -m pip install`——pipx 场景下恰好装进其隔离 venv（技术正确），并在完成提示里附一句「pipx 用户建议用 `pipx inject dragon-quant` 语义跟踪」的说明。仓库先例（tmdx 提示补装、chromium 自动下载）均为**无交互**形态；本次「提示 + 同意后安装」是新增形态，需配套 `--yes` / 环境变量 / 非 TTY 降级路径。
7. **测试策略**：API 单测用 `fastapi.testclient.TestClient`（httpx 前置，作 dev 依赖）；E2E 维持真实 CLI 子进程形态（SIGTERM/port=0/URL 行契约与 harness `web.py` 均基于子进程，TestClient 无法覆盖）。

## 事实依据

### A. 版本与兼容

| 包 | 最新稳定版 | requires-python | 许可证 | 备注 |
|---|---|---|---|---|
| fastapi | 0.162.0（2026-10-23） | ≥3.10 | MIT | 0.16x 起 `opentelemetry-api>=1.44.0` 为**基础依赖**；0.160/0.162 breaking 仅涉表单/参数模型，不影响组合 |
| uvicorn | 0.54.0 | ≥3.10 | BSD-3-Clause | 裸包仅 h11+click；`[standard]` 增量为 uvloop/httptools/watchfiles 等（性能项与 --reload/--env-file 能力），本地单机 HTTP 服务用不到 |
| pydantic | 2.14.2 | ≥3.10 | MIT | fastapi 传递安装 |
| starlette | 1.10.0 | ≥3.11 | BSD-3-Clause | 1.8.0 起要求 ≥3.11；0.x 线止于 0.52.1（≥3.10）；3.10 环境 pip 自动解析 ≤1.7.0 |

出处：pypi.org/pypi/{fastapi,uvicorn,pydantic,starlette}/json；fastapi.tiangolo.com/release-notes/（0.135.2 pydantic≥2.9.0 下界、0.136.1 starlette 1.0.0、0.142.3 starlette 1.7.0）。

### B. SPA 托管与运行时

- `app.frontend("/", directory="dist", fallback="index.html")`：官方 API（0.138.0，PR #15800），fallback 仅对 `Accept: text/html` 的 GET/HEAD 生效，缺失静态资源仍 404；FastAPI path operations 优先命中。文档：fastapi.tiangolo.com/tutorial/frontend/。
- Cache-Control：starlette `StaticFiles`/`FileResponse` 均不设置 cache-control，需自行加（现 stdlib 行为：`web_ui/server.py:169-178` `/assets/` → `max-age=31536000, immutable`，其余 no-cache）。
- uvicorn 编程式启动：`uvicorn.run(app)` 适合脚本；嵌入需 `Server(Config(...))` + asyncio task。port=0 取回真实端口官方模式：`asyncio.create_task(server.serve())` → 轮询 `server.started` → `server.servers[0].sockets[0].getsockname()[1]`（uvicorn issue #761 maintainer 回复 + uvicorn 自测 PR #1758）；等价方案：预绑定 socket 传 `server.run(sockets=[sock])`。
- SIGTERM：`Server.serve()` 内 `capture_signals()` 安装 SIGINT/SIGTERM → `should_exit` → close sockets → 优雅退出（源码 server.py）。现 E2E 断言 `test_web_api.py:390-413`（W8）可直接对齐。
- sync/async：普通 `def` 端点跑 anyio 线程池（默认 40），`async def` 占事件循环；阻塞 IO（雪球 K 线）应声明普通 def。文档：fastapi.tiangolo.com/async/；anyio threads 文档（limiter=40）。

### C. 运行期自装 `[web]` extra

- venv：标准安装。pipx：官方入口是 `pipx inject`；直接 pip 往 pipx venv 装技术可行但绕过注入元数据（pipx 文档 how-to/inject-packages.html）。PEP 668：非 venv + `EXTERNALLY-MANAGED` 标记 → pip 报 `externally-managed-environment` 错误退出（逃生阀只有 `--break-system-packages`，禁止使用）→ 我们的策略：捕获失败，stderr 打手动安装指令，exit 1。
- 「首调同意 + 安装」无主流先例（主流是 ImportError 指路），先例仅小工具（check-install、magicimporter 等）；仓库内先例（tmdx `warn_if_unavailable()` `infra/provider/tdx.py:74-88`、chromium 下载 `infra/provider/cookie.py:54-83`）均为无交互形态。本特性按用户明确要求保留交互确认，并配套非交互降级。

### D. 测试与打包

- TestClient：`from fastapi.testclient import TestClient`，前置依赖 httpx（fastapi 官方 `[standard]` extra 含 `httpx>=0.23`）；作 dev 依赖即可，不进 `[web]`。
- extras 语法：`[project.optional-dependencies]`，键=extra 名，值为 requirement 数组（setuptools 官方 dependency_management 文档）。
- 现打包事实：`pyproject.toml:5-10` 已含 `web_ui` 与 `web_ui.dist/**`；dependencies 仅 playwright/tmdx（:19-22）；尚无 optional-dependencies 表。

### E. 必须保留的既有契约（重写的兼容红线）

- 启动行 `🐉 Review Web UI 已启动 → http://localhost:<port>` 被 E2E harness 正则解析（`tests/e2e/harness/web.py` `_URL_RE`）。
- W8：SIGTERM 后端口释放且立即可复用；port=0 时 URL 永不为 0（`test_web_api.py:390-413`）。
- 端口冲突策略：隐式 `--port` 被占 → 警告一次并 port=0 重试一次；显式 `--port` 被占 → exit 1（`commands/review.py:82-94`）。
- B2 惰性初始化：`import dragon_quant` / `-h` 不建任何数据目录/文件；backend 模块 import 不得触网/触库/建目录。
- 表名白名单与目录穿越防护（`_resolve_static`、source 白名单）语义保留。

## 风险与缓解

| 风险 | 级别 | 缓解 |
|---|---|---|
| fastapi 基础依赖含 opentelemetry-api，基础安装变重 | 低 | 接受；其许可证为 Apache-2.0，与 MIT 兼容（保留署名/NOTICE 义务），合规无碍；AGENTS.md/README 依赖说明同步写明 |
| 交互式自装无主流先例，用户环境不可控（pipx/PEP 668） | 中 | 非 TTY / `--yes` / 环境变量降级路径 + 失败时手动指令兜底；文档写明 pipx 建议 `pipx inject` |
| `app.frontend()` 是 2026-06 才有的新 API，需 fastapi≥0.138 | 低 | 下界直接 pin `>=0.162.0` |
| E2E 契约面广（URL 行/SIGTERM/端口策略/harness） | 中 | 方案阶段列出逐条契约映射表，E2E 用例改写但断言语义不变 |

## 已知问题（不阻塞）

- uvicorn `[standard]` 未装时 `--reload` / `--env-file` / yaml log-config 不可用——本项目不用。
- 交互式安装的 y/n 在非 TTY 下无意义——按降级路径处理。
