# MiMo Auto 版本路线图（v2 — 基于补充调研修订）

> 核心变化：利用 MiMo-Code 原生 MCP/permission/cron/orchestrator 能力，大幅减少自研工作量
> 日期：2024-07-24

---

## 核心理念（修订）

> MiMo-Code 已内建 MCP 客户端、permission 权限引擎、cron 调度器、orchestrator 多 agent 编排。
> 我们要做的不是造轮子，而是**配置 + 封装**。

---

## 版本规划

### v5.1.0 — 安全止血（1 周）

**目标**：堵住 server.py 安全漏洞

| 任务 | 说明 | 优先级 |
|------|------|--------|
| IP 白名单 | server.py 添加 ingress 来源检查 | P0 |
| CORS 收窄 | 8 处 `"*"` 改为动态 origin | P0 |
| 文件写收窄 | ALLOWED_PREFIXES 移除 `/config` | P0 |
| 移除 GET→write | do_GET 不再路由到 fs/write | P0 |
| 版本号统一 | config.yaml + manifest.json | P2 |

**不改**：绑定地址（ingress 需要 0.0.0.0）、tcp_proxy（外部 API 需要）。

---

### v5.2.0 — 接 ha-mcp（1 周）

**目标**：MiMo 原生调用 HA 设备，零代码

| 任务 | 说明 | 依赖 |
|------|------|------|
| MCP 配置 | 在 mimo.json 中添加 ha-mcp remote server | 无 |
| 验证工具发现 | 确认 MiMo 能发现 ha.call_service 等工具 | 无 |
| 权限规则 | 配置 permission 规则，高危操作走 ask | 无 |

**配置示例**：
```json
{
  "mcp": {
    "ha": {
      "url": "http://supervisor/core/api/mcp",
      "transport": "streamable-http"
    }
  }
}
```

**验收标准**：
- 用户说"开灯"→ MiMo 通过 MCP 调用 ha.turn_on → 灯亮
- 用户说"温度多少"→ MiMo 通过 MCP 调用 ha.get_state → 返回温度

---

### v5.3.0 — 权限 + 确认机制（1-2 周）

**目标**：高危操作拦截 + 飞书/微信确认

| 任务 | 说明 | 依赖 |
|------|------|------|
| permission 规则 | 配置 ask 规则拦截高危操作 | v5.2.0 |
| inbox 转发 | ask 请求转发到飞书/微信 | v5.2.0 |
| 确认卡对接 | action_confirm.py 接入 permission ask | v5.2.0 |
| 审计日志 | 记录所有设备控制操作 | 无 |

**permission 规则示例**：
```json
{
  "permissions": [
    {"permission": "mcp.ha.call_service", "pattern": "lock/*|cover/*", "action": "ask"},
    {"permission": "mcp.ha.call_service", "pattern": "light/*|switch/*", "action": "allow"}
  ]
}
```

**验收标准**：
- 开灯 → 直接执行（allow）
- 解锁 → 弹出确认卡（ask）
- 所有操作有审计日志

---

### v5.4.0 — HA 专属技能（2 周）

**目标**：自然语言管家能力

| 技能 | 功能 | 触发示例 |
|------|------|----------|
| ha-control | 灯/空调/窗帘/场景控制 | "打开客厅灯" |
| ha-status | 设备状态查询 | "现在家里哪些灯亮着" |
| ha-automation | 自动生成自动化 | "每晚 11 点关灯" |
| ha-routine | 家庭作息编排 | "帮我编排晨间流程" |

**存储**：`.mimocode/skills/ha-*/SKILL.md`（addon 持久卷）

**验收标准**：自然语言控制成功率 > 90%

---

### v5.5.0 — 记忆系统（1 周）

**目标**：AI 跨会话记得你家

| 任务 | 说明 |
|------|------|
| MEMORY.md 初始化 | 设备清单 + 家庭成员 + 常用场景 |
| 设备上下文增强 | 包含使用习惯 |
| 作息记忆 | 家庭成员作息时间 |
| 偏好学习 | 温度/亮度等偏好 |

---

### v5.6.0 — cron 定时 + 自升级（2 周）

**目标**：AI 自主学习和进化

| 任务 | 说明 | 依赖 |
|------|------|------|
| cron 配置 | 用原生 cron 引擎配标准 cron 表达式 | 无 |
| evolve 审查 | 每周日 3:00 自动回顾 + 沉淀新技能 | v5.4.0 |
| skills.urls | 指向 HA 托管的技能目录 | v5.4.0 |
| .mimocode/ git 跟踪 | 所有自升级可回滚 | 无 |

**cron 配置示例**：
```json
{
  "cron": {
    "evolution_review": {
      "schedule": "0 3 * * 0",
      "prompt": "回顾本周会话，找出重复 3+ 次的操作，用 evolve 沉淀成新 skill"
    }
  }
}
```

---

### v5.7.0 — orchestrator 多 agent 管家（2-3 周）

**目标**：专职子 agent 分工协作

| Agent | 职责 | 依赖 |
|-------|------|------|
| ha-butler（orchestrator） | 总调度，理解用户意图 | v5.2.0 |
| ha-device-control | 接 ha-mcp 控制设备 | v5.2.0 |
| ha-safety-reviewer | 高危操作拦截 | v5.3.0 |
| ha-memory-keeper | 记忆沉淀 | v5.5.0 |

**架构**：
```
用户消息 → ha-butler (orchestrator)
  ├── 意图识别
  ├── ha-device-control (调用 MCP)
  ├── ha-safety-reviewer (权限检查)
  └── ha-memory-keeper (记忆更新)
```

---

### v5.8.0 — WebUI 全面优化（3-4 周）

**目标**：现代化界面 + 完整功能

| 任务 | 说明 |
|------|------|
| 布局重构 | 侧边栏 + 主聊天区 + 设备面板 |
| 设备控制面板 | 实时设备状态 + 点击控制 |
| 自动化管理 | 创建/编辑自动化 |
| 技能市场 | 浏览/安装/管理技能 |
| 会话历史 | 搜索/导出/分享 |
| 移动端适配 | 响应式布局 |

---

## 时间线总览

```
v5.1.0 (1w)  →  v5.2.0 (1w)  →  v5.3.0 (1-2w)  →  v5.4.0 (2w)
   安全止血        接 ha-mcp        权限+确认          HA 技能
       ↓
v5.5.0 (1w)  →  v5.6.0 (2w)  →  v5.7.0 (2-3w)  →  v5.8.0 (3-4w)
   记忆系统        cron+自升级       orchestrator       WebUI 优化
```

总计约 13-17 周（3-4 个月）。

---

## 与首轮方案的对比

| 首轮方案 | 修订方案 | 变化原因 |
|----------|----------|----------|
| T1 手搓 ha-call-service 工具 | T2 接 ha-mcp（零代码） | MiMo 原生 MCP 客户端 |
| T3 自造 ha-safety hook | T3 用 permission 规则 | 原生三态权限引擎 |
| T5 loop 周期 evolve | T5 cron 引擎（标准 cron） | 更精确、可持久化 |
| T6 drive-mimo 分离 | T7 orchestrator 多 agent | 原生编排能力 |
| （无） | T8 本地模型分流 | provider 自定义系统 |

---

## 关键原则

1. **配置优于编码**：优先用 mimo 的原生配置能力，而非自己写代码
2. **安全第一**：T0 安全止血必须最先完成
3. **渐进增强**：每个版本只做一件事
4. **用户可感知**：每个版本都有直接体验到的改进
