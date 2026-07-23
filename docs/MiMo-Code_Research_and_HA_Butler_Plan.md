# MiMo-Code 全量研究报告 + HA 管家/自我升级方案

> 研究对象：`XiaomiMiMo/MiMo-Code`（github.com，你 `mimo_auto` addon 中 `mimo` 二进制的来源，npm 包 `@mimo-ai/cli`）
> 研究方法：直接拉取真实源码与文档（非文档摘要），核到 `packages/opencode/src` 下 `tool/ plugin/ config/ session/ server/ skill` 各模块 + `docs/architecture/codex-microkernel-runtime.md` + `README.md` + `.mimocode/` 配置。
> 日期：2026-07-24

---

## 一、执行摘要（TL;DR）

MiMo-Code 是一个**终端原生 AI 编程助手**，本质是 OpenCode 的改造内核（fork），在其上叠加了：统一 Session 引擎、按模型裁切的 Codex 风格工具 ABI、QuickJS 沙箱化的 `exec`、持久记忆、子代理、工作流、以及一套**可热更新的技能/插件/钩子体系**。

对"HA 管家 + 自我升级"最关键的三个原生能力：

1. **`evolve` 技能 = 全层自改总开关**：能往 `.mimocode/` 写 `tools / hooks / skills / workflows / tui`，分层热更新。这是"自我升级"的天然杠杆。
2. **技能支持远程 URL 下发**：config 的 `skills.urls` 可指向 `https://.../.well-known/skills/`，实现技能远程自动更新——这是"自升级"的传输通道。
3. **`mimo serve` 原生支持 Basic Auth**：通过 `MIMOCODE_SERVER_PASSWORD` 开启。你之前审计的 P0-A（文件写无鉴权）在原版引擎层**已有现成解法**，问题出在你 addon 的 webui 代理层没用上。

结论：MiMo-Code **已经内建了成为"会自我进化 HA 管家"所需的所有机制**，缺的不是能力，而是**针对 HA 场景的技能/钩子封装**和**把原生鉴权用起来**。

---

## 二、项目概览与定位

### 2.1 它是什么

| 维度 | 内容 |
|------|------|
| 定位 | "终端原生 AI coding assistant"，模型与 Agent 共同进化（*Where Models and Agents Co-Evolve*） |
| 内核 | OpenCode 改造（packages/opencode，即 `mimo` 二进制，v0.1.8） |
| 语言/运行时 | TypeScript + Bun（部分 Node 兼容） |
| 免费通道 | **MiMo Auto**（匿名、零配置，官方内置）—— 你的 addon 名字 `mimo_auto` 即源于此 |
| 其他通道 | 小米 MiMo 平台(OAuth) / Codex(ChatGPT Pro/Plus OAuth) / 导入 Claude Code / 任意 OpenAI 兼容 API |

### 2.2 Monorepo 结构（与本研究相关）

```
MiMo-Code/
├── .mimocode/              # 用户可写扩展区（技能/插件/主题/TUI 配置）
│   ├── skills/             # 项目技能（SKILL.md）
│   ├── plugins/            # 插件（.tsx / .json）
│   ├── tui.json            # TUI 插件启用与快捷键
│   ├── themes/             # 主题
│   └── workflows/          # 自定义工作流 .js
├── packages/
│   ├── opencode/           # 核心引擎（mimo 二进制）
│   │   └── src/{tool,plugin,config,session,server,skill,mcp,agent,workflow}/
│   ├── plugin/             # 插件 SDK（@mimo-ai/plugin: tool/tui/shell）
│   ├── sdk/js/             # JS SDK
│   ├── console/            # 控制台（app/core/function/mail/resource）
│   ├── web/ ui/ desktop/   # 各形态前端
│   └── enterprise/         # 企业版
├── sdks/vscode/            # VS Code 扩展
└── docs/architecture/      # codex-microkernel-runtime.md（内部运行时权威文档）
```

---

## 三、内部运作机制（微内核运行时）

> 来自 `docs/architecture/codex-microkernel-runtime.md`（官方架构文档，非推测）

### 3.1 核心设计

