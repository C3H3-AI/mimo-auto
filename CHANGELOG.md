# Changelog

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