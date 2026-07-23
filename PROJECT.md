# Mimo Auto — 项目文档

> 将 [MiMo Code](https://github.com/XiaomiMiMo/MiMo-Code)（小米开源 AI 编码智能体）改造为 Home Assistant 智能家居管家，通过飞书/微信多通道控制智能家居。

---

## 一、项目定位

### 核心定位

**Mimo Auto** 是一个 HA Supervisor Addon（mimo-code/）+ HA 自定义组件（custom_components/mimo_auto/）的完整解决方案，将 MiMo Code 引擎包装为 HA 的 AI 智能体，提供：

- **AI 智能对话** — 通过 HA Conversation Agent 或 IM 通道（飞书/企业微信/个人微信）
- **智能家居控制** — 通过 MCP 协议调用 HA 设备
- **系统运维管理** — 通过 SSH 和 Supervisor API 管理 HA 系统
- **自学习进化** — 通过对话分析自动提取经验，持续优化回复质量

### 项目结构

```
mimo_auto/
├── mimo-code/                           # HA Supervisor Addon 容器
│   ├── config.yaml                      # Addon 配置声明
│   ├── Dockerfile                       # 多阶段构建
│   ├── CHANGELOG.md
│   └── rootfs/
│       ├── etc/s6-overlay/s6-rc.d/      # s6 服务定义（2 个内部服务）
│       │   ├── mimocode/                #    AI 引擎 (port 14095)
│       │   └── mimocode-webui/          #    WebUI + 通道 (port 8099)
│       └── usr/share/mimocode/webui/    # Addon 核心逻辑 (22 个 Python 文件)
│           ├── server.py                #    FastAPI 主服务 + 代理
│           ├── channel_manager.py       #    统一消息路由
│           ├── client.py                #    MimoAIClient (NDJSON 流式)
│           ├── base_channel.py          #    通道协议 + system prompt 构建
│           ├── feishu_client.py         #    飞书 WS 通道
│           ├── wechat_client.py         #    企业微信通道
│           ├── wechat_personal.py       #    个人微信通道 (iLink Bot)
│           ├── session_store.py         #    会话持久化
│           ├── ha_context.py            #    HA 设备上下文注入
│           ├── ha_entities.py           #    HA 实体定义
│           ├── ha_services.py           #    HA 服务调用
│           ├── evolution_review.py      #    进化回顾（自学习）
│           ├── persona.py               #    人格配置（灵犀）
│           ├── media.py                 #    富媒体标签解析
│           ├── media_utils.py           #    CDN 上传/下载
│           ├── tts.py                   #    Edge TTS 语音合成
│           ├── card.py                  #    飞书交互卡片
│           ├── tcp_proxy.py             #    TCP 端口转发 (14096→14095)
│           └── tests/                   #    单元测试
│
├── custom_components/mimo_auto/         # HA 自定义组件（桥接层）
│   ├── __init__.py                      # 组件入口、平台注册
│   ├── config_flow.py                   # UI 配置流程
│   ├── coordinator.py                   # 服务检测 + 状态监控
│   ├── conversation.py                  # 对话代理（Claw 兼容）
│   ├── agent_impl.py                    # AI 对话实现
│   ├── sensor.py                        # 4 个状态传感器
│   ├── entity.py                        # MiMoEntity 基类
│   ├── mimo_proxy.py                    # API 跨域代理
│   ├── mcp_client.py                    # MCP 客户端
│   ├── ssh_client.py                    # SSH 客户端
│   ├── supervisor_client.py             # Supervisor 客户端
│   ├── const.py                         # 常量
│   ├── services.yaml                    # 服务定义
│   └── translations/                    # 国际化
│
├── webui/                               # React SPA 前端源码
│   ├── src/                              #   源码
│   └── dist/                            #   构建产物
│
├── docs/                                # 文档
│   ├── PROJECT.md                       # 项目文档
│   ├── OPTIMIZATION_PLAN.md             # 优化方案
│   └── README.md                        # 文档索引
│
└── hacs.json                            # HACS 配置
```

---

## 二、Addon 内部架构

### 2.1 三进程架构（ha-mcp 可外部化）

Addon 容器内部默认运行 4 个服务，但其中 **ha-mcp 是冗余的**。

> **关键认识**：Addon 内置了一个 `ha_mcp_server.py`（8 静态工具）作为默认 MCP 服务。
> 但我们**已经有完整的 ha-mcp 集成**部署在 HA 上
> （`https://api.homediy.top:8443/api/webhook/mcp_97521c4cb653c43b9c9448410d0745d5`），
> 提供 83+ 工具的全量 MCP 访问。
>
> **因此**：应在 `options.json` 中配置 `ha_mcp_url` 指向外部 ha-mcp 集成，然后删除内置的 `ha-mcp` 服务。

```
┌──────────────────────────────────────────────────────────────┐
│                    HA Supervisor Addon 容器                    │
│                                                               │
│  ┌─────────────────┐   ┌────────────────┐                     │
│  │  mimocode        │   │  webui         │                     │
│  │  (AI 引擎)       │   │  (WebUI+通道)  │                     │
│  │  port 14095      │   │  port 8099     │                     │
│  └────────┬─────────┘   └───────┬────────┘                     │
│           │                     │                              │
│           │  MCP remote         │  channel_manager             │
│           │  (外部 ha-mcp)      │  → MimoAIClient → mimo serve │
│           ▼                     │                              │
│  ┌──────────────────┐           │                              │
│  │ ha-mcp 集成       │           │                              │
│  │ (HA 服务器上已部署)│           │                              │
│  │ 83+ 工具          │           │                              │
│  └──────────────────┘           │                              │
│                                                               │
│  ┌───────────────────────────────────────────────────┐         │
│  │  tcp_proxy.py   port 14096 → 14095               │         │
│  │  (解决 localhost 绑定限制, 允许外部访问)             │         │
│  └───────────────────────────────────────────────────┘         │
│                                                               │
│  ┌───────────────────────────────────────────────────┐         │
│  │  ⚠️ ha_mcp_server.py（内置，建议删除）              │         │
│  │  port 8234, 仅 8 静态工具, 冗余于外部 ha-mcp       │         │
│  └───────────────────────────────────────────────────┘         │
└──────────────────────────────────────────────────────────────┘
```

**配置方式**：在 Addon options.json 中设置 `ha_mcp_url` 指向外部 ha-mcp 集成：

```json
{
  "port": 14096,
  "ha_mcp_url": "https://api.homediy.top:8443/api/webhook/mcp_97521c4cb653c43b9c9448410d0745d5",
  "mimo_version": ""
}
```

| 组件 | 端口 | 说明 |
|------|------|------|
| **mimocode** | 14095 | MiMo Code serve，核心 AI 推理引擎 |
| **webui** | 8099 | React SPA + 通道管理 + API 代理 |
| **tcp_proxy** | 14096 | 端口转发，解决 localhost 绑定限制 |
| ~~ha-mcp~~ | ~~8234~~ | ~~建议删除，由外部 ha-mcp 集成替代~~ |

### 2.2 通信链路

```
用户消息 → 飞书/微信 → channel_manager → MimoAIClient → mimo serve (14095)
                                                             ↓
                                                  ha-mcp 集成 (外部, 83+ 工具)
```

### 2.3 组件 <-> Addon 分离

- **Addon**：运行 AI 引擎、通道管理、Web UI（MCP 由外部集成提供）
- **Component**：轻量桥接层，注册 HA 实体、服务、对话代理
- 通信通过 HTTP 进行，组件不直接依赖 Addon 内部实现

---

## 三、核心模块详解

### 3.1 channel_manager.py — 统一消息路由

**职责**：管理所有 IM 通道，将消息路由到 mimo serve。

**关键流程**：
```
消息进入 → _handle_message() → _call_mimo_serve()
    ↓
1. 从 SessionStore 恢复 session_id
2. 通过 MimoAIClient.ensure_session() 验证/创建 session
3. 构建 system prompt（persona + HA 设备上下文 + 进化经验）
4. 发送消息，支持 409/404 重试（session 忙时自动创建新 session）
5. 解析响应（NDJSON 流式），调度进化回顾
```

**通道管理**：
- 飞书：WebSocket 长连接，双线程模型（WS 线程 + Worker 线程）
- 企业微信：支持多个账号
- 个人微信：iLink Bot API，QR 码登录，长轮询

### 3.2 client.py — MimoAIClient

**职责**：与 mimo serve 通信的异步 HTTP 客户端。

**核心方法**：
- `ensure_session(session_id)` — 验证 session 存在，不存在则创建
- `send_message(text, session_id, system=)` — 发送消息，返回响应
- `send_message_stream(...)` — 流式发送，yield NDJSON 事件

**NDJSON 解析**：支持 text/reasoning/tool-call 三种事件类型，支持去重。

### 3.3 feishu_client.py — 飞书通道

**架构**：双线程模型（参考 cn_im_hub 模式）
- **WS 线程**：接收飞书 WebSocket 事件 → 推入队列（非阻塞）
- **Worker 线程**：从队列拉取 → 调用 AI → 通过 API 回复

**关键特性**：
- 飞书消息去重（`_seen_message_ids` OrderedDict，上限 512）
- 实时 reasoning 推送（PATCH 更新消息，实现打字效果）
- 富媒体支持（图片/视频/文件/卡片）
- 断线重连（最多 8 次，5 秒间隔）
- 使用 lark-oapi SDK

### 3.4 wechat_personal.py — 个人微信通道

**协议**：腾讯 iLink Bot API（参考 cn_im_hub）

**关键特性**：
- QR 码登录（完全异步）
- 长轮询消息接收（带退避重试）
- Session 过期检测（暂停 1 小时）
- Typing 指示器
- CDN 媒体上传

**容错机制**：
```
连续失败 < 3 → 2 秒后重试
连续失败 ≥ 3 → 30 秒退避
总失败 ≥ 8 → 停止连接
Session 过期 → 暂停 1 小时
```

### 3.5 session_store.py — 会话持久化

**存储格式**：JSON 文件 `/data/mimocode/sessions.json`
```json
{
  "feishu:chat_id_1": "session_id_abc",
  "wechat:user_id_2": "session_id_def"
}
```

**特性**：
- 线程安全（threading.Lock）
- 原子写入（先写 .tmp 再 rename）
- Debounce 写入（2 秒延迟，批量快速变更）

### 3.6 ha_context.py — HA 设备上下文注入

**职责**：从 HA REST API 获取设备状态，注入到 system prompt。

**缓存策略**：30 秒 TTL，双检锁（asyncio.Lock）

**优先级域**：light, climate, switch, cover, media_player, fan, lock, vacuum, camera, sensor, binary_sensor

**上下文格式**：
```
当前时间：2026-07-23 10:30

可用设备：

[light]
- 客厅灯 (light.living_room): on (brightness=180)
- 卧室灯 (light.bedroom): off

[climate]
- 客厅空调 (climate.living_room_ac): cool (temp=26, current=28)
```

### 3.7 evolution_review.py — 进化回顾

**职责**：每次对话后，后台分析交互模式，提取可复用经验。

**流程**：
1. 对话结束 → `schedule_review()` 检查是否值得回顾
2. 创建独立 session（不污染用户 session）
3. 发送分析 prompt → AI 返回 lessons JSON
4. 持久化到 `/data/mimocode/lessons.json`（最多 100 条）
5. TTL 1 小时，避免重复审查
6. 下次对话时注入到 system prompt

### 3.8 persona.py — 人格配置

**默认人格**：
```json
{
  "name": "灵犀",
  "role": "Home Assistant 管家",
  "tone": "友好、简洁",
  "language": "中文",
  "owner": "主人",
  "custom": ""
}
```

**存储**：`/data/mimocode/persona.json`

### 3.9 media.py — 富媒体解析

**支持标签**：`[IMAGE:source]`, `[VOICE:text]`, `[FILE:source]`, `[VIDEO:source]`, `[GIF:source]`, `[CARD:json]`

**Segment 类型**：TextSegment, ImageSegment, VoiceSegment, FileSegment, VideoSegment, GifSegment, CardSegment

### 3.10 ha_mcp_server.py — MCP 工具服务器（已删除）

**原状态**：Addon 内部自建 MCP 服务器，仅 8 个静态工具。

**⚠️ 冗余问题**：与外部 ha-mcp 集成（83+ 工具）功能重叠。

**处理结果**（2026-07-22）：
1. ✅ 配置 `ha_mcp_url` 指向外部 ha-mcp 集成
2. ✅ 删除 `ha_mcp_server.py` 文件
3. ✅ 删除 `ha-mcp` s6 服务目录（`run`, `type`, `finish`, `dependencies.d/mimocode`）
4. ✅ 删除 `user/contents.d/ha-mcp` 启动顺序文件
5. ✅ 修改 `mimocode/run`：移除 fallback 逻辑，改为打印警告

**验证结果**：部署后容器运行正常，s6 服务仅剩 2 个（mimocode + mimocode-webui），外部 ha-mcp 连接正常。

---

## 四、三大项目对比审计

### 4.1 项目概览

| 维度 | Mimo Auto | Claw Assistant (ha-claw) | CN IM Hub |
|------|-----------|--------------------------|-----------|
| **领域** | AI 对话 + IM 通道 + 系统管理 | 深度 AI 对话代理 + 工具系统 | 多通道 IM 网关 |
| **核心能力** | 智能体对话、设备控制、IM 消息、自学习 | 50+ 工具、多 Agent 级联、Hook 管道、记忆图谱 | 消息收发、媒体路由 |
| **架构复杂度** | 高（Addon 4 进程 + Component 桥接） | 极高（Hook 管道 + Agent 编排 + 存储 + 工具 + 插件） | 高（Provider 插件体系 + 子条目） |
| **HA 集成深度** | 深（对话 + 传感器 + 服务 + 代理） | 极深（Hook 注入 HA 管道 + 流式 WS + 文件上传 + 意图拦截） | 深（对话 + 传感器 + 选择器 + 服务 + 子条目） |
| **部署方式** | Addon + Component 分离 | 纯组件（无 Addon） | 纯组件（子条目） |
| **代码行数** | ~3,000(Addon) + ~1,200(Component) | ~10,000+（50+ 文件） | ~3,500 |

### 4.2 功能矩阵对比

| 功能类别 | 具体功能 | Mimo Auto | Claw Assistant (ha-claw) | CN IM Hub |
|---------|---------|:---------:|:------------------------:|:---------:|
| **对话** | HA Conversation Agent | ✅ | ✅（Hook 注入管道） | ✅ |
| | 多轮对话上下文 | ✅（Session 持久化） | ✅（50 轮工具循环） | ❌ |
| | 会话复用 | ✅ | ✅ | ❌ |
| | 多 Agent 路由 | ❌ | ✅（3 级级联） | ✅ |
| | 自学习进化 | ✅（evolution_review） | ✅（evolution_review + 技能更新） | ❌ |
| | 人格配置 | ✅（persona） | ✅（Workspace Persona 8 文档） | ❌ |
| | 自然语言意图 | ❌ | ✅（Hook 拦截 + 本地意图简化） | ❌ |
| | 语音管道识别 | ❌ | ✅（设备/卫星身份检测） | ❌ |
| **设备控制** | MCP 协议控制 | ✅（8 工具） | ❌（自有 50+ 工具） | ❌ |
| | HA 设备上下文注入 | ✅（30s 缓存） | ✅（通过工具系统） | ❌ |
| | 对话中控制设备 | ✅ | ✅（50+ 工具） | ❌ |
| **工具系统** | 内置工具数 | 8（MCP，可外部化→83+）+ 3（客户端） | 50+（8 大类） | ❌ |
| | 插件系统 | ❌ | ✅（Plugin Store） | ❌ |
| | 技能系统 | ❌ | ✅（Markdown 技能） | ❌ |
| | 斜杠命令 | ❌ | ✅（Slash Commands） | ❌ |
| | 心跳调度 | ❌ | ✅（Heartbeat Ticker） | ❌ |
| | 记忆图谱 | ❌ | ✅（SQLite + FTS5 知识图谱） | ❌ |
| | 文件上传 | ❌ | ✅（Base64 + Multipart + 视频裁剪） | ❌ |
| | 网页搜索 | ❌ | ✅（内置） | ❌ |
| **IM 消息** | 飞书接入 | ✅（原生 WS 双线程） | ❌（通过 channel_manager） | ✅ |
| | 企业微信接入 | ✅（原生，支持多账号） | ❌ | ✅ |
| | 个人微信接入 | ✅（iLink Bot API） | ❌ | ✅ |
| | QQ/钉钉/小懿 | ❌ | ❌ | ✅ |
| | IM 消息路由 | ❌ | ✅（通过 channel_manager） | ✅ |
| **富媒体** | 图片/视频/文件 | ✅（Addon 层） | ❌（通过 IM 通道） | ✅ |
| | TTS 语音合成 | ✅（Edge TTS） | ❌ | ✅ |
| | 交互卡片 | ✅（飞书卡片） | ❌ | ✅ |
| | 审批卡片 | ❌ | ❌ | ✅ |
| | 摄像头快照 | ❌ | ❌ | ✅ |
| **系统管理** | SSH 远程执行 | ✅（Component 层） | ❌ | ❌ |
| | Supervisor API | ✅（Component 层） | ❌ | ❌ |
| | HA 重启/备份 | ✅ | ❌ | ❌ |
| | Addon 管理 | ✅ | ❌ | ❌ |
| | HACS 管理 | ❌ | ✅ | ❌ |
| | Shell 执行 | ❌ | ✅ | ❌ |
| | 配置文件编辑 | ❌ | ✅ | ❌ |
| **传感器** | 服务器状态 | ✅ | ✅（运行状态） | ✅（健康状态） |
| | MCP 状态 | ✅ | ❌ | ❌ |
| | SSH 状态 | ✅ | ❌ | ❌ |
| | Supervisor 状态 | ✅ | ❌ | ❌ |
| | 目标选择器 | ❌ | ❌ | ✅ |
| | 开关/按钮实体 | ❌ | ✅（Switch + Button） | ❌ |
| **服务** | 调用 AI 对话 | ✅ | ✅ | ✅（发送消息） |
| | 技能/工具管理 | ❌ | ✅ | ❌ |
| | 工作区管理 | ❌ | ✅ | ❌ |
| **Web UI** | 独立面板 | ✅（React SPA） | ✅（WebSocket 流式） | ❌（仅 Lovelace 卡片） |
| | 卡片组件 | ❌ | ❌ | ✅ |
| | 前端 JS 注入 | ❌ | ✅（ha_crack 机制） | ❌ |
| **配置** | Config Flow | ✅ | ✅ | ✅（子条目） |
| | 多实例/多账号 | ✅（多微信账号） | ❌ | ✅（多 Provider） |

### 4.3 核心差异分析

#### Mimo Auto vs Claw Assistant (ha-claw)

**Claw Assistant** 是一个来自 [ha-china](https://github.com/ha-china) 的极深度 HA 对话代理集成。它通过 Hook 机制注入到 HA 的 conversation 管道，提供 50+ 内置工具、多 Agent 级联、记忆图谱、技能/插件系统等功能。

**Mimo Auto 的差异**：
- **架构不同**：Mimo Auto 依赖外部 Addon 容器运行 AI 引擎；Claw Assistant 是纯组件，直接在 HA 进程中运行
- **工具系统**：Mimo Auto 通过 MCP 协议连接外部 ha-mcp 集成（83+ 工具）；Claw Assistant 有 50+ 自有工具（8 大类）
- **HA 集成深度**：Claw Assistant 通过 Hook 注入 HA 管道、拦截意图、注入前端 JS，集成更深
- **IM 通道**：Mimo Auto 有原生 IM 通道（飞书/微信）；Claw Assistant 无原生 IM（通过 channel_manager 扩展）
- **系统管理**：Mimo Auto 有 SSH/Supervisor；Claw Assistant 无
- **记忆系统**：Claw Assistant 有 SQLite+FTS5 知识图谱；Mimo Auto 无
- **扩展性**：Claw Assistant 有插件/技能系统；Mimo Auto 无

**对比总结**：

| 对比维度 | Mimo Auto 优势 | Claw Assistant 优势 |
|---------|---------------|-------------------|
| AI 引擎 | 独立 Addon 容器，资源隔离 | 进程内运行，更低延迟 |
| 工具丰富度 | 外部 ha-mcp 集成 83+ 工具 | 50+ 自有工具 |
| IM 通道 | 原生飞书/微信（3 通道） | 无原生 IM |
| 系统管理 | SSH/Supervisor 独有 | 无 |
| HA 集成深度 | 标准组件桥接 | Hook 注入管道，深度集成 |
| 扩展性 | 有限 | 插件/技能/斜杠命令 |
| 记忆系统 | 无 | SQLite 知识图谱 |
| 部署复杂度 | Addon + Component | 单组件 |

#### Mimo Auto vs CN IM Hub — 通信方式对比

**核心结论**：Mimo Auto 的 IM 通道**直接基于 CN IM Hub 的代码和架构**，核心协议基本一致，但 Mimo Auto 在此基础上增加了 AI 推理推送和 MiMo Code 集成。

| 对比维度 | Mimo Auto | CN IM Hub |
|---------|-----------|-----------|
| **飞书协议** | 两者相同：lark-oapi SDK WebSocket 长连接 + 双线程（WS 接收→队列→Worker 处理→API 回复） |
| | ✅ 代码标注 `following cn_im_hub pattern` | 原始实现 |
| | ✅ 额外：推理推送（PATCH 实时更新，打字效果） | ❌ 无 |
| | ✅ 额外：卡片按钮回调处理 | ✅ 有 |
| | ✅ 额外：线程隔离（lark_oapi.ws 模块缓存清理） | ❌ 无 |
| | 两者相同：消息去重、最多 8 次重连、5 秒间隔 |
| **个人微信协议** | 两者相同：腾讯 iLink Bot API，长轮询消息，QR 码登录 |
| | ✅ 代码标注 `based on cn_im_hub` / `from cn_im_hub` | 原始实现 |
| | ✅ 额外：推理推送（Typing 指示器） | ❌ 无 |
| | ✅ 额外：TTS 语音消息（Edge TTS→SILK） | ❌ 无 |
| | 两者相同：CDN 媒体上传、会话过期暂停 1 小时、退避重试 |
| **企业微信协议** | 两者不同： |
| | Webhook 接收（XML 签名验证） | 需确认 |
| | 异步 Token 刷新（aiohttp） | 需确认 |
| | 支持多账号 | 需确认 |
| **Provider 数量** | 3 个：飞书、企业微信、个人微信 | 7 个：飞书、企业微信、QQ、钉钉、个人微信、小懿、自定义 |
| **架构差异** | 集中式：`channel_manager.py` 统一路由 | 模块化：`providers/` 目录 + `provider_flow.py` |
| | 单通道单实例，无子条目 | 子条目机制，支持多实例 |
| | 路由到单个 `mimo serve` | 多 Agent 路由（`core/routing.py`） |
| **媒体处理** | 扁平文件：`media.py`、`media_utils.py`、`card.py`、`tts.py` | 模块化：`media/rich_media.py`、`media/card.py`、`media/camera.py`、`media/tts.py` |
| | ❌ 无摄像头集成 | ✅ 有摄像头快照/录像 |
| | ❌ 无审批卡片 | ✅ 有审批卡片 |

**总结**：Mimo Auto 的通信方式与 CN IM Hub **核心协议一致**（飞书 WS + 个人微信 iLink Bot），可以直接看作是 CN IM Hub 的**子集 + AI 增强版**：
- 子集：Provider 数量少（3 vs 7），无摄像头/审批卡片
- 增强：增加了 AI 推理推送（PATCH 打字效果 + Typing 指示器）、TTS 语音、MiMo Code 集成

#### Mimo Auto vs CN IM Hub — 其他维度

**CN IM Hub** 是"通用 IM 消息网关"——纯消息路由和媒体转换，Provider 插件体系。

**Mimo Auto 的差异**：
- 以 AI 智能体为中心，IM 是 AI 能力的"通道"而非核心
- 缺少 QQ/钉钉/小懿 Provider，但多了个人微信
- 缺少摄像头集成
- 缺少审批卡片和子条目支持

### 4.4 功能实现总结

#### Addon 层已实现（✅）
- `mimocode` AI 引擎服务
- `ha-mcp` MCP 工具服务器（8 工具）
- `webui` React SPA + 通道管理
- `tcp_proxy` 端口转发
- `channel_manager` 统一消息路由
- `feishu_client` 飞书 WS 通道
- `wechat_client` 企业微信通道
- `wechat_personal` 个人微信通道
- `session_store` 会话持久化（debounce 写入）
- `ha_context` HA 设备上下文注入（30s 缓存）
- `evolution_review` 进化回顾（自学习）
- `persona` 人格配置（灵犀）
- `media` 富媒体解析（6 种标签）
- `media_utils` CDN 上传/下载
- `tts` Edge TTS 语音合成
- `card` 飞书交互卡片
- `server` FastAPI 主服务
- 409/404 重试机制
- 限流检测
- aiohttp session 复用
- worker 线程独立 event loop

#### Component 层已实现（✅）
- Config Flow 配置
- Conversation Agent 对话
- 4 个状态传感器
- MCP/SSH/Supervisor 客户端
- 跨域 API 代理
- 服务注册

#### 已知问题（⚠️）
| # | 问题 | 优先级 | 说明 |
|---|------|--------|------|
| 1 | 微信轮询停止 | P0 | 部署后微信心跳日志消失，`_message_loop` 可能未执行 |
| 2 | 容器重启丢代码 | P1 | Python 源码在 overlay 层，重启后丢失 |
| 3 | MCP 工具不完整 | P2 | 只有 8 个静态工具，需改为动态发现 |
| 4 | Config Flow 冗余步骤 | P2 | 有 4 步配置但 channel 配置在 Addon 端 |

---

## 五、设计理念

### 5.1 核心设计原则

#### 1. AI 智能体优先
```
mimo serve (AI 大脑) → ha-mcp (设备控制) → HA 设备
                      → channel_manager (IM 通道) → 飞书/微信
                      → Web UI (人机交互)
                      → SSH/Supervisor (系统管理) → HA 系统
```

Mimo Auto 的设计核心是**以 AI 智能体为中心**，所有其他能力都是 AI 的"工具"。

#### 2. 双线程通道模型
所有 IM 通道采用双线程架构：
- **接收线程**：WS/轮询接收消息 → 推入队列（立即返回，不阻塞）
- **Worker 线程**：从队列拉取 → 调用 AI → 发送回复

#### 3. 自学习进化
每次对话后后台分析交互模式，提取经验，注入到下次对话的 system prompt 中。这是区别于其他集成的核心特性。

#### 4. Addon 自包含
Addon 容器内部完成所有核心逻辑（AI 引擎、通道管理、MCP 工具），Component 仅做桥接。

### 5.2 与 CN IM Hub 的借鉴关系

Mimo Auto 的 Addon 层代码明显参考了 CN IM Hub 的架构：
- `feishu_client.py` — 双线程模型 + 飞书 WS（说明：*参考 cn_im_hub 模式*）
- `wechat_personal.py` — iLink Bot API（说明：*基于 cn_im_hub*）
- 富媒体解析和卡片格式

但 Mimo Auto 将其整合到了**以 AI 智能体为中心**的框架中，而不是纯消息路由。

### 5.3 与 Claw Assistant 的关系

**Claw Assistant**（ha-claw）是 ha-china 社区的深度对话代理集成，与 Mimo Auto 有互补关系。

**差异**：
- Claw Assistant 专注**深度 HA 对话集成**（Hook 管道、50+ 工具、记忆图谱）
- Mimo Auto 专注**AI 引擎 + IM 通道 + 系统管理**（Addon 容器、原生 IM、SSH/Supervisor）

**互补**：
- Mimo Auto 的 IM 通道（channel_manager）参考了 Claw Assistant 的架构
- Claw Assistant 的 evolution_review 和 Mimo Auto 的 evolution_review 功能相似
- 两者可以共存：Claw Assistant 做深度对话，Mimo Auto 做 IM 通道和系统管理

**Mimo Auto 的 Component 层曾声明兼容 Claw Assistant，但实际 IM 通道完全是 Addon 原生实现的。**

---

## 六、配置文件

### 6.1 mimo.json（Addon 配置）

```json
{
  "model": "mimo/mimo-auto",
  "channels": {
    "feishu": {
      "enabled": true,
      "app_id": "...",
      "app_secret": "...",
      "show_reasoning": true
    },
    "wechat": {
      "enabled": true,
      "corp_id": "...",
      "agent_id": "...",
      "secret": "..."
    },
    "personal_wechat": {
      "enabled": true,
      "credentials": {
        "token": "...",
        "user_id": "...",
        "base_url": "https://ilinkai.weixin.qq.com"
      }
    }
  }
}
```

### 6.2 环境变量

| 变量 | 说明 |
|------|------|
| `SUPERVISOR_TOKEN` | HA Supervisor API Token |
| `HASSIO_TOKEN` | HA Supervisor Token（备用）|
| `HA_MCP_PORT` | MCP 服务端口（默认 8234）|
| `MIMOCODE_SERVER_PASSWORD` | WebUI 密码 |

---

## 七、MiMo Code 原项目研究

### 7.1 项目概况

- **项目**: [XiaomiMiMo/MiMo-Code](https://github.com/XiaomiMiMo/MiMo-Code)
- **类型**: 终端原生 AI 编码智能体
- **架构**: 基于 OpenCode
- **模型**: 免费 MiMo-V2.5（也可接入其他模型）
- **许可证**: 开源
- **状态**: 活跃开发（~400 commits, 500+ issues, 200+ PRs）

### 7.2 原项目核心特性

| 特性 | 描述 | 本项目的利用 |
|------|------|------------|
| 终端 Agent | 命令行交互式 AI 编程助手 | 作为 HA Addon 的 `mimo serve` 运行 |
| 80+ 工具 | 代码编辑、文件操作、Git 操作等 | 替换为 HA MCP 工具 |
| 智能体记忆 | 长期记忆和上下文管理 | 扩展为 evolution_review 自学习 |
| 多连接方式 | CLI / WEB / IDE 扩展 | 利用 Web UI + IM 通道 |
| 多模型支持 | 可切换不同 AI 模型 | 通过 OpenAI API 复用 |
| 自然语言编程 | 用自然语言描述需求，AI 生成代码 | 转化为智能家居指令 |

### 7.3 适配工作

```
MiMo Code (原始)                Mimo Auto (适配后)
────────────────────────────────────────────────
编码工具 (80+)    ──→  HA 设备控制工具 (MCP 8 个)
终端交互           ──→  HA Conversation Agent + IM 通道
文件系统操作       ──→  系统管理 (SSH/Supervisor)
Git 操作           ──→  备份管理
智能体记忆         ──→  evolution_review 自学习
代码审查           ──→  (移除)
代码补全           ──→  (移除)
```

---

## 八、优化方向

详见 [OPTIMIZATION_PLAN.md](docs/OPTIMIZATION_PLAN.md)，核心优化项：

| 优先级 | 任务 | 状态 |
|--------|------|------|
| **P0** | 微信轮询修复 | 待修复 |
| **P0** | 容器重启丢代码 | 待修复 |
| **P1** | 同步 I/O 修复 | ✅ 已完成 |
| **P1** | aiohttp session 复用 | ✅ 已完成 |
| **P1** | evolution session 隔离 | ✅ 已完成 |
| **P2** | 类级别状态修复 | ✅ 已完成 |
| **P2** | session_store debounce | ✅ 已完成 |
| **P2** | MCP 工具动态发现 | 待开发 |
| **P2** | Config Flow 简化 | 待开发 |
| **P3** | 媒体发送代码抽取 | 待开发 |

---

## 九、部署与验证

### 9.1 部署

```powershell
# 部署自定义组件
scp -4 -i ~/.ssh/id_ha -r custom_components/mimo_auto root@api.homediy.top:/config/custom_components/
ssh -i ~/.ssh/id_ha root@api.homediy.top "rm -rf /config/custom_components/mimo_auto/__pycache__"

# 部署 Addon Python 文件（热更新）
$files = @("feishu_client.py","channel_manager.py","ha_context.py", ...)
$src = "D:\ai-hub\integrations\mimo_auto\mimo-code\rootfs\usr\share\mimocode\webui"
foreach ($f in $files) {
    $content = Get-Content "$src\$f" -Raw -Encoding UTF8
    $content = $content -replace "`r`n", "`n"
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($content)
    $base64 = [Convert]::ToBase64String($bytes)
    echo $base64 | ssh -4 -i ~/.ssh/id_ha root@api.homediy.top `
        "docker exec -i addon_local_mimo-code sh -c 'base64 -d > /usr/share/mimocode/webui/$f'"
}

# 重启 HA Core
$token = $env:HA_TOKEN
ssh -i ~/.ssh/id_ha root@api.homediy.top "curl -s -X POST -H 'Authorization: Bearer ${token}' -H 'Content-Type: application/json' -d '{}' 'http://localhost:8123/api/services/homeassistant/restart'"
```

### 9.2 验证

```bash
# 检查进程
docker exec addon_local_mimo-code ps aux | grep -E 'python|mimo'

# 检查日志
docker logs addon_local_mimo-code --tail 30

# 检查健康
docker exec addon_local_mimo-code curl -s http://127.0.0.1:8234/health
docker exec addon_local_mimo-code curl -s http://127.0.0.1:14095/session
```

---

## 十、附录

### 10.1 Addon 文件清单（23 个 Python 文件）

| 文件 | 职责 |
|------|------|
| `server.py` | FastAPI 主服务 + 静态文件 + API 代理 |
| `channel_manager.py` | 统一消息路由 + 通道生命周期管理 |
| `client.py` | MimoAIClient + NDJSON 流式解析 |
| `base_channel.py` | 通道协议抽象 + system prompt 构建 |
| `feishu_client.py` | 飞书 WebSocket 通道 |
| `wechat_client.py` | 企业微信通道 |
| `wechat_personal.py` | 个人微信通道（iLink Bot） |
| `session_store.py` | 会话持久化（debounce 写入） |
| `ha_context.py` | HA 设备上下文注入（30s 缓存） |
| `ha_mcp_server.py` | MCP 工具服务器（8 工具） |
| `ha_entities.py` | HA 实体定义 |
| `ha_services.py` | HA 服务调用 |
| `evolution_review.py` | 进化回顾（自学习） |
| `persona.py` | 人格配置（灵犀） |
| `media.py` | 富媒体标签解析 |
| `media_utils.py` | CDN 上传/下载/压缩 |
| `tts.py` | Edge TTS 语音合成 |
| `card.py` | 飞书交互卡片 |
| `tcp_proxy.py` | TCP 端口转发 |
| `tests/test_client.py` | 客户端测试 |
| `tests/test_channel_manager.py` | 通道管理器测试 |
| `tests/test_feishu_structure.py` | 飞书结构测试 |
| `tests/conftest.py` | 测试配置 |

### 10.2 Component 文件清单

| 文件 | 行数 | 职责 |
|------|:----:|------|
| `__init__.py` | ~100 | 组件入口、设置、平台注册 |
| `config_flow.py` | ~100 | 配置流程 |
| `coordinator.py` | ~100 | 协调器（进程管理 + 状态监控） |
| `conversation.py` | ~50 | 对话代理注册 |
| `agent_impl.py` | ~50 | AI 对话实现 |
| `sensor.py` | ~100 | 传感器实体 |
| `entity.py` | ~50 | MiMoEntity 基类 |
| `mimo_proxy.py` | ~110 | API 代理 |
| `mcp_client.py` | ~215 | MCP 客户端 |
| `ssh_client.py` | ~220 | SSH 客户端 |
| `supervisor_client.py` | ~310 | Supervisor 客户端 |
| `const.py` | ~30 | 常量 |
| `services.yaml` | ~20 | 服务定义 |
| `manifest.json` | ~20 | 组件元数据 |
| `hacs.json` | ~10 | HACS 元数据 |

### 10.3 参考项目

| 项目 | 用途 | 参考点 |
|------|------|--------|
| [XiaomiMiMo/MiMo-Code](https://github.com/XiaomiMiMo/MiMo-Code) | 上游 AI 引擎 | Agent 架构、工具系统 |
| [cn_im_hub](https://github.com/C3H3-AI/cn_im_hub) | IM 通道参考 | Provider 架构、Feishu WS、iLink Bot、媒体处理 |
| [Claw Assistant](https://github.com/ha-china/ha-claw) | 深度对话代理参考 | Hook 管道、Agent 编排、工具系统、记忆图谱 |