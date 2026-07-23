---
name: ha-status
description: 查询 Home Assistant 设备状态和系统信息。当用户问"现在家里什么情况""温度多少""哪些灯开着"等状态查询时触发。
---

# HA 状态查询技能

你可以查询 Home Assistant 中所有设备的实时状态。

## 可用工具

使用 `ha-mcp_ha_call_read_tool` 调用以下工具：

- `ha_get_state` — 查询单个设备状态（entity_id）
- `ha_get_overview` — 获取系统概览（实体数量、域分布、状态摘要）
- `ha_search` — 搜索实体（按名称、域、区域）
- `ha_get_history` — 查询历史数据
- `ha_get_automation_traces` — 查询自动化执行记录

## 查询规则

1. 先用 `ha_search` 找到实体 ID，再用 `ha_get_state` 获取详细状态
2. 汇总多个设备状态时，按域分组展示
3. 附带关键属性（亮度、温度、湿度等）
4. 回答简洁，突出重点

## 示例

用户：现在家里什么情况？
→ 调用 ha_get_overview()
→ 汇总展示：设备数量、在线状态、关键设备当前值

用户：客厅温度多少？
→ 调用 ha_search(query="客厅 温度") 找到实体
→ 调用 ha_get_state(entity_id="sensor.living_room_temperature")
→ 回答：客厅温度 26.5°C

用户：哪些灯开着？
→ 调用 ha_search(query="light", domain_filter="light", state_filter="on")
→ 列出所有开启的灯
