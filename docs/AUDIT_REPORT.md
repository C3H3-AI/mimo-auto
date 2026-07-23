# MiMo Auto 代码审计报告

> 审计对象：`D:\ai-hub\integrations\mimo_auto`
> 审计范围：HA 集成（`custom_components/mimo_auto`）、Docker Addon 后端（`mimo-code`）、React/TS 前端（`webui`）、部署脚本与 CI
> 审计角色：资深开发工程师（Senior Developer）— 代码质量把控 / 安全 / 架构评审
> 审计日期：2026-07-23

---

## 0. 执行摘要

这是一个**功能完整、架构有想法**的项目（三层混合：MCP + REST + SSH/Supervisor，外加 Web 面板与多通道接入）。团队已经做过一轮清理（v4.0.0 删了 375 行死代码），基础工程素养是有的。

但审计发现 **多处会直接导致安全事故或线上故障的缺陷**，主要集中在「安全」和「并发/资源」两个维度。按严重程度分级：

- **P0 严重（2 项）**：明文长期令牌提取、Addon 文件写 API 无鉴权暴露
- **P1 高（6 项）**：代理视图 `requires_auth=False`、SSH 关闭主机密钥校验、SSH 命令注入、硬编码 JSON-RPC id 并发错乱、前端流式解析与后端不一致、HA Agent 卸载未清理
- **P2 中（10+ 项）**：密钥存明文 config、封装被破坏（`_` 私有成员跨类访问）、`async_timeout` 弃用、调试日志残留、空壳代码、版本漂移等
- **P3 低**：`console.log` 残留、CI 打包带入 `__pycache__`、构建脚本内联巨型 node 命令等

**结论**：当前代码「能用」，但离「可放心交给团队维护、可在公网/多用户环境部署」还有明显差距。后文给出分级修复清单 + 团队能力拉齐建议。

---

## 1. 严重程度定义

| 级别 | 含义 | 处理时限 |
|------|------|----------|
| **P0** | 安全/数据泄露，可被利用造成当面损失 | 立即（发布前必须修） |
| **P1** | 高概率线上故障或明确安全弱点 | 本迭代内 |
| **P2** | 质量/可维护性/健壮性隐患 | 下个迭代 |
| **P3** | 代码卫生、可优化项 | 顺手清理 |

---

## 2. 关键发现

### 🔴 P0 — 严重

#### P0-1. `get_token.py` 直接读取 HA `.storage` 并提取长期 API 令牌
**文件**：`get_token.py:5-13`
```python
with open("/config/.storage/core.config_entries") as f:
    ...
    token = e.get("data", {}).get("access_token", "")
```
- 直接解析 HA 内部存储文件，绕过 HA 安全模型。
- 提取的是 `http` 集成里的 **`access_token`**——通常是拥有 HA 完整控制权的高权限长期令牌。
- 明文 `print` 到 stdout，极易被日志/终端捕获。
- **风险**：该令牌一旦泄露 = 别人完全接管你的 HA 实例。
- **修复**：
  1. 立即作废此方式。改用 HA 官方「长期访问令牌」（Long-Lived Access Token，用户在 HA 个人资料页创建），通过配置项安全注入。
  2. 若需 Supervisor 通信，用容器内的 `SUPERVISOR_TOKEN` 环境变量（HA addon 自动注入），**不要**从 `.storage` 抠。
  3. 若此脚本仅为调试用途，删除它，并在文档中明确「禁止把 HA 令牌写入文件或打印」。

#### P0-2. Addon WebUI 文件写 API 无鉴权 + 绑定 `0.0.0.0` + CORS `*`
**文件**：`mimo-code/.../webui/server.py`
- `:954` 监听 `("0.0.0.0", PORT)` —— 监听所有网卡。
- `:302` 所有响应带 `Access-Control-Allow-Origin: *`。
- `:879` `_handle_fs_write` 提供 **任意文件写入**，允许路径前缀仅 `/data`、`/config`、`/usr/share/mimocode`（`:806`）。

