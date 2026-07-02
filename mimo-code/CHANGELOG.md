# Changelog

## v2.1.0 (2026-07-02)

### ✨ 新增

- **HA Add-on 支持** — 新增 `mimo-code/` 目录，提供完整的 Home Assistant Add-on 支持
- **多架构构建** — 支持 `aarch64` / `amd64` / `armv7` 三种架构
- **s6-overlay 服务管理** — 使用 s6-overlay 管理 `mimo serve` 进程生命周期，崩溃自动重启
- **Supervisor 集成** — 集成可通过 Supervisor API 检测 add-on 运行状态
- **看门狗** — config.yaml 配置 `watchdog` 端点，Supervisor 自动检查健康状态

### 🔧 优化

- **多阶段 Docker 构建** — 第一阶段安装 npm 包，第二阶段仅复制原生二进制到 HA 基础镜像，无需 Node.js 运行时
- **配置驱动** — 通过 `bashio` 读取 add-on 配置，端口可自定义