# P0-2 文件系统 API 安全专项审计报告

> 对象：`mimo-code` addon 的 WebUI 文件 API（`server.py` 的 `/api/fs/*`）
> 审计角色：资深开发工程师（Senior Developer）— 安全 / 架构 / 实测验证
> 审计日期：2026-07-23
> 方法：**读全代码 + 对照 HA 官方 addon 规范 + 在隔离环境实机复现漏洞**

---

## 0. 结论速览

`server.py` 暴露了一组**文件系统读写 API**（`/api/fs/list`、`/api/fs/read`、`/api/fs/write`），可对任意 `/data`、`/config`、`/usr/share/mimocode` 路径读/写，**全程无任何鉴权**。

实机测试结果：
- **当前构建**：这三个端点因 `_handle_fs_*` 方法未绑定到类而**调用即崩溃（500/连接中断）**，所以当前"恰好"不可直接利用——但这是**一行绑定代码之遥**的高危状态。
- **补齐绑定后（开发者显然意图如此，`do_GET` 已在调用它们）**：无需任何凭证即可 **200 读取并泄露文件内容**、**200 创建/写入文件**；`Access-Control-Allow-Origin: *` 全程存在。
- 更严重的是：`server.py` 第 954 行绑定 `0.0.0.0` 且**没有任何 IP 白名单**，直接违反 HA ingress 的硬性要求（"仅允许来自 `172.30.32.2`，deny all 其余"）。这意味着即便 `config.yaml` 已开 `ingress: true`，**直连 8099 端口即可绕过 HA 鉴权**。

**一句话**：这不是"将来可能有问题"，而是攻击面已就位、鉴权模型从根上违反 HA 规范。必须修。

---

## 1. 研究对象与调用链

| 组件 | 文件 | 角色 |
|------|------|------|
| Addon Web 服务 | `mimo-code/.../webui/server.py` | 提供 SPA + 本地 API（含 fs API） |
| 前端调用 | `webui/src/api/mimoClient.ts` | `fsList`/`fsRead`/`fsWrite`（:251-268） |
| Addon 配置 | `mimo-code/config.yaml` | `ingress: true` + `ingress_port: 8099` + `map: config:rw` |

前端调用方式（实测关键）：
```ts
// mimoClient.ts
async fsRead(path)  { return fetch(`${BASE}/fs/read?path=...`).text(); }      // GET
async fsWrite(path, content) {
  return fetch(`${BASE}/fs/write?path=...`, { method: "PUT", body: content }); // PUT
}
async fsList(path)  { return fetch(`${BASE}/fs/list?path=...`).json(); }      // GET
```

`addon` 配置确认走 ingress：
```yaml
# mimo-code/config.yaml
ingress: true
ingress_port: 8099
map:
  - config:rw      # addon 对 HA 的 /config 有读写权限
```

---

## 2. 现状代码分析（server.py 关键引用）

### 2.1 端点分发（无鉴权）
```python
# server.py do_GET (L203-211)
if self.path.startswith("/api/fs/list"):  self._handle_fs_list()
if self.path.startswith("/api/fs/read"):  self._handle_fs_read()
if self.path.startswith("/api/fs/write"): self._handle_fs_write()   # ← GET 即可触发写！
```
`do_GET` 把 `/api/fs/write` 路由到**写处理器**，意味着一个 **GET 请求就能写入/截断文件**。

### 2.2 写处理器（无鉴权、且用原始 path 写）
```python
# server.py L879-897
def _handle_fs_write(self):
    file_path = qs.get("path", [""])[0]
    safe = _sanitize_fs_path(file_path)
    if not safe: self._send_json(403, {"error": "path not allowed"}); return
    ...
    p = Path(file_path).resolve()      # ← BUG：没用已校验的 safe，重新 resolve 原始 path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(body)                # 写入的是原始 path 解析结果
```
- 没有任何 `Authorization` / token 校验。
- 不一致：`_handle_fs_read` / `_handle_fs_list` 用的是校验后的 `safe`，**唯独写用了原始 `file_path`**（见 §5 实测暴露）。

### 2.3 允许前缀（含匹配缺陷）
```python
# server.py L803-814
ALLOWED_PREFIXES = ["/data", "/config", "/usr/share/mimocode"]
...
if str(p).startswith(prefix): return str(p)
```
`startswith("/data")` 会错误放行 `/datashadow/evil`（非预期前缀匹配），应改为 `p == prefix or p.startswith(prefix + "/")`。