**组合风险**：任何人只要能访问该端口（HA addon 常被映射/暴露），**无需任何凭证**即可通过浏览器跨域写入 `/config`（HA 配置目录）或 `/data`。配合 CORS `*`，一个恶意网页即可静默篡改 HA 配置。**这是最严重的攻击面。**

- **修复（必须做）**：
  1. 文件系统写接口（`/api/fs/write`、`/api/fs/read`、`/api/fs/list`）必须置于鉴权之后。Addon 内应通过 Supervisor ingress 访问，由 HA 负责鉴权；若直连，至少为这些端点加 token 校验（复用 `SUPERVISOR_TOKEN` 或 addon 自有密钥）。
  2. 收紧 CORS：不要全局 `*`，仅允许同源 / ingress 来源。
  3. 默认只绑定 `127.0.0.1`；对外暴露应通过 HA ingress 代理，而非直接 `0.0.0.0`。
  4. 文件系统 API 的 `ALLOWED_PREFIXES` 应进一步收窄（如仅限 `/data/mimocode`），并防范符号链接逃逸。

---

### 🟠 P1 — 高

#### P1-1. HA 集成代理视图 `requires_auth = False`
**文件**：`custom_components/mimo_auto/mimo_proxy.py:40,63`
```python
requires_auth = False  # iframe panels cannot pass HA tokens
```
- `MiMoCreateSessionView` / `MiMoSendMessageView` 完全免鉴权。注释理由是「iframe 无法带 HA token」——但这是一个**错误的取舍**：这意味着任何能访问 HA 地址的人都能免登录向 AI 发消息、创建会话。若 AI 具备设备控制能力（本项目正是），等于**未授权设备控制**。
- **修复**：通过 HA ingress 暴露面板（ingress 自动带鉴权），并将 `requires_auth = True`。前端经 ingress 访问时 HA 会注入鉴权头，无需手动传 token。若暂时无法改前端，至少加一层共享密钥 / IP 限制，并在文档中标红风险。

#### P1-2. SSH 客户端关闭主机密钥校验（MITM 风险）
**文件**：`custom_components/mimo_auto/ssh_client.py:80`；`deploy.sh:18,32`；`deploy_addon.sh:14-33`
```python
"-o", "StrictHostKeyChecking=no",
```
- 全局关闭主机指纹校验，攻击者可中间人劫持 SSH，进而执行任意命令。本项目多处（集成、两个部署脚本）都用这个写法。
- **修复**：改用 `StrictHostKeyChecking=accept-new`（首次接受、之后校验），或预置 `known_hosts`。

#### P1-3. SSH 命令拼接存在注入风险
**文件**：`custom_components/mimo_auto/ssh_client.py:150-153`
```python
cmd = "backups new"
if name:
    cmd += f' --name "{name}"'
return await self.execute_ha_command(cmd)
```
- `name` 被直接拼进 shell 命令字符串，经远程 shell 解释。若 `name` 含 `"`、`;`、`$()` 等，可注入命令。虽当前 `name` 来自内部调用，但一旦接自动化/用户输入即成漏洞。
- **修复**：`execute_command` 应改为「命令 + 参数列表」避免 shell 解释；或对所有插值做严格白名单/转义。不要在命令字符串里直接插值用户数据。

#### P1-4. MCP 客户端 JSON-RPC `id` 硬编码，并发调用会错乱
**文件**：`custom_components/mimo_auto/mcp_client.py:81,119,175`
```python
"id": 1,   # tools/list
"id": 2,   # tools/call
"id": 0,   # initialize
```
- 所有请求复用固定 `id`。若并发发起多个 `tools/call`，响应 `id` 都是 2，无法区分归属（虽然当前是单连接串行，但属于脆弱设计，且 `call_tool` 共用同一 session 时并发会出错）。
- **修复**：用自增/UUID 生成 `id`，并在响应里按 `id` 匹配请求（维护 `pending` 映射）。