MiMo-Code **不为 GPT 新建 Agent 引擎**，而是在统一 Session 运行时上做三件事：

1. 用 GPT/Codex 专属 system prompt 约定工具选择与调度；
2. 通过 `ToolRegistry` 装配更小的模型专属工具 ABI；
3. 提供 QuickJS `exec`，在不扩大权限前提下组合宿主工具。

```
模型(GPT/Codex) → SystemPrompt + ToolRegistry → [bash/apply_patch/view_image] + [exec/QuickJS]
                                                          ↓
                                               Filtered host tools → Permission+path guard
                                                          ↓
                                            Filesystem/Shell/MCP → SessionProcessor/TUI
```

**核心原则**：模型决定做什么，`exec` 负责如何组合，宿主决定是否允许及如何产生副作用。

### 3.2 GPT 工具 ABI（`ToolRegistry.available()`）

当模型 ID 含 `gpt-`（排除 `oss`/`gpt-4`）时启用 GPT profile，暴露：

| 工具 | 作用 |
|------|------|
| `bash` | 用 rg/sed 检查搜索文件、执行命令 |
| `apply_patch` | 结构化 patch 改文本文件 |
| `view_image` | 本地图片转模型附件 |
| `exec` | 在 QuickJS 中批量调用/聚合宿主工具 |

GPT profile 会隐藏与上面重叠的 `read/write/edit/multiedit/grep/glob/notebook_edit`。其他工具仍按 provider、agent allowlist、运行时 permission 治理。

### 3.3 `exec` 微内核（两层安全边界）

- `ToolScriptTool` 对模型暴露为 `exec`：模型提交一段 TS/JS async function body，通过 `tools.<name>()` 调宿主工具。
- 用 **late-bound registry**，使 `exec` 取得与外層相同、已过滤的 `Tool.Def`——外层不可见的工具不会在 exec 内重新出现。
- **两层安全边界**：
  1. `evalScript()` 用 **QuickJS** 隔离 guest code（不提供 Node / `process` / `fetch` / timer / 模块加载）；
  2. 真实副作用仍由宿主工具执行，经 permission / external-directory / memory guard / 工具自身校验。
- `bash` 仍是**真实 Shell，不是容器沙箱**——QuickJS 只隔离 exec 代码，不隔离 bash。
- 资源上限：嵌套调用默认 50/最高 500、并发 8、活跃计算默认 60s/最高 600s、wall clock 30min、guest 内存 64MiB。

### 3.4 权限与持久化

- 权限、路径、子进程、取消、持久化、UI **始终由宿主控制**。
- 提示词路由（`SystemPrompt.provider()`）独立选择 `gpt.txt / codex.txt / beast.txt`。

### 3.5 记忆与上下文系统（持久化核心）

基于 **SQLite FTS5 全文检索**：

| 记忆 | 作用 |
|------|------|
| `MEMORY.md`（项目记忆） | 持久项目知识、规则、架构决策 |
| `checkpoint.md`（会话检查点） | 由 checkpoint-writer 子代理自动维护的结构化快照 |
| `notes.md`（草稿） | 临时笔记区 |
| `tasks/<id>/progress.md` | 每任务进度日志 |

- **自动检查点**：按上下文窗口决定何时保存。
- **上下文重建**：接近上限时从最新 checkpoint + 项目记忆 + 任务进度 + 保留近期消息重建。
- **预算注入**：用 token 预算控制多少内容进入上下文，带重要性排序。

### 3.6 Agents / 子代理 / Goal

- 主 Agent：`build`（默认全权限）/ `plan`（只读分析）/ `compose`（编排模式）。
- 子代理（subagent）：按需创建，共享会话上下文，可并行、可取消、可后台。
- **`/goal` 停止判定**：设停止条件后，由独立"法官模型"评估对话是否满足，防过早乐观停止。

### 3.7 工作流（Workflows）

确定性 JS 脚本，编排多 Agent，带有限重试与自动并行，**fire-and-forget 无需交互**。内置 4 个：

