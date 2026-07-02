"""Conversation entity for MiMo Auto — registered manually for claw_assistant compatibility."""
from __future__ import annotations

import logging

from homeassistant.components.conversation import ConversationEntity, ConversationInput, ConversationResult
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import MATCH_ALL
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .agent_impl import MiMoConversationAgent as AgentImpl
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


class MiMoConversationEntity(ConversationEntity):
    """Conversation entity for MiMo Auto."""

    _attr_has_entity_name = True

    def __init__(
        self,
        hass: HomeAssistant,
        agent_impl: AgentImpl,
        config_entry: ConfigEntry,
    ) -> None:
        """Initialize."""
        super().__init__(config_entry)
        self._hass = hass
        self._agent_impl = agent_impl
        self._attr_unique_id = f"{config_entry.entry_id}_conversation"
        self._attr_name = "MiMo Auto"

    @property
    def supported_languages(self) -> list[str]:
        return [MATCH_ALL]

    async def async_process(self, user_input: ConversationInput) -> ConversationResult:
        return await self._agent_impl.async_process(user_input)


async def async_register_entity(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    agent_impl: AgentImpl,
) -> None:
    """Create and register a conversation entity."""
    try:
        entity = MiMoConversationEntity(hass, agent_impl, config_entry)
        registry = er.async_get(hass)
        registry.async_get_or_create(
            "conversation",
            DOMAIN,
            entity.unique_id,
            suggested_object_id="mimo_auto",
            config_entry=config_entry,
        )
        # Add entity to HA's state machine
        entity.hass = hass
        entity.async_write_ha_state()
        _LOGGER.info("MiMo Auto conversation entity registered for claw_assistant")
    except Exception as err:
        _LOGGER.warning("Could not register conversation entity: %s", err)