#### P1-5. 前端流式解析假设与后端不一致
**文件**：`webui/src/api/mimoClient.ts:176` vs `custom_components/mimo_auto/agent_impl.py:667-762`
- 前端对 `/session/{id}/message` 的最终解析是单次 `JSON.parse(fullText)`（`:176`）。
- 后端代理视图对 NDJSON 是逐 chunk 转发（server.py:337-352）；而集成侧 `_parse_json_stream` 用的是 **多个拼接 JSON 对象** 的 `raw_decode` 循环。
- **风险**：若 mimo 返回多个 JSON 对象拼接，前端最终 `JSON.parse` 会抛错、丢失整段回复。`tryExtractProgress` 里 `JSON.parse(buf)` 中途失败被静默吞掉，用户可能看到半截内容或无响应。
- **修复**：前后端统一一个流式协议契约（要么单对象 chunked，要么标准 NDJSON 行分隔），并在前端用与后端一致的增量解析；至少对最终解析失败做兜底（回退到已累积文本）。

#### P1-6. Conversation Agent 卸载时未反注册
**文件**：`custom_components/mimo_auto/conversation.py` + `__init__.py`
- `async_added_to_hass` 调用 `ha_conversation.async_set_agent(...)`，但卸载/重载路径没有对应的 `async_unset_agent` / 清理逻辑（仅 `async_unload_entry` 移除了 coordinator 和服务）。
- **风险**：重载集成后，旧 agent 引用可能残留，导致对话指向已销毁的 coordinator，出现「僵尸 agent」或崩溃。
- **修复**：在 `async_remove_entry` 或 entity `async_will_remove_from_hass` 中调用 `ha_conversation.async_unset_agent(hass, entry)`。

---

### 🟡 P2 — 中（质量 / 架构 / 健壮性）

#### P2-1. 密钥明文存于 config entry `data`
**文件**：`config_flow.py:53-78`（feishu_app_secret / wechat_secret / token / encoding_aes_key 全部进 `data`）
- HA 的 `config.entry.data` 以明文存于 `.storage/core.config_entries`。这些属于高敏感密钥。
- **修复**：HA 2024.7+ 支持 config entry `data` 中 `encrypted` 字段加密存储；或单独用 `entry.runtime_data` 持有、不落盘明文；若未来实现 `async_get_config_entry_diagnostics`，必须显式 redact 这些 key（项目已有的 `_SECRET_KEYS` 掩码思路可复用）。

#### P2-2. 封装被破坏：传感器跨类访问 `_` 私有成员
**文件**：`sensor.py:76,79,110,111,142,144`
```python
attrs["external_mode"] = self._coordinator._external_mode
attrs["pid"] = self._coordinator._process.pid
"url": self._coordinator.mcp_client._url,
```
- 直接读别的对象的私有属性。一旦内部实现改动，传感器静默出错。
- **修复**：在被访问类上暴露只读 `property`（如 `coordinator.external_mode`、`mcp_client.url`）。

#### P2-3. `async_timeout` 已弃用
**文件**：`agent_impl.py:11` `from async_timeout import timeout`
- `async_timeout` 在 HA 生态中已被 `asyncio.timeout` 取代，未来会移除。
- **修复**：`from asyncio import timeout as async_timeout`（或直接 `asyncio.timeout`）。

#### P2-4. 会话映射加载不可靠
**文件**：`agent_impl.py:99-108` `_load_session_map`
```python
if self._store and hasattr(self._store, 'data') and self._store.data:
    self._session_map = self._store.data.get("session_map", {})
```
- `Store.data` 仅在 `await store.async_load()` 之后才有值；`__init__` 是同步的，此时 `data` 通常为 `None` → **重启后会话映射永远为空**，历史会话丢失。
- **修复**：在 `async_setup_entry` 里 `await store.async_load()` 后再初始化 agent，或把加载改为 `async` 启动步骤。

