"""Conversation platform for MiMo Auto."""
from __future__ import annotations

import logging

from homeassistant.components.conversation import ConversationEntity, ConversationInput, ConversationResult
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import intent
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the MiMo Auto conversation entity."""
    data = hass.data[DOMAIN].get(config_entry.entry_id)
    if not data:
        _LOGGER.warning("No data for entry %s", config_entry.entry_id)
        return

    agent_impl = data.get("agent_impl")
    if not agent_impl:
        _LOGGER.warning("No agent_impl for entry %s", config_entry.entry_id)
        return

    entity = MiMoConversationEntity(hass, agent_impl, config_entry)
    async_add_entities([entity])
    _LOGGER.info("MiMo Auto conversation entity created")


class MiMoConversationEntity(ConversationEntity):
    """Conversation entity for MiMo Auto."""

    _attr_has_entity_name = True

    def __init__(
        self,
        hass: HomeAssistant,
        agent_impl,
        config_entry: ConfigEntry,
    ) -> None:
        """Initialize."""
        super().__init__()
        self._hass = hass
        self._agent_impl = agent_impl
        self._config_entry = config_entry
        self._attr_unique_id = f"{config_entry.entry_id}_conversation"
        self._attr_name = "MiMo Auto"

    @property
    def supported_languages(self) -> list[str] | str:
        return "*"

    async def async_process(self, user_input: ConversationInput) -> ConversationResult:
        """Process a conversation turn."""
        return await self._agent_impl.async_process(user_input)

    async def async_added_to_hass(self) -> None:
        """Register as conversation agent when entity is added."""
        await super().async_added_to_hass()
        from homeassistant.components import conversation as ha_conversation
        ha_conversation.async_set_agent(self.hass, self._config_entry, self)
        _LOGGER.info("MiMo Auto conversation agent registered for claw_assistant")
