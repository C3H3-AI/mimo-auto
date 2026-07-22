# MiMo HA 管家 — 技术架构文档

> 版本：1.0 | 日期：2026-07-22

---

## 1. 项目概述

### 1.1 定位

**MiMo HA 管家**是一个 Home Assistant Addon，将小米 MiMo Code（基于 OpenCode 的终端 AI 编程 Agent）封装为 HA 智能家居的 AI 助手。用户通过飞书、微信、WebUI 或 HA 原生对话与 AI 交互，AI 可以自主控制 HA 设备。

### 1.2 核心能力

| 能力 | 实现方式 |
|------|----------|
| AI 对话 | `mimo serve` HTTP API，NDJSON 流式响应 |
| 多渠道接入 | 飞书（WS 长连接）、企业微信（HTTP 轮询）、个人微信（iLink Bot）、WebUI（SPA）、HA 对话（ConversationEntity） |
| 设备控制 | mimo serve 通过 MCP 协议调用 `ha_mcp_server`，后者通过 HA Supervisor API 执行设备操作 |
| 配置管理 | Supervisor `options.json` → 环境变量 → `config.py`，支持运行时 WebUI 修改 |

### 1.3 技术栈

| 层 | 技术 |
|----|------|
| 容器 | Docker + HA Supervisor + s6-overlay |
| AI 引擎 | `@mimo-ai/cli`（mimo serve）— 基于 OpenCode，内置 MCP 客户端 |
| 工具协议 | MCP（Model Context Protocol）stdio |
| 后端 | Python 3，aiohttp（异步 HTTP），asyncio |
| 前端 | React SPA（Vite 构建），通过 WebUI 代理服务 |
| 通信 | NDJSON 流式响应，HTTP REST API |

---

## 2. 系统架构

### 2.1 架构总览

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        Home Assistant Core                               │
│  ┌─────────────────────────────────────────────────────────────────────┐ │
│  │                  Supervisor (addon 生命周期管理)                      │ │
│  │  ┌─────────────────────────────────────────────────────────────────┐ │ │
│  │  │  custom_components/mimo_auto  (可选薄桥接)                      │ │ │
│  │  │  ┌───────────────────────────────────────────────────────────┐  │ │ │
│  │  │  │  conversation.py  ←  ConversationEntity 注册               │  │ │ │
│  │  │  │  agent_impl.py    ←  HTTP → addon:8099/api/chat           │  │ │ │
│  │  │  └───────────────────────────────────────────────────────────┘  │ │ │
│  │  └─────────────────────────────────────────────────────────────────┘ │ │
│  └─────────────────────────────────────────────────────────────────────┘ │
└──────────────────────────────┬───────────────────────────────────────────┘
                               │ Docker / ingress
