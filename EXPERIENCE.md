# 2026-07-02/03 MiMo Auto 集成开发

## 做了什么
- 排查 conversation.mimo_auto 在语音助手中灰色不可选的问题
  - 根因：supported_languages 返回 ["*"]（列表）而不是 "*"（字符串）
- 修复 Add-on 的 Web UI API 端点（/chat → /message, /messages → /message）
- 添加 ingress 支持（config.yaml 加 ingress: true）
- 探索 HA Supervisor 本地 addon 注册问题
  - Supervisor 只启动时扫描 /addons/
  - 需手动编辑 /data/apps.json 注册 local_mimo-code
  - config.yaml 的 watchdog URL 格式需要 [HOST] 和 [PORT:N] 占位符
  - arch 字段不能含 armv7/armhf/i386（已废弃）
- 升级 Supervisor 从 2026.05.0 到 2026.06.2
- 清理 git 仓库根目录的无关脚本（~40 个 HA 运维脚本）
- 审计全部代码并记录

## 关键决策
- conversation.py 的 supported_languages 返回 "*"（字符串）
- async_set_agent 保留在 __init__.py（对话助手）+ conversation.py（claw）
- Addon config 简化为最小可用版（先过验证，再加功能）
- entity.py 保留但标记为死代码
