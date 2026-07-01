# MiMo Auto — Home Assistant 集成

在 Home Assistant 中免费使用小米 MiMo Auto AI 模型。

## 架构

```
┌─────────────────────────────────────────────────┐
│                  HA 宿主机                       │
│                                                   │
│  ┌──────────────────────┐    ┌────────────────┐  │
│  │  Docker 容器          │    │  宿主机进程      │  │
│  │  ┌────────────────┐  │    │  ┌────────────┐ │  │
│  │  │ mimo_auto      │  │    │  │ mimo serve │ │  │
│  │  │ custom_component│──┼────┼─▶│:14096      │ │  │
│  │  └────────────────┘  │    │  └────────────┘ │  │
│  └──────────────────────┘    └────────────────┘  │
└─────────────────────────────────────────────────┘
```

两种模式：
- **Docker 模式（推荐）**：宿主机运行 `mimo serve`，HA 容器通过 host networking 连接
- **本地模式**：HA 直接启动 `mimo serve` 子进程

## 安装

### 1️⃣ 安装 MiMo CLI

**macOS / Linux：**
```bash
curl -fsSL https://mimo.xiaomi.com/install | bash
# 或
npm install -g @mimo-ai/cli
```

**Windows：**
```bash
npm install -g @mimo-ai/cli
```

**⚠️ Alpine Linux（musl）注意：**
Alpine 系统上 npm 会自动下载 musl 版本，无需额外操作。

验证安装：
```bash
mimo --version
```

### 2️⃣ 启动服务（Docker 部署必须）

如果 HA 跑在 Docker 里，需要在**宿主机**上启动 `mimo serve`：

```bash
# 手动启动
mimo serve --port 14096

# 设置开机自启（Alpine / OpenRC）
cat > /etc/local.d/mimoserve.start << 'EOF'
#!/bin/sh
MIMO_BIN=$(which mimo)
nohup "$MIMO_BIN" serve --port 14096 --print-logs >> /var/log/mimo-serve.log 2>&1 &
EOF
chmod +x /etc/local.d/mimoserve.start
/etc/local.d/mimoserve.start
```

HA 容器如果使用 host 网络模式，组件会自动连接 `localhost:14096`。

### 3️⃣ 部署组件

将 `custom_components/mimo_auto/` 复制到 HA 的 `custom_components` 目录：

```bash
# 假设 HA 配置目录为 /config
cp -r custom_components/mimo_auto /config/custom_components/
```

### 4️⃣ 重启 HA 并添加集成

```
设置 → 设备与服务 → 添加集成 → 搜索 MiMo Auto
```

端口默认 `14096`，二进制路径可留空。

## 使用

### 对话助手

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

## 工作原理

组件通过启动本地 `mimo serve` 进程调用 MiMo Auto 免费 AI 模型。小米服务端对 MiMo Code 原生二进制（`mimo.exe`/`mimo`）携带的设备指纹和签名进行认证，其他第三方客户端直接调用 API 会返回 403。

```
HA → mimo_auto 组件 → mimo serve (本地) → MiMo 服务端
                        ↑
                 保留原生二进制指纹
```

## 配置

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `port` | `14096` | `mimo serve` 监听端口 |
| `mimo_bin_path` | 自动查找 | `mimo` 二进制路径 |

## 文件结构

```
custom_components/mimo_auto/
├── __init__.py       组件入口
├── agent_impl.py     对话代理核心逻辑
├── coordinator.py    mimo serve 进程管理
├── config_flow.py    UI 配置流程
├── const.py          常量
├── manifest.json     组件声明
└── services.yaml     服务定义
```

## 已知限制

- 首次对话需等待 `mimo serve` 初始化（约 10-15 秒）
- 每次对话创建新 session，不保留上下文
- 依赖宿主机 Node.js 运行时