┌──────────────────────────────▼───────────────────────────────────────────┐
│                     HA Supervisor Addon 容器                               │
│                                                                           │
│  ┌─────────────────────────────────────────────────────────────────────┐  │
│  │  s6-overlay 服务监督                                                  │  │
│  │                                                                       │  │
│  │  ┌─────────────────────────────────────────────────────────────────┐  │  │
│  │  │  mimocode (main service)                                         │  │  │
│  │  │                                                                   │  │  │
│  │  │  ┌──────────────────────┐     ┌──────────────────────────────┐  │  │  │
│  │  │  │  mimo serve          │     │  TCP Proxy (0.0.0.0:14096)   │  │  │  │
│  │  │  │  127.0.0.1:14095     │◄────│  → 127.0.0.1:14095           │  │  │  │
│  │  │  │  (AI 推理引擎)       │     │  (外部访问入口)               │  │  │  │
│  │  │  └──────────┬───────────┘     └──────────────────────────────┘  │  │  │
│  │  │             │ MCP (remote)                                      │  │  │
│  │  │  ┌──────────▼───────────┐                                       │  │  │
│  │  │  │  mimocode.json       │                                       │  │  │
│  │  │  │  mcp: { ha-mcp: {    │                                       │  │  │
│  │  │  │    url, type:remote }│                                       │  │  │
│  │  │  └─────────────────────┘                                       │  │  │
│  │  └─────────────────────────────────────────────────────────────────┘  │  │
│  │                                                                       │  │
│  │  ┌─────────────────────────────────────────────────────────────────┐  │  │
│  │  │  ha-mcp (secondary service, depends on: mimocode)               │  │  │
│  │  │                                                                   │  │  │
│  │  │  ┌────────────────────────────────────────────────────────────┐  │  │  │
│  │  │  │  ha_mcp_server.py                                          │  │  │  │
│  │  │  │  stdio MCP Server   →  8 个 HA 工具                        │  │  │  │
│  │  │  │  (turn_on, turn_off, set_brightness, etc.)                  │  │  │  │
│  │  │  │  → HTTP → Supervisor API → HA Core                         │  │  │  │
│  │  │  └────────────────────────────────────────────────────────────┘  │  │  │
│  │  └─────────────────────────────────────────────────────────────────┘  │  │
│  │                                                                       │  │
│  │  ┌─────────────────────────────────────────────────────────────────┐  │  │
│  │  │  webui (secondary service, depends on: mimocode)                │  │  │
│  │  │                                                                   │  │  │
│  │  │  ┌────────────────────────────────────────────────────────────┐  │  │  │
│  │  │  │  webui_server.py (port 8099)                               │  │  │  │
│  │  │  │  ├── SPA 静态文件 (React)                                   │  │  │  │
│  │  │  │  ├── API 代理 → mimo serve                                │  │  │  │
│  │  │  │  ├── /api/chat 端点 (薄桥接用)                              │  │  │  │
│  │  │  │  ├── 通道管理端点 (channels CRUD)                          │  │  │  │
│  │  │  │  └── 文件系统管理端点 (fs/list/read/write)                  │  │  │  │
│  │  │  └────────────────────────────────────────────────────────────┘  │  │  │
│  │  │                                                                   │  │  │
│  │  │  ┌────────────────────────────────────────────────────────────┐  │  │  │
│  │  │  │  channel_manager + 各通道客户端                              │  │  │  │
│  │  │  │  ├── MimoAIClient    (async 客户端，替代 urllib)            │  │  │  │
│  │  │  │  ├── FeishuClient    (WS 长连接)                            │  │  │  │
│  │  │  │  ├── WeChatWorkClient (HTTP 轮询)                           │  │  │  │
│  │  │  │  └── PersonalWeChatClient (iLink Bot API)                   │  │  │  │
│  │  │  └────────────────────────────────────────────────────────────┘  │  │  │
│  │  └─────────────────────────────────────────────────────────────────┘  │  │
│  └─────────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────┘
```

### 2.2 数据流

```
用户消息 → [飞书/微信/WebUI/HA]
    │
    ▼
channel_manager (通道统一入口)
    │
    ▼
channel_manager → MimoAIClient (async) 或 FeishuClient → MimoClientSync (sync)
    │
    ├── POST /session (创建/确认会话)
    ├── POST /session/{id}/message (发送消息)
    │
    ▼
mimo serve (AI 推理引擎)
    │
    ├── [需要设备控制] → MCP 调用 → ha_mcp_server → HA Supervisor API
    │
    └── [纯对话] → 直接生成 NDJSON 流响应
    │
    ▼
parse_ndjson_chunk (统一解析器)
    │
    ▼
回复 → [飞书/微信/WebUI/HA]
```

### 2.3 关键设计原则

1. **Addon 是主体** — 所有 IM 通道、AI 引擎、MCP 工具都在容器内，自包含
2. **custom_component 是薄桥接** — 只做 HA 对话入口，不做进程管理，不做 NDJSON 解析
3. **MimoAIClient 是单一入口** — 所有通道通过它调用 mimo serve，消除 4 处重复解析
4. **MCP 是标准协议** — 工具调用走 MCP stdio，不另造轮子
5. **s6-overlay 是标准底座** — 服务监督、健康检查、信号处理，零额外代码
6. **TCP 代理分离内外端口** — mimo serve 只绑 127.0.0.1，安全隔离

---

## 3. 组件详情

### 3.1 s6-overlay 服务定义

#### mimocode（主服务）

| 属性 | 值 |
|------|-----|
| 类型 | `longrun` |
| 启动顺序 | 最先 |
| 职责 | 启动 `mimo serve` + TCP proxy + 生成 `mimocode.json` |

**启动流程：**
1. 读取 `options.json`，获取配置参数
2. 生成 `mimocode.json`，注册 `ha-mcp` 为远程 MCP 服务器
3. 可选升级 `@mimo-ai/cli` 版本
4. 启动 TCP proxy（后台）：`0.0.0.0:$PORT` → `127.0.0.1:$INNER_PORT`
5. `exec` 启动 `mimo serve --port $INNER_PORT`（前台，s6 管理）

> **注意：** MCP 工具描述由 AI SDK 的 tool definitions 自动注入到模型请求中，无需手动设置 `instructions` 字段。AI 模型看到的工具列表包含 `ha_turn_on`、`ha_get_state` 等，自带 `description` 字段供模型理解。

**退出流程：** `finish` 脚本 kill TCP proxy 进程

#### ha-mcp（次服务，新增）

| 属性 | 值 |
|------|-----|
| 类型 | `longrun` |
| 依赖 | `mimocode`（通过 `dependencies` 文件） |
| 职责 | 启动 `ha_mcp_server.py`，提供 MCP 工具服务 |

**说明：** 当前版本中 `ha_mcp_server.py` 代码存在但未被任何启动脚本拉起。新架构中作为独立 `ha-mcp` s6 服务启动，由 s6 管理生命周期。

**文件结构：**
```
rootfs/etc/s6-overlay/s6-rc.d/ha-mcp/
├── type           # 内容: longrun
├── run            # 启动 ha_mcp_server.py
├── finish         # 清理 (可选)
└── dependencies   # 内容: mimocode (确保 mimocode 先启动)
```

**run 脚本内容：**
```bash
#!/usr/bin/with-contenv bashio
exec python3 /app/src/mimocode/mcp_server.py
```

`ha_mcp_server.py` 通过 stdio 实现 MCP 协议，暴露 8 个 HA 设备控制工具，通过 HTTP 调用 Supervisor API。

#### webui（次服务）

| 属性 | 值 |
|------|-----|
| 类型 | `longrun` |
| 依赖 | `mimocode`（通过 `dependencies` 文件） |
| 职责 | 启动 `webui_server.py`（SPA + API 代理 + 通道管理 + `/api/chat`） |

| 属性 | 值 |
|------|-----|
| 类型 | `longrun` |
| 依赖 | `mimocode` |
| 职责 | 启动 `webui_server.py`（SPA + API 代理 + 通道管理 + `/api/chat`） |

**启动流程：**
1. 读取 `options.json`，通过环境变量注入通道配置
2. 启动 `webui_server.py`，监听 `0.0.0.0:8099`
3. 内部初始化 `channel_manager`（使用 `MimoClientSync` 连接 `mimo serve`）

### 3.2 MimoAIClient（核心新增组件）

**位置：** `src/mimocode/client.py`

**职责：**
- 封装 `mimo serve` 的 HTTP API（`/session`、`/session/{id}/message`）
- 统一 NDJSON 流解析（`parse_ndjson_chunk`）
- 统一错误处理、超时控制
- 提供流式和非流式两种接口

**三个导出单元：**

| 类/函数 | 用途 | 使用者 |
|----------|------|--------|
| `parse_ndjson_chunk()` | 纯函数，缓冲区 → 解析对象列表 + 剩余缓冲区 | `MimoAIClient` 内部 |
| `MimoAIClient` | 纯 async 类，`send_message_stream()` 返回 `AsyncIterator[dict]` | `agent_impl.py`、`channel_manager`（async 上下文） |
| `MimoClientSync` | 同步封装，内部维护独立事件循环 | `feishu_client`（worker 线程） |

#### parse_ndjson_chunk

```python
def parse_ndjson_chunk(
    buffer: str,
    *,
    collect_text: bool = True,
    collect_reasoning: bool = False,
    collect_tool_calls: bool = False,
    dedup_by_id: bool = False,
    seen_ids: set[str] | None = None,
) -> tuple[list[dict[str, Any]], str]:
```

**这是 4 处重复 NDJSON 解析的核心统一函数。** 调用方只需传入缓冲区和需要的提取选项，即可获得解析后的对象列表。

#### MimoAIClient

```python
class MimoAIClient:
    async def ensure_session(session_id: str, timeout: float = 5.0) -> str
    async def send_message(text: str, session_id: str, *, timeout: float | None = None) -> str
    async def send_message_stream(text: str, session_id: str, *, timeout: float | None = None) -> AsyncIterator[dict]
    async def health_check(timeout: float = 5.0) -> bool
    async def close() -> None
```

#### MimoClientSync

```python
class MimoClientSync:
    def __init__(self, base_url: str = "http://127.0.0.1:14096"): ...
    def ensure_session(session_id: str, timeout: float = 5.0) -> str
    def send_message(text: str, session_id: str, timeout: float = 180.0) -> str
    def health_check(timeout: float = 5.0) -> bool
    def close() -> None
```

**⚠️ 盲点 #2：线程安全约束**

`MimoClientSync` 的实现不能使用 `asyncio.run()`，因为：
- `asyncio.run()` 创建一个新事件循环，如果调用者已经在一个 running loop 中（如 HA 的 async context），会抛出 `RuntimeError: asyncio.run() cannot be called from a running event loop`
- 这在 `channel_manager._handle_message()`（async 方法）中调用时就会触发

**正确使用规则：**

| 调用方 | 上下文 | 使用哪个类 | 原因 |
|--------|--------|-----------|------|
| `channel_manager.py` | async 方法（`_handle_message` 是 async def） | `MimoAIClient`（直接 async） | 同一事件循环，await 即可 |
| `feishu_client.py` | worker 线程（非 asyncio 线程） | `MimoClientSync`（`asyncio.run()`） | 无 running loop，安全 |
| `agent_impl.py` | HA event loop | `MimoAIClient`（直接 async） | 同一事件循环 |
| `webui_server.py` | HTTP 处理线程 | `MimoClientSync`（`asyncio.run()`） | 无 running loop，安全 |

**实现修正：**

```python
class MimoClientSync:
    """同步封装。只能从非 asyncio 线程调用！"""
    
    def __init__(self, base_url: str = "http://127.0.0.1:14096"):
        self._loop: asyncio.AbstractEventLoop | None = None
        self._client: MimoAIClient | None = None

    def _ensure(self) -> tuple[asyncio.AbstractEventLoop, MimoAIClient]:
        if self._loop is None or self._loop.is_closed():
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
        if self._client is None:
            self._client = MimoAIClient(self._base_url, loop=self._loop)
        return self._loop, self._client

    def send_message(self, text: str, session_id: str, timeout: float = 180.0) -> str:
        loop, client = self._ensure()
        return loop.run_until_complete(client.send_message(text, session_id, timeout=timeout))
    # ...
```

**调用方修正：** `channel_manager` 应改用 `MimoAIClient`（async），而非 `MimoClientSync`。这是 OPTIMIZATION_PLAN.md 中迁移路径的修正。

### 3.3 通道客户端

#### FeishuClient

| 属性 | 值 |
|------|-----|
| 通信 | `lark-oapi` SDK WebSocket 长连接模式 |
| 线程模型 | WS 线程（接收事件 → 入队） + Worker 线程（出队 → 调用 AI → 回复） |
| 重试 | 最多 8 次，5s 间隔 |
| 去重 | `_seen_message_ids` OrderedDict，上限 512 |
| 会话持久化 | 保存到 `/data/mimocode/.mimocode/feishu_sessions.json` |
| 迁移要点 | `_call_mimo()` 改用 `MimoClientSync`，保留推理过程实时推送 |

#### WeChatWorkClient

| 属性 | 值 |
|------|-----|
| 通信 | HTTP 回调模式（接收企业微信消息回调） |
| 消息路由 | 通过 `on_message` 回调 → `channel_manager._handle_message()` |
| 迁移要点 | 无直接 AI 调用，无需改造 |

#### PersonalWeChatClient

| 属性 | 值 |
|------|-----|
| 通信 | iLink Bot API（腾讯微信机器人接口） |
| 登录 | 二维码扫码登录 |
| 迁移要点 | 已修复（async + aiohttp），无需改造 |

### 3.4 ha_mcp_server（MCP 工具服务器）

| 属性 | 值 |
|------|-----|
| 协议 | MCP stdio（`2024-11-05`） |
| 工具数量 | 8 个 |
| 工具列表 | `ha_turn_on`、`ha_turn_off`、`ha_toggle`、`ha_set_brightness`、`ha_set_temperature`、`ha_get_state`、`ha_get_all_lights`、`ha_call_service` |
| 调用方式 | `mimo serve` 通过 MCP 远程调用 → `ha_mcp_server` → HTTP → Supervisor API |

**⚠️ 盲点 #1：AI 工具调用机制**

这是整个架构中最关键的链路。AI 要能正确调用 HA 工具，需要满足三个条件：

**条件 A：mimo serve 的 MCP 客户端能力**
`mimo serve`（基于 OpenCode）内置 MCP 客户端。启动时通过 `mimocode.json` 注册 `ha-mcp` 为远程 MCP 服务器，mimo serve 会自动调用 `tools/list` 发现工具列表。这是 OpenCode 原生能力，可用。

**条件 B：AI 模型支持工具调用（function calling）**
这是最大的风险点。`mimo serve` 使用的模型（如 MiMo Auto）必须原生支持 function calling / tool use。如果模型不支持：
- 所有 MCP 工具注册后模型不会调用
- 需要降级为系统 prompt 注入指令，让模型在文本回复中输出 JSON 格式的工具调用请求，由 ha_mcp_server 解析执行

**验证方法：** 启动后向 `mimo serve` 发送 "打开客厅灯"，如果模型返回工具调用请求，则支持。如果只返回文本，则需要降级方案。

**条件 C：系统 prompt 注入设备上下文**
AI 需要知道有哪些设备可用。`ha_mcp_server` 提供了 `ha_get_all_lights` 工具，但模型需要知道先调用它。解决方案：

```
在 mimo serve 的 system prompt 中注入指令：
1. 当用户要求控制设备时，先调用 ha_get_all_lights 查询可用设备
2. 根据设备列表和用户意图，选择合适的工具
3. 执行工具调用
```

system prompt 可通过 `mimocode.json` 的 `instructions` 字段注入。

**当前状态：** 代码存在但未被任何 s6 服务拉起。新架构中作为独立 `ha-mcp` 服务启动，由 s6 管理生命周期。

### 3.5 webui_server（WebUI + API 代理）

| 属性 | 值 |
|------|-----|
| 端口 | 8099（HA ingress） |
| 框架 | Python `http.server` + `ThreadingMixIn` |
| 职责 | SPA 静态文件服务 + API 代理 + 通道管理 + 文件管理 + `/api/chat` 端点 |

**新增 `/api/chat` 端点：**
- 用途：custom_component 的薄桥接入口
- 输入：`{"text": "用户消息", "session_id": "xxx"}`
- 输出：`{"text": "AI 回复", "session_id": "xxx"}`
- 内部：使用 `MimoAIClient` 调用 `mimo serve`，自动聚合流式响应
- 价值：custom_component 不需要 NDJSON 解析能力

### 3.6 custom_component（可选薄桥接）

**位置：** `custom_components/mimo_auto/`

| 组件 | 职责 |
|------|------|
| `__init__.py` | 注册对话代理，配置加载 |
| `manifest.json` | 依赖声明（无第三方依赖） |
| `config_flow.py` | 配置流：输入 addon 地址 |
| `conversation.py` | `ConversationEntity` 注册 |
| `agent_impl.py` | 薄桥接：`HTTP POST → addon:8099/api/chat` |

**不包含：** 进程管理（`coordinator.py`）、NDJSON 解析、MCP 客户端 — 这些全部在 addon 内完成。

### 3.7 会话持久化（新增）

**⚠️ 盲点 #3：会话持久化缺失**

当前所有通道每次对话都创建新 session，addon 重启后会话上下文全部丢失。对于"管家"场景（多轮对话），这是致命缺陷。

**方案：统一会话映射 + 文件持久化**

```
/data/mimocode/.mimocode/sessions.json
{
  "feishu:oc_xxx": "session_abc123",
  "wechat:user_xxx": "session_def456",
  "webui:conv_xxx": "session_ghi789",
  "ha:ha_conv_xxx": "session_jkl012"
}
```

**每条记录对应一个通道的会话映射：**

| 通道 | Key 格式 | 持久化策略 |
|------|----------|-----------|
| 飞书 | `feishu:{open_id}` | 启动时从文件加载，每次成功创建 session 后写入 |
| 企业微信 | `wechat:{user_id}` | 同上 |
| WebUI | `webui:{conv_id}` | 同上 |
| HA 对话 | `ha:{conv_id}` | 同上 |

**实现方式：**

```python
class SessionStore:
    """跨重启的会话映射持久化。"""
    
    _PATH = "/data/mimocode/.mimocode/sessions.json"
    
    def __init__(self):
        self._map: dict[str, str] = {}
        self._load()
    
    def get(self, channel: str, channel_id: str) -> str | None:
        return self._map.get(f"{channel}:{channel_id}")
    
    def set(self, channel: str, channel_id: str, session_id: str) -> None:
        self._map[f"{channel}:{channel_id}"] = session_id
        self._save()
    
    def _load(self) -> None:
        try:
            if os.path.exists(self._PATH):
                with open(self._PATH) as f:
                    self._map = json.load(f)
        except Exception:
            self._map = {}
    
    def _save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self._PATH), exist_ok=True)
            with open(self._PATH, "w") as f:
                json.dump(self._map, f)
        except Exception:
            pass
