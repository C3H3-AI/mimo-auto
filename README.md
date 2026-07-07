# MiMo Code — Home Assistant 集成

在 Home Assistant 中免费使用小米 MiMo Auto AI 模型。

## 功能特点

- **侧边栏 Web UI** — 现代化聊天界面，支持流式输出、思考过程显示、文件浏览器
- **HA 对话助手** — 在 HA 语音助手中使用 MiMo Auto 模型
- **Claw Assistant 兼容** — 会话复用，支持上下文保持
- **多模式支持** — Plan/Agent/Build 三种交互模式切换
- **自动化服务** — 通过 `mimo_auto.chat` 服务在自动化中调用 AI

## 架构

```
┌────────────────────────────────────────────────────────┐
│                     HA 宿主机                           │
│                                                         │
│  ┌──────────────────────┐    ┌──────────────────────┐  │
│  │  Docker: homeassistant│    │  Docker: addon       │  │
│  │  ┌────────────────┐  │    │  ┌────────────────┐  │  │
│  │  │ mimo_auto       │  │    │  │ tcp_proxy      │  │  │
│  │  │ custom_component│──┼────┼─▶│ 0.0.0.0:14096  │  │  │
│  │  │ coordinator     │  │    │  └──────┬─────────┘  │  │
│  │  │ ↓ 检测 addon    │  │    │         ↓            │  │
│  │  │ √ 已连接        │  │    │  ┌────────────────┐  │  │
│  │  └────────────────┘  │    │  │ mimo serve      │  │  │
│  │  ┌────────────────┐  │    │  │ 127.0.0.1:14095 │  │  │
│  │  │ panel_iframe   │  │    │  └────────────────┘  │  │
│  │  │ MiMo Chat 侧边栏│──┼────┼── ingress:8099     │  │
│  │  └────────────────┘  │    │  ┌────────────────┐  │  │
│  │                      │    │  │ Web UI (SPA)   │  │  │
│  │                      │    │  │ 模型/设置/命令  │  │  │
│  │                      │    │  └────────────────┘  │  │
│  └──────────────────────┘    └──────────────────────┘  │
└────────────────────────────────────────────────────────┘
```

### 组件说明

本项目包含两个组件：

| 组件 | 类型 | 功能 | 是否可选 |
|------|------|------|---------|
| **MiMo Code** | Supervisor Add-on | 运行 `mimo serve` 服务 + Web UI | 推荐（自动管理） |
| **MiMo Auto** | HA Custom Component | 对话代理 + 服务 + Claw 兼容 | 可选（仅需 HA 集成时） |

### 通信链路

```
HA 组件 → localhost:14096 → tcp_proxy (addon) → mimo serve (addon)
HA 侧边栏 → ingress:8099 → Web UI (addon) → mimo serve API
```

## 安装

### 方式一（推荐）：Add-on + 组件

**1. 添加仓库到 Add-on 商店**

仓库地址：`https://github.com/C3H3-AI/mimo-auto`

```
HA → 设置 → 加载项商店 → 右上角三个点 → 仓库 → 添加
```

**2. 安装 MiMo Code**

刷新后找到 **MiMo Code** add-on，点击安装。安装完成后侧边栏自动出现 **MiMo Code** 入口。

**3. 添加 MiMo Auto 集成（可选）**

```
HA → 设置 → 设备与服务 → 添加集成 → 搜索 MiMo Auto
```

端口保持默认 `14096`，集成会自动检测 Add-on 通道，无需额外配置。

### 方式二：仅 Add-on（独立使用）

如果只需要 Web UI 聊天界面，仅安装 Add-on 即可，不需要添加集成。

```
HA → 设置 → 加载项商店 → 安装 MiMo Code
```

侧边栏出现 **MiMo Code**，点击即用。

### 方式三：仅组件（手动启动 mimo）

适用于已自行运行 `mimo serve` 的场景：

```bash
# 启动 mimo 服务
mimo serve --port 14096
```

然后将 `custom_components/mimo_auto/` 复制到 HA 的 `custom_components` 目录，重启 HA 并添加集成。

## 使用

### Web UI 侧边栏（Add-on 自带）

安装 MiMo Code Add-on 后，HA 侧边栏出现 **MiMo Code** 图标。支持：

- **聊天对话** — 流式输出，思考过程折叠显示，消息时间统计
- **Agent 切换** — Build/Plan/Compose 模式选择器
- **文件浏览器** — 右侧边栏，浏览项目文件
- **命令面板** — Ctrl+K 打开，搜索/执行命令
- **主题切换** — 深色/浅色/系统跟随
- **设置面板** — 提供商、技能、统计信息
- **移动端适配** — 响应式布局，侧边栏点击遮罩关闭

### HA 对话助手

需要安装 MiMo Auto 组件：

```
设置 → 语音助手 → 添加助手
  名称: MiMo Auto
  对话代理: 选 MiMo Auto
```

### Claw Assistant

MiMo Auto 会自动注册 `conversation.mimo_auto` 实体，支持会话复用。

在 Claw Assistant 设置中：
- **Primary Agent**: 选择 `conversation.mimo_auto`
- **Secondary Fallback Agent**: 选择 `conversation.mimo_auto`