| 工作流 | 阶段 | 用途 |
|--------|------|------|
| `compose` | Brainstorm→Design→Implement→Verify→Review→Report→Merge | 完整开发流水线，自动并行到隔离 git worktree |
| `deep-research` | Brief→Plan→Research→Reflect→Write→Review | 多源深度研究 |
| `fact-check` | Plan→Search→Extract→Group→Crosscheck→Report | 对抗式事实核查 |
| `research-experiment` | Baseline→Loop→Audit→Report | 自主优化循环（可验证指标） |

自定义：放 `.mimocode/workflows/*.js` 或用同名覆盖内置。

---

## 四、技能（Skills）管理体系

### 4.1 格式

```markdown
---
name: effect
description: Answer questions about the Effect framework
---
# Effect
... 指令正文 ...
```

`name` + `description`（description 是触发条件描述，不是功能摘要）+ 正文指令。

### 4.2 发现顺序（scan order，后者覆盖前者）

```
内置 bundle (packages/opencode/src/skill/builtin/.bundle/*)
  → 项目 .mimocode/skills/<name>/SKILL.md
  → 个人 ~/.claude/skills/  ~/.opencode/skills/ 等
  → config skills.paths（额外文件夹）
  → config skills.urls（远程 .well-known/skills/）  ← 自升级关键
```

同名用户技能**覆盖**内置——这是可插拔的核心机制。

### 4.3 搜索与匹配

（`README` + `config/skills.ts` 证实）

- 按**精确名 + 本地化别名 + BM25 相关性**检索非 Compose 技能。
- 高置信匹配**自动加载**；不确定匹配排好序交给 Agent 评估。
- TUI 中 `/` 浏览、`/<skill-name>` 直接调用；一条消息提 2+ 技能自动加载并注入多技能编排计划。

### 4.4 内置 26 个技能（节选关键的）

`arxiv`、`claude-code`、`codex`、`compose-next`、`data-analytics`、`deep-research`、`design-blueprint`、`docx/pdf/pptx/xlsx-official`、`drive-mimo`、`evolve`、`frontend-design`、`html-to-video-pipeline`、`learn-everything`、`loop`、`mimocode-docs`、`modern-python-toolchain`、`research-paper-writing`、`sales`、`skill-creator`、`super-research` 等。

**对 HA 管家最相关**：`evolve`（自改）、`loop`（定时）、`drive-mimo`（驱动另一实例）、`skill-creator`（造技能）、`mimocode-docs`（自文档）。

### 4.5 远程下发（自升级传输通道）

`config/skills.ts` 的 `Info` schema 明确支持：

```ts
export const Info = Schema.Struct({
  paths: Schema.optional(Schema.Array(Schema.String)),   // 额外本地技能文件夹
  urls: Schema.optional(Schema.Array(Schema.String)),     // 远程技能源，如 https://example.com/.well-known/skills/
})
```

→ **只要把 `skills.urls` 指向你自建的 HA 侧端点，技能即可远程自动更新，无需碰容器镜像。**

---

## 五、插件（Plugins）管理体系

### 5.1 两种来源 + 四段加载

`PluginLoader`（`plugin/loader.ts`）流程：

```
resolve(spec, kind):
  1. install  → resolvePluginTarget（npm 插件按需安装）
  2. entry    → createPluginEntry（探测 server/tui 等入口）
  3. missing  → 无该 kind 入口则跳过
  4. compat   → npm 插件校验 opencode 版本闸门（file 插件跳过）
load(resolved) → 动态 import 模块
```

`config/plugin.ts`：`plugin` 配置项可以是字符串（spec）或 `[spec, options]` 元组；支持 npm 包与 `file://` 本地路径两种 spec；按 load identity 去重。

### 5.2 插件类型

| 类型 | 形态 | 入口 |
|------|------|------|
| TUI 插件 | `.tsx` | 在 `tui.json` 中声明 enabled / keybinds / label |
| Server 插件 | `.ts` | server 端扩展 |
| 主题插件 | `.json` | `theme.json` schema（defs + theme） |

示例（仓库自带）：
- `plugins/tui-smoke.tsx` + `tui.json` 中 `enabled:false`（默认关）。
- `plugins/smoke-theme.json`（Nord 配色主题）。

### 5.3 插件 SDK（`@mimo-ai/plugin`）

