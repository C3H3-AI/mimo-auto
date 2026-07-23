# MiMo Auto — 优化方案

## 一、P0 问题修复

### 1.1 微信轮询停止

**现象**：部署后微信心跳日志消失，`_message_loop` 未执行。

**根因分析**：
- `PersonalWeChatClient.start()` 使用 `asyncio.create_task(self._message_loop())`
- 这依赖调用 `start()` 时所在的 event loop 持续运行
- 如果 `channel_manager.start()` 在一个短暂的 context 中执行，loop 可能被回收

**修复方案**：
```python
# 方案 A：worker 线程拥有独立 event loop（推荐）
async def start(self) -> None:
    self._running = True
    self._status = "connected"
    # 不用 create_task，让 _message_loop 在调用者的 loop 中运行
    # 或者用 threading + 独立 loop
    self._loop_task = asyncio.ensure_future(self._message_loop())

# 方案 B：改用 threading + 独立 loop
def start(self) -> None:
    self._running = True
    self._thread = threading.Thread(target=self._run_loop, daemon=True)
    self._thread.start()

def _run_loop(self):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(self._message_loop())
```

**验证**：检查 `docker logs` 中出现 `WeChat polling .../getupdates` 日志。

### 1.2 容器重启丢代码

**现象**：Addon 容器重启后，overlay 层重置，Python 源码丢失。

**修复方案**：
1. **Dockerfile 中添加 volume mount**：
   ```dockerfile
   VOLUME ["/usr/share/mimocode/webui"]
   ```

