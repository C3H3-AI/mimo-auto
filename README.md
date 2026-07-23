# MiMo Auto — Home Assistant 集成

在 Home Assistant 中免费使用小米 MiMo AI 模型。

## 架构

```
┌────────────────────────────────────────────────────────────┐
│                       HA 宿主机                            │
│                                                            │
│  ┌────────────────────────┐    ┌────────────────────────┐  │
│  │  Docker: homeassistant  │    │  Docker: addon         │  │
│  │  (host networking)      │    │  (bridge, :14096)      │  │
│  │                         │    │                        │  │
│  │  miモ_auto              │    │  tcp_proxy:14096       │  │
│  │  custom_component      │────┼──▶  │                    │  │
│  │  coordinator ──▶ health │    │     └──▶ 127.0.0.1:14095│  │
│  │  check GET /session     │    │         (mimo serve)     │  │
│  │                         │    │                        │  │
│  │  conversation entity    │    │  Web UI (SPA)          │  │
│  │  ← HA voice assistant  │    │  ← ingress:8099        │  │
│  │  ← Automation service  │    │                        │  │
│  └────────────────────────┘    └────────────────────────┘  │
│                                                            │
│              Supervisor API (addon detection)              │
└────────────────────────────────────────────────────────────┘
```

## 组件说明

本集成作为 **Addon 桥接层**，将 MiMo Code Addon 的能力接入 Home Assistant：

| 组件 | 类型 | 功能 |
|------|------|------|
| **MiMo Code** | Supervisor Add-on | 运行 `mimo serve` + IM 通道 + Web UI |
| **MiMo Auto** | HA Custom Component | 对话实体 + 自动化服务 + 状态监控 |

集成本身**不管理任何子进程**——Addon 容器完全独立运行。

### 通信链路

```
HA 对话/服务 → localhost:14096 → tcp_proxy (addon) → mimo serve (addon)
HA 侧边栏   → supervisor ingress:8099 → Web UI (addon) → mimo serve API
```

## 前置要求

- Home Assistant (HAOS / Supervised)
- MiMo Code Addon 已安装并运行

## 安装

### 1️⃣ 安装 Addon

```
HA → 设置 → 加载项商店 → 右上角三个点 → 仓库 → 添加
仓库地址: https://github.com/C3H3-AI/mimo-auto

刷新后找到 MiMo Code → 安装
```

### 2️⃣ 添加集成

```
HA → 设置 → 设备与服务 → 添加集成 → 搜索 MiMo Auto
```

配置参数：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `mimo serve 地址` | `http://127.0.0.1:14096` | Addon 的 tcp_proxy 端口 |
| `WebUI 地址` | `http://127.0.0.1:8099` | Web UI 面板地址（可选） |
| `Supervisor 检测` | 启用 | 通过 Supervisor API 自动检测 Addon |

## 使用

### HA 对话助手

```
设置 → 语音助手 → 添加助手
名称: MiMo Auto
对话代理: 选 MiMo Auto
```

### 自动化调用

```yaml
action: mimo_auto.chat
data:
  message: "明天天气怎么样？"
response_variable: reply
```

```yaml
action: mimo_auto.chat
data:
  message: "检查一下 HA 系统状态"
  session_id: "ses_xxx"  # 可选，延续上下文
response_variable: reply
```

## 验证环境

| 环境 | 值 |
|------|-----|
| HA 版本 | 2026.7+ |
| 宿主机系统 | HAOS / Supervised |
| Addon 架构 | aarch64 / amd64 |
| 部署方式 | Supervisor Addon + Custom Component |
| 容器网络 | HA: host / Addon: bridge |

## 工作原理

小米服务端对 MiMo Code 原生二进制携带的设备指纹和签名进行认证，其他方式直接调用 API 返回 403。

```
HA 对话 → miモ_auto conv.entity → HTTP:14096 → tcp_proxy → mimo serve → MiMo API
```

Addon 通过 tcp_proxy（0.0.0.0:PORT → 127.0.0.1:PORT-1）解决 `mimo serve` 仅绑定 localhost 的限制。

## 文件结构

```
mimo-auto/
├── custom_components/mimo_auto/      ← HA 自定义组件
│   ├── __init__.py                   组件入口 + 服务注册 + V1→V2 迁移
│   ├── coordinator.py               Addon 连接管理 + 健康检查
│   ├── conversation.py              对话实体 (HA voice assistant)
│   ├── config_flow.py               单步配置流程
│   ├── sensor.py                    Addon 状态传感器
│   ├── const.py                     常量
│   ├── supervisor_client.py         Supervisor API 客户端
│   ├── manifest.json                组件声明
│   └── services.yaml                服务定义
├── mimo-code/                        ← Addon 包
│   ├── config.yaml                  Addon 配置
│   ├── Dockerfile                   多阶段构建
│   └── rootfs/                      s6-overlay 服务 + tcp_proxy + WebUI
├── webui/                            ← Web UI (React + Vite)
├── CHANGELOG.md                      更新日志
└── README.md                         本文件
```

## 从旧版本升级

如果从 v4.x 或更早版本升级，集成会自动迁移配置：

| 旧参数 | 新参数 |
|--------|--------|
| `port` (int) | `server_url` → `http://127.0.0.1:{port}` |
| `mimo_bin_path` | 已移除（Addon 管理） |
| `auto_install` | 已移除（Addon 管理） |
| `channels` (dict) | 已移除（Addon 管理） |
| `mcp_url`, `ssh_*` | 已移除 |

## 更新日志

见 [CHANGELOG.md](CHANGELOG.md)