#### P2-5. 设备状态排序逻辑其实是坏的
**文件**：`agent_impl.py:236-238`
```python
important = [s for s in device_states if any(s.startswith(p) for p in ["- ", "light.", ...])]
others   = [s for s in device_states if not any(s.startswith(p) for p in ["- ", ...])]
```
- 所有行都以 `"- "` 开头，所以 **全部** 落入 `important`，`others` 永远为空。「重要设备优先」的意图完全没生效。
- **修复**：按实体 domain 前缀（去掉 `"- "` 前缀后取 `entity_id.split(".")[0]`）做分类。

#### P2-6. Coordinator 残留调试日志
**文件**：`coordinator.py:344-348, 355-357`
```python
_LOGGER.warning("ADDON_DBG: addons_type=%s keys=%s", ...)
```
- `ADDON_DBG` 调试日志残留在生产代码。虽然级别是 warning，但属于排查遗留，会污染运行日志。
- **修复**：删除或降级为 `debug`，并清理 `get_addons_info` 的探测式日志。

#### P2-7. `SimpleNamespace` 伪装进程对象
**文件**：`coordinator.py:260-261`
```python
self._process = SimpleNamespace(returncode=None, pid="external")
```
- 用 `SimpleNamespace` 顶替 `asyncio.subprocess.Process` 是一种脆弱 hack，后续多处 `getattr(self._process, "pid", None)` 与之耦合，易出 `AttributeError`。
- **修复**：引入明确的 `mode` 字段（`"subprocess" | "external"`），进程句柄用 `Optional[Process]` 表达，避免类型伪装。

#### P2-8. config_flow 空壳步骤 + 死代码
**文件**：`config_flow.py:157-162`
```python
async def async_step_channels(self, ...):
    return await self.async_step_feishu()
```
- `async_step_channels` 只转发，无实际 UI/逻辑，属冗余间接层。
- **修复**：去掉该步骤，让 `async_step_user` 直接进 `async_step_feishu`（或保留但加注释说明编排意图）。

#### P2-9. MCP server 工具数与文档/声明严重不符
- `CHANGELOG.md:7` 与 `mcp_client.py:4` 宣称「83 工具」；实际 `ha_mcp_server.py:77-168` 只实现 **8 个**工具。
- 二者对不上，会误导使用者和后续维护者。
- **修复**：要么补齐工具集，要么把文档/注释改为真实数量（8）。

#### P2-10. 测试脚本中硬编码 API Key
**文件**：`test_mimo_api.py`
```python
api_key = "tp-sxthz0z7108xos912hecuflqh69ft2o1hn4hozx35p9usaxe"
```
- 明文凭证入库（即便在根目录 test 文件、不被 HACS zip 打包，仍在 git 历史里）。若仓库公开即泄露。
- **修复**：立即作废并轮换该 key；改从环境变量读取（`os.environ["MIMO_API_KEY"]`）；用 `git filter-repo` 清除历史中的明文。

#### P2-11. 版本漂移（CHANGELOG 与实际代码不符）
- `CHANGELOG.md` v2.0.1 称 `MAX_RESTART_ATTEMPTS` 调为 10，但 `const.py:23` 实际是 `3`。
- `EXPERIENCE.md` 称「entity.py 保留但标记死代码」，而 `CHANGELOG.md` v4.0.0 称「删除 entity.py」。
- **修复**：以代码为准，修订 CHANGELOG / 经验文档，建立「改代码必同步文档」的纪律。

#### P2-12. MCP server 每请求新建 aiohttp session
**文件**：`ha_mcp_server.py:46,60,71`
- `call_ha_service` / `get_entity_state` / `get_all_states` 每次调用都 `aiohttp.ClientSession()`，无连接复用、无超时统一控制。
- **修复**：模块级复用单个 `ClientSession`（注意事件循环绑定），并加统一 timeout。

