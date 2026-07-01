"""Init for MiMo Auto integration.

This component integrates MiMo Auto AI into Home Assistant by starting
a local `mimo serve` subprocess and providing a conversation agent
that communicates with it via HTTP API.
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import (
    ATTR_MESSAGE,
    ATTR_SESSION_ID,
    CONF_AUTO_INSTALL,
    CONF_MIMO_BIN,
    CONF_PORT,
    DEFAULT_AUTO_INSTALL,
    DEFAULT_MIMO_BIN,
    DEFAULT_PORT,
    DOMAIN,
    DOMAIN_NAME,
    ERROR_SERVER_NOT_RUNNING,
    SERVICE_CHAT,
)
from .agent_impl import MiMoConversationAgent
from .coordinator import MiMoCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.CONVERSATION]

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Optional(CONF_PORT, default=DEFAULT_PORT): cv.port,
                vol.Optional(CONF_MIMO_BIN, default=DEFAULT_MIMO_BIN): cv.string,
            }
        )
    },
    extra=vol.ALLOW_EXTRA,
)

# Service schema for chat service
SERVICE_CHAT_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_MESSAGE): cv.string,
        vol.Optional(ATTR_SESSION_ID): cv.string,
    }
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the MiMo Auto integration from configuration.yaml.

    Args:
        hass: The HomeAssistant instance.
        config: The configuration dictionary.

    Returns:
        True if setup was successful.
    """
    if DOMAIN not in config:
        return True

    # Forward to config flow for import
    hass.async_create_task(
        hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": "import"},
            data=config[DOMAIN],
        )
    )

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up MiMo Auto from a config entry.

    Initializes the coordinator (starts the mimo server subprocess),
    registers the conversation agent, and sets up services.

    Args:
        hass: The HomeAssistant instance.
        entry: The config entry to set up.

    Returns:
        True if setup was successful.

    Raises:
        ConfigEntryNotReady: If the mimo server could not be started.
    """
    hass.data.setdefault(DOMAIN, {})

    config = {
        CONF_PORT: entry.data.get(CONF_PORT, DEFAULT_PORT),
        CONF_MIMO_BIN: entry.data.get(CONF_MIMO_BIN, DEFAULT_MIMO_BIN),
        CONF_AUTO_INSTALL: entry.data.get(CONF_AUTO_INSTALL, DEFAULT_AUTO_INSTALL),
    }

    # Create coordinator
    coordinator = MiMoCoordinator(hass, config)

    # Start the server
    _LOGGER.info("Starting MiMo server for config entry %s", entry.entry_id)
    server_started = await coordinator.start_server()
    if not server_started:
        _LOGGER.error("Failed to start MiMo server for config entry %s", entry.entry_id)
        raise ConfigEntryNotReady("Could not start MiMo Auto server")

    # Store coordinator and agent in hass.data
    agent_impl = MiMoConversationAgent(hass, coordinator, entry)
    hass.data[DOMAIN][entry.entry_id] = {
        "coordinator": coordinator,
        "agent_impl": agent_impl,
    }

    # Register services
    await _async_register_services(hass)

    # Register MiMo Chat panel
    await _async_register_panel(hass)

    # Register update listener for config entry changes
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    _LOGGER.info(
        "MiMo Auto integration set up successfully (entry: %s)", entry.entry_id
    )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry.

    Stops the mimo server and removes the conversation agent.

    Args:
        hass: The HomeAssistant instance.
        entry: The config entry to unload.

    Returns:
        True if unload was successful.
    """
    _LOGGER.info("Unloading MiMo Auto integration (entry: %s)", entry.entry_id)

    data = hass.data[DOMAIN].get(entry.entry_id)
    if data is None:
        return True

    # Stop the coordinator (kills the mimo process)
    coordinator: MiMoCoordinator = data.get("coordinator")
    if coordinator:
        await coordinator.stop_server()

    # Remove entry data
    hass.data[DOMAIN].pop(entry.entry_id, None)

    # Clean up services if no entries remain
    if not hass.data[DOMAIN]:
        _LOGGER.debug("No more MiMo entries, removing services")
        hass.services.async_remove(DOMAIN, SERVICE_CHAT)

    _LOGGER.info("MiMo Auto integration unloaded successfully")
    return True


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle removal of a config entry.

    Called when the user removes the integration from the UI.
    Any cleanup beyond async_unload_entry should happen here.

    Args:
        hass: The HomeAssistant instance.
        entry: The config entry being removed.
    """
    _LOGGER.info("Removing MiMo Auto integration (entry: %s)", entry.entry_id)
    # Most cleanup is handled by async_unload_entry


async def _async_update_listener(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    """Handle config entry update.

    Restarts the coordinator with the new configuration.

    Args:
        hass: The HomeAssistant instance.
        entry: The updated config entry.
    """
    _LOGGER.info("Reconfiguring MiMo Auto (entry: %s)", entry.entry_id)
    await hass.config_entries.async_reload(entry.entry_id)


async def _async_register_services(hass: HomeAssistant) -> None:
    """Register custom services for the integration.

    Registers the `chat` service that allows sending messages to
    MiMo Auto from automations and scripts.

    Args:
        hass: The HomeAssistant instance.
    """

    async def handle_chat_service(call: ServiceCall) -> ServiceResponse:
        """Handle the chat service call."""
        from homeassistant.components import conversation as ha_conversation

        # Find the first active entry
        if not hass.data.get(DOMAIN):
            raise HomeAssistantError("No MiMo Auto instance is configured")

        entry_id = next(iter(hass.data[DOMAIN]))
        data = hass.data[DOMAIN][entry_id]
        agent: MiMoConversationAgent | None = data.get("agent_impl")
        coordinator: MiMoCoordinator | None = data.get("coordinator")

        if agent is None or coordinator is None:
            raise HomeAssistantError("MiMo Auto is not properly initialized")

        if not coordinator.is_running:
            raise HomeAssistantError(ERROR_SERVER_NOT_RUNNING)

        message = call.data[ATTR_MESSAGE]

        # Create a conversation input
        conversation_input = ha_conversation.ConversationInput(
            text=message,
            conversation_id=call.data.get(ATTR_SESSION_ID),
            context=None,
            language="en",
            device_id=None,
        )

        # Process the conversation
        response = await agent.async_process(conversation_input)

        return {
            ATTR_RESPONSE: response.response.speech.get("plain", {}).get("speech", "") if response.response.speech else "",
            ATTR_SESSION_ID: response.conversation_id,
        }

    # Register service only if not already registered
    if hass.services.has_service(DOMAIN, SERVICE_CHAT):
        return

    hass.services.async_register(
        DOMAIN,
        SERVICE_CHAT,
        handle_chat_service,
        schema=SERVICE_CHAT_SCHEMA,
        supports_response=True,
    )
