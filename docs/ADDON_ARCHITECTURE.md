# MiMo Code Addon — 最优架构方案

> 将 MiMo Code 包装为 HA Supervisor Addon，提供 AI 智能体 + 多通道 IM + Web UI 的完整解决方案。

---

## 一、设计理念

### 1.1 核心原则

**Addon 做"重活"，Component 做"桥接"**

```
Addon (容器)                    Component (HA 插件)
────────────────────────────────────────────────────
AI 引擎 (mimo serve)           对话代理 (Conversation Agent)
IM 通道 (飞书/微信/个人微信)    状态传感器 (Server Status)
Web UI (React SPA)             系统管理服务 (SSH/Supervisor)
HA 设备控制 → 外部 ha-mcp      API 代理 (跨域转发)
```

Addon 是独立的容器，有完整的运行时环境，适合运行需要长时间驻留、资源隔离、网络访问的服务。Component 是 HA 进程内的插件，负责将 Addon 的能力暴露为 HA 原生实体和服务。

### 1.2 架构原则

| 原则 | 说明 |
|------|------|
| **最小依赖** | Addon 只依赖 `mimo serve` 二进制 + Python 运行时，不依赖外部服务 |
| **外部化共享服务** | MCP 设备控制由外部 ha-mcp 集成提供，Addon 不重复造轮子 |
| **配置驱动** | 所有配置通过 `options.json` 注入，不改代码即可调整行为 |
| **代码持久化** | Python 热更新代码部署到 `/data/mimocode/webui/`，穿越容器重建 |
| **单进程模型** | 尽可能减少 s6 子服务，降低启动复杂度和故障点 |

### 1.3 定位

> **MiMo Code Addon = AI 引擎 + IM 消息网关 + Web UI 面板**

它不是一个"HA 集成"，而是一个**独立的 AI 智能体容器**，通过标准 HTTP 协议与 HA 通信。HA 只是它的一个"上游服务"（通过 ha-mcp 提供设备控制），而不是它的宿主。

---

## 二、最优架构

### 2.1 服务拓扑

```
┌─────────────────────────────────────────────────────────────┐
│                HA Supervisor (宿主机)                         │
│                                                              │
│  ┌──────────────────────────────────────────────────────┐    │
│  │  Addon: mimo-code                                     │    │
│  │                                                       │    │
│  │  ┌──────────────────────┐  ┌───────────────────────┐  │    │
│  │  │  s6: mimocode         │  │  s6: mimocode-webui   │  │    │
│  │  │  mimo serve (14095)   │  │  server.py (8099)     │  │    │
│  │  │  AI 推理引擎          │  │  FastAPI 主服务       │  │    │
│  │  │  MCP → 外部 ha-mcp   │  │  ├── IM 通道管理      │  │    │
│  │  └──────────────────────┘  │  ├── 进化回顾          │  │    │
│  │                             │  ├── HA 上下文注入    │  │    │
│  │                             │  ├── 会话持久化       │  │    │
│  │                             │  └── React SPA 静态   │  │    │
│  │                             └───────────────────────┘  │    │
│  └──────────────────────────────────────────────────────┘    │
│                                                              │
│  ┌──────────────────────────────────────────────────────┐    │
│  │  ha-mcp 集成 (83+ 工具)                               │    │
│  │  https://api.homediy.top:8443/api/webhook/mcp_...   │    │
│  └──────────────────────────────────────────────────────┘    │
│                                                              │
│  ┌──────────────────────────────────────────────────────┐    │
│  │  HA Core (REST API + WebSocket)                      │    │
│  │  └── 设备控制、状态查询、服务调用                      │    │
│  └──────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────┘
```

### 2.2 通信链路

```
用户 → 飞书/微信 → mimocode-webui (8099)
                     │
                     ├─ channel_manager → MimoAIClient → mimo serve (14095)
                     │                                              │
                     │                                     ha-mcp 集成 (外部)
                     │                                              │
                     │                                     HA REST API
                     │
                     └─ React SPA (Ingress 侧边栏)
```

### 2.3 只需要 2 个 s6 服务

| 服务 | 端口 | 启动顺序 | 用途 |
|------|------|---------|------|
| `mimocode` | 14095 | 1 | `mimo serve` AI 推理引擎 |
| `mimocode-webui` | 8099 | 2 (依赖 mimocode) | FastAPI + 通道 + Web UI |

**删掉的**：~~`ha-mcp`~~（port 8234，内置 8 静态工具，冗余）

---

## 三、对比现状

### 3.1 现状 vs 最优