`packages/plugin/src/`：`tool.ts`（造工具）、`tui.ts`（TUI 插槽/命令/对话框）、`shell.ts`、`index.ts`。这是第三方扩展的标准入口——**HA 专用工具应走这个 SDK 注册**，而非裸 bash。

---

## 六、evolve 自我修改机制（成为自升级管家的核心）

> 来自内置 `evolve` 技能 SKILL.md 全文（这是"自我升级"的官方总开关）

**一句话**：`evolve` 让 Agent 把"任何关于自身的改动"都通过写 `.mimocode/` 下的文件完成，分层热更新。

### 6.1 五类可改层 + 热更新语义

| 类型 | 路径 | 热更新 |
|------|------|--------|
| Tools（新能力/覆盖内置） | `.mimocode/tools/<name>.ts` | 下一轮 |
| Hooks（拦截/改写行为） | `.mimocode/hooks/<name>.ts` | 下一轮 |
| Skills（持久知识） | `.mimocode/skills/<name>/SKILL.md` | 下一轮 |
| Workflows（多 Agent 编排） | `.mimocode/workflows/*.js` | 调用时 |
| TUI（界面面板/命令） | `.mimocode/tui/*.tsx` | 重启 |

**这意味着：Agent 可以在运行中给自己加工具、加钩子、加技能——本身就是"自我升级"。**

### 6.2 Hook 事件（行为拦截的精细度）

| 事件 | 能力 |
|------|------|
| `tool.execute.before` | 改写 `args` 或 `cancel=true` 阻止（如拦截 `rm -rf /`） |
| `tool.execute.after` | 改写 `output`/`title`/`metadata` |
| `tool.definition` | 改工具描述/参数 |
| `chat.params` | 改 temperature / topP / maxTokens |
| `experimental.chat.system.transform` | 追加 system prompt |
| `experimental.chat.messages.transform` | 改发给 LLM 的消息列表 |
| `session.pre/post` | 会话生命周期；post 收完整轨迹 |
| `session.userQuery.pre/post` | 每 LLM 步生命周期；可取消/审视 |
| `actor.preStop/postStop` | 拦截子代理交付；`continue=true` 强制再来一轮 |
| `shell.env` | 注入环境变量 |

→ **HA 安全管家层应大量用 `tool.execute.before` 钩子**：任何要调 HA 设备/执行 shell 的操作，先过一道白名单/确认闸门。

### 6.3 evolve 触发信号（Agent 自主进化逻辑）

| 信号 | 动作 |
|------|------|
| 同一 bash/API 序列跑 3+ 次 | 包成 tool |
| 反复犯同一错 / 用户反复纠正同一行为 | 加 hook 结构性阻断 |
| 学到非显然的项目知识 | 写 skill 持久化 |
| 内置工具行为冲突项目需求 | 同名覆盖 |
| 手搓的工作流效果好且会复现 | 存成 workflow |

→ 这是"自我升级"的**决策逻辑**——已经内建了"何时该进化"的判断。

---

## 七、mimo serve 服务端（你 HA 桥接的真相）

### 7.1 架构

`packages/opencode/src/server/` 用 **Hono** 提供 HTTP/WebSocket 服务。`adapter.ts` 区分 bun/node 适配器；`auth.ts` 提供 **Basic Auth**。

### 7.2 原生鉴权（堵 P0-A 的现成解法）

```ts
// server/auth.ts
export function serverAuthHeader(credentials?) {
  const password = credentials?.password ?? Flag.MIMOCODE_SERVER_PASSWORD
  if (!password) return undefined
  const username = credentials?.username ?? Flag.MIMOCODE_SERVER_USERNAME ?? "mimocode"
  return `Basic ${Buffer.from(`${username}:${password}`).toString("base64")}`
}
```

→ **只要给 `mimo serve` 设 `MIMOCODE_SERVER_PASSWORD`，引擎层就带鉴权了。** 你之前审计的 P0-A（无鉴权文件写）漏洞在**你的 webui 代理层（server.py）**，不在 mimo serve——修法应是 webui 用 ingress 鉴权 + 改绑 127.0.0.1 + 复用 serve 的 Basic Auth，而不是自己造轮子。

