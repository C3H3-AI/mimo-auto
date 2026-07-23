# MiMo Auto — 项目文档

## 一、项目概述

将 MiMo Code（小米 AI 编程助手）改造为 Home Assistant 智能家居管家，通过飞书/微信多通道控制智能家居。

## 二、架构

### 2.1 整体架构

```
┌─────────────────────────────────────────────────────────────┐
│                    HA Supervisor Addon 容器                   │
│                                                              │
│  ┌─────────────────┐  ┌─────────────────┐  ┌──────────────┐ │
│  │  mimocode       │  │  ha-mcp         │  │  webui       │ │
│  │  (AI 引擎)      │  │  (MCP 工具)     │  │  (WebUI+通道)│ │
│  │  port 14095     │  │  port 8234      │  │  port 8099   │ │
│  └────────┬────────┘  └────────┬────────┘  └──────┬───────┘ │
│           │ MCP remote         │                   │         │
│           └────────────────────┘                   │         │
│                                                    │         │
│  ┌──────────────────────────────────────────────────┐         │
│  │  channel_manager.py → MimoAIClient → mimo serve  │         │
│  │  ├── feishu_client.py (飞书 WS)                   │         │
│  │  ├── wechat_client.py (企业微信)                   │         │
│  │  └── wechat_personal.py (个人微信)                 │         │
│  └──────────────────────────────────────────────────┘         │
└──────────────────────────────────────────────────────────────┘
```

### 2.2 组件说明

| 组件 | 类型 | 端口 | 功能 |
|------|------|------|------|
| **mimocode** | AI 引擎 | 14095 | MiMo Code serve，核心 AI 推理 |
| **ha-mcp** | MCP 工具服务器 | 8234 | 暴露 HA 设备控制工具给 AI |
| **webui** | WebUI + 通道 | 8099 | 聊天界面 + 飞书/微信通道管理 |
| **tcp_proxy** | TCP 代理 | 14096 | 将 14096 转发到 14095（解决 localhost 限制）|

### 2.3 通信链路

```
用户消息 → 飞书/微信 → channel_manager → MimoAIClient → mimo serve (14095)
                                                          ↓
                                                    ha-mcp (8234) ←→ HA REST API
```

## 三、文件结构

```
mimo-auto/
├── custom_components/mimo_auto/     ← HA 自定义组件（集成层）
│   ├── __init__.py                  组件入口
│   ├── agent_impl.py               对话代理（会话复用）
│   ├── coordinator.py              服务检测 + Add-on 通道
│   ├── conversation.py             对话实体 (Claw 兼容)
│   ├── config_flow.py              UI 配置流程
│   ├── const.py                    常量
│   ├── mimo_proxy.py               API 代理
│   ├── manifest.json               组件声明
│   └── services.yaml               服务定义
│
├── mimo-code/                       ← Add-on 包
│   ├── config.yaml                 Add-on 配置
│   ├── Dockerfile                  多阶段构建
│   └── rootfs/
│       ├── etc/s6-overlay/s6-rc.d/  s6 服务定义
│       │   ├── mimocode/           mimo serve 服务
│       │   ├── mimocode-webui/     Web UI 服务
│       │   └── ha-mcp/            MCP 工具服务
│       └── usr/share/mimocode/webui/
│           ├── server.py           WebUI + API 代理
│           ├── tcp_proxy.py        TCP 端口转发
│           ├── ha_mcp_server.py    MCP 工具服务器
│           ├── client.py           MimoAIClient + NDJSON 解析
│           ├── channel_manager.py  统一消息路由 + 409 重试
│           ├── feishu_client.py    飞书 WS + 媒体发送 + 卡片
│           ├── wechat_client.py    企业微信
│           ├── wechat_personal.py  个人微信 + 异步轮询
│           ├── session_store.py    SessionStore 会话持久化
│           ├── base_channel.py     通道抽象 + system prompt
│           ├── persona.py          人格配置（灵犀）
│           ├── ha_context.py       HA 设备上下文注入
│           ├── evolution_review.py 进化回顾（自升级）
│           ├── media.py            富媒体标签解析
│           ├── media_utils.py      CDN 上传/下载
│           ├── card.py             飞书交互卡片
│           ├── tts.py              Edge TTS 语音合成
│           ├── ha_services.py      HA 服务调用
│           └── ha_entities.py      HA 实体定义
│
├── webui/                           ← Web UI (Vite + React SPA)
│   ├── src/                        React 源码
│   └── dist/                       构建产物
│
└── hacs.json                        HACS 配置
```

