"""Sensor platform for MiMo Auto integration.

Simplified sensor — shows the Addon connection status only.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, DOMAIN_NAME

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up MiMo Auto sensor."""
    data = hass.data[DOMAIN].get(config_entry.entry_id)
    if not data:
        return
    coordinator = data.get("coordinator")
    if not coordinator:
        return
    async_add_entities([MiMoAddonStatusSensor(coordinator, config_entry)])


class MiMoAddonStatusSensor(SensorEntity):
    """Sensor for MiMo Code Addon connection status."""

    _attr_has_entity_name = True
    _attr_name = "Addon Status"
    _attr_icon = "mdi:server"

    def __init__(self, coordinator, config_entry: ConfigEntry) -> None:
        """Initialize the sensor."""
        self._coordinator = coordinator
        self._attr_unique_id = f"{config_entry.entry_id}_addon_status"
        self._attr_device_info = dr.DeviceInfo(
            identifiers={(DOMAIN, config_entry.entry_id)},
            name=DOMAIN_NAME,
            manufacturer="MiMo",
            model="MiMo Code Addon",
        )

    @property
    def native_value(self) -> str:
        """Return the sensor value."""
        if self._coordinator.is_running:
            return "connected"
        if self._coordinator.addon_slug:
            return "detected"
        return "disconnected"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional attributes."""
        return {
            "server_url": self._coordinator.server_url,
            "addon_slug": self._coordinator.addon_slug or "",
        }
