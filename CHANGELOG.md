# Changelog

## v5.0.1 (2026-07-23)

### 修复

- **集成重载失败** — `async_unload_entry` 正确调用 `async_unload_platforms`，解决 HA 重启/重载后实体不注册的问题
- **语音助手空响应** — `_send_message` 请求体从 `{"message": ...}` 改为 `{"parts": [{"type": "text", "text": ...}]}`，匹配 Addon API 格式
- **health check task 阻塞关闭** — coordinator.stop 在 unload 时正确取消 health check 任务

### 改进

- **传感器和对话实体显示** — 添加 DeviceInfo，关联到设备，在集成卡片中直接可见
- **支持重新配置** — 添加 `async_step_reconfigure`，可在 UI 中修改 server_url 等设置
- **补充 translations** — 添加 reconfigure 步骤的翻译（en/zh-Hans）

## v5.0.0 (2026-07-23)

### 🏗 架构重构 — Addon 桥接模式

- **删除了子进程管理** — 集成不再管理 `mimo serve`，Addon 容器完全独立运行（-400 行代码）
- **删除了冗余模块** — 移除 `mcp_client.py`、`ssh_client.py`、`mimo_proxy.py`、`agent_impl.py`（-4 文件）
- **合并对话逻辑** — `agent_impl.py` 核心功能合并到 `conversation.py`，删除 Claw Assistant 依赖
- **简化传感器** — 从 4 个传感器精简为 1 个 Addon 状态传感器（connected/detected/disconnected）
- **简化配置流程** — 从 4 步（端口 + 飞书 + 企微 + 个人微信）精简为 1 步（server_url + webui_url）

### ⚠️ 不兼容变更

- 最低 HA 版本要求不变（2025.1+）
- 旧配置条目自动迁移（V1→V2），手动安装用户需重新配置
- 不再支持本地 `mimo serve` 子进程模式 — 必须配合 Addon 使用

### 🔧 其他

- `DEFAULT_SERVER_URL` 默认端口从 `14095` 改为 `14096`（匹配 Addon tcp_proxy）
- Config flow 新增 `use_supervisor` 开关
- 删除旧版 translations（飞书/企微/个人微信配置）
- 版本号 `5.0.0`

---

## v4.0.0 (2026-07-17)

### ✨ 新功能

- **三层混合架构** — MCP 作为主要设备控制层（83工具），REST API 作为备用，SSH/Supervisor 处理系统运维
- **MCP 客户端** — 新增 `mcp_client.py`，支持连接外部 HA MCP Addon
- **SSH 客户端** — 新增 `ssh_client.py`，支持系统级操作（更新、备份、主机管理）
- **Supervisor 客户端** — 新增 `supervisor_client.py`，支持 Addon 管理和系统操作
- **状态传感器** — 新增 `sensor.py`，暴露服务器状态、MCP/SSH/Supervisor 连接状态
- **MiMo Code 升级** — 更新到 `@mimo-ai/cli@0.1.6`

### 🔧 优化

- **代码清理** — 删除 ~375 行死代码，agent_impl.py 从 1152 行降至 777 行
- **删除 entity.py** — 移除从未使用的死代码文件
- **修复硬编码路径** — 用 `%APPDATA%` 动态路径替代硬编码的 `C:\Users\duola\...`
- **修复 OS 检测** — 用 `sys.platform` 替代 `config_dir.startswith(("C:", "D:"))`
- **修复双重注册** — 删除冗余的 `async_set_agent()` 调用

### 📦 其他

- 架构文档更新
- 新增 MCP/SSH/Supervisor 配置选项

## v2.1.1 (2026-07-03)

### 🔧 修复

- **Add-on 网络隔离** — `host_network` 改为 `true`，集成可通过 `127.0.0.1` 连接 add-on 内的 `mimo serve`
- **Add-on 可配置端口** — WebUI server.py 从环境变量读取 `MIMO_PORT`，响应 add-on 配置的端口
- **版本对齐** — add-on config.yaml 版本与 manifest.json 同步为 2.1.1

## v2.1.0 (2026-07-02)

### ✨ 新增

- **HA Add-on 支持** — 新增 `mimo-code/` 目录，完整的 HA Add-on 实现（Dockerfile、config.yaml、build.yaml、s6-overlay 服务管理）
- **多架构构建** — add-on 支持 `aarch64` / `amd64` / `armv7` 三种架构
- **Add-on 自动检测** — 集成 `start_server()` 增加 Step 0：通过 Supervisor API 自动检测 add-on 是否运行，HA OS/Supervised 用户开箱即用
- **s6-overlay 进程管理** — add-on 使用 s6-overlay 管理 `mimo serve` 生命周期，崩溃自动重启
- **看门狗** — config.yaml 配置 `watchdog` 端点，Supervisor 自动健康检查
- **CI 自动化** — GitHub Actions 自动构建多架构 Docker 镜像并创建 GitHub Release

### 🔧 优化

- **Docker 多阶段构建** — Node.js 镜像安装 npm 包 → HA 基础镜像仅复制原生二进制，无需 Node.js 运行时，镜像体积更小

## v2.0.1 (2026-07-02)

### 🔧 修复

- **健康检查永久放弃问题** — 外部服务器模式下，健康检查失败超过阈值后不再永久放弃，重置计数器并持续轮询，适配 Docker host 网络场景
- **健康检查停止顺序** — `_stop_process` 现在总是先停止健康检查任务，防止资源泄露
- **CPU 占用过高** — 杀掉两个从 7月1日起持续运行、各占 95% CPU 的 `mimo --prompt hi` 进程

### 📦 其他

- `HEALTH_CHECK_INTERVAL_SECONDS` 从 30 秒调整为 120 秒
- `MAX_RESTART_ATTEMPTS` 从 3 次调整为 10 次

## v2.0.0 (2026-07-02)

### ✨ 新功能

- **MiMo Chat 侧边栏面板** — 集成添加后自动出现在侧边栏，浏览器内直接与 MiMo 对话
- **Claw Assistant 兼容** — 注册为 `conversation.mimo_auto` 实体，支持被 Claw Assistant 等智能体发现和使用
- **API 代理** — 通过 HA HTTPS 代理 MiMo 服务器 HTTP 请求，解决浏览器混合内容限制
- **语音助手兼容** — 修复 `supported_languages` 返回值类型，语音助手下拉菜单可正常选择

### 🔧 优化

- 移除 `MATCH_ALL` 使用，改为标准字符串返回
- `IntentResponse` 使用 `language` 替代 `hass` 构造，修复 500 错误
- 健康检查任务在关闭时正确处理，不再阻塞 HA 关闭

### 📦 其他

- codeowners 更新为 `@C3H3-AI`
- 添加 `iot_class`、`documentation`、`issue_tracker` 到 manifest.json
- 添加 HACS 标准发布配置（hacs.json）
- 更新 README 文档

## v1.0.0 (2026-06-28)

- 初始发布
- 基础对话代理功能
- `mimo serve` 进程管理
- 配置流程（端口、二进制路径）