```
现状 (3 个 s6 服务):
┌──────────┐   ┌──────────┐   ┌──────────────┐
│ mimocode  │──▶│  ha-mcp  │──▶│ mimocode-webui│
│ (14095)   │   │ (8234)   │   │ (8099)        │
│           │   │ 8 工具   │   │               │
└──────────┘   └──────────┘   └──────────────┘
                    │
                    │ 冗余！改写后重跑
                    ▼
              ha-mcp 集成 (外部, 83+ 工具)

最优 (2 个 s6 服务):
┌──────────┐   ┌──────────────┐
│ mimocode  │──▶│ mimocode-webui│
│ (14095)   │   │ (8099)        │
│ MCP → 外部│   │               │
└──────────┘   └──────────────┘
```

### 3.2 清理清单

| 操作 | 文件/目录 | 影响 |
|------|----------|------|
| 删除 | `mimo-code/rootfs/etc/s6-overlay/s6-rc.d/ha-mcp/` | 移除 s6 服务 |
| 删除 | `mimo-code/rootfs/usr/share/mimocode/webui/ha_mcp_server.py` | 移除冗余代码 |
| 删除 | `mimo-code/rootfs/etc/s6-overlay/s6-rc.d/user/contents.d/ha-mcp` | 移除启动顺序 |
| 修改 | `mimocode/run` 中移除 `ha_mcp_url` 回退逻辑 | 不再需要内置回退 |
| 配置 | `options.json` 中设置 `ha_mcp_url` | 指向外部 ha-mcp |

---

## 四、核心工作流

### 4.1 消息处理流

```
IM 消息 → feishu_client._on_message_event()
         → 消息去重 (OrderedDict, 512)
         → 推入 _msg_queue (非阻塞, WS 线程立即返回)
         → Worker 线程拉取
         → channel_manager._handle_message()
              → 构建 system prompt (persona + HA 上下文 + 进化经验)
              → MimoAIClient.send_message() → mimo serve (14095)
              → 解析 NDJSON 响应
              → 安排进化回顾
              → 发送回复 (文字/图片/视频/文件/卡片)
```

### 4.2 进化回顾流

```
对话完成 → schedule_review()
         → 创建独立 session
         → 发送分析 prompt
         → AI 返回 lessons JSON
         → 持久化到 /data/mimocode/lessons.json (最多 100 条)
         → TTL 1 小时防重复
         → 下次对话注入 system prompt
```

### 4.3 HA 上下文注入流

```
用户消息 → 构建 system prompt
         → ha_context.get_context()
              → 检查缓存 (30s TTL)
              → 如果过期 → HA REST API /api/states
              → 按优先级域排序: light, climate, switch, cover...
              → 格式化: "当前时间：...\n\n可用设备：\n\n[light]\n- 客厅灯: on..."
         → 注入到 system prompt
         → 发送给 mimo serve
```

---

## 五、配置体系

### 5.1 options.json (Addon 配置)

```json
{
  "port": 14096,
  "ha_mcp_url": "https://api.homediy.top:8443/api/webhook/mcp_97521c4cb653c43b9c9448410d0745d5",
  "feishu_enabled": true,
  "feishu_app_id": "cli_xxx",
  "feishu_app_secret": "xxx",
  "wechat_enabled": true,
  "wechat_corp_id": "xxx",
  "wechat_agent_id": "xxx",
  "wechat_secret": "xxx",
  "personal_wechat_enabled": false,
  "mimo_version": ""
}
```

### 5.2 配置流向

```
options.json (Addon UI)
    │
    ├──→ mimocode/run → mimo serve --port ${MIMO_PORT}
    │                     └── HA_MCP_URL → 外部 ha-mcp
    │
    └──→ mimocode-webui/run → server.py (环境变量注入)
                              ├── FEISHU_* → feishu_client.py
                              ├── WECHAT_* → wechat_client.py
                              ├── PERSONAL_WECHAT_* → wechat_personal.py
                              └── HA_MCP_URL → 透传
```

### 5.3 数据持久化

```
/data/mimocode/
  ├── mimo.json           # mimo serve 配置
  ├── sessions.json       # 会话映射 (feishu:xxx → session_id)
  ├── lessons.json        # 进化回顾经验
  ├── persona.json        # 人格配置
  ├── webui/              # 热更新代码 (穿越容器重建)
  │   ├── server.py
  │   ├── feishu_client.py
  │   └── ...
  └── config.json         # 运行时持久化配置
```

---

## 六、部署与运维

### 6.1 部署流程

