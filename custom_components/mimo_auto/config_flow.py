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

# Channel configuration keys
CONF_CHANNELS = "channels"
CONF_FEISHU = "feishu"
CONF_FEISHU_APP_ID = "app_id"
CONF_FEISHU_APP_SECRET = "app_secret"
CONF_FEISHU_ENABLED = "enabled"
CONF_WECHAT = "wechat"
CONF_WECHAT_CORP_ID = "corp_id"
CONF_WECHAT_AGENT_ID = "agent_id"
CONF_WECHAT_SECRET = "secret"
CONF_WECHAT_TOKEN = "token"
CONF_WECHAT_ENCODING_AES_KEY = "encoding_aes_key"
CONF_WECHAT_ENABLED = "enabled"
CONF_PERSONAL_WECHAT = "personal_wechat"
CONF_PERSONAL_WECHAT_ENABLED = "enabled"

# Step 1: Basic configuration
STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_PORT, default=DEFAULT_PORT): vol.Coerce(int),
        vol.Optional(CONF_MIMO_BIN, default=""): str,
        vol.Optional(CONF_AUTO_INSTALL, default=DEFAULT_AUTO_INSTALL): bool,
    }
)

# Step 2: Feishu configuration
STEP_FEISHU_DATA_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_FEISHU_ENABLED, default=False): bool,
        vol.Optional(CONF_FEISHU_APP_ID, default=""): str,
        vol.Optional(CONF_FEISHU_APP_SECRET, default=""): str,
    }
)

# Step 3: WeChat Work configuration
STEP_WECHAT_DATA_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_WECHAT_ENABLED, default=False): bool,
        vol.Optional(CONF_WECHAT_CORP_ID, default=""): str,
        vol.Optional(CONF_WECHAT_AGENT_ID, default=""): str,
        vol.Optional(CONF_WECHAT_SECRET, default=""): str,
        vol.Optional(CONF_WECHAT_TOKEN, default=""): str,
        vol.Optional(CONF_WECHAT_ENCODING_AES_KEY, default=""): str,
    }
)

# Step 4: Personal WeChat configuration
STEP_PERSONAL_WECHAT_DATA_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_PERSONAL_WECHAT_ENABLED, default=False): bool,
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

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._channels: dict[str, Any] = {}

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
                # Store basic config and move to channel setup
                self._basic_config = validated
                return await self.async_step_channels()

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
            description_placeholders={
                "default_port": str(DEFAULT_PORT),
                "default_bin": DEFAULT_MIMO_BIN,
            },
        )

    async def async_step_channels(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Handle the channel configuration step."""
        return await self.async_step_feishu()

    async def async_step_feishu(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Handle Feishu configuration step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            # Store feishu config
            self._channels[CONF_FEISHU] = {
                CONF_FEISHU_ENABLED: user_input.get(CONF_FEISHU_ENABLED, False),
                CONF_FEISHU_APP_ID: user_input.get(CONF_FEISHU_APP_ID, ""),
                CONF_FEISHU_APP_SECRET: user_input.get(CONF_FEISHU_APP_SECRET, ""),
            }
            # Move to WeChat config
            return await self.async_step_wechat()

        return self.async_show_form(
            step_id="feishu",
            data_schema=STEP_FEISHU_DATA_SCHEMA,
            errors=errors,
        )

    async def async_step_wechat(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Handle WeChat Work configuration step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            # Store wechat config
            self._channels[CONF_WECHAT] = {
                CONF_WECHAT_ENABLED: user_input.get(CONF_WECHAT_ENABLED, False),
                CONF_WECHAT_CORP_ID: user_input.get(CONF_WECHAT_CORP_ID, ""),
                CONF_WECHAT_AGENT_ID: user_input.get(CONF_WECHAT_AGENT_ID, ""),
                CONF_WECHAT_SECRET: user_input.get(CONF_WECHAT_SECRET, ""),
                CONF_WECHAT_TOKEN: user_input.get(CONF_WECHAT_TOKEN, ""),
                CONF_WECHAT_ENCODING_AES_KEY: user_input.get(CONF_WECHAT_ENCODING_AES_KEY, ""),
            }
            # Move to Personal WeChat config
            return await self.async_step_personal_wechat()

        return self.async_show_form(
            step_id="wechat",
            data_schema=STEP_WECHAT_DATA_SCHEMA,
            errors=errors,
        )

    async def async_step_personal_wechat(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Handle Personal WeChat configuration step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            # Store personal wechat config
            self._channels[CONF_PERSONAL_WECHAT] = {
                CONF_PERSONAL_WECHAT_ENABLED: user_input.get(CONF_PERSONAL_WECHAT_ENABLED, False),
            }

            # Create the config entry with all data
            await self.async_set_unique_id(DOMAIN)
            self._abort_if_unique_id_configured()

            data = {
                **self._basic_config,
                CONF_CHANNELS: self._channels,
            }

            return self.async_create_entry(
                title=DOMAIN_NAME,
                data=data,
            )

        return self.async_show_form(
            step_id="personal_wechat",
            data_schema=STEP_PERSONAL_WECHAT_DATA_SCHEMA,
            errors=errors,
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