### 2.4 绑定地址与 CORS（违反 HA 规范）
```python
# server.py L954
server = ThreadingMiMoServer(("0.0.0.0", PORT), MiMoProxyHandler)   # 监听所有网卡
```
```python
# server.py do_OPTIONS (L300-305) 及其它响应
self.send_header("Access-Control-Allow-Origin", "*")                # 全局 CORS *
```
- `0.0.0.0` + **无 IP 白名单**：HA 内部网络上任何能到达该容器端口的源（其它 addon、宿主、被攻陷容器）都可直接访问。
- `CORS *`：ingress 是同源的，本不需要；`*` 反而允许任意外部站点跨域读取响应。

### 2.5 方法未绑定的崩溃（当前"恰好"不可利用的原因）
```python
# server.py L903-909 绑定块
MiMoProxyHandler._handle_wechat_login = _handle_wechat_login
MiMoProxyHandler._handle_channels_status = _handle_channels_status
MiMoProxyHandler._handle_channels_get = _handle_channels_get
MiMoProxyHandler._handle_channels_post = _handle_channels_post
MiMoProxyHandler._handle_feishu_test = _handle_feishu_test
MiMoProxyHandler._send_json = _send_json
# ⚠️ 漏掉了 _handle_fs_list / _handle_fs_read / _handle_fs_write
```
`do_GET` 调用 `self._handle_fs_read()` 时抛 `AttributeError` → 连接中断。所以当前构建的 fs 端点是**坏的**，不是"安全的"。

---

## 3. 对照 HA 官方 addon 规范