```powershell
# 1. 部署 Python 热更新代码到持久化目录
$files = @(
    "server.py", "channel_manager.py", "feishu_client.py",
    "wechat_client.py", "wechat_personal.py", "base_channel.py",
    "client.py", "session_store.py", "ha_context.py",
    "evolution_review.py", "persona.py", "media.py", "media_utils.py",
    "tts.py", "card.py", "tcp_proxy.py"
)
$src = "D:\ai-hub\integrations\mimo_auto\mimo-code\rootfs\usr\share\mimocode\webui"
foreach ($f in $files) {
    $content = Get-Content "$src\$f" -Raw -Encoding UTF8
    $content = $content -replace "`r`n", "`n"
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($content)
    $base64 = [Convert]::ToBase64String($bytes)
    echo "Deploying $f..."
    echo $base64 | ssh -4 -i ~/.ssh/id_ha root@api.homediy.top `
        "docker exec -i addon_local_mimo-code sh -c 'mkdir -p /data/mimocode/webui && base64 -d > /data/mimocode/webui/$f'"
}

# 2. 重启 HA Core (重启 Addon 内核)
$token = $env:HA_TOKEN
ssh -i ~/.ssh/id_ha root@api.homediy.top "curl -s -X POST -H 'Authorization: Bearer ${token}' -H 'Content-Type: application/json' -d '{}' 'http://localhost:8123/api/services/homeassistant/restart'"
```

### 6.2 验证

```bash
# 检查进程
docker exec addon_local_mimo-code ps aux

# 检查日志
docker logs addon_local_mimo-code --tail 50

# 检查健康
docker exec addon_local_mimo-code curl -s http://127.0.0.1:8099/healthcheck
# → OK

# 检查 mimo serve 会话
docker exec addon_local_mimo-code curl -s http://127.0.0.1:14095/session
# → [{"id":"ses_xxx","slug":"...","version":"0.1.7",...}]

# 检查外部 ha-mcp 连接 (通过 SSH 直接测试)
curl -s -X POST -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' \
  'https://api.homediy.top:8443/api/webhook/mcp_xxx'
# → 返回 {"jsonrpc":"2.0","id":1,"result":{"tools":[...]}}

# 检查 IM 通道状态 (从日志查看)
docker logs addon_local_mimo-code 2>&1 | grep -E 'channel|feishu|WeChat|websocket'
# → 应看到: Feishu channel started, Personal WeChat channel started, 飞书 WebSocket 已连接

# 检查 s6 服务状态
docker exec addon_local_mimo-code s6-svstat /run/service/mimocode
docker exec addon_local_mimo-code s6-svstat /run/service/mimocode-webui
# → up (pid X) XXX seconds

# 测试 AI 对话
docker exec addon_local_mimo-code curl -s -X POST http://127.0.0.1:8099/api/session \
  -H 'Content-Type: application/json' -d '{}'
# 获取 session_id，然后:
docker exec addon_local_mimo-code curl -s -N -X POST \
  'http://127.0.0.1:8099/api/session/{session_id}/message' \
  -H 'Content-Type: application/json' \
  -d '{"message":"你好","parts":[{"type":"text","text":"你好"}]}'
# → 返回 NDJSON 流式响应，包含 reasoning + text 事件
```

### 6.3 热更新机制

`mimocode-webui/run` 第 38-39 行已有覆盖逻辑：

```bash
if [ -d /data/mimocode/webui ] && [ -n "$(ls -A /data/mimocode/webui 2>/dev/null)" ]; then
    cp -rf /data/mimocode/webui/. /usr/share/mimocode/webui/
fi
```

这意味着：
- 向 `/data/mimocode/webui/` 部署新代码后，重启 Addon 即可生效
- 代码穿越容器重建，`ha addons update` 后不会丢失
- 回滚方法：`docker exec $CONTAINER rm -rf /data/mimocode/webui`

---

## 七、与 Component 的关系

### 7.1 职责边界

| 能力 | Addon 负责 | Component 负责 |
|------|-----------|---------------|
| AI 对话 | `mimo serve` 引擎 | 注册 Conversation Agent |
| IM 通道 | 飞书/微信收发消息 | ❌ 不参与 |
| Web UI | React SPA | ❌ 不参与 |
| 设备控制 | 通过外部 ha-mcp | ❌ 不参与 (但提供 MCP 客户端) |
| 系统管理 | ❌ 不参与 | SSH/Supervisor 客户端 |
| 状态展示 | ❌ 不参与 | 4 个状态传感器 |
| API 代理 | ❌ 不参与 | 跨域代理 |

### 7.2 通信方式

Addon 和 Component 完全独立，通过 HTTP 通信：

```
Component → Addon:  /api/mimo_auto/proxy/* → HTTP → Addon API
Addon → HA:         ha-mcp 集成 → HA REST API
```

**没有进程内耦合**，Addon 和 Component 可以独立部署、独立升级。

---

## 八、与 CN IM Hub 的关系

### 8.1 子集 + 增强

