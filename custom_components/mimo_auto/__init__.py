"""Init for MiMo Auto integration.

This integration bridges HA with the MiMo Code Addon for:
- Conversation agent (HA voice assistant / UI chat)
- Chat service (automations)
- Addon status monitoring
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import (
    ATTR_MESSAGE,
    ATTR_SESSION_ID,
    ATTR_RESPONSE,
    CONF_SERVER_URL,
    CONF_USE_SUPERVISOR,
    CONF_WEBUI_URL,
    DEFAULT_SERVER_URL,
    DEFAULT_WEBUI_URL,
    DEFAULT_USE_SUPERVISOR,
    DOMAIN,
    DOMAIN_NAME,
    ERROR_SERVER_NOT_RUNNING,
    SERVICE_CHAT,
)
from .coordinator import MiMoCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.CONVERSATION, Platform.SENSOR]

SERVICE_CHAT_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_MESSAGE): cv.string,
        vol.Optional(ATTR_SESSION_ID): cv.string,
    }
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the MiMo Auto integration from configuration.yaml."""
    if DOMAIN not in config:
        return True
    hass.async_create_task(
        hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "import"}, data=config[DOMAIN],
        )
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up MiMo Auto from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    config = {
        CONF_SERVER_URL: entry.data.get(CONF_SERVER_URL, DEFAULT_SERVER_URL),
        CONF_USE_SUPERVISOR: entry.data.get(CONF_USE_SUPERVISOR, DEFAULT_USE_SUPERVISOR),
    }

    coordinator = MiMoCoordinator(hass, config)
    connected = await coordinator.start()
    if not connected:
        _LOGGER.warning(
            "MiMo Code Addon not reachable at %s. Will retry in background.",
            config[CONF_SERVER_URL],
        )

    hass.data[DOMAIN][entry.entry_id] = {
        "coordinator": coordinator,
    }

    await _async_register_services(hass)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    _LOGGER.info("MiMo Auto integration set up (entry: %s)", entry.entry_id)
    return True


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate config entry from V1 to V2.

    V1 had: port, mimo_bin_path, auto_install, channels (dict with feishu/wechat/etc)
    V2 has: server_url, webui_url, use_supervisor
    """
    if entry.version == 1:
        _LOGGER.info("Migrating MiMo Auto config entry from V1 to V2")
        data = {**entry.data}
        # Map old port to server_url
        old_port = data.pop("port", 14096)
        data[CONF_SERVER_URL] = data.get(CONF_SERVER_URL, f"http://127.0.0.1:{old_port}")
        data[CONF_WEBUI_URL] = data.get(CONF_WEBUI_URL, DEFAULT_WEBUI_URL)
        data[CONF_USE_SUPERVISOR] = data.get(CONF_USE_SUPERVISOR, DEFAULT_USE_SUPERVISOR)
        # Remove old unused keys
        data.pop("mimo_bin_path", None)
        data.pop("mimo_bin", None)
        data.pop("auto_install", None)
        data.pop("channels", None)
        data.pop("mcp_url", None)
        data.pop("ssh_host", None)
        data.pop("ssh_port", None)
        data.pop("ssh_username", None)
        data.pop("ssh_key_path", None)
        data.pop("supervisor_token", None)

        hass.config_entries.async_update_entry(entry, data=data, version=2)
        _LOGGER.info("MiMo Auto config entry migrated to V2")

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    data = hass.data[DOMAIN].get(entry.entry_id)
    if data:
        coordinator: MiMoCoordinator = data.get("coordinator")
        if coordinator:
            await coordinator.stop()
        hass.data[DOMAIN].pop(entry.entry_id, None)

    if not hass.data[DOMAIN]:
        hass.services.async_remove(DOMAIN, SERVICE_CHAT)

    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle config entry update."""
    await hass.config_entries.async_reload(entry.entry_id)


async def _async_register_services(hass: HomeAssistant) -> None:
    """Register custom services for the integration."""

    async def handle_chat_service(call: ServiceCall) -> ServiceResponse:
        """Handle the chat service call."""
        from homeassistant.components import conversation as ha_conversation

        if not hass.data.get(DOMAIN):
            raise HomeAssistantError("未配置 MiMo Auto")

        entry_id = next(iter(hass.data[DOMAIN]))
        data = hass.data[DOMAIN][entry_id]
        coordinator: MiMoCoordinator = data.get("coordinator")

        if not coordinator or not coordinator.is_running:
            raise HomeAssistantError(ERROR_SERVER_NOT_RUNNING)

        message = call.data[ATTR_MESSAGE]

        conversation_input = ha_conversation.ConversationInput(
            text=message,
            conversation_id=call.data.get(ATTR_SESSION_ID),
            context=None,
            language="zh-cn",
            device_id=None,
        )

        # Find and use the conversation entity
        for entity in hass.data.get("entity_registry", {}).values():
            if entity.platform == DOMAIN and isinstance(entity, ha_conversation.ConversationEntity):
                response = await entity.async_process(conversation_input)
                return {
                    ATTR_RESPONSE: response.response.speech.get("plain", {}).get("speech", "")
                    if response.response.speech else "",
                    ATTR_SESSION_ID: response.conversation_id,
                }

        raise HomeAssistantError("未找到 MiMo Auto 对话实体")

    if hass.services.has_service(DOMAIN, SERVICE_CHAT):
        return

    hass.services.async_register(
        DOMAIN,
        SERVICE_CHAT,
        handle_chat_service,
        schema=SERVICE_CHAT_SCHEMA,
        supports_response=True,
    )