```

**各通道集成方式：**

| 通道 | 集成点 |
|------|--------|
| `channel_manager` | `_handle_message` 中先查 `SessionStore.get()`，没有再创建 |
| `feishu_client` | 已实现 `feishu_sessions.json`，迁移到统一 `SessionStore` |
| `agent_impl` | `_send_message` 中先查 `SessionStore.get("ha", conv_id)` |
| `webui_server` | `/api/chat` 端点中先查 `SessionStore.get("webui", session_id)` |

**mimo serve 原生 session 持久化：** mimo serve 可能自身支持 session checkpoint。如果支持，`SessionStore` 只需映射 channel_id → session_id，不需要额外维护上下文。

---

## 4. 目录结构

### 4.1 Addon 代码

```
addons/mimo-code/
├── Dockerfile                         # 两阶段构建
├── config.yaml                        # Addon 配置定义
├── build.yaml                         # 构建配置
├── CHANGELOG.md
├── README.md
│
├── rootfs/                            # s6-overlay 标准布局
│   ├── etc/
│   │   └── s6-overlay/
│   │       └── s6-rc.d/
│   │           ├── mimocode/          # 主服务
│   │           │   ├── type
│   │           │   ├── run
│   │           │   └── finish
│   │           ├── ha-mcp/            # [新增] MCP 工具服务
│   │           │   ├── type
│   │           │   ├── run
│   │           │   ├── finish
│   │           │   └── dependencies
│   │           ├── webui/             # WebUI 服务 (原 mimocode-webui)
│   │           │   ├── type
│   │           │   ├── run
│   │           │   ├── finish
│   │           │   └── dependencies
│   │           └── user/
│   │               └── contents.d/
│   │                   ├── ha-mcp
│   │                   ├── webui
│   │                   └── mimocode
│   └── usr/local/bin/
│       └── mimo-init.sh               # 初始化脚本
│
├── src/                               # Python 源码
│   ├── __init__.py
│   └── mimocode/                      # Python 包
│       ├── __init__.py
│       ├── client.py                  # [核心] MimoAIClient + MimoClientSync + parse_ndjson_chunk
│       ├── config.py                  # 配置管理
│       ├── channel_manager.py         # IM 通道管理
│       ├── feishu.py                  # 飞书 WS 客户端
│       ├── wechat.py                  # 企业微信客户端
│       ├── wechat_personal.py         # 个人微信客户端
│       ├── mcp_server.py              # HA MCP 工具服务器
│       ├── webui_server.py            # WebUI + API 代理
│       └── tests/                     # 单元测试
│           ├── __init__.py
│           └── test_client.py         # parse_ndjson_chunk 测试
│
├── webui/                             # 前端 SPA 源码
│   └── dist/                          # 构建产物
│
└── requirements.txt
```

### 4.2 custom_component 代码

```
custom_components/mimo_auto/
├── __init__.py
├── manifest.json
├── config_flow.py
├── conversation.py
└── agent_impl.py
```

### 4.3 与当前结构的差异

| 当前 | 新结构 | 说明 |
|------|--------|------|
| `rootfs/usr/share/mimocode/webui/` 下所有 Python 文件 | `src/mimocode/` 包 | 集中管理，`PYTHONPATH` 统一 |
| 文件名散乱（`server.py`, `ha_mcp_server.py`, `channel_manager.py`） | 统一命名空间（`mimocode.*`） | `from mimocode.client import MimoAIClient` |
| `mimocode-webui` 一个 s6 服务包含所有 | `webui` + `ha-mcp` 两个独立服务 | 职责分离，独立重启 |
| 无 `ha-mcp` s6 服务 | 新增 `ha-mcp` s6 服务 | 修复 `ha_mcp_server.py` 未被拉起的漏洞 |
| 无 `client.py` | 新增 `client.py` | 统一 AI 客户端，消除 4 处重复解析 |
| 无 `tests/` | 新增 `tests/` | 核心逻辑可测试 |

---

## 5. API 设计

### 5.1 MimoAIClient 完整 API 签名

```python
# 异常
class MimoClientError(Exception):
    """所有 MimoAIClient 操作的基类异常。"""

