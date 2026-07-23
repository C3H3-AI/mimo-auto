# MiMo Code 全面审计报告（实跑代码）

> 审计对象：`D:\ai-hub\integrations\mimo_auto`
> 审计范围：Addon 后端（`mimo-code/rootfs/usr/share/mimocode/webui/*.py`）、HA 集成（`custom_components/mimo_auto/*.py`）、前端（仅构建产物 `webui/dist`、`assets`，无 TS 源码）
> 审计日期：2026-07-22
> 审计角色：主理人齐活林（交付总监）牵头，按 SOP 编排工程/安全/测试视角

---

## 0. 重要前提：旧审计报告已大幅过时

仓库里已有的 `docs/AUDIT_REPORT.md`（标注 2026-07-23）引用了以下文件，但**在当前仓库中均不存在**：

- `mimo_proxy.py`、`ssh_client.py`、`mcp_client.py`、`agent_impl.py`、`ha_mcp_server.py`、`get_token.py`

这说明旧报告是针对**更早/另一版架构**写的，而用户说"已完工运行"的当前版本已经过**薄桥接重构**：

- **Addon 自包含**：`mimo serve` 引擎 + 飞书/企业微信/个人微信通道 + React WebUI + 文件系统 API + 进化回顾，全部在容器内。
- **HA 集成只做薄桥接**：`conversation.py`（对话实体）、`coordinator.py`（健康探活）、`sensor.py`（状态传感器）、`config_flow.py`（填 Addon 地址）、`supervisor_client.py`（重启 Addon）。

**结论**：旧报告的 P0-1（明文取 HA 令牌）、P1-1（免鉴权代理视图）、P1-2/P1-3（SSH 跳过校验+注入）、P1-4（MCP id 错乱）、P2-1（密钥入 config）、P2-9/P2-10（工具数不符/硬编码 key）等，**已随架构重写而解决或不再适用**。本次是面向真实在跑代码的重新审计，旧报告仅作"已修复项"对照参考。

---

## 1. TL;DR（一句话结论）

- **最大安全短板未变**：Addon WebUI 的**文件写 API 仍无鉴权**，且允许写入 `/config`（HA 配置目录），绑 `0.0.0.0` + CORS `*`，一旦端口被直连即可未授权篡改 HA 配置 —— 与旧报告 P0-2 一致，**代码确认仍在**。
- **新发现 1 个真 bug（必修）**：HA 侧 `mimo_auto.chat` 服务因错误地用 `hass.data.get("entity_registry")` 取实体注册表，**必然失败**。
- **多处"静默失败"健壮性缺陷**：NDJSON 解析硬编码要求 `finish=="stop"`，否则整段回复被丢弃且不报错；连接异常也被吞掉只返回空串。
- **旧报告部分问题确实已修复**：`config_flow` 不再存密钥、无免鉴权代理、`coordinator`/`sensor` 干净、无 SSH 客户端、无 MCP id 问题。

> 总体判断：功能"能用且已上线"，但安全边界和错误可观测性仍偏弱，距离"可放心多用户/公网部署"有明显差距。

---

## 2. 发现清单（按严重程度分级）

### 🔴 P0 — 严重（发布前必须处理）

#### P0-A. Addon 文件写 API 无鉴权 + 可写 `/config` + 绑定 `0.0.0.0` + CORS `*`
**文件**：`mimo-code/rootfs/usr/share/mimocode/webui/server.py`
- `:1750` `ThreadingMiMoServer(("0.0.0.0", PORT), ...)` —— 监听所有网卡
- `:343,383,431,440,1481,1499,1582,1659` 所有响应带 `Access-Control-Allow-Origin: *`
- `:1593` `ALLOWED_PREFIXES = ["/data", "/config", "/usr/share/mimocode"]`
- `:1666` `_handle_fs_write()` 提供任意文件写入（PUT `/api/fs/write?path=...`）

**风险**：若 8099（或经 tcp_proxy 暴露的 14096 侧）被直连（未走 HA ingress），**任何人都无需凭证**即可向 `/config`（HA 配置目录）写文件，可静默篡改 `configuration.yaml` 或覆盖密钥文件。CORS `*` 又允许恶意网页跨域发起写入。这是当前最严重的攻击面。

