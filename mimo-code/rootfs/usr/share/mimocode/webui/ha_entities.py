"""HA Entity definitions for mimo_auto integration.

Provides sensor and select entities for HA dashboard integration.
"""

from __future__ import annotations

import logging
from typing import Any

_LOGGER = logging.getLogger(__name__)


# Sensor definitions for HA dashboard
SENSORS = [
    {
        "name": "MiMo 通道状态",
        "unique_id": "mimo_auto_channel_status",
        "state": "unknown",
        "icon": "mdi:lan-connect",
        "attributes": {},
    },
    {
        "name": "MiMo 最后消息时间",
        "unique_id": "mimo_auto_last_message_time",
        "state": "unknown",
        "icon": "mdi:clock-outline",
        "attributes": {},
    },
    {
        "name": "MiMo 消息计数",
        "unique_id": "mimo_auto_message_count",
        "state": "0",
        "icon": "mdi:message-text",
        "attributes": {"today": 0, "total": 0},
    },
]

# Select definitions for HA dashboard
SELECTS = [
    {
        "name": "MiMo 默认通道",
        "unique_id": "mimo_auto_default_channel",
        "options": ["飞书", "企业微信", "个人微信"],
        "current_option": None,
        "icon": "mdi:message-reply-text",
    },
    {
        "name": "MiMo 推理显示",
        "unique_id": "mimo_auto_show_reasoning",
        "options": ["开启", "关闭"],
        "current_option": "开启",
        "icon": "mdi:brain",
    },
]


def get_sensor_state(unique_id: str) -> dict[str, Any] | None:
    """Get sensor definition by unique_id."""
    for sensor in SENSORS:
        if sensor["unique_id"] == unique_id:
            return sensor.copy()
    return None


def get_select_state(unique_id: str) -> dict[str, Any] | None:
    """Get select definition by unique_id."""
    for select in SELECTS:
        if select["unique_id"] == unique_id:
            return select.copy()
    return None
