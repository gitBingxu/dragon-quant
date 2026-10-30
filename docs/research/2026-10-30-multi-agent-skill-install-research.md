# 调研：dragon-quant-skills 多 Agent 安装目标

- 日期：2026-10-30
- 阶段：阶段 1（第三方 harness 接入调研）
- 调研问题：现有 `--agent NAME[,NAME...]` 多选流程不变时，openclaw / hermes / deepseek-harness / pi / claude-code / codex 的项目级与用户级 Skill 根目录应如何映射；哪些提示不能跨 Agent 复用。
- 证据口径：仅官方文档 / 官方仓库；main 对关键页面做了二次阅读。未对六个客户端逐一做真实运行验证。

## 1. 结论矩阵

| Agent 目标 | 项目级安装根 | 用户级安装根 | 关键差异 |
|---|---|---|---|
| `claude-code` | `<projectRoot>/.claude/skills` | `~/.claude/skills` | Enterprise > Personal > Project；安装后可 `/reload-skills` |
| `codex` | `<projectRoot>/.agents/skills` | `~/.agents/skills` | 官方说同名可并存于 selectors，不写 Claude 覆盖优先级 |
| `openclaw` | `<workspace>/skills` | `${OPENCLAW_STATE_DIR:-~/.openclaw}/skills` | 工作区不是必然的 git root；workspace > project-agent > personal > managed |
| `hermes` | `<projectRoot>/.hermes/skills` | `~/.hermes/skills` | 项目需 `hermes skills trust`；project > local |
| `deepseek` | `<projectRoot>/.dsh/skills` | `${DSH_HOME:-~/.dsh}/skills` | 官方 dsh；developer preview；只发现扫描根的直接子项 |
| `pi` | `<cwd>/.pi/skills`（`--target-dir` 可覆盖） | `${PI_CODING_AGENT_DIR:-~/.pi/agent}/skills` | 原生 `.pi` 不向上找；项目需 trust；编辑后 `/reload` |
| `agents`（兼容别名） | `<projectRoot>/.agents/skills` | `~/.agents/skills` | 保留既有通用目录，不纳入 `all`，避免与 codex 重复 |

`all` 推荐展开六个真实 harness：`claude-code,codex,openclaw,hermes,deepseek,pi`。现有 `agents` 是目录别名，不代表第七个 harness。

## 2. 官方证据

### Claude Code

- https://code.claude.com/docs/en/skills
- Project：`.claude/skills/<skill-name>/SKILL.md`；Personal：`~/.claude/skills/<skill-name>/SKILL.md`。
- 官方同名规则：“Enterprise over personal, and personal over project.”

### Codex

- https://learn.chatgpt.com/docs/build-skills（原 developers.openai.com/codex/skills 的当前跳转）
- REPO：从 `$CWD/.agents/skills` 向祖先扫描至 `$REPO_ROOT/.agents/skills`；USER：`$HOME/.agents/skills`。
- 官方：“If two skills share the same `name`, Codex doesn’t merge them; both can appear in skill selectors.” 未给同名覆盖顺序。

### OpenClaw

- https://docs.openclaw.ai/tools/skills
- 官方遵循 AgentSkills spec；加载顺序：`<workspace>/skills` → `<workspace>/.agents/skills` → `~/.agents/skills`（default state）→ `<state-dir>/skills`。
- state dir 默认 `~/.openclaw`，可由 `OPENCLAW_STATE_DIR` 覆盖；项目安装采用最高优先级 workspace `skills/`。

### Hermes Agent

- https://hermes-agent.nousresearch.com/docs/user-guide/features/skills
- 用户主目录：`~/.hermes/skills/`；项目：`<project-root>/.hermes/skills/` 与 `.agents/skills/`。
- 项目根是最近 `.git` 祖先；项目不会从任意 clone 自动加载，需 `hermes skills trust`；优先级 project → local → create_dir → external_dirs。

### DeepSeek Harness

- https://github.com/deepseek-ai/deepseek-harness
- https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/skill/skill-filesystem/README.md
- 官方命令 `dsh`；项目 `<projectRoot>/.dsh/skills`；用户 `${DSH_HOME:-~/.dsh}/skills`。
- 只识别 `<root>/<name>/SKILL.md` 或平铺 `<root>/<name>.md`，不递归嵌套；当前 bundle 布局兼容。
- 项目处于 developer preview，官方警告可能有兼容性破坏变更。

### Pi

- https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/skills.md
- https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/configuration.md
- 官方实现 Agent Skills spec；用户 `<agent-dir>/skills`，默认 `~/.pi/agent/skills`；`PI_CODING_AGENT_DIR` 可覆盖 agent-dir。
- 原生项目 `.pi/skills/` 位于当前工作目录，不向祖先查找；`.agents/skills` 才向祖先发现。项目需 trust，编辑后 `/reload`。

## 3. 候选接入路径对比

| 路径 | 做法 | 结论 |
|---|---|---|
| A. 官方目录注册表（推荐） | 在现有 `AGENT_TARGETS` 声明六个 harness 的原生路径、env 覆盖与提示，复用 manifest 和多选流程 | 零新依赖，install/doctor/uninstall 可共用；路径可被单测精确固定 |
| B. 全部写 `.agents/skills` | 只保留通用目录，依赖各客户端兼容扫描 | 不满足 OpenClaw 最高优先级 workspace、Hermes 主目录、DeepSeek dsh 根、Pi 原生项目语义与 env 覆盖，拒绝 |
| C. 调用各客户端原生安装器 | 分别调用 openclaw/hermes/pi 等 CLI | npm/本地源支持不一致，增加可执行依赖和配置副作用，无法保持当前零依赖及 doctor/uninstall 状态机，拒绝 |

## 4. 对现有实现的约束

- `lib/paths.mjs` 当前已有逗号多选、去重和 `all`，无需新交互依赖；新增目标与路径解析即可。
- `codex` 与既有 `agents` 同根，不应让 `all` 重复展开二者。
- 当前 install/doctor 对所有 Agent 都输出 Claude 的优先级与 reload 提示，必须改为目标感知。
- 现有 `findProjectRoot` 不能套用于 Pi 原生 `.pi/skills`；OpenClaw 以 workspace 为基准，`--target-dir` 必须可覆盖。
- 保持零运行时依赖、单 manifest 状态机、install/doctor/uninstall 共用目标注册表。

## 5. 不在本期验证 / 不承诺

- 不声称六个客户端均已做端到端真实加载；本调研只证明官方目录与发现语义。
- 不自动执行 `hermes skills trust`、修改 Pi project trust 或客户端配置；安装后给提示，由用户决定。
- 不引入交互式多选 UI；沿用 `--agent a,b`，无新依赖。
- 不为 DeepSeek 未来 developer-preview 变化做兼容保证；以测试和文档锁定当前官方路径。