# 核心函数
def parse_ndjson_chunk(
    buffer: str,
    *,
    collect_text: bool = True,
    collect_reasoning: bool = False,
    collect_tool_calls: bool = False,
    dedup_by_id: bool = False,
    seen_ids: set[str] | None = None,
) -> tuple[list[dict[str, Any]], str]:
    """解析 NDJSON 缓冲区，返回 (解析对象列表, 剩余缓冲区)。

    此函数是纯函数，无副作用，可在同步和异步上下文中使用。
    所有调用方通过此函数统一 NDJSON 解析逻辑。
    """

# 异步客户端
class MimoAIClient:
    def __init__(
        self,
        base_url: str = "http://127.0.0.1:14096",
        session: aiohttp.ClientSession | None = None,
        default_timeout: float = 180.0,
    ): ...

    async def ensure_session(self, session_id: str, timeout: float = 5.0) -> str:
        """确保会话存在。"""

    async def send_message(self, text: str, session_id: str, *, timeout: float | None = None) -> str:
        """发送消息，返回完整文本。"""

    async def send_message_stream(self, text: str, session_id: str, *, timeout: float | None = None) -> AsyncIterator[dict]:
        """发送消息，流式 yield 解析后的 JSON 对象。"""

    async def health_check(self, timeout: float = 5.0) -> bool:
        """检查服务是否可达。"""

    async def close(self) -> None: ...