**修复（必须做）**：
1. 文件系统写接口（`/api/fs/write/read/list`）置于鉴权之后：走 HA ingress（由 HA 鉴权）或加 token 校验（复用 `SUPERVISOR_TOKEN` / addon 自有密钥）。
2. 收紧 CORS 到同源 / ingress 来源，删掉全局 `*`。
3. 默认只绑 `127.0.0.1`；对外暴露走 HA ingress 代理。
4. `ALLOWED_PREFIXES` 收窄（如仅 `/data/mimocode`），并防符号链接逃逸（当前 `Path.resolve()` 已跟进符号链接，这点是对的，但仍建议收窄前缀）。

---

### 🟠 P1 — 高（本迭代内）

#### P1-A（新增）. `mimo_auto.chat` 服务必然失败（实体注册表误用）
**文件**：`custom_components/mimo_auto/__init__.py:189`
```python
for entity in hass.data.get("entity_registry", {}).values():
    if entity.platform == DOMAIN and isinstance(entity, ha_conversation.ConversationEntity):
        response = await entity.async_process(conversation_input)
        ...
raise HomeAssistantError("未找到 MiMo Auto 对话实体")
```
- `hass.data` 上**没有**可用的 `"entity_registry"` 键；正确做法是 `from homeassistant.helpers import entity_registry as er; reg = er.async_get(hass); reg.entities.values()`。
- `hass.data.get("entity_registry", {})` 几乎必然返回 `{}` → for 循环不执行 → 抛"未找到 MiMo Auto 对话实体"。自动化里调用 `mimo_auto.chat` 服务会**全部失败**。
- **修复**：改用 `er.async_get(hass).entities` 遍历，按 `platform == DOMAIN` 过滤；或直接用 `hass.services` 已注册的 conversation agent 调用。

#### P1-B. Conversation Agent 卸载/重载后未反注册
**文件**：`custom_components/mimo_auto/conversation.py:308` `async_added_to_hass` 调用 `ha_conversation.async_set_agent(...)`，但**无**对应的 `async_will_remove_from_hass` / `async_unset_agent`。
- 重载集成后旧 agent 引用可能残留，指向已销毁的 coordinator，出现"僵尸 agent"或崩溃。
- **修复**：补 `async_will_remove_from_hass` 调用 `ha_conversation.async_unset_agent(self.hass, self._config_entry)`。

#### P1-C. NDJSON 解析硬编码 `finish=="stop"`，否则整段回复丢失
**文件**：`client.py:67` 与 `conversation.py:296`
```python
if info.get("role") != "assistant" or info.get("finish") != "stop":
    continue
```
- 只有 `finish == "stop"` 的消息才被收集文本。**若 mimo serve 返回 `finish:"length"`（截断）或中间分包不带 `finish`、或 schema 微调**，全部文本被静默跳过 → 用户收到**空回复**，且无任何错误日志。
- 这是整个产品的"单点脆断"：只要流式末包格式与预期不符，通道侧（`channel_manager` → `send_message` 拿空串）和 HA 侧（`_parse_json_stream` 返回 `{"text": None}` → "MiMo 返回了空响应"）都会表现为"答非所问/无响应"。
- **修复**：放宽过滤——`role=="assistant"` 即收集 `parts` 文本；对 `finish` 仅用于"是否结束流式"，不作为丢弃条件。并补充单测覆盖截断/多对象拼接场景。

#### P1-D. 流式连接异常被静默吞掉
**文件**：`client.py:234-235`
```python
except (aiohttp.ClientError, asyncio.TimeoutError) as err:
    _LOGGER.error("send_message_stream failed for session %s: %s", session_id, err)
    # 生成器直接结束，调用方拿不到任何事件，也收不到异常
```
- `send_message_stream` 是 `AsyncIterator`，异常在生成器内被 `except` 吞掉，**不向调用方抛出**。`channel_manager._call_mimo_serve` 拿到空 `collected_text` → 返回 `""`，用户看到"没反应"，排查极难。
- **修复**：在生成器内 `raise`，或在结束时若 `collected_text` 为空且发生过错误则抛出 `MimoAPIError`，让上层走 409/404 重试或报错分支。

