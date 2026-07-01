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
- **Docker HA + 宿主机模式（已验证）**：HA 跑在 Docker 容器内，宿主机运行 `mimo serve`，通过 host networking 连接。适用于 Home Assistant OS / Supervised 部署
- **本地模式**：HA 直接启动 `mimo serve` 子进程。适用于 Core / Container 直接安装在系统上的场景

## 安装

### ✅ 已验证的安装方式（HA OS / Docker + 宿主机）

本集成已在以下环境验证通过：

| 环境 | 值 |
|------|-----|
| HA 版本 | 2026.6.4 |
| 宿主机系统 | Alpine Linux v3.24 (aarch64, musl) |
| 部署方式 | Docker 容器 + 宿主机 `mimo serve` |
| 网络模式 | host networking |

**宿主机上安装 mimo：**

```bash
# 1. 安装 Node.js（如已安装可跳过）
apk add nodejs npm

# 2. 全局安装 mimo CLI
npm install -g @mimo-ai/cli

# 3. 验证安装
mimo --version

# 4. 手动启动服务
mimo serve --port 14096 --print-logs

# 5. 设置开机自启（Alpine / OpenRC）
cat > /etc/local.d/mimoserve.start << 'EOF'
#!/bin/sh
MIMO_BIN=$(which mimo)
nohup "$MIMO_BIN" serve --port 14096 --print-logs >> /var/log/mimo-serve.log 2>&1 &
EOF
chmod +x /etc/local.d/mimoserve.start
/etc/local.d/mimoserve.start
```

**⚠️ Alpine Linux（musl）注意：**
Alpine 系统上 `npm install -g @mimo-ai/cli` 会自动拉取 `mimocode-linux-arm64-musl` 版本。但默认的 `mimo` 包装脚本可能找到 glibc 版本导致无法运行，推荐用以下方法之一：

```bash
# 方法 A：检查实际运行的二进制
ls /usr/local/lib/node_modules/@mimo-ai/cli/node_modules/@mimo-ai/
# 输出应包含 mimocode-linux-arm64-musl/

# 方法 B：如果默认 mimo 命令报错，直接用 musl 版本启动
/usr/local/lib/node_modules/@mimo-ai/cli/node_modules/@mimo-ai/mimocode-linux-arm64-musl/bin/mimo serve --port 14096
```

### 其他安装方式

**macOS / Linux（glibc）：**

```bash
curl -fsSL https://mimo.xiaomi.com/install | bash
# 或
npm install -g @mimo-ai/cli
```

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

### HA 对话助手

```
设置 → 语音助手 → 添加助手
  名称: MiMo Auto
  对话代理: 选 MiMo Auto
```

### 直接使用 mimo 命令行

`mimo` 是一个独立的命令行 AI 助手，不依赖 HA 也可以使用：

```bash
# 交互式聊天
mimo

# 单次问答
mimo --prompt "用一句话介绍小米汽车"

# 指定 session 文件保存历史
mimo --session my-session.json

# 启动服务（给 HA 或其他客户端用）
mimo serve --port 14096
```

在终端里直接敲 `mimo` 回车即可进入交互模式，适合在宿主机上快速测试或日常使用。

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
