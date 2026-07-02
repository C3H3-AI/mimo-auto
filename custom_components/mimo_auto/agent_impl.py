"""Conversation agent for MiMo Auto integration."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import aiohttp
from async_timeout import timeout

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import intent

from .const import (
    API_CREATE_SESSION,
    API_SEND_MESSAGE,
    DOMAIN,
    ERROR_CONNECTION_FAILED,
    ERROR_SERVER_NOT_RUNNING,
    ERROR_TIMEOUT,
    MESSAGE_TIMEOUT_SECONDS,
)
from .coordinator import MiMoCoordinator

_LOGGER = logging.getLogger(__name__)

# Lazy conversation module (avoids startup circular import timeout)
_conv_mod = None


def _conv():
    global _conv_mod
    if _conv_mod is None:
        from homeassistant.components import conversation as c
        _conv_mod = c
    return _conv_mod


# Use a placeholder base class to avoid importing conversation at module level
# The real AbstractConversationAgent is resolved lazily
_conversation_base = None


def _get_conversation_base():
    global _conversation_base
    if _conversation_base is None:
        from homeassistant.components.conversation import AbstractConversationAgent
        _conversation_base = AbstractConversationAgent
    return _conversation_base


class MiMoConversationAgent:
    """Conversation agent for MiMo Auto.

    This agent communicates with the local MiMo Auto server via its HTTP API.
    For each conversation turn, a new session is created on the server.
    The agent sends the user message, streams the SSE response, and returns
    the AI's reply.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: MiMoCoordinator,
        config_entry: ConfigEntry,
    ) -> None:
        """Initialize the conversation agent.

        Args:
            hass: The HomeAssistant instance.
            coordinator: The MiMoCoordinator managing the server process.
            config_entry: The config entry for this integration.
        """
        self._hass = hass
        self._coordinator = coordinator
        self._config_entry = config_entry

    @property
    def supported_languages(self) -> list[str]:
        """Return a list of supported languages."""
        return ["zh-cn", "zh", "en", "*"]

    async def async_process(
        self,
        user_input: _conv().ConversationInput,
    ) -> _conv().ConversationResult:
        """Process a conversation turn.

        Creates a new session on the MiMo server, sends the user's message,
        and returns the AI's response.

        Args:
            user_input: The conversation input from HA.

        Returns:
            A ConversationResult containing the AI's reply.
        """
        language = user_input.language

        if not self._coordinator.is_running:
            _LOGGER.warning("MiMo server is not running")
            intent_resp = intent.IntentResponse(language)
            intent_resp.async_set_speech(ERROR_SERVER_NOT_RUNNING)
            return _conv().ConversationResult(
                response=intent_resp,
            )

        message_text = user_input.text
        if not message_text or not message_text.strip():
            intent_resp = intent.IntentResponse(language)
            intent_resp.async_set_speech("Please provide a message to send to MiMo Auto.")
            return _conv().ConversationResult(
                response=intent_resp,
            )

        try:
            async with timeout(MESSAGE_TIMEOUT_SECONDS):
                # Step 1: Create a new session
                session_id = await self._create_session()
                if session_id is None:
                    intent_resp = intent.IntentResponse(language)
                    intent_resp.async_set_speech(ERROR_CONNECTION_FAILED)
                    return _conv().ConversationResult(
                        response=intent_resp,
                    )

                # Step 2: Send the message and get the response
                reply = await self._send_message(session_id, message_text)
                if reply is None:
                    intent_resp = intent.IntentResponse(language)
                    intent_resp.async_set_speech("MiMo Auto returned an empty response.")
                    return _conv().ConversationResult(
                        response=intent_resp,
                    )

                # Build intent response with extra data
                intent_response = intent.IntentResponse(language)
                intent_response.async_set_speech(reply)

                # Return conversation result
                return _conv().ConversationResult(
                    response=intent_response,
                    conversation_id=session_id,
                )

        except asyncio.TimeoutError:
            _LOGGER.error("MiMo conversation timed out after %d seconds", MESSAGE_TIMEOUT_SECONDS)
            intent_resp = intent.IntentResponse(language)
            intent_resp.async_set_speech(ERROR_TIMEOUT)
            return _conv().ConversationResult(response=intent_resp)
        except (aiohttp.ClientError, OSError) as err:
            _LOGGER.error("MiMo connection error: %s", err)
            intent_resp = intent.IntentResponse(language)
            intent_resp.async_set_speech(ERROR_CONNECTION_FAILED)
            return _conv().ConversationResult(response=intent_resp)
        except Exception as err:
            _LOGGER.exception("Unexpected error processing conversation: %s", err)
            intent_resp = intent.IntentResponse(language)
            intent_resp.async_set_speech(f"An unexpected error occurred: {err}")
            return _conv().ConversationResult(response=intent_resp)

    async def _create_session(self) -> str | None:
        """Create a new session on the MiMo server.

        Sends a POST request to /session to create a new conversation session.

        Returns:
            The session ID string, or None if creation failed.
        """
        url = f"{self._coordinator.server_url}{API_CREATE_SESSION}"
        _LOGGER.debug("Creating MiMo session: POST %s", url)

        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(url, json={}, timeout=10) as response:
                    if response.status != 200:
                        body = await response.text()
                        _LOGGER.error(
                            "Failed to create session: HTTP %d - %s",
                            response.status,
                            body,
                        )
                        return None

                    data = await response.json()
                    session_id = data.get("id")
                    if not session_id:
                        _LOGGER.error("Session response missing 'id' field: %s", data)
                        return None

                    _LOGGER.debug("Created MiMo session: %s", session_id)
                    return session_id
            except (aiohttp.ClientError, asyncio.TimeoutError) as err:
                _LOGGER.error("Error creating session: %s", err)
                return None

    async def _send_message(self, session_id: str, message: str) -> str | None:
        """Send a message to the MiMo server and collect the SSE response.

        Args:
            session_id: The session ID to send the message to.
            message: The message text content.

        Returns:
            The AI response text, or None if an error occurred.
        """
        url = f"{self._coordinator.server_url}{API_SEND_MESSAGE.format(session_id=session_id)}"
        _LOGGER.debug("Sending message to MiMo session %s", session_id)

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    json={
                        "message": message,
                        "parts": [{"type": "text", "text": message}],
                    },
                    timeout=aiohttp.ClientTimeout(total=MESSAGE_TIMEOUT_SECONDS),
                ) as response:
                    if response.status != 200:
                        body = await response.text()
                        _LOGGER.error(
                            "Failed to send message: HTTP %d - %s",
                            response.status,
                            body,
                        )
                        return None

                    # Parse JSON stream response (chunked JSON objects)
                    reply = await self._parse_json_stream(response)

                    if reply:
                        _LOGGER.debug(
                            "Received response from MiMo (session %s): %d chars",
                            session_id,
                            len(reply),
                        )
                    else:
                        _LOGGER.warning(
                            "Empty response from MiMo (session %s)", session_id
                        )

                    return reply
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            _LOGGER.error("Error sending message: %s", err)
            return None

    async def _parse_json_stream(self, response: aiohttp.ClientResponse) -> str | None:
        """Parse a chunked JSON stream from the MiMo server response.

        The MiMo server returns a stream of JSON objects (one per chunk)
        with Content-Type: application/json and Transfer-Encoding: chunked.
        Each JSON object has the structure:
            {"info": {...}, "parts": [{"type": "text", "text": "..."}, ...]}

        This parser:
        - Collects all text parts from the final assistant message
        - Ignores tool calls, reasoning, step-start/step-finish parts
        - Handles incomplete JSON chunks by buffering

        Args:
            response: The HTTP response with JSON chunked encoding.

        Returns:
            The concatenated user-facing response text, or None if no text found.
        """
        buffer = ""
        collected_texts: list[str] = []
        seen_ids: set[str] = set()

        async for chunk_bytes in response.content:
            if not chunk_bytes:
                continue
            chunk = chunk_bytes.decode("utf-8", errors="replace")
            buffer += chunk

            # Try to extract complete JSON objects from the buffer
            while True:
                buffer = buffer.lstrip()
                if not buffer:
                    break

                try:
                    obj, idx = json.JSONDecoder().raw_decode(buffer)
                    buffer = buffer[idx:]

                    if not isinstance(obj, dict):
                        continue

                    # Only process assistant messages that finished (finish: "stop")
                    info = obj.get("info", {})
                    parts = obj.get("parts", [])
                    if not isinstance(parts, list):
                        continue

                    finish = info.get("finish")
                    role = info.get("role")

                    # Only collect text from the final "stop" assistant message
                    if role != "assistant" or finish != "stop":
                        continue

                    msg_id = info.get("id", "")
                    if msg_id in seen_ids:
                        continue
                    seen_ids.add(msg_id)

                    for part in parts:
                        if not isinstance(part, dict):
                            continue
                        ptype = part.get("type")
                        # Only extract user-facing text parts
                        if ptype == "text":
                            text = part.get("text", "").strip()
                            if text:
                                collected_texts.append(text)

                except json.JSONDecodeError:
                    # Need more data, wait for next chunk
                    break

        if collected_texts:
            return "\n".join(collected_texts)
        return None