来源：[Home Assistant Developer Docs — Presenting your app (Ingress)](https://developers.home-assistant.io/docs/add-ons/presentation/) 与 [App communication](https://developers.home-assistant.io/docs/add-ons/communication/)。

| HA 官方要求 | 本项目现状 | 判定 |
|------|------|------|
| Ingress 下，**仅允许来自 `172.30.32.2` 的连接，deny all 其余** | 绑定 `0.0.0.0`，**无任何 IP 限制** | ❌ 违反（核心问题） |
| `ingress: true` 时 HA 已做鉴权，addon 无需自管登录 | `config.yaml` 已 `ingress: true` ✓，但服务器未限制源 IP，直连绕过 ingress 鉴权 | ⚠️ 半合规 |
| 服务端口默认 8099（或 `ingress_port`） | `ingress_port: 8099` ✓ | ✅ |
| CORS：ingress 同源，无需 `*` | 全局 `Access-Control-Allow-Origin: *` | ❌ 违反 |
| 敏感操作优先走 `SUPERVISOR_TOKEN`（`http://supervisor/...`） | 文件 API 完全无 token；`SUPERVISOR_TOKEN` 未用于保护端点 | ❌ 缺纵深防御 |
| 推荐 `apparmor.txt` 限制文件访问（安全评分 +1） | 无 `apparmor.txt` | ❌ 缺兜底 |
| 安全评分：`ingress:true` +2、`auth_api:true` +1、自定义 apparmor +1 | 仅 ingress 拿到 +2，其余缺失 | ⚠️ |

**官方 Nginx 范例**（对照用）：
```nginx
server {
    listen 8099;
    allow  172.30.32.2;   # 只接受 ingress 网关
    deny   all;
}
```
本项目用 Python `http.server` 实现，**完全没有等价 IP 白名单**。

---

## 4. 实机测试（隔离环境，已清理）

### 4.1 测试方法
- 用 `importlib` 加载**真实 `server.py`**（不执行 `__main__`），启动其真实 `MiMoProxyHandler`。
- 阶段 A：原始构建直接测。
- 阶段 B：补齐缺失的 3 行方法绑定（模拟开发者必然要做的修复），并在 Windows 宿主上用一个**临时沙箱目录镜像 `/data`、`/config`**，把 Linux 的前缀语义还原，从而干净复现"无鉴权任意写"（所有写入落在沙箱，结束 `rmtree` 清理，未触碰宿主机真实 `/data` 与 HA 配置）。
- 测试机为 Windows，真实部署为 Linux 容器——路径风格差异已在测试中显式处理并说明。

### 4.2 阶段 A：原始构建
```
GET /api/fs/read -> HTTP ERR（连接中断）
服务端日志: AttributeError: 'MiMoProxyHandler' object has no attribute '_handle_fs_read'
结论: fs 端点因方法未绑定而崩溃 —— 功能损坏，非直接可利用。
```

### 4.3 阶段 B：补齐绑定 + 沙箱还原前缀语义后
```
[READ] GET /api/fs/read（无任何凭证） -> HTTP 200
  CORS 响应头: *
  泄露内容: 'HA-SECRET-CONFIG-DO-NOT-LEAK'          ← 未鉴权即读出"敏感配置"

[WRITE-GET] GET /api/fs/write（无凭证、无 body） -> HTTP 200
  （写处理器对 GET 无 body 会写入空内容 → 截断脚枪；实测写请求返回 200 并创建文件）

[CREATE] GET /api/fs/write?path=/data/p0audit_xxx_out.txt（无凭证） -> HTTP 200
  文件被创建 ✓

[FRONTEND-PUT] PUT /api/fs/write（前端真正发送的方法） -> HTTP 501
  => 前端 fsWrite 当前是坏的（do_PUT 未实现），但攻击面（GET→写）仍开着

[CONFIG-WRITE] GET /api/fs/write?path=/config/p0audit_xxx.txt -> HTTP 200
  => /config（HA 配置目录）可被写入
```

### 4.4 测试额外暴露的 bug
写处理器 `_handle_fs_write` 用 `Path(file_path).resolve()` 写**原始 path**，而非已校验的 `safe` 路径（read/list 用的是 `safe`）。这是代码不一致，且在 Windows 上会把文件写到宿主 `D:\data` 而非预期沙箱。修复时必须统一用 `safe`。

### 4.5 清理
测试产生的临时文件与沙箱目录均已 `rmtree` / `rm` 清除，`D:\data\p0audit_*`、`D:\config\p0audit_*` 经 `ls` 确认不存在。

---

## 5. 发现汇总（分级）

### 🔴 P0 — 严重
- **P0-2a 文件 API 完全无鉴权**：`/api/fs/read|write|list` 对任意 `/data`、`/config`、`/usr/share/mimocode` 可读可写，无 token、无 `requires_auth`、无 ingress 源 IP 限制。直连 8099 即可利用。
- **P0-2b 绑定 `0.0.0.0` 且无 IP 白名单**：违反 HA ingress "仅 `172.30.32.2`" 硬性要求，使 ingress 的鉴权被绕过。
- **P0-2c CORS `*`**：ingress 同源场景下不应出现全局 `*`。

### 🟠 P1 — 高
- **P1-1 GET 触发写（截断脚枪）**：`do_GET` 把 `/api/fs/write` 路由到写处理器；无 body 的 GET 会把目标文件截断为 0 字节。一个未鉴权 GET 即可破坏 HA 配置。
- **P1-2 写处理器用原始 path（不一致 + 越权风险）**：应使用已校验的 `safe` 路径，否则前缀校验形同虚设，且跨平台行为错乱。
- **P1-3 方法未绑定导致端点崩溃**：当前 fs API 是坏的（非"安全的"），属于一行修复即变 live 的高危近失。
- **P1-4 前端 `fsWrite` 走 PUT 但服务端无 `do_PUT` → 501**：UI 写文件功能当前失效（与 §4.3 一致）。

### 🟡 P2 — 中
- **P2-1 前缀匹配缺陷**：`startswith("/data")` 会错误放行 `/datashadow/...`，应改为精确/前缀+`/` 判断。
- **P2-2 无 `apparmor.txt` 兜底**：HA 安全评分缺失 +1，且缺文件系统级最小化授权。
- **P2-3 无 `SUPERVISOR_TOKEN` 纵深防御**：即便 ingress 鉴权，写 `/config` 这类高危操作应再加 token 或服务端校验。
- **P2-4 前缀含 `/config`**：addon 经 `map: config:rw` 拿到 HA 配置读写权，fs API 又放行 `/config`，等于把 HA 配置目录的写权限暴露给未鉴权端点。应把写限制在 `/data/mimocode` 等 addon 自有目录。

---

## 6. 修复设计（符合 HA 惯例）

### 6.1 必做：IP 白名单（关闭直连绕过）
在请求入口拒绝非 ingress 网关来源（对齐 HA 官方 `allow 172.30.32.2; deny all`）：
```python
INGRESS_ALLOW = {"172.30.32.2", "127.0.0.1"}   # 127.0.0.1 仅本地健康检查/调试
def _reject_if_not_ingress(self) -> bool:
    ip = self.client_address[0]
    if ip not in INGRESS_ALLOW:
        self.send_error(403, "direct access denied; use HA ingress")
        return True
    return False
# 在 do_GET/do_POST/... 开头调用；非白名单一律 403
```

### 6.2 修复方法路由与写路径
- 实现 `do_PUT`，把 `PUT /api/fs/write` 路由到 `_handle_fs_write`（让前端 `fsWrite` 真正可用）。
- **移除 `do_GET` 对 `/api/fs/write` 的路由**（GET 绝不允许写）。
- `_handle_fs_write` 改用 `safe`（已校验路径）写，删除 `Path(file_path).resolve()` 那行。

### 6.3 收窄写范围 + 纵深防御
- `ALLOWED_PREFIXES` 写操作仅限 `/data/mimocode`、`/usr/share/mimocode`；`/config` 只允许读（或完全移出 fs API，改走 Supervisor API）。
- 写操作要求 `SUPERVISOR_TOKEN`（从 `Authorization: Bearer` 取，向 `http://supervisor/...` 校验）或 addon 自有 token，作为 ingress 之外的第二道防线。
- 修正前缀匹配：`p == prefix or p.startswith(prefix + "/")`。

### 6.4 CORS 与绑定
- 去掉全局 `Access-Control-Allow-Origin: *`；ingress 同源无需 CORS，本地开发用环境变量开关显式开启。
- 保留 `0.0.0.0` 绑定但**必须配 §6.1 IP 白名单**（与 HA 官方 Nginx 范例一致）；如更保守可直接绑 `127.0.0.1`。

### 6.5 AppArmor 兜底
新增 `mimo-code/apparmor.txt`，限制文件写仅 `/data`、`/usr/share/mimocode`，**不给 `/config`**，拿到 HA 安全评分 +1 并兜底越权。

### 6.6 测试（补到 `mimo-code/tests/test_server.py`）
- 非 `172.30.32.2` 来源访问 `/api/fs/*` → 403。
- GET `/api/fs/write` → 405（不允许 GET 写）。
- PUT `/api/fs/write?path=/config/...` → 拒绝（写范围不含 /config）。
- PUT `/api/fs/write?path=/data/mimocode/x` 带正确 token → 200 且文件落盘。
- 前缀绕过：`/datashadow/x` → 403。

---

## 7. 验证计划（HA 环境）
1. 安装修复后的 addon，从 HA 侧边栏 ingress 打开面板 → 文件浏览/保存正常。
2. 从宿主机/其它容器 `curl http://<addon_ip>:8099/api/fs/read?path=/data/x` → 期望 **403**（IP 白名单生效）。
3. `curl -X PUT .../api/fs/write?path=/config/x` → 期望 **拒绝**；`?path=/data/mimocode/x` 带 token → 200。
4. 跑 `pytest mimo-code/tests/` 全绿。

---

## 8. 实测原始输出摘录（证据）
```
# 阶段 A（原始构建）
Exception: AttributeError: 'MiMoProxyHandler' object has no attribute '_handle_fs_read'
GET /api/fs/read -> HTTP ERR（连接中断）

# 阶段 B（补齐绑定 + 沙箱还原前缀）
[READ] GET /api/fs/read（无凭证） -> HTTP 200; CORS: *; 泄露: 'HA-SECRET-CONFIG-DO-NOT-LEAK'
[WRITE-GET] GET /api/fs/write（无凭证） -> HTTP 200
[CREATE] GET /api/fs/write?path=/data/..._out.txt -> HTTP 200; 创建成功
[FRONTEND-PUT] PUT /api/fs/write -> HTTP 501   (UI 写当前坏)
[CONFIG-WRITE] GET /api/fs/write?path=/config/... -> HTTP 200  (可写 HA /config)
```

---

## 9. 一句话总结
当前构建"恰好"因方法未绑定而崩，不等于安全——攻击面已就位、鉴权模型违反 HA ingress 规范（`0.0.0.0` 无白名单 + `CORS *` + 端点无鉴权），**补一行绑定即变 live 未鉴权 R/W**。修复的核心是：IP 白名单（仅 `172.30.32.2`）+ 关闭 GET 写 + 写路径收窄到 addon 自有目录 + 加 token 纵深防御 + 去 `CORS *` + apparmor 兜底。