### 7.3 路由面（HA 桥接用得到的）

实例路由（`routes/instance/index.ts`）含：`/session` `/permission` `/workflows` `/question` `/bash-interactive` `/provider` `/sync` `/file` `/event` `/mcp` `/tui` `/config` `/experimental` `/pty` + `/instance/dispose` `/path`。

`MIMOCODE_EXPERIMENTAL_HTTPAPI` 开后额外暴露：`/question`(GET/reply/reject)、`/permission`(GET/reply)、`/config/providers`、`/provider`(auth/oauth)、`/project`——**你 addon 的 webui 正是代理这套**，应优先走实验 HTTP API 而非裸文件写。

---

## 八、解决方案：成为更好的 HA 管家 + 自我升级

### 8.1 现状对照（你 mimo_auto addon 的差距）

| 能力 | 原版 MiMo-Code 已有 | 你 addon 现状 | 缺口 |
|------|---------------------|---------------|------|
| 引擎 | mimo serve（含 Basic Auth） | 代理到 serve | webui 代理层无鉴权(P0-A) |
| 技能 | 26 内置 + 远程 URL 下发 | 未用技能体系 | 无 HA 专属技能 |
| 钩子 | evolve/hooks 拦截 | 未用 | 无 HA 安全闸门 |
| 工具 | @mimo-ai/plugin SDK | 裸 bash/exec | 未封装 HA 工具 |
| 记忆 | MEMORY.md/checkpoint | 用但未针对 HA | 无设备清单/作息记忆 |
| 自升级 | evolve + skills.urls + loop | 无 | 完全未启用 |

### 8.2 方案 A：HA 管家能力层（用原生机制封装）

**A1. HA 专属技能**（放 addon 持久卷 `.mimocode/skills/ha-*/SKILL.md`）：
- `ha-control`：灯/空调/窗帘/场景的自然语言控制，含实体命名映射。
- `ha-automation`：根据"每晚 11 点关灯"类诉求生成自动化 YAML。
- `ha-routine`：家庭作息编排（结合 MEMORY.md 里的家庭成员偏好）。
- `ha-status`：一问即报"现在家里哪些灯亮着/温度多少"。

**A2. HA 安全钩子**（`.mimocode/hooks/ha-safety.ts`）：
```ts
export default {
  "tool.execute.before": async (input, output) => {
    // 任何调 HA 设备/危险 shell 的操作先过白名单 + 确认
    if (input.tool === "ha-call-service" && isDangerous(input.args)) {
      output.cancel = true
      output.cancelReason = "需确认：高危操作已拦截，请经飞书/微信点确认"
    }
  },
}
```
→ 这正好对接你已部署的 `action_confirm.py`（飞书/微信确认卡片）。

**A3. HA 工具**（走 `@mimo-ai/plugin` 的 `tool()`，而非裸 bash）：
- `ha-call-service`：封装 ha-mcp 的调用（你已有 ha-mcp 集成）。
- `ha-get-state` / `ha-set-state`：读/写实体。
- `ha-list-entities`：列举设备。

**A4. HA 记忆**：在 MEMORY.md 写"设备清单 + 家庭成员偏好 + 常用场景"，让 Agent 跨会话记得你家。

### 8.3 方案 B：自我升级机制（核心诉求）

三层自升级，全部基于已证实的原生能力：

**B1. 技能远程下发（传输层）**
在 addon 持久卷的 mimo 配置里设：
```jsonc
// .mimocode/config 或 opencode 配置 skills.urls
{ "skills": { "urls": ["https://你的HA域名/.well-known/skills/"] } }
```
HA 侧放一个静态目录托管 `SKILL.md`——**更新技能只需改 HA 上的文件，MiMo 自动拉取，无需重建容器**。可让 MiMo 自己用 `evolve` 把新技能推到这个目录（自举）。