#### P1-E. 并发写 `mimo.json` 无锁，凭证/状态可能丢更新
**文件**：`channel_manager.py:232` `_persist_sync_buf`
```python
loop.run_in_executor(None, self._read_config_file)   # 读
... 修改 ...
loop.run_in_executor(None, self._write_config_file, cfg)  # 写
```
- 个人微信每次长轮询都会触发 `_persist_sync_buf`（保存 `get_updates_buf`）。多账户/高频时多个 executor 任务做 read-modify-write，**无文件锁**，后写覆盖前写，可能丢凭证或丢其他通道配置。
- **修复**：用 `asyncio.Lock()`（或线程锁）串行化配置文件读写；或改为只更新单字段的原子写（临时文件 + `os.replace`）。

---

### 🟡 P2 — 中（下个迭代）

#### P2-A. `async_timeout` 已弃用
**文件**：`conversation.py:16` `from async_timeout import timeout`
- HA 生态已用 `asyncio.timeout` 取代，未来移除。
- **修复**：`from asyncio import timeout`（或直接 `asyncio.timeout(...)`）。

#### P2-B. 会话映射同步读异步存储，永远为空
**文件**：`conversation.py:107-115` `_load_session_map`
```python
self._store = Store(self._hass, 1, "mimo_auto_session_map")
if self._store and hasattr(self._store, 'data') and self._store.data:
    self._session_map = self._store.data.get("session_map", {})
```
- `Store.data` 仅在 `await store.async_load()` 之后才有值；`__init__` 是同步的，`async_load()` 从未被调用 → `self._store.data` 永远是 `None` → **重启后 HA 对话会话全部丢失**。
- **修复**：在 `async_added_to_hass` 里 `await self._store.async_load()` 后再读取；或将加载并入 `async_setup_entry`。

#### P2-C. 每次请求新建 `aiohttp.ClientSession`
**文件**：`conversation.py:245,260` `_create_session` / `_send_message` 内 `async with aiohttp.ClientSession() as session:`
- 每轮对话都新建并关闭 session，无连接复用，高并发下易触达文件描述符上限。
- **修复**：用 `homeassistant.helpers.aiohttp_client.async_get_clientsession(hass)` 复用 HA 托管会话。

#### P2-D. `asyncio.get_event_loop()` 弃用
**文件**：`channel_manager.py:166` `channel_loop=asyncio.get_event_loop()`
- 在无线程循环的上下文（或某些 Python 版本）会抛 `DeprecationWarning` / `RuntimeError`。当前因 `_start_feishu` 在运行中的 loop 内被调用而侥幸工作，属脆弱写法。
- **修复**：传 `asyncio.get_running_loop()` 或让 `FeishuClient` 自行 `get_running_loop()`。

#### P2-E. subprocess fallback 重启 `mimo` 进程
**文件**：`server.py:454-494` `_handle_chat_via_mimo`
- 仅在 `urllib URLError`（mimo serve 不可达）时 fallback 到 `subprocess.run(["/usr/local/bin/mimo", "run", ...])`。
- 用 list 形式传参，**无 shell 注入风险**（这点比旧架构好）。但每次调用起一个独立 `mimo run` 进程、cwd=`/data/mimocode`、无并发控制；若触发频繁会雪崩。
- **修复**：尽量不依赖 fallback；若保留，加进程级锁与并发上限，并明确这是"降级路径"而非常态。

---

### 🟢 P3 — 低（顺手清理 / 可观测性）

- **前端 TS 源码缺失**：`webui/src` 在仓库中无 `.ts/.tsx`（仅 `dist`/`assets` 构建产物）。旧报告 P1-5"前后端流式解析契约不一致"**无法在本仓库核对**，建议把前端源码纳入仓库以便审计与统一契约。
- **测试覆盖偏窄**：`webui/tests/` 仅 `test_client.py`（client 解析/会话）、`test_feishu_structure.py`（结构）。`server.py`（含 fs 写/无鉴权）、`channel_manager.py`、`conversation.py` 均**无单测**。建议补 fs 越权用例（验证 P0-A 修复）、HA 对话空响应用例。
- **宽泛 `except` 吞异常**：`server.py` 多处 `except Exception: pass`、`_load_session_map` 裸吞；关键路径应 `warning` 并保留痕迹，便于线上排障。
- **调试日志残留**：`server.py` 等仍有 `sys.stderr.write("[MiMo WebUI] ...")` 风格日志，建议统一 `logging` 并分级。

