"""Config flow for MiMo Auto integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .const import (
    CONF_AUTO_INSTALL,
    CONF_MIMO_BIN,
    CONF_PORT,
    DEFAULT_AUTO_INSTALL,
    DEFAULT_MIMO_BIN,
    DEFAULT_PORT,
    DOMAIN,
    DOMAIN_NAME,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_PORT, default=DEFAULT_PORT): vol.Coerce(int),
        vol.Optional(CONF_MIMO_BIN, default=""): str,
        vol.Optional(CONF_AUTO_INSTALL, default=DEFAULT_AUTO_INSTALL): bool,
    }
)


async def _validate_config(data: dict[str, Any]) -> dict[str, Any]:
    """Validate the user input and return validated data.

    Args:
        data: The user input data to validate.

    Returns:
        Validated configuration data.

    Raises:
        InvalidPort: If the port number is invalid.
    """
    port = data[CONF_PORT]
    if not isinstance(port, int) or port < 1024 or port > 65535:
        raise InvalidPort(
            f"Port must be between 1024 and 65535, got {port}"
        )

    mimo_bin = data.get(CONF_MIMO_BIN, "")
    if mimo_bin:
        # Basic validation - a path was provided
        if not mimo_bin.strip():
            data[CONF_MIMO_BIN] = ""
    else:
        data[CONF_MIMO_BIN] = DEFAULT_MIMO_BIN

    return data


class MiMoAutoConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for MiMo Auto."""

    VERSION = 1

    async def async_step_user(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Handle the initial step of the config flow.

        Args:
            user_input: Optional user input from the form.

        Returns:
            The config flow result.
        """
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                validated = await _validate_config(user_input)
            except InvalidPort as err:
                errors["base"] = "invalid_port"
                _LOGGER.error("Validation error: %s", err)
            except Exception as err:
                errors["base"] = "unknown"
                _LOGGER.exception("Unexpected error: %s", err)
            else:
                await self.async_set_unique_id(DOMAIN)
                self._abort_if_unique_id_configured()

                return self.async_create_entry(
                    title=DOMAIN_NAME,
                    data=validated,
                )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
            description_placeholders={
                "default_port": str(DEFAULT_PORT),
                "default_bin": DEFAULT_MIMO_BIN,
            },
        )

    async def async_step_import(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Handle import from configuration.yaml.

        Args:
            user_input: Optional imported configuration data.

        Returns:
            The config flow result.
        """
        return await self.async_step_user(user_input)


class InvalidPort(HomeAssistantError):
    """Error to indicate an invalid port number."""
