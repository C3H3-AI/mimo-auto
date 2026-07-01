"""Conversation entity platform for MiMo Auto."""
from __future__ import annotations

import logging

from homeassistant.components.conversation import ConversationEntity, ConversationInput, ConversationResult
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import MATCH_ALL
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_platform

from .agent_impl import MiMoConversationAgent as AgentImpl
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: entity_platform.AddEntitiesCallback,
) -> None:
    """Set up the MiMo Auto conversation entity."""
    data = hass.data[DOMAIN].get(config_entry.entry_id)
    if data is None:
        _LOGGER.error("No data found for entry %s", config_entry.entry_id)
        return

    coordinator = data.get("coordinator")
    agent_impl = data.get("agent_impl")
    if agent_impl is None:
        _LOGGER.error("No agent_impl found for entry %s", config_entry.entry_id)
        return

    entity = MiMoConversationEntity(hass, agent_impl, config_entry)
    async_add_entities([entity])
    _LOGGER.info("MiMo Auto conversation entity added for %s", config_entry.entry_id)


class MiMoConversationEntity(ConversationEntity):
    """Conversation entity for MiMo Auto."""

    _attr_has_entity_name = True

    def __init__(
        self,
        hass: HomeAssistant,
        agent_impl: AgentImpl,
        config_entry: ConfigEntry,
    ) -> None:
        """Initialize the conversation entity."""
        super().__init__(config_entry)
        self._hass = hass
        self._agent_impl = agent_impl
        self._attr_unique_id = f"{config_entry.entry_id}_conversation"
        self._attr_name = "MiMo Auto"

    @property
    def supported_languages(self) -> list[str]:
        """Return supported languages."""
        return [MATCH_ALL]

    async def async_process(self, user_input: ConversationInput) -> ConversationResult:
        """Process a conversation turn."""
        return await self._agent_impl.async_process(user_input)
