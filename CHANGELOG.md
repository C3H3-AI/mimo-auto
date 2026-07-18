# Changelog

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