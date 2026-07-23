"""Conversation platform for MiMo Auto.

Provides a conversation agent that talks to the MiMo Code Addon's
`mimo serve` API. Supports session reuse, tool calls via HA services,
and reasoning thought display.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import aiohttp
from async_timeout import timeout

from homeassistant.components.conversation import (
    ConversationEntity,
    ConversationEntityFeature,
    ConversationInput,
    ConversationResult,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import intent
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    API_CREATE_SESSION,
    API_SEND_MESSAGE,
    DOMAIN,
    DOMAIN_NAME,
    ERROR_CONNECTION_FAILED,
    ERROR_SERVER_NOT_RUNNING,
    ERROR_TIMEOUT,
    MESSAGE_TIMEOUT_SECONDS,
)
from .coordinator import MiMoCoordinator

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

    coordinator: MiMoCoordinator = data.get("coordinator")
    if not coordinator:
        _LOGGER.warning("No coordinator for entry %s", config_entry.entry_id)
        return

    entity = MiMoConversationEntity(hass, coordinator, config_entry)
    async_add_entities([entity])


class MiMoConversationEntity(ConversationEntity):
    """Conversation entity for MiMo Auto.

    Communicates with the Addon's `mimo serve` API for AI responses.
    """

    _attr_has_entity_name = True
    _attr_supported_features = ConversationEntityFeature.CONTROL

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: MiMoCoordinator,
        config_entry: ConfigEntry,
    ) -> None:
        """Initialize."""
        super().__init__()
        self._hass = hass
        self._coordinator = coordinator
        self._config_entry = config_entry
        self._attr_unique_id = f"{config_entry.entry_id}_conversation"
        self._attr_name = "MiMo Auto"
        self._attr_device_info = dr.DeviceInfo(
            identifiers={(DOMAIN, config_entry.entry_id)},
            name=DOMAIN_NAME,
            manufacturer="MiMo",
            model="MiMo Code Addon",
        )

        # Session mapping: conversation_id -> mimo_session_id
        self._session_map: dict[str, str] = {}
        self._last_session_id: str | None = None
        self._last_response: str | None = None
        self._store = None
        self._load_session_map()

    @property
    def supported_languages(self) -> list[str] | str:
        return "*"

    def _load_session_map(self) -> None:
        """Load session map from HA storage."""
        try:
            from homeassistant.helpers.storage import Store
            self._store = Store(self._hass, 1, "mimo_auto_session_map")
            if self._store and hasattr(self._store, 'data') and self._store.data:
                self._session_map = self._store.data.get("session_map", {})
        except Exception:
            pass

    def _save_session_map(self) -> None:
        """Save session map to HA storage."""
        try:
            if self._store:
                self._hass.async_create_task(
                    self._store.async_save({"session_map": self._session_map})
                )
        except Exception:
            pass

    async def async_process(self, user_input: ConversationInput) -> ConversationResult:
        """Process a conversation turn."""
        language = user_input.language

        # Try to connect if not running (lazy connect on first use)
        if not self._coordinator.is_running:
            await self._coordinator.async_check_health()
            if not self._coordinator.is_running:
                intent_resp = intent.IntentResponse(language)
                intent_resp.async_set_speech(ERROR_SERVER_NOT_RUNNING)
                return ConversationResult(response=intent_resp)

        message_text = user_input.text
        if not message_text or not message_text.strip():
            intent_resp = intent.IntentResponse(language)
            intent_resp.async_set_speech("请输入消息。")
            return ConversationResult(response=intent_resp)

        # Try native HA device control first (fast path)
        native_result = await self._try_native_device_control(
            message_text, language, user_input.context, user_input.device_id,
        )
        if native_result:
            message_text = f"{message_text}\n\n[系统提示：已通过 Home Assistant 执行: {native_result}]"

        # Get or create session
        conversation_id = user_input.conversation_id
        session_id = await self._get_or_create_session(conversation_id)
        if session_id is None:
            intent_resp = intent.IntentResponse(language)
            intent_resp.async_set_speech(ERROR_CONNECTION_FAILED)
            return ConversationResult(response=intent_resp)

        try:
            async with timeout(MESSAGE_TIMEOUT_SECONDS):
                result = await self._send_message(session_id, message_text)
                if result is None:
                    intent_resp = intent.IntentResponse(language)
                    intent_resp.async_set_speech("MiMo 返回了空响应。")
                    return ConversationResult(response=intent_resp)

                reply_text = result.get("text") if isinstance(result, dict) else result

                self._last_session_id = session_id
                self._last_response = reply_text

                intent_response = intent.IntentResponse(language)
                intent_response.async_set_speech(reply_text or "")

                return ConversationResult(
                    response=intent_response,
                    conversation_id=conversation_id or session_id,
                )

        except asyncio.TimeoutError:
            if conversation_id and conversation_id in self._session_map:
                del self._session_map[conversation_id]
            intent_resp = intent.IntentResponse(language)
            intent_resp.async_set_speech(ERROR_TIMEOUT)
            return ConversationResult(response=intent_resp)
        except (aiohttp.ClientError, OSError) as err:
            _LOGGER.error("Connection error: %s", err)
            intent_resp = intent.IntentResponse(language)
            intent_resp.async_set_speech(ERROR_CONNECTION_FAILED)
            return ConversationResult(response=intent_resp)
        except Exception as err:
            _LOGGER.exception("Unexpected error: %s", err)
            intent_resp = intent.IntentResponse(language)
            intent_resp.async_set_speech(f"错误: {err}")
            return ConversationResult(response=intent_resp)

    async def _try_native_device_control(
        self, text: str, language: str | None, context, device_id: str | None,
    ) -> str | None:
        """Try HA's native conversation agent as fast path for device control."""
        if not text or len(text) > 200 or "\n" in text:
            return None
        try:
            from homeassistant.components.conversation import async_converse
            from homeassistant.components.conversation.const import HOME_ASSISTANT_AGENT

            native_result = await async_converse(
                self._hass, text=text, conversation_id=None,
                context=context, language=language or self._hass.config.language,
                agent_id=HOME_ASSISTANT_AGENT, device_id=device_id,
            )
            if native_result and native_result.response:
                speech = native_result.response.speech
                if isinstance(speech, dict):
                    plain = speech.get("plain", {})
                    if isinstance(plain, dict):
                        result_text = plain.get("speech", "")
                        if result_text and any(
                            kw in result_text.lower()
                            for kw in ["done", "executed", "turned", "set", "activated"]
                        ):
                            return result_text
        except Exception:
            pass
        return None

    async def _get_or_create_session(self, conversation_id: str | None) -> str | None:
        """Get existing session or create new one."""
        if conversation_id and conversation_id in self._session_map:
            return self._session_map[conversation_id]

        if not conversation_id and self._last_session_id:
            return self._last_session_id

        session_id = await self._create_session()
        if session_id and conversation_id:
            self._session_map[conversation_id] = session_id
            self._save_session_map()
        return session_id

    async def _create_session(self) -> str | None:
        """Create a new session on the Addon's mimo serve."""
        url = f"{self._coordinator.server_url}{API_CREATE_SESSION}"
        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(url, json={}, timeout=10) as response:
                    if response.status != 200:
                        return None
                    data = await response.json()
                    return data.get("id")
            except (aiohttp.ClientError, asyncio.TimeoutError):
                return None

    async def _send_message(self, session_id: str, message: str) -> dict | None:
        """Send a message to the Addon's mimo serve and get response."""
        url = f"{self._coordinator.server_url}{API_SEND_MESSAGE.format(session_id=session_id)}"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    json={"parts": [{"type": "text", "text": message}]},
                    timeout=aiohttp.ClientTimeout(total=MESSAGE_TIMEOUT_SECONDS),
                ) as response:
                    if response.status != 200:
                        return None
                    return await self._parse_json_stream(response)
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return None

    async def _parse_json_stream(self, response: aiohttp.ClientResponse) -> dict | None:
        """Parse a chunked JSON stream from mimo serve response."""
        buffer = ""
        collected_texts: list[str] = []

        async for chunk_bytes in response.content:
            if not chunk_bytes:
                continue
            chunk = chunk_bytes.decode("utf-8", errors="replace")
            buffer += chunk

            while True:
                buffer = buffer.lstrip()
                if not buffer:
                    break
                try:
                    obj, idx = json.JSONDecoder().raw_decode(buffer)
                    buffer = buffer[idx:]
                    if not isinstance(obj, dict):
                        continue
                    info = obj.get("info", {})
                    parts = obj.get("parts", [])
                    if not isinstance(parts, list):
                        continue
                    if info.get("role") != "assistant" or info.get("finish") != "stop":
                        continue
                    for part in parts:
                        if isinstance(part, dict) and part.get("type") == "text":
                            text = part.get("text", "").strip()
                            if text:
                                collected_texts.append(text)
                except json.JSONDecodeError:
                    break

        return {"text": "\n".join(collected_texts) if collected_texts else None}

    async def async_added_to_hass(self) -> None:
        """Register as conversation agent when entity is added."""
        await super().async_added_to_hass()
        from homeassistant.components import conversation as ha_conversation
        ha_conversation.async_set_agent(self.hass, self._config_entry, self)
        _LOGGER.info("MiMo Auto conversation agent registered")