## 四、核心模块详解

### 4.1 channel_manager.py — 统一消息路由

**职责**：管理所有 IM 通道（飞书/企微/个人微信），将消息路由到 mimo serve。

**关键流程**：
```
消息进入 → _handle_message() → _call_mimo_serve()
    ↓
1. 从 SessionStore 恢复 session_id
2. 通过 MimoAIClient.ensure_session() 验证/创建 session
3. 构建 system prompt（persona + HA 设备上下文 + 进化经验）
4. 发送消息，支持 409/404 重试（session 忙时自动创建新 session）
5. 解析响应，调度进化回顾
```

**409 重试机制**：
```python
try:
    await self._mimo_client.send_message(...)
except MimoAPIError as send_err:
    if send_err.status in (409, 404):
        # Session 忙或不存在，创建新 session 重试
        session_id = await self._mimo_client.ensure_session("")
        await self._mimo_client.send_message(...)
```

### 4.2 client.py — MimoAIClient

**职责**：与 mimo serve 通信的异步 HTTP 客户端。

**核心方法**：
- `ensure_session(session_id)` — 验证 session 存在，不存在则创建
- `send_message(text, session_id, system=)` — 发送消息，返回响应
- `send_message_stream(...)` — 流式发送，yield NDJSON 事件

**NDJSON 解析**：
```python
def parse_ndjson_chunk(buffer, ...) -> tuple[list[dict], str]:
    """解析 NDJSON 缓冲区，返回 (提取的对象, 剩余缓冲区)"""
```

### 4.3 feishu_client.py — 飞书通道

**架构**：双线程模型
- **WS 线程**：接收飞书 WebSocket 事件 → 推入队列（非阻塞）
- **Worker 线程**：从队列拉取 → 调用 AI → 通过 API 回复

**关键特性**：
- 飞书消息去重（`_seen_message_ids` OrderedDict，上限 512）
- 实时 reasoning 推送（PATCH 更新消息，实现打字效果）
- 富媒体支持（图片/视频/文件/卡片）
- 断线重连（最多 8 次，5 秒间隔）

### 4.4 wechat_personal.py — 个人微信通道

**协议**：腾讯 iLink Bot API

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

### 4.5 session_store.py — 会话持久化

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

### 4.6 ha_context.py — HA 设备上下文注入

**职责**：从 HA REST API 获取设备状态，注入到 system prompt。

**缓存策略**：30 秒 TTL，双检锁（asyncio.Lock）

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

### 4.7 evolution_review.py — 进化回顾

**职责**：每次对话后，后台分析交互模式，提取可复用经验。

**流程**：
1. 对话结束 → `schedule_review()` 检查是否值得回顾
2. 创建独立 session（不污染用户 session）
3. 发送分析 prompt → AI 返回 lessons JSON
4. 持久化到 `/data/mimocode/lessons.json`
5. 下次对话时注入到 system prompt

### 4.8 persona.py — 人格配置

**默认人格**：
```json
{
  "name": "灵犀",
  "role": "Home Assistant 管家",
  "tone": "友好、简洁",
  "language": "中文",
  "owner": "主人"
}
```

**存储**：`/data/mimocode/persona.json`

### 4.9 media.py — 富媒体解析

**支持的标签**：
- `[IMAGE:source]` — 图片
- `[VOICE:text]` — 语音
- `[FILE:source]` — 文件
- `[VIDEO:source]` — 视频
- `[GIF:source]` — GIF
- `[CARD:json]` — 飞书卡片