---

## 3. 与旧报告对照（已解决 / 仍存在 / 新增）

| 旧报告条目 | 状态 | 说明 |
|-----------|------|------|
| P0-1 明文取 HA 令牌 (`get_token.py`) | ✅ 已解决/不适用 | 文件已不存在，密钥改由 Addon options + 持久卷管理 |
| P0-2 Addon 文件写 API 无鉴权 + `/config` | 🔴 **仍存在** | 代码确认（`server.py:1593,1666,1750`） |
| P1-1 代理视图 `requires_auth=False` | ✅ 已解决 | 无代理视图，仅 conversation 实体 |
| P1-2/3 SSH 跳过校验+注入 | ✅ 已解决 | `ssh_client.py` 已不存在 |
| P1-4 MCP client id 错乱 | ✅ 已解决 | `mcp_client.py` 已不存在 |
| P1-5 前后端流式不一致 | ⚠️ 待核 | 前端源码不在仓库，无法核对 |
| P1-6 Agent 卸载未反注册 | 🟠 **仍存在** | `conversation.py` 无 `will_remove`（P1-B） |
| P2-1 密钥入 config.data | ✅ 已解决 | `config_flow.py` 仅存 server_url/webui_url |
| P2-3 `async_timeout` 弃用 | 🟠 **仍存在** | `conversation.py:16`（P2-A） |
| P2-4 会话映射同步读异步存储 | 🟠 **仍存在** | `conversation.py:107`（P2-B） |
| P2-9/10 工具数不符/硬编码 key | ✅ 已解决 | 相关文件已不存在 |
| — | 🟠 **新增** | `mimo_auto.chat` 服务实体注册表误用（P1-A） |
| — | 🟠 **新增** | NDJSON `finish=="stop"` 硬过滤丢回复（P1-C） |
| — | 🟠 **新增** | 流式异常静默吞（P1-D） |
| — | 🟠 **新增** | `mimo.json` 并发写无锁（P1-E） |

---

## 4. 优先级修复路线图

| 顺序 | 事项 | 级别 | 预计 |
|------|------|------|------|
| 1 | P0-A：fs 写加鉴权 + CORS 收窄 + 绑 127.0.0.1 + 收窄前缀 | P0 | 1d |
| 2 | P1-A：修复 `mimo_auto.chat` 实体注册表读取 | P1 | 0.5d |
| 3 | P1-C/P1-D：放宽 NDJSON 过滤 + 流式异常上抛 | P1 | 1d |
| 4 | P1-B：Agent 卸载反注册 | P1 | 0.5d |
| 5 | P1-E：配置文件读写加锁 | P1 | 0.5d |
| 6 | P2-A/B/C/D：弃用 API、会话加载、会话复用、get_running_loop | P2 | 1d |
| 7 | 补测试（fs 越权、对话空响应、client 解析）+ 前端源码入仓 | P3 | 1d |

> 总计约 5–6 人日，可把项目从"能用"拉到"可信赖"。**P0-A 与 P1-A 建议本周内先止血**（前者是安全隐患，后者是功能硬 bug）。

---

## 5. 架构总体评价

**亮点**：
- 薄桥接重构方向正确：HA 集成极简，Addon 自包含，职责清晰，部署解耦。
- 跨线程/跨循环调度用了正确模式（`run_coroutine_threadsafe` 把通道消息调度进主循环 + 共享 `MimoAIClient`，移除了旧设计里脆弱的 `MimoClientSync`）。
- subprocess fallback 用 list 形式，无注入风险。
- `supervisor_client.py` 用标准 `http://supervisor` + Bearer token，符合 HA 规范。
- `coordinator.py`/`sensor.py` 干净，任务取消处理正确。