---

### 🟢 P3 — 低（代码卫生）

- `webui/src/api/mimoClient.ts:27` 与 `App.tsx:7` 残留 `console.log`（应移除或接日志开关）。
- `package.json:8` 构建脚本是一整段内联 `node -e`，极难维护 —— 抽成单独 `scripts/sync-dist.mjs`。
- `build-addon.yml` 的 `zip -r` 会把 `custom_components/mimo_auto/__pycache__` 打进发布包（非安全但污染、非确定）。应在 `.gitignore` 忽略并在打包前 `find -name __pycache__ -delete`。
- `ha_mcp_server.py:32` 裸 `except:`（捕获 `KeyboardInterrupt`/`SystemExit`）——改为 `except Exception`。
- `sensor.py:20` 导入 `CoordinatorEntity` 但未使用（死导入）。
- `agent_impl.py` 多处 `try/except` 仅 `_LOGGER.debug` 吞掉异常，生产排障困难；关键路径应 `warning`。
- `config_flow.py:81` `_validate_config` 标 `async` 但内部全同步；`async_step_user` 里 `await` 它无必要。
- `_handle_wechat_login`（`server.py`）每次请求 `asyncio.new_event_loop()` + 跨线程 `run_until_complete`，并共享 `_login_states` 无锁 —— 并发登录有竞态。

---

## 3. 架构评估

**亮点**：
- 三层混合架构（MCP 主通道 + REST 备通道 + SSH/Supervisor 运维通道）思路清晰，职责分离合理。
- 进程生命周期有看门狗 + 自动重启 + 外部模式识别，考虑到了 Docker host 网络场景。
- 前端用 Zustand + MUI，结构分层（api / store / components / hooks），工程组织不混乱。
- Markdown 渲染正确使用了 `DOMPurify` 后再 `dangerouslySetInnerHTML`，**XSS 防护做对了**（这是很多团队会翻车的地方）。

**风险点**：
1. **安全模型整体薄弱**：多处「为方便放弃鉴权」（代理视图、Addon 文件 API、SSH 跳过校验）。这是当前最该补课的方向。
2. **同步/异步边界混乱**：`server.py` 用 stdlib 多线程 `http.server` + 跨线程 asyncio loop，脆弱且难测试；`_load_session_map` 在同步上下文读异步存储。
3. **错误处理偏「吞异常」**：大量 `except Exception: _LOGGER.debug`，线上出问题难定位。
4. **文档与代码漂移**：工具数、版本号、文件存废状态对不上，说明缺乏「文档即代码」的同步纪律。

---

## 4. 测试与质量保障现状

- `mimo-code/tests/test_server.py` 存在（pytest 缓存也在），但根目录 `test_*.py` 是**一次性手测脚本**（连真实 API key），非可回归测试。
- 集成侧（`custom_components/mimo_auto`）**无测试目录、无 conftest、无 pytest 配置**。
- CI（`build-addon.yml`）只做「打包 + 构建镜像 + 发 Release」，**没有 lint / type-check / 单元测试门禁**。
- **结论**：当前是「能跑就行」，没有任何防止回归的网。团队要提升质量，第一步就是把测试与 lint 卡进 CI。

---

## 5. 团队技术能力提升建议（重点）

用户明确提出要「提升团队技术水平」。结合本次审计，给出可落地的建设清单：

### 5.1 建立「安全审查清单」（最高优先级）
把本次 P0/P1 抽象成团队 PR 必检项，每次提交自查：
- [ ] 任何新 HTTP 端点是否默认免鉴权？（默认否，确需开放须说明理由 + 加限制）
- [ ] 是否直接读取/打印密钥、token、`.storage`？（禁止）
- [ ] 外部输入是否拼进 shell / SQL / 命令？（禁止字符串拼接，用参数化）
- [ ] 是否关闭了 TLS/主机密钥校验？（禁止 `StrictHostKeyChecking=no`）
- [ ] 是否监听 `0.0.0.0` 并带 `CORS *`？（默认仅 `127.0.0.1`，CORS 收窄）