### 4.10 ha_mcp_server.py — MCP 工具服务器

**协议**：MCP (Model Context Protocol) over HTTP

**工具列表**：
| 工具 | 功能 |
|------|------|
| `ha_turn_on` | 开启设备 |
| `ha_turn_off` | 关闭设备 |
| `ha_toggle` | 切换设备状态 |
| `ha_set_brightness` | 设置亮度 |
| `ha_set_temperature` | 设置温度 |
| `ha_get_state` | 查询设备状态 |
| `ha_get_all_lights` | 获取所有灯光 |
| `ha_call_service` | 调用任意 HA 服务 |

## 五、配置文件

### 5.1 mimo.json（Addon 配置）

```json
{
  "model": "mimo/mimo-auto",
  "channels": {
    "feishu": {
      "enabled": true,
      "app_id": "...",
      "app_secret": "..."
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

### 5.2 环境变量

| 变量 | 说明 |
|------|------|
| `SUPERVISOR_TOKEN` | HA Supervisor API Token |
| `HASSIO_TOKEN` | HA Supervisor Token（备用）|
| `HA_MCP_PORT` | MCP 服务端口（默认 8234）|
| `MIMOCODE_SERVER_PASSWORD` | WebUI 密码 |

## 六、部署

### 6.1 部署命令

```powershell
# 部署 Python 文件
$files = @("feishu_client.py","channel_manager.py","ha_context.py",
           "ha_mcp_server.py","session_store.py","evolution_review.py")
$src = "D:\ai-hub\integrations\mimo_auto\mimo-code\rootfs\usr\share\mimocode\webui"
foreach ($f in $files) {
    $content = Get-Content "$src\$f" -Raw -Encoding UTF8
    $content = $content -replace "`r`n", "`n"
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($content)
    $base64 = [Convert]::ToBase64String($bytes)
    echo $base64 | ssh -4 -i ~/.ssh/id_ha root@api.homediy.top `
        "docker exec -i addon_local_mimo-code sh -c 'base64 -d > /usr/share/mimocode/webui/$f'"
}

# 重启容器
ssh -4 -i ~/.ssh/id_ha root@api.homediy.top "docker restart addon_local_mimo-code"
```

### 6.2 验证

```bash
# 检查进程
docker exec addon_local_mimo-code ps aux | grep -E 'python|mimo'

# 检查日志
docker logs addon_local_mimo-code --tail 30

# 检查健康
docker exec addon_local_mimo-code curl -s http://127.0.0.1:8234/health
docker exec addon_local_mimo-code curl -s http://127.0.0.1:14095/session
```

## 七、已完成功能

| 功能 | 状态 |
|------|------|
| 统一消息路由（所有通道走 channel_manager）| ✅ |
| Session 持久化（JSON 文件 + debounce）| ✅ |
| 409/404 重试（session 忙时自动创建新 session）| ✅ |
| 人格注入（persona → system prompt）| ✅ |
| HA 设备上下文注入（30s 缓存）| ✅ |
| 进化回顾（后台学习，独立 session）| ✅ |
| 飞书富媒体（图片/视频/文件/卡片）| ✅ |
| 微信 CDN 上传 | ✅ |
| Edge TTS 语音合成 | ✅ |
| 限流检测 | ✅ |
| ha-mcp MCP 连接 | ✅ |
| aiohttp session 复用 | ✅ |
| worker 线程 event loop 独立 | ✅ |

## 八、待修复问题

| # | 问题 | 优先级 | 说明 |
|---|------|--------|------|
| 1 | 微信轮询停止 | P0 | 部署后微信心跳日志消失，`_message_loop` 可能未执行 |
| 2 | 容器重启丢代码 | P1 | Python 源码在 overlay 层，重启后丢失 |
| 3 | MCP 工具不完整 | P2 | 只有 8 个静态工具，对比 ha-mcp 的 83 个差很远 |