Mimo Auto Addon 的 IM 通道是 CN IM Hub 的**子集 + AI 增强版**：

```
CN IM Hub (7 通道)         Mimo Auto Addon (3 通道)
────────────────────────────────────────────────────
✅ 飞书 WS                  ✅ 飞书 WS (加了推理推送)
✅ 企业微信                 ✅ 企业微信
✅ 个人微信 iLink Bot       ✅ 个人微信 iLink Bot (加了 TTS 语音)
✅ QQ                       ❌
✅ 钉钉                     ❌
✅ 小懿                     ❌
✅ 自定义                   ❌
✅ 摄像头                   ❌
✅ 审批卡片                 ❌
```

### 8.2 建议方向

如果想减少维护成本，最直接的方式是：
1. Addon 的 IM 通道直接依赖 CN IM Hub 的 Provider 包
2. 不再维护 `feishu_client.py`、`wechat_personal.py` 等副本
3. 只维护 AI 增强层（推理推送、TTS、进化回顾、HA 上下文）

---

## 九、总结：这个 Addon 是什么

```
MiMo Code Addon =
  MiMo Code AI 引擎 (mimo serve)
  + IM 消息网关 (飞书/企业微信/个人微信, 基于 CN IM Hub 架构)
  + Web UI (React SPA)
  + 进化回顾 (自学习系统)
  + HA 上下文注入 (设备状态感知)
  + 人格系统 (灵犀管家)
  + 会话持久化
  + 富媒体消息 (图片/视频/文件/语音/卡片)
  - MCP 设备控制 (由外部 ha-mcp 集成提供)
  - HA 系统管理 (由 Component 提供)
```

它不是一个"HA 集成"——它是一个**独立的 AI 智能体容器**，IM 通道是它的"嘴巴"，ha-mcp 是它的"手"，进化回顾是它的"大脑"。HA 只是它服务的对象之一。它能做的事情远不止 HA 控制，你可以通过它问问题、查资料、写代码、管理文件，就像使用任何 AI 助手一样。

---

## 附录：部署验证报告

### 验证时间

2026-07-23 10:30 CST (UTC+8)

### 容器状态

| 检查项 | 结果 | 详情 |
|--------|------|------|
| 容器运行 | ✅ | `addon_local_mimo-code` Up 57 分钟 |
| s6: mimocode | ✅ | `up (pid 43) 3465 seconds` |
| s6: mimocode-webui | ✅ | `up (pid 49) 3465 seconds` |
| ~~s6: ha-mcp~~ | ✅ 已删除 | 原冗余服务已移除 |
| 端口暴露 | ✅ | 14096/tcp → 0.0.0.0:14096 |

### 服务健康

| 检查项 | 结果 | 详情 |
|--------|------|------|
| WebUI Healthcheck | ✅ | `GET /healthcheck → 200 OK` |
| mimo serve 会话 | ✅ | 4 个活跃会话 |
| 外部 ha-mcp MCP | ✅ | `tools/list` 返回完整工具列表 (ha_search, ha_manage_backup 等) |
| MCP 配置 | ✅ | `mimo.json` 中 `ha_mcp_url` 已配置 |

### IM 通道

| 检查项 | 结果 | 详情 |
|--------|------|------|
| 飞书 WebSocket | ✅ | `飞书 WebSocket 已连接` |
| 个人微信 | ✅ | `Personal WeChat channel started (from saved credentials)` |
| 企业微信 | ✅ 已禁用 | 未配置凭证 |
| 通道数量 | ✅ | `Started 2 channel(s): feishu, personal_wechat_default` |

### AI 对话测试

| 检查项 | 结果 | 详情 |
|--------|------|------|
| 创建会话 | ✅ | `POST /api/session → 201` |
| 发送消息 | ✅ | `POST /api/session/{id}/message → NDJSON 流式响应` |
| 模型 | ✅ | `mimo-auto` (免费版) |
| 推理能力 | ✅ | 返回 `reasoning` 事件 (中文思考过程) |
| 文本回复 | ✅ | 返回完整中文自我介绍 |
| Token 用量 | ✅ | 33,768 input + 120 output + 86 reasoning |

### 进化回顾系统

| 检查项 | 结果 | 详情 |
|--------|------|------|
| 已学习经验 | ✅ | 16 条 lessons (style/technique/correction) |
| 持久化 | ✅ | `/data/mimocode/lessons.json` |
| 注入 | ✅ | 对话时注入 system prompt |

### 错误日志

| 检查项 | 结果 | 详情 |
|--------|------|------|
| Errors | ✅ 无 | 日志中无 error/fail/exception/traceback |
| Warnings | ⚠️ 1 项 | `MIMOCODE_SERVER_PASSWORD` 未设置 (非功能问题) |