**风险点**：
1. **安全模型整体仍偏弱**：唯一实质性鉴权依赖 HA ingress；Addon 自身 API（含 fs 写）零鉴权。
2. **静默失败文化**：NDJSON 解析、流式异常、会话加载失败都被"吞掉"，线上出问题极难定位。
3. **可观测性不足**：大量 `except: pass` + stderr 风格日志。
4. **可审计性受限**：前端仅构建产物、测试覆盖窄。

**一句话总结**：重构让代码"变轻变对"了（旧报告大半问题已消失），但**安全边界和错误处理仍是两块最大短板**——先把 P0-A（文件写鉴权）和 P1-A（chat 服务 bug）堵上，质量会实打实上一个台阶。

---

## 6. 修复状态更新（2026-07-23 部署轮次）

> 2026-07-23 晚完成一轮修复并 SCP 上线、干净启动。以下为各发现的最新状态（已在仓库代码中逐一核实，非空报）。

| 发现 | 状态 | 说明 |
|------|------|------|
| **P0-A** 文件写 API 无鉴权 + `/config` + `0.0.0.0` + CORS `*` | 🔴 **未修** | 最高优先级安全债，本轮未包含 |
| **P1-A** `mimo_auto.chat` 服务必失败 | 🔴 **未修** | `custom_components/mimo_auto/__init__.py:189` 仍误用 `hass.data.get("entity_registry")` |
| **P1-B** Agent 卸载未反注册 | 🟠 **未修** | `conversation.py:308` 仍无 `async_unset_agent` |
| **P1-C** NDJSON `finish=="stop"` 硬过滤丢回复 | ✅ **已修（已验证）** | `client.py:67` 改为仅按 role 收集；微信正常回复验证通过 |
| **P1-D** 流式异常静默吞 | ✅ **已修** | `client.py:237` 改为 `raise` 重新抛出 |
| **P1-E** `mimo.json` 并发写无锁 | 🟠 **未修** | `channel_manager.py` 写配置仍无锁 |
| **BUG-1** `session_manager.py:175` `await _save()` 漏 self | ✅ **已修** | 现为 `await self._save()` |
| **BUG-2** `session_manager.py:52` 新建 Lock 不用 | ✅ **已修** | 统一用 `self._lock` 保护全部读写 |
| **409** 重试用同一 session 致 self-busy | ✅ **已修（已验证）** | `channel_manager.py` 每通道串行锁 + 3 次退避重试 + 每次 `force_new`；最坏降级友好提示 |
| **tcp_proxy** task 追踪致 coroutine 冲突 | ✅ **已修** | 改为 `asyncio.gather` 双向 relay，无 tasks 列表残留 |
| **新增** 操作确认管理器 | ✅ **新增能力** | `action_confirm.py`：拦截 tool-call → 等用户确认 → 执行 HA 服务 |
| **新增** 飞书交互卡片确认 | ✅ **新增能力** | `feishu_client.py:105 send_confirmation`（确认/取消按钮） |
| **P2-A~E** 弃用 API / 会话加载 / 复用 / get_running_loop | 🟡 **未修** | 下个迭代 |
| **版本号不一致** | 🟡 **未修** | `config.yaml` 4.1.0 / `manifest.json` 5.0.0（ARCH-3，待统一） |

### 验证情况
- ✅ **核心验证通过**：微信消息正常回复（mimo serve 返回正常文本），证明 P1-C 修复生效。
- ⏳ **待验证**：微信发一条**长回复**测试消息，确认截断场景（`finish:"length"`）下也能正常返回（P1-C 完整性验证）。

### 剩余高优先级（建议尽快单独处理）
1. 🔴 **P0-A 安全**：文件写接口无鉴权、可写 `/config`、绑 `0.0.0.0` —— 最高优先级安全债。
2. 🔴 **P1-A 功能硬崩**：`mimo_auto.chat` 服务必失败，自动化调用全挂。
3. 🟠 **P1-B**：Agent 卸载未反注册，留僵尸 agent。
4. 🟡 版本号三处不一致，待统一。