会话上下文会在同一 conversation_id 的多次对话中保持。

### 自动化调用

```yaml
action: mimo_auto.chat
data:
  message: "明天天气怎么样？"
response_variable: reply
```

## Add-on 配置

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `port` | `14096` | 对外服务端口（内部自动偏移为 port-1）|

## 组件配置

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `port` | `14096` | 连接 Add-on 的端口 |
| `auto_install` | `true` | 找不到 mimo 时自动安装 |

## 验证环境

| 环境 | 值 |
|------|-----|
| HA 版本 | 2026.7+ |
| 宿主机系统 | HAOS / Supervised |
| Add-on 架构 | aarch64 / amd64 |
| 部署方式 | Supervisor Add-on + Custom Component |

## 工作原理

```
HA 对话 → mimo_auto 组件 → HTTP → addon tcp_proxy → mimo serve → MiMo API
                                   ↑
                            TCP 代理 (0.0.0.0:PORT → 127.0.0.1:PORT-1)
                            解决 mimo serve 仅绑定 localhost 的限制
```

小米服务端对 MiMo Code 原生二进制携带的设备指纹和签名进行认证，其他方式直接调用 API 返回 403。

## 文件结构

```
mimo-auto/
├── custom_components/mimo_auto/     ← HA 自定义组件
│   ├── __init__.py                  组件入口
│   ├── agent_impl.py               对话代理（会话复用）
│   ├── coordinator.py              服务检测 + Add-on 通道
│   ├── conversation.py             对话实体 (Claw 兼容)
│   ├── entity.py                   ConversationEntity 注册
│   ├── config_flow.py              UI 配置流程
│   ├── const.py                    常量
│   ├── mimo_proxy.py               API 代理
│   ├── manifest.json               组件声明
│   └── services.yaml               服务定义
├── webui/                           ← Web UI (Vite + React)
│   ├── src/
│   │   ├── components/             12 个 React 组件
│   │   ├── store/                  Zustand 状态管理
│   │   ├── api/                    API 客户端
│   │   ├── hooks/                  自定义 Hooks
│   │   ├── theme/                  主题配置
│   │   ├── types/                  TypeScript 类型
│   │   ├── App.tsx                 应用入口
│   │   ├── main.tsx                React 入口
│   │   └── index.css               全局样式
│   ├── dist/                       构建产物
│   └── package.json
├── mimo-code/                       ← Add-on 包
│   ├── config.yaml                 Add-on 配置
│   ├── Dockerfile                  多阶段构建
│   ├── build.yaml                  构建参数
│   └── rootfs/
│       ├── etc/s6-overlay/s6-rc.d/
│       │   ├── mimocode/           mimo serve 服务
│       │   └── mimocode-webui/     Web UI 服务
│       └── usr/share/mimocode/webui/
│           ├── dist/               Vite 构建产物（部署目标）
│           ├── server.py           HTTP 代理服务器
│           └── tcp_proxy.py        TCP 端口转发代理
├── hacs.json                        HACS 配置
└── README.md                        本文件
```

## 更新日志

### v3.2.0

- **Claw 会话复用** — 使用 conversation_id 复用 mimo serve 会话，保持上下文
- **Agent 模式选择器** — 输入框上方显示当前 Agent，支持切换
- **消息重新生成** — 助手消息显示重新生成按钮
- **消息时间显示** — 显示创建时间和响应耗时
- **文件浏览器** — 使用 mimo serve 原生 /file API
- **移动端优化** — 侧边栏默认隐藏，点击遮罩关闭
- **流式阶段指示** — 显示 Sending/Thinking/Processing 状态

### v3.1.0

- **全新 Web UI** — React + Vite + MUI 现代化界面
- **完整 Markdown 渲染** — 代码块复制、语法高亮
- **思考过程显示** — 可折叠的 reasoning 区域
- **Token 统计** — 显示总 token 和推理 token
- **4 种主题** — 深色、浅色、德古拉、北欧
- **键盘快捷键** — Ctrl+K/N/E/B/Shift+C

### v3.0.0

- **Add-on 独立化** — MiMo Code 作为独立 Add-on 运行，自带侧边栏 Web UI
- **TCP 端口转发** — 解决 `mimo serve` 仅绑定 localhost 的问题，支持 bridge 网络模式
- **Ingress 侧边栏** — 通过 Supervisor ingress 在 HA 侧边栏内嵌 Web UI
- **Web UI 增强** — 模型选择、命令面板 Ctrl+K、主题切换、代理模式、提供商管理
- **Add-on 检测** — 组件自动检测 Add-on 通道，无需手动配置连接地址
- **架构重构** — 分离 Add-on（运行层）和 Custom Component（集成层）

### v2.1.0

- 对话实体注册 (Claw Assistant 兼容)
- 流式消息处理
- 崩溃自动恢复

## 键盘快捷键

| 快捷键 | 功能 |
|--------|------|
| Ctrl+K | 命令面板 |
| Ctrl+N | 新建会话 |
| Ctrl+E | 文件浏览器 |
| Ctrl+B | 切换侧边栏 |
| Ctrl+Shift+C | 设置面板 |
| Escape | 关闭弹窗 |
| Enter | 发送消息 |
| Shift+Enter | 换行 |