# 同步封装（用于线程上下文）
class MimoClientSync:
    def __init__(self, base_url: str = "http://127.0.0.1:14096"): ...

    def ensure_session(self, session_id: str, timeout: float = 5.0) -> str: ...
    def send_message(self, text: str, session_id: str, timeout: float = 180.0) -> str: ...
    def health_check(self, timeout: float = 5.0) -> bool: ...
    def close(self) -> None: ...
```

### 5.2 `/api/chat` 端点（薄桥接用）

```
POST /api/chat
Content-Type: application/json

请求:
{
  "text": "开灯",
  "session_id": "ha-conversation-abc"    // 可选，不传则自动创建
}

响应:
{
  "text": "已为您打开客厅灯",
  "session_id": "ha-conversation-abc"
}

错误:
HTTP 502
{
  "error": "mimo serve 不可达",
  "code": "SERVICE_UNAVAILABLE"
}
```

### 5.3 现有 API 端点（保持不变）

| 路径 | 方法 | 用途 |
|------|------|------|
| `/api/session` | POST | 创建会话 |
| `/api/session/{id}/message` | POST | 发消息（NDJSON 流） |
| `/api/config` | GET/PATCH | 读写配置 |
| `/api/provider` | GET | 列出 AI 提供商 |
| `/api/channels/status` | GET | 通道状态 |
| `/api/channels` | GET/POST | 通道管理 |
| `/api/fs/list` | GET | 文件列表 |
| `/api/fs/read` | GET | 读文件 |
| `/api/fs/write` | POST | 写文件 |
| `/api/wechat/login` | POST | 微信扫码登录 |
| `/api/wechat/login/status` | GET | 查询登录状态 |
| `/api/feishu/test` | POST | 飞书连接测试 |
| `/healthcheck` | GET | 健康检查（Supervisor 使用） |

---

## 6. 迁移路径

### 6.1 4 处 NDJSON 解析 → 统一 client.py

| 当前位置 | 当前行数 | 替换为 | 迁移方式 |
|----------|----------|--------|----------|
| `channel_manager.py` `_parse_response()` | ~55 行 | `MimoAIClient.send_message()`（async） | 改用 async，删除方法 |
| `feishu_client.py` `_call_mimo()` 内联解析 | ~40 行 | `MimoClientSync.send_message_stream()`（同步） | 换调用，保留推理推送 |
| `agent_impl.py` `_parse_json_stream()` | ~90 行 | `MimoAIClient.send_message_stream()`（async） | 换调用，保留 tool_calls 提取 |
| `server.py` `_proxy_request()` 流式路径 | 豁免 | 不变 | 纯字节透传，不解析 |

> **修正说明：** `channel_manager._handle_message()` 是 async 方法，应使用 `MimoAIClient`（async），而非 `MimoClientSync`（同步）。`feishu_client._call_mimo()` 在 worker 线程中运行，使用 `MimoClientSync`（同步）正确。

### 6.2 代码位置迁移

| 当前文件 | 新位置 | 改动 |
|----------|--------|------|
| `webui/server.py` | `src/mimocode/webui_server.py` | 重命名，新增 `/api/chat` 端点 |
| `webui/channel_manager.py` | `src/mimocode/channel_manager.py` | 改用 `MimoClientSync` |
| `webui/feishu_client.py` | `src/mimocode/feishu.py` | 改用 `MimoClientSync` |
| `webui/wechat_client.py` | `src/mimocode/wechat.py` | 不变 |
| `webui/wechat_personal.py` | `src/mimocode/wechat_personal.py` | 不变 |
| `webui/ha_mcp_server.py` | `src/mimocode/mcp_server.py` | 重命名 |
| `webui/tcp_proxy.py` | 保留在 rootfs | 不变 |
| custom_components/* | 不变 | `agent_impl.py` 改用 `MimoAIClient` |

### 6.3 s6 服务变更

| 当前 | 新 | 说明 |
|------|-----|------|
| `mimocode` (run + finish + type) | 不变 | 主服务 |
| `mimocode-webui` (run + finish + type + dependencies + user) | 拆分为 `webui` + `ha-mcp` | 职责分离 |
| 无 | `ha-mcp` (run + finish + type + dependencies + user) | 新增，修复现有漏洞 |

---

## 7. 实施计划

### 阶段一：基础建设 [2h]

**目标：** 新增 `client.py` + `tests/test_client.py`

- 实现 `parse_ndjson_chunk()` 纯函数
- 实现 `MimoAIClient` async 类
- 实现 `MimoClientSync` 同步封装
- 编写 `test_client.py`（7 个 parse_ndjson_chunk 单测 + 9 个 MimoAIClient 单测）

### 阶段二：目录重构 [2h]

**目标：** 建立新目录结构，迁移现有代码

- 创建 `src/mimocode/` 包结构
- 迁移 7 个 Python 文件到新位置，统一命名空间
- 更新 `webui_server.py`（原 `server.py`）新增 `/api/chat` 端点
- 更新 Dockerfile 中 `COPY src/` 路径和 `PYTHONPATH`

### 阶段三：通道层迁移 [2h]

**目标：** `channel_manager.py` + `feishu.py` 改用 `MimoClientSync`

- `channel_manager.py`：删除 `_call_mimo_serve()` + `_parse_response()`，注入 `MimoClientSync`
- `feishu.py`：`_call_mimo()` 改用 `MimoClientSync.send_message_stream()`，保留推理过程推送
- 删除内联 NDJSON 解析代码

### 阶段四：HA 侧 + s6 修复 [2h]

**目标：** `agent_impl.py` 改用 `MimoAIClient` + 新增 `ha-mcp` s6 服务

- `agent_impl.py`：`_send_message()` 改用 `MimoAIClient.send_message_stream()`，删除 `_parse_json_stream()`
- 新增 `ha-mcp` s6 服务目录（type + run + finish + dependencies）
- 拆 `webui` 服务（原 `mimocode-webui`）移除 WebUI 对 channel 的耦合

### 阶段五：清理与测试 [1h]

**目标：** 删除死代码，验证全部功能

- 删除旧目录下的已迁移文件
- 更新 `ARCHITECTURE.md` 中的架构图
- 容器内运行全部测试
- 部署到 HA 验证

### 实施甘特图

```
阶段一 (基础建设)      ████████████░░░░  2h
阶段二 (目录重构)      ████████████████  2h
阶段三 (通道层迁移)     ████████████████  2h
阶段四 (HA 侧 + s6)    ████████████████  2h
阶段五 (清理与测试)     ████████░░░░░░░░  1h
                    ─────────────────
                     总计 ~9h