**B2. evolve 周期审查（进化决策层）**
用 `loop` 技能定时（如每周一 03:00）跑一次"自我进化审查"：
```
/loop "每周一 03:00 执行：回顾上周所有会话，找出重复 3+ 次的操作/用户纠正，
用 evolve 沉淀成新 skill 或 hook，写入 .mimocode/，并同步到 skills.urls 托管目录"
```
→ 这就是"自我升级"的发动机：Agent 自主发现该进化什么、自己写扩展、自己发布。

**B3. 自举驱动（多实例）**
`drive-mimo` 技能可驱动另一个 MiMoCode 进程——可用于"生产实例"与"进化沙箱实例"分离：沙箱进化验证通过后，再同步到生产。

**B4. 版本与回滚**
- `.mimocode/` 整个目录 git 跟踪 → 任何自升级都可 `git revert` 回滚。
- mimo 引擎自身升级：`upgrade-opentui` 脚本 + `npm update @mimo-ai/cli`；可做一个 workflow 周期性检查新版本并升级（需人工确认关卡）。

### 8.4 方案 C：安全加固（堵 P0-A + 配套）

1. **mimo serve 开 Basic Auth**：设 `MIMOCODE_SERVER_PASSWORD`（环境变量注入 addon）。
2. **webui 改绑 127.0.0.1**：由 HA ingress / tcp_proxy 转发，攻击面消失。
3. **webui 复用 ingress 鉴权**：不再自己造无鉴权文件写接口；文件浏览走实验 HTTP API（`/file` 路由，本身受 serve 鉴权保护）。
4. **HA 危险操作一律过 `action_confirm` 确认卡**：与 A2 钩子配合。

### 8.5 实施路线图（建议顺序）

| 阶段 | 内容 | 依赖 | 产出 |
|------|------|------|------|
| T0 安全止血 | mimo serve 开密码 + webui 绑 127.0.0.1 + 修 P1-A chat 服务 | 无 | 消除 P0-A |
| T1 工具封装 | 用 @mimo-ai/plugin 造 ha-call-service 等 3 个工具 | T0 | HA 可控 |
| T2 技能沉淀 | 写 ha-control/ha-status/ha-automation 技能 | T1 | 自然语言管家 |
| T3 安全钩子 | ha-safety hook 拦截高危 + 接 action_confirm | T1 | 确认闸门 |
| T4 记忆初始化 | MEMORY.md 写设备清单/家庭作息 | 无 | 跨会话记忆 |
| T5 自升级闭环 | skills.urls 指向 HA 托管 + loop 周期 evolve 审查 + .mimocode git 跟踪 | T2/T3 | 自我进化 |
| T6 沙箱隔离(可选) | drive-mimo 分离进化/生产实例 | T5 | 安全自举 |

---

## 九、风险与边界

1. **evolve 的能力边界**：tools/hooks 与 bash 同权限，**不能改权限系统本身**，工具输出截断 50KB/2000 行。自升级要防"自己写死循环 hook"。
2. **QuickJS 不隔离 bash**：`exec` 内的宿主工具仍可能 shell out，HA 高危操作必须靠 `tool.execute.before` 钩子 + action_confirm 兜底，不能依赖沙箱。
3. **自升级需要人类确认关卡**：loop 周期 evolve 若无回滚与人工抽检，可能累积错误扩展。`.mimocode/` git 跟踪 + 沙箱实例是必要护栏。
4. **mimo serve 版本兼容**：npm 插件有 opencode 版本闸门，自升级时若动到插件需对齐版本。
5. **你 addon 的 webui 是薄代理**：所有 HA 侧增强应尽量落在 `.mimocode/` 扩展（技能/钩子/工具），而非改 webui 的 server.py——后者是代理层，应越薄越安全。

---

## 十、结论

MiMo-Code **不是"需要被改造才能当 HA 管家"**，而是**已经内建了当管家+自升级的全部原语**：`evolve` 给自改能力、`skills.urls` 给远程下发、`loop` 给周期进化、`hooks` 给安全闸门、`mimo serve` 自带 Basic Auth。你真正要做的，是用这些原语**把 HA 领域知识封装成技能/工具/钩子**，并**把原版鉴权用起来**——而不是另起炉灶。

最高优先级永远是 **T0 安全止血**（P0-A），其余按 T1→T5 渐进，自升级闭环在 T5 自然闭合。
