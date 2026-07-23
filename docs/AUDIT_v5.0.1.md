# MiMo Auto v5.0.1 全面审计报告

## 一、审计范围

审计 `mimo-code/rootfs/usr/share/mimocode/webui/` 下全部 26 个文件，聚焦安全性、正确性、性能。

---

## 二、BUG（必须修复）

### BUG-1: `session_manager.py:175` — 引用 `_save()` 缺少 `self`

```python
async def clear_session(self, channel_key: str) -> None:
    await self._load()
    async with self._lock:
        self._data.pop(channel_key, None)
        await _save()  # ❌ 应为 self._save()
```

**影响**：`NameError` 崩溃，clear_session 功能完全不可用。

**修复**：
```python
await self._save()
```

### BUG-2: `session_manager.py:52` — 创建新 Lock 而非使用 self._lock

```python
async def _load(self) -> None:
    if self._loaded:
        return
    try:
        if os.path.exists(self._path):
            async with asyncio.Lock():  # ❌ 每次创建新 Lock，未与 _lock 共享
                loop = asyncio.get_event_loop()
                self._data = await loop.run_in_executor(None, self._read_file)
```

**影响**：并发加载时无锁保护，可能导致数据竞争。

**修复**：移除 `async with asyncio.Lock()`（`_loaded` flag 已防止重入），或使用 `self._lock`。

### BUG-3: `feishu_client.py:343-348` — 闭包变量捕获问题

```python
async def _reply_fn(reply_text: str, as_card: bool = False) -> str | None:
    try:
        return await self._reply(chat_type, chat_id, open_id, reply_text, as_card)
    except Exception as e:
        _LOGGER.error("飞书回复失败: %s", e)
        return None
```

`chat_type`、`chat_id`、`open_id` 是闭包变量，在 `while` 循环中每次迭代会被覆盖。如果消息处理速度慢于队列消费速度，`_reply_fn` 可能引用错误的变量值。

**修复**：使用默认参数绑定：
```python
async def _reply_fn(reply_text, as_card=False, _ct=chat_type, _cid=chat_id, _oid=open_id):
    return await self._reply(_ct, _cid, _oid, reply_text, as_card)
```

### ~~BUG-4: `ha_mcp_server.py` 文件缺失~~ — 误判，已移除

架构变更：Addon 不再自建 MCP，`ha_mcp_server.py` 是有意移除的。MCP 工具由 HA 侧的 `ha-mcp` Addon 提供。

---

## 三、安全问题

### SEC-1: 飞书代理端点无认证

`mimo_proxy.py` 中 `requires_auth = False`，任何网络可达的客户端都能调用 MiMo API。

**风险**：未授权访问 AI 服务。

**建议**：通过 ingress 代理，或添加 IP 白名单。

### SEC-2: `ssh_client.py` 中 `StrictHostKeyChecking=no`

降低 SSH 连接安全性，易受 MITM 攻击。

**建议**：使用 `known_hosts` 文件。

---

## 四、架构问题

### ARCH-1: 两套 Session 管理系统并存

| 文件 | 类 | 用途 |
|------|-----|------|
| `session_store.py` | `SessionStore` | 线程安全，debounce 写入 |
| `session_manager.py` | `MimoSessionManager` | async，409 重试 |

`channel_manager.py` 使用 `MimoSessionManager`，但 `SessionStore` 仍存在且被 `feishu_client.py` 的 `_load_sessions`/`_save_sessions` 引用（虽然方法体为空）。

**建议**：删除 `session_store.py`，统一使用 `MimoSessionManager`。

### ARCH-2: `BaseChannel` 类未被使用

`base_channel.py` 定义了 `BaseChannel` 基类，但 `FeishuClient` 和 `PersonalWeChatClient` 都没有继承它。这是未完成的重构。

**建议**：要么完成重构让所有通道继承 `BaseChannel`，要么删除 `BaseChannel`。

### ARCH-3: 版本号不一致

| 文件 | 版本 |
|------|------|
| `config.yaml` | 4.1.0 |
| `manifest.json` | 5.0.0 |
| 用户声明 | 5.0.1 |

**建议**：统一版本号。

---

## 五、性能问题

### PERF-1: `feishu_client.py` 多处创建新 aiohttp.ClientSession