### 5.2 卡死 CI 门禁
在 `build-addon.yml` 或新增 workflow 加入：
- Python：`ruff`（lint）+ `mypy --strict` + `pytest`（集成侧至少补 smoke test）。
- TS：`tsc --noEmit` + `eslint` + `vitest`。
- 任何 lint/type/test 失败 → 阻断合并。
- 加 `detect-secrets` 或 `gitleaks` 步骤，防止密钥再次入库。

### 5.3 补测试，从「冒烟」开始
- 集成：补 `test_config_flow.py`（流程能走完）、`test_coordinator.py`（启停/崩溃重启）、`test_mcp_client.py`（mock HTTP 验证 id 匹配）。
- 前端：对 `mimoClient.sendMessageStream` 做解析单测（覆盖多对象拼接场景，直接堵 P1-5）。
- Addon：现有 `test_server.py` 扩展，覆盖 `/api/fs/*` 越权访问用例（验证 P0-2 修复有效）。

### 5.4 代码评审文化
- 强制 PR 评审（至少 1 人），评审重点看「边界条件 + 错误处理 + 安全」。
- 引入「审计即文档」：像本项目 EXPERIENCE.md 那样，每次大修后写一段「根因 + 决策」，沉淀为团队知识库（本项目已做，值得保持并规范化）。
- 每周 30 分钟「踩坑复盘」，把本次 P1-3 这类注入、P1-4 这类并发错乱作为反面教材讲一遍。

### 5.5 依赖与版本纪律
- `CHANGELOG` / `const` / `manifest` 版本号三处必须一致（用户级记忆里已有发布检查清单，建议团队复用）。
- 废弃 API 红线：`async_timeout` → `asyncio.timeout`、Python 版本上限等，CI 用 `ruff` 规则 `RUF` 系列自动抓。

---

## 6. 优先级修复路线图

| 顺序 | 事项 | 级别 | 预计工作量 |
|------|------|------|-----------|
| 1 | P0-1 废除 `get_token.py` 明文取令牌，改用 LLAT / SUPERVISOR_TOKEN | P0 | 0.5d |
| 2 | P0-2 Addon 文件 API 加鉴权 + CORS 收窄 + 绑定 127.0.0.1 | P0 | 1d |
| 3 | P1-1 代理视图改 `requires_auth=True` + 走 ingress | P1 | 0.5d |
| 4 | P1-2/P1-3 SSH 改 `accept-new` + 参数化命令，消除注入 | P1 | 0.5d |
| 5 | P1-4 MCP client 动态 id + 响应匹配 | P1 | 0.5d |
| 6 | P1-5 前后端统一流式解析契约 + 前端兜底 | P1 | 1d |
| 7 | P1-6 Agent 卸载反注册 | P1 | 0.5d |
| 8 | P2-10 轮换并清除硬编码 API key（含 git 历史） | P2 | 0.5d |
| 9 | P2-1/P2-2/P2-3/P2-4 密钥存储、封装、弃用 API、会话加载 | P2 | 1.5d |
| 10 | CI 加 lint+type+test 门禁 + gitleaks | P2 | 1d |
| 11 | 清理 P2-5~P2-12、P3 卫生项 | P3 | 1d |

> 总计约 9–10 人日可把项目从「能用」拉到「可信赖、可维护」。P0 两项建议**本周内**先止血。

---

## 7. 一句话总结

团队工程基础不差，但**安全意识是最大短板**——多处为「方便」牺牲了鉴权与校验。先把 P0 两个口子堵上，再把「安全清单 + CI 门禁 + 评审文化」三件套立起来，团队技术水平会实打实地上一个台阶。
