"""Sensor platform for MiMo Auto integration.

Exposes system status and metrics as HA sensor entities.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up MiMo Auto sensors."""
    data = hass.data[DOMAIN].get(config_entry.entry_id)
    if not data:
        return

    coordinator = data.get("coordinator")
    if not coordinator:
        return

    entities = [
        MiMoServerStatusSensor(coordinator, config_entry),
        MiMoMCPStatusSensor(coordinator, config_entry),
        MiMoSSHStatusSensor(coordinator, config_entry),
        MiMoSupervisorStatusSensor(coordinator, config_entry),
    ]

    async_add_entities(entities, update_before_add=False)


class MiMoServerStatusSensor(SensorEntity):
    """Sensor for MiMo server status."""

    _attr_has_entity_name = True
    _attr_name = "Server Status"
    _attr_icon = "mdi:server"

    def __init__(self, coordinator, config_entry: ConfigEntry) -> None:
        """Initialize the sensor."""
        self._coordinator = coordinator
        self._attr_unique_id = f"{config_entry.entry_id}_server_status"

    @property
    def native_value(self) -> str:
        """Return the sensor value."""
        if self._coordinator.is_running:
            return "running"
        return "stopped"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional attributes."""
        attrs = {
            "port": self._coordinator.port,
            "server_url": self._coordinator.server_url,
            "external_mode": self._coordinator._external_mode,
        }
        if self._coordinator._process:
            attrs["pid"] = self._coordinator._process.pid
        return attrs


class MiMoMCPStatusSensor(SensorEntity):
    """Sensor for MCP connection status."""

    _attr_has_entity_name = True
    _attr_name = "MCP Status"
    _attr_icon = "mdi:connection"

    def __init__(self, coordinator, config_entry: ConfigEntry) -> None:
        """Initialize the sensor."""
        self._coordinator = coordinator
        self._attr_unique_id = f"{config_entry.entry_id}_mcp_status"

    @property
    def native_value(self) -> str:
        """Return the sensor value."""
        if not self._coordinator.mcp_client:
            return "not_configured"
        if not self._coordinator.mcp_client.is_available:
            return "unavailable"
        return "available"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional attributes."""
        if not self._coordinator.mcp_client:
            return {"url": None, "tools_count": 0}
        return {
            "url": self._coordinator.mcp_client._url,
            "tools_count": len(self._coordinator.mcp_client._tools_cache or []),
        }


class MiMoSSHStatusSensor(SensorEntity):
    """Sensor for SSH connection status."""

    _attr_has_entity_name = True
    _attr_name = "SSH Status"
    _attr_icon = "mdi:terminal"

    def __init__(self, coordinator, config_entry: ConfigEntry) -> None:
        """Initialize the sensor."""
        self._coordinator = coordinator
        self._attr_unique_id = f"{config_entry.entry_id}_ssh_status"

    @property
    def native_value(self) -> str:
        """Return the sensor value."""
        if not self._coordinator.ssh_client:
            return "not_configured"
        if not self._coordinator.ssh_client.is_available:
            return "unavailable"
        return "available"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional attributes."""
        if not self._coordinator.ssh_client:
            return {"host": None}
        return {
            "host": self._coordinator.ssh_client._host,
            "port": self._coordinator.ssh_client._port,
            "username": self._coordinator.ssh_client._username,
        }


class MiMoSupervisorStatusSensor(SensorEntity):
    """Sensor for Supervisor API status."""

    _attr_has_entity_name = True
    _attr_name = "Supervisor Status"
    _attr_icon = "mdi:home-assistant"

    def __init__(self, coordinator, config_entry: ConfigEntry) -> None:
        """Initialize the sensor."""
        self._coordinator = coordinator
        self._attr_unique_id = f"{config_entry.entry_id}_supervisor_status"

    @property
    def native_value(self) -> str:
        """Return the sensor value."""
        if not self._coordinator.supervisor_client:
            return "not_configured"
        if not self._coordinator.supervisor_client.is_available:
            return "unavailable"
        return "available"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional attributes."""
        if not self._coordinator.supervisor_client:
            return {"base_url": None}
        return {
            "base_url": self._coordinator.supervisor_client._base_url,
        }