以下位置每次调用都创建新 session：

| 行号 | 方法 | 问题 |
|------|------|------|
| 460 | `_reply()` | 获取 model name 时创建新 session |
| 525 | `_update_message()` | PATCH 消息时创建新 session |
| 546 | `_get_tenant_token()` | 获取 token 时创建新 session |

**影响**：频繁创建/销毁 TCP 连接，浪费资源。

**建议**：复用 `self._mimo_client._session` 或维护一个共享 session。

### PERF-2: `evolution_review.py` 每次 review 创建新 session

`schedule_review` 调用 `mimo_client.ensure_session("")` 创建独立 session，但这些 session 从未被清理。

**影响**：长期运行后 mimo serve 上积累大量废弃 session。

**建议**：review 完成后清理 session，或复用固定 session。

### PERF-3: `session_manager.py` 每次 `get_or_create_session` 都写磁盘

```python
async with self._lock:
    self._data[channel_key] = session_id
    await self._save()  # 每次都写磁盘
```

**建议**：添加 debounce 机制（参考 `session_store.py` 的实现）。

---

## 六、代码质量

### QUAL-1: `feishu_client.py:404` — 冗余导入

```python
from media import parse_reply_segments, TextSegment, ImageSegment, VoiceSegment, FileSegment, VideoSegment, GifSegment, CardSegment
from card import parse_card_source, build_feishu_card
```

这些已在文件顶部导入，此处重复导入。

### QUAL-2: `wechat_personal.py:515` — `_on_reasoning` 是同步函数但被 async 调用

```python
def _on_reasoning(text: str) -> None:  # 同步
    ...
    asyncio.create_task(self._send_reasoning_chunk(...))  # 在同步函数中调用
```

`_on_reasoning` 是同步回调，但内部使用 `asyncio.create_task`。如果调用者不在 event loop 中运行，会失败。

**实际上**：`_on_reasoning` 在 `_message_loop`（async）中被调用，所以 `asyncio.create_task` 可以工作。但函数签名应标注为 `Callable[[str], None]` 而非 `Awaitable`。

### QUAL-3: `ha_context.py:150` — 属性名拼写错误

```python
if " hvac_mode" in attrs or "mode" in attrs:
```

`" hvac_mode"` 前有一个空格，应该是 `"hvac_mode"`。

---

## 七、已修复问题确认

| 问题 | 状态 |
|------|------|
| feishu_client.py asyncio.run() 冲突 | ✅ 已修复（使用 channel_loop + run_coroutine_threadsafe）|
| channel_manager.py 同步文件 I/O | ✅ 已修复（run_in_executor）|
| ha_context.py aiohttp session 不复用 | ✅ 已修复（共享 session）|
| evolution_review.py 污染用户 session | ✅ 已修复（独立 session）|
| feishu_client.py 类级别共享状态 | ✅ 已修复（实例变量）|
| session_store.py 频繁写盘 | ✅ 已修复（debounce）|
| 微信轮询停止 | ✅ 已修复（asyncio.create_task）|

---

## 八、修复优先级

| 优先级 | 问题 | 文件 | 修复方案 |
|--------|------|------|----------|
| **P0** | BUG-1: `_save()` 缺少 self | session_manager.py:175 | `await self._save()` |
| **P1** | BUG-2: 双重 Lock | session_manager.py:52 | 移除新 Lock 或用 self._lock |
| **P1** | BUG-3: 闭包变量捕获 | feishu_client.py:343 | 默认参数绑定 |
| **P1** | PERF-1: aiohttp session 不复用 | feishu_client.py | 复用共享 session |
| **P2** | ARCH-1: 两套 Session 管理 | session_store.py | 删除或统一 |
| **P2** | ARCH-2: BaseChannel 未使用 | base_channel.py | 完成重构或删除 |
| **P2** | PERF-2: evolution session 泄漏 | evolution_review.py | review 后清理 |
| **P2** | PERF-3: session_manager 频繁写盘 | session_manager.py | 添加 debounce |
| **P3** | QUAL-1: 冗余导入 | feishu_client.py:404 | 删除重复导入 |
| **P3** | QUAL-3: 属性名空格 | ha_context.py:150 | 修复拼写 |
| **P3** | 版本号不一致 | config.yaml/manifest.json | 统一版本 |
