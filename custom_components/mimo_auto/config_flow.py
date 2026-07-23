"""Config flow for MiMo Auto integration.

Simplified for Addon-bridge architecture:
- Server URL (mimo serve endpoint)
- WebUI URL (optional, for panel integration)
- Supervisor auto-detection toggle
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .const import (
    CONF_SERVER_URL,
    CONF_WEBUI_URL,
    CONF_USE_SUPERVISOR,
    DEFAULT_SERVER_URL,
    DEFAULT_WEBUI_URL,
    DEFAULT_USE_SUPERVISOR,
    DOMAIN,
    DOMAIN_NAME,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_SERVER_URL, default=DEFAULT_SERVER_URL): str,
        vol.Optional(CONF_WEBUI_URL, default=DEFAULT_WEBUI_URL): str,
        vol.Optional(CONF_USE_SUPERVISOR, default=DEFAULT_USE_SUPERVISOR): bool,
    }
)


class MiMoAutoConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for MiMo Auto."""

    VERSION = 2

    async def async_step_reauth(
        self, user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Handle reauthentication (migrate old config)."""
        return await self.async_step_reconfigure(user_input)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Handle reconfiguration of an existing entry."""
        errors: dict[str, str] = {}
        entry = self._get_reconfigure_entry()

        defaults = {
            CONF_SERVER_URL: entry.data.get(CONF_SERVER_URL, DEFAULT_SERVER_URL),
            CONF_WEBUI_URL: entry.data.get(CONF_WEBUI_URL, DEFAULT_WEBUI_URL),
            CONF_USE_SUPERVISOR: entry.data.get(CONF_USE_SUPERVISOR, DEFAULT_USE_SUPERVISOR),
        }

        if user_input is not None:
            # Validate connection
            try:
                from .coordinator import MiMoCoordinator
                coordinator = MiMoCoordinator(self.hass, user_input)
                connected = await coordinator.start()
                await coordinator.stop()

                if not connected:
                    errors["base"] = "cannot_connect"
            except Exception as err:
                _LOGGER.exception("Unexpected error during setup: %s", err)
                errors["base"] = "unknown"

            if not errors:
                return self.async_update_reload_and_abort(
                    entry,
                    data=user_input,
                )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SERVER_URL, default=defaults[CONF_SERVER_URL]): str,
                    vol.Optional(CONF_WEBUI_URL, default=defaults[CONF_WEBUI_URL]): str,
                    vol.Optional(CONF_USE_SUPERVISOR, default=defaults[CONF_USE_SUPERVISOR]): bool,
                }
            ),
            errors=errors,
            description_placeholders={
                "default_server_url": DEFAULT_SERVER_URL,
            },
        )

    async def async_step_user(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            await self.async_set_unique_id(DOMAIN)
            self._abort_if_unique_id_configured()

            # Validate connection
            try:
                from .coordinator import MiMoCoordinator
                coordinator = MiMoCoordinator(self.hass, user_input)
                connected = await coordinator.start()
                await coordinator.stop()

                if not connected:
                    errors["base"] = "cannot_connect"
            except Exception as err:
                _LOGGER.exception("Unexpected error during setup: %s", err)
                errors["base"] = "unknown"

            if not errors:
                return self.async_create_entry(
                    title=DOMAIN_NAME,
                    data=user_input,
                )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
            description_placeholders={
                "default_server_url": DEFAULT_SERVER_URL,
            },
        )

    async def async_step_import(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Handle import from configuration.yaml."""
        return await self.async_step_user(user_input)