2. **或在 s6 init 脚本中复制到 /data/**：
   ```bash
   #!/bin/with-contenv bashio
   # 如果 /data/mimocode/webui/ 不存在，从 /usr/share 复制
   if [ ! -d /data/mimocode/webui ]; then
       cp -r /usr/share/mimocode/webui /data/mimocode/webui
   fi
   export PYTHONPATH=/data/mimocode/webui:$PYTHONPATH
   ```

3. **推荐方案**：在 `config.yaml` 中添加 bind mount：
   ```yaml
   map:
     - config:rw
     - data:rw
   ```

## 二、P1 问题修复

### 2.1 channel_manager.py 同步文件 I/O

**已完成** ✅：`_on_state_change` 改为 `run_in_executor` 异步写入。

### 2.2 aiohttp session 不复用

**已完成** ✅：`ha_context.py` 和 `ha_mcp_server.py` 复用模块级共享 session。

### 2.3 evolution_review 污染用户 session

**已完成** ✅：每次 review 创建独立 session。

## 三、P2 问题修复

### 3.1 feishu_client.py 类级别共享状态

**已完成** ✅：`_seen_message_ids`、`_model_name`、`_session_store` 改为实例变量。

### 3.2 session_store.py 频繁写盘

**已完成** ✅：添加 2 秒 debounce 写入。

### 3.3 MCP 工具不完整（已废弃 — 改用外部 ha-mcp）

**现状**：`ha_mcp_server.py` 只有 8 个静态工具。

**⚠️ 本问题已废弃**。我们已有完整的 **ha-mcp 集成** 部署在 HA 服务器上：

- 外部 URL：`https://api.homediy.top:8443/api/webhook/mcp_97521c4cb653c43b9c9448410d0745d5`
- 内部 URL：`http://192.168.3.3:8123/api/webhook/mcp_97521c4cb653c43b9c9448410d0745d5`
- 提供 83+ 工具的全量 MCP 访问

**新方案**：
1. 在 `options.json` 中配置 `ha_mcp_url` 指向外部 ha-mcp 集成
2. 删除 `ha_mcp_server.py` 和 `ha-mcp` s6 服务
3. 删除 `ha-mcp` 在 `s6-rc.d` 中的依赖定义

**注意**：`mimocode/run` 启动脚本第 49-51 行已支持外部 `ha_mcp_url` 配置。
如果配置了 `ha_mcp_url` 则使用外部 URL，否则回退到内置的 `http://127.0.0.1:8234`。
所以只需配置即可生效，无需修改启动脚本。

### 3.4 Config Flow 冗余步骤

**现状**：`config_flow.py` 有 4 步（user → feishu → wechat → personal_wechat），但 channel 配置在 custom_components 中从未使用。

**优化方案**：
- 删除 feishu/wechat/personal_wechat 步骤
- Config Flow 只保留端口和二进制路径配置
- Channel 配置完全由 `mimo.json` 管理（通过 Addon UI 或手动编辑）

## 四、P3 改进建议

### 4.1 媒体发送代码重复

**现状**：`feishu_client.py` 和 `wechat_personal.py` 中的媒体发送逻辑高度相似。

**优化方案**：抽取 `media_sender.py`：
```python
class MediaSender:
    """统一的媒体发送器。"""
    
    async def resolve_and_upload(self, source: str, channel: str) -> bytes | str:
        """解析媒体源 → 压缩 → 上传到对应 CDN。"""
        data, filename = await resolve_media_source(source)
        if channel == "feishu":
            return await upload_feishu(data, filename)
        elif channel == "wechat":
            return await upload_wechat_cdn(data, filename)
```

### 4.2 类型注解一致性

**现状**：回调类型用 `Any`。

**优化方案**：
```python
from typing import Callable, Awaitable

MessageCallback = Callable[[dict, Callable | None], Awaitable[str]]
ReasoningCallback = Callable[[str], Awaitable[None]]
```

### 4.3 飞书 client.py asyncio.run() 冲突

**已完成** ✅：worker 线程拥有独立 event loop。

### 4.4 日志增强

**建议**：在关键路径添加结构化日志：
```python
_LOGGER.info(
    "message_processed",
    extra={
        "channel": "feishu",
        "user_id": open_id,
        "latency_ms": (end - start) * 1000,
        "token_count": len(response),
    }
)
```

## 五、架构优化

### 5.1 Addon 自包含化

**目标**：Addon 容器完全自包含，不依赖 custom_components。

**方案**：
- 移除 `custom_components/mimo_auto` 中的 agent_impl.py
- Addon 通过 Ingress 提供完整 WebUI
- HA 对话助手通过 REST API 直接调用 Addon

### 5.2 多账号支持

**目标**：支持多个飞书/微信账号同时在线。

**现状**：`channel_manager.py` 已支持（dict 存储多个 client）。

**待完善**：
- WebUI 前端多账号管理界面
- 每个账号独立的 session store
- 账号级别的 persona 配置

### 5.3 消息队列解耦

**目标**：channel_manager 与 mimo serve 解耦。

**方案**：
```
飞书/微信 → Redis Stream → channel_manager → mimo serve
                              ↓
                        消费者组（多 worker 并发处理）
```

**优点**：
- 支持消息持久化（重启不丢）
- 支持多 worker 并发处理
- 支持消息优先级

### 5.4 监控与告警

**目标**：添加 Prometheus metrics + 告警。

**指标**：
- `mimo_messages_total` — 消息总数（按 channel 分）
- `mimo_message_latency_seconds` — 消息处理延迟
- `mimo_session_count` — 活跃 session 数
- `mimo_error_total` — 错误总数（按 type 分）
- `mimo_rate_limit_total` — 限流次数

## 六、优先级排序

| 优先级 | 任务 | 预估工时 | 状态 |
|--------|------|----------|------|
| **P0** | 微信轮询修复 | 2h | 待修复 |
| **P0** | 容器重启丢代码 | 1h | 待修复 |
| **P1** | 同步 I/O 修复 | 1h | ✅ 已完成 |
| **P1** | aiohttp session 复用 | 1h | ✅ 已完成 |
| **P1** | evolution session 隔离 | 1h | ✅ 已完成 |
| **P2** | 类级别状态修复 | 1h | ✅ 已完成 |
| **P2** | session_store debounce | 1h | ✅ 已完成 |
| **P2** | MCP 工具动态发现 | 4h | 待开发 |
| **P2** | Config Flow 简化 | 2h | 待开发 |
| **P3** | 媒体发送抽取 | 3h | 待开发 |
| **P3** | 类型注解增强 | 1h | 待开发 |
| **架构** | 多账号前端 | 8h | 待开发 |
| **架构** | 消息队列解耦 | 16h | 待规划 |

## 七、下一步行动

### 立即（今天）
1. 修复微信轮询停止问题
2. 修复容器重启丢代码问题

### 本周
3. 完成 MCP 工具动态发现
4. 简化 Config Flow
5. 抽取 media_sender.py

### 本月
6. 多账号前端支持
7. 监控告警系统
8. 消息队列解耦评估
