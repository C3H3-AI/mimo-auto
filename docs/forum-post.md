# [集成] MiMo Auto — 在 HA 中免费使用小米 MiMo AI 对话模型

> 不用 API Key、不用注册账号、完全免费，装个命令行工具就能在 HA 里用 AI 对话。

---

## 这是什么？

MiMo Auto 是一个 Home Assistant 自定义组件，让你在 HA 里直接和 MiMo AI 对话。底层用的是小米的 `mimo` 命令行工具——就是那个 MiMo Code 的 AI 能力，可以聊天、问答、写代码。

最关键的是：**完全免费**，不需要任何 API Key，不需要注册第三方账号。

## 工作原理

```
HA 对话界面 → mimo_auto 组件 → mimo serve (宿主机) → 小米 MiMo 服务端
```

小米服务端对 `mimo` 原生二进制携带的设备指纹进行签名认证，其他第三方客户端直接调 API 会返回 403。所以组件不直接调 API，而是通过启动本地的 `mimo serve` 进程来代理请求。

## 验证环境

| 项目 | 配置 |
|------|------|
| HA 版本 | 2026.6.4 |
| 宿主机 | Alpine Linux v3.24 (aarch64) |
| 部署方式 | Docker + 宿主机 `mimo serve` |
| 网络模式 | host networking |

## 安装步骤

### 1️⃣ 宿主机上安装 mimo

SSH 进 HA 宿主机：

```bash
# 安装 Node.js
apk add nodejs npm

# 安装 mimo
npm install -g @mimo-ai/cli

# 验证
mimo --version
```

⚠️ **Alpine/musl 注意**：Alpine 上 npm 会自动拉取 musl 版本，但包装脚本有时会找错二进制。如果 `mimo` 命令报错，直接用绝对路径：

```bash
# 查看实际安装了哪个版本
ls /usr/local/lib/node_modules/@mimo-ai/cli/node_modules/@mimo-ai/
# 输出应包含 mimocode-linux-arm64-musl/

# 用 musl 版本直接启动
/usr/local/lib/node_modules/@mimo-ai/cli/node_modules/@mimo-ai/mimocode-linux-arm64-musl/bin/mimo serve --port 14096
```

### 2️⃣ 开机自启

```bash
cat > /etc/local.d/mimoserve.start << 'EOF'
#!/bin/sh
MIMO_BIN=$(which mimo)
nohup "$MIMO_BIN" serve --port 14096 --print-logs >> /var/log/mimo-serve.log 2>&1 &
EOF
chmod +x /etc/local.d/mimoserve.start
rc-update add local
/etc/local.d/mimoserve.start
```

### 3️⃣ 部署组件

从 GitHub 下载 `custom_components/mimo_auto/` 目录，放到 HA 的 `custom_components` 下：

```bash
# 假设 HA 配置目录挂载在 /config
cp -r custom_components/mimo_auto /config/custom_components/
```

或者直接用 HACS 自定义仓库添加：
- 仓库地址：`https://github.com/C3H3-AI/mimo-auto`
- 类别：Integration

### 4️⃣ 添加集成

```
设置 → 设备与服务 → 添加集成 → 搜索 MiMo Auto
```

端口默认 `14096`，直接提交即可。

### 5️⃣ 配置对话助手

```
设置 → 语音助手 → 添加助手
  名称: MiMo Auto
  对话代理: 选 MiMo Auto
```

然后在 HA 右下角的对话气泡里就能跟 MiMo 聊天了。

## 使用效果

实测对话效果：

> ❓ 问：用一句话介绍你自己
>
> 🤖 答：我是 MiMoCode，一个交互式 CLI 工具，帮助用户处理软件工程任务。

> ❓ 问：明天天气怎么样？
>
> 🤖 答：我无法直接获取实时天气信息，但你可以告诉我你的城市，我可以帮你规划如何查询。

(如果需要天气等信息，可以结合 HA 的 `conversation` 管道或其他传感器一起使用)

## 自动化调用

```yaml
action: mimo_auto.chat
data:
  message: "现在几点了？"
response_variable: reply
```

## 直接命令行使用

`mimo` 本身也是一个独立的 AI 命令行工具，不依赖 HA：

```bash
mimo                    # 交互式聊天模式
mimo --prompt "你好"    # 单次问答
```

## 项目地址

GitHub：https://github.com/C3H3-AI/mimo-auto

## 已知限制

- 首次对话需等待 `mimo serve` 初始化（约 10-15 秒）
- 每次对话创建新 session，不保留上下文（后续版本计划支持）
- 宿主机需要 Node.js 运行时

---

欢迎试用和反馈，有问题可以直接在 GitHub 提 Issue。