```

---

## 8. 测试策略

### 8.1 单元测试

| 被测 | 优先级 | 用例数 | 工具 |
|------|--------|--------|------|
| `parse_ndjson_chunk` | 高 | 7 | `pytest` |
| `MimoAIClient` | 高 | 9 | `pytest-aiohttp` + mock |
| `MimoClientSync` | 中 | 4 | `pytest` + mock |

### 8.2 集成测试

| 场景 | 方法 | 验证点 |
|------|------|--------|
| channel_manager 替换 | 用 `MimoClientSync` 替换后发送测试消息 | 返回文本与之前一致 |
| feishu 替换 | 替换 `_call_mimo` 后处理飞书消息 | 推理过程 + 最终回复均正常 |
| agent_impl 替换 | 替换 `_send_message` 后 | `tool_calls` + `reasoning` 正确提取 |
| ha-mcp s6 服务 | 启动后检查进程 | 进程运行，`mimo serve` 可调用 |
| `/api/chat` 端点 | HTTP POST 测试 | 返回纯文本，无 NDJSON |

### 8.3 回滚策略

每个阶段可独立回滚：
- 阶段一：删 `client.py`，回退到旧代码
- 阶段二：改 Dockerfile 路径，简单 revert
- 阶段三：保留旧 `_call_mimo_serve` 方法，加 flag 切换
- 阶段四：`agent_impl.py` 保留旧 `_send_message` 方法

---

## 9. 风险与缓解

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|----------|
| AI 模型不支持 MCP 工具调用 | 中 | 高 | 阶段一验证：发"打开客厅灯"测试工具调用；不支持则降级为 system prompt 文本指令 + ha_mcp_server 解析 |
| `MimoClientSync` 事件循环泄漏 | 低 | 中 | `close()` 确保清理；`loop.run_until_complete()` 替代 `asyncio.run()` |
| feishu 推理过程推送时序变化 | 中 | 低 | 流式接口保持逐块处理，时序不变 |
| agent_impl 的 `tool_calls` 提取遗漏 | 低 | 中 | 统一 `parse_ndjson_chunk` 后，`tool_use` 部分标记为 `collect_tool_calls=True` |
| ha-mcp s6 新服务启动顺序问题 | 低 | 高 | `dependencies` 确保 `mimocode` 先启动 |
| Dockerfile 路径变更导致构建失败 | 中 | 中 | 阶段二单独验证构建，回退路径保留 |
| 并行调用 `MimoClientSync` 共享 loop 冲突 | 低 | 中 | 单线程使用，feishu 已有 worker 线程，不跨线程共享实例 |
| session 持久化文件损坏 | 低 | 低 | `SessionStore._save()` 写前先写临时文件，原子 rename |

---

## 10. 未来增强

### 10.1 主动通知（管家核心能力）

当前架构只覆盖了"用户发消息 → AI 回复"的被动模式。管家的核心差异化是主动告知：

- 人到家了 → 通知
- 温度异常 → 建议开空调
- 晚安时间 → 自动关灯

**实现路径：**

```
HA 事件 → [Supervisor API / 直接 WebSocket] → 容器内事件监听器
    │
    ▼
channel_manager → 判断是否需要通知 → 选择通道
    │
    ├── → 飞书卡片消息
    ├── → 企业微信消息
    └── → WebUI 推送
```

**技术选型：**

| 方案 | 优点 | 缺点 |
|------|------|------|
| **HA WebSocket API** | 实时事件，无需轮询 | 需要保持长连接 |
| **Supervisor API 定时轮询** | 实现简单 | 延迟高（~30s） |
| **MQTT 桥接** | 标准协议 | 需要额外配置 MQTT |

**建议：** 初期阶段不进，作为 V2 功能。优先级低于当前的架构重构。

### 10.2 直接 MCP 桥接

当前方案：
```
custom_component → HTTP POST addon:8099/api/chat → MimoAIClient → mimo serve
```

替代方案：
```
custom_component → MCP 客户端 → MCP 协议 → mimo serve (/mcp 端点)
```

mimo serve 本身可能暴露 MCP HTTP 端点，custom_component 可以作为 MCP 客户端直接连接，不需要 `/api/chat` 中间层。但这更复杂，`/api/chat` 方案足够用。**不进当前阶段。**

---

## 11. 附录

### 11.1 当前架构问题清单

| # | 问题 | 严重度 | 修复方式 |
|---|------|--------|----------|
| 1 | NDJSON 解析 4 处重复 | 中 | `client.py` 统一 |
| 2 | urllib + aiohttp 混用 | 中 | `MimoAIClient` + `MimoClientSync` 统一 |
| 3 | `ha_mcp_server.py` 未被拉起 | 高 | 新增 `ha-mcp` s6 服务 |
| 4 | 代码散落在 `rootfs/usr/share/` 深层 | 低 | `src/mimocode/` 集中 |
| 5 | 无统一客户端类 | 中 | `MimoAIClient` |
| 6 | 无单元测试 | 中 | `tests/test_client.py` |
| 7 | Supervisor 配置未桥接到环境变量 | **已修复** | `mimocode-webui/run` 中 export 环境变量 |
| 8 | 会话持久化缺失 | 中 | `SessionStore` 统一映射 |
| 9 | AI 工具调用链路未验证 | 高 | 启动后验证 MCP 工具调用，否则降级 |

### 10.2 参考文档

- [MiMo Code 官方文档](https://mimo.xiaomi.com/zh/mimocode)
- [OpenCode GitHub](https://github.com/opencode-ai/opencode)
- [MCP 协议规范](https://modelcontextprotocol.io/)
- [HA Addon 开发文档](https://developers.home-assistant.io/docs/add-ons/)