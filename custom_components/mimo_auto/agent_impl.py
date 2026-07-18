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

# Native HA intent agent constants (lazy-loaded)
_HA_AGENT_ID = "homeassistant"

# ai_hub domain
DOMAIN_AI_HUB = "ai_hub"

from .const import (
    API_CREATE_SESSION,
    API_SEND_MESSAGE,
    API_GET_MESSAGES,
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
    Supports session reuse for maintaining conversation context across turns.
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
        # Session mapping: conversation_id -> mimo_session_id
        self._session_map: dict[str, str] = {}
        self._last_session_id: str | None = None
        self._last_model: str | None = None
        self._last_response: str | None = None
        # Storage for persistent session mapping
        self._store = None
        # Device states cache
        self._device_states_cache: str | None = None
        self._device_states_cache_time: float = 0
        # Load session map from storage
        self._load_session_map()

    def _load_session_map(self) -> None:
        """Load session map from HA storage."""
        try:
            from homeassistant.helpers.storage import Store
            self._store = Store(self._hass, 1, "mimo_auto_session_map")
            if self._store and hasattr(self._store, 'data') and self._store.data:
                self._session_map = self._store.data.get("session_map", {})
                _LOGGER.debug("Loaded session map with %d entries", len(self._session_map))
        except Exception as err:
            _LOGGER.debug("Could not load session map: %s", err)

    def _save_session_map(self) -> None:
        """Save session map to HA storage."""
        try:
            if self._store:
                self._hass.async_create_task(
                    self._store.async_save({"session_map": self._session_map})
                )
        except Exception as err:
            _LOGGER.debug("Could not save session map: %s", err)

    @property
    def supported_languages(self) -> list[str]:
        """Return a list of supported languages."""
        return ["zh-cn", "zh", "en", "*"]

    @property
    def state_attributes(self) -> dict[str, Any]:
        """Return entity state attributes for Claw Assistant."""
        attrs = {
            "entity": "mimo_auto",
            "last_used_agent": "mimo_auto",
        }
        if self._last_session_id:
            attrs["session_id"] = self._last_session_id
        if self._last_model:
            attrs["model"] = self._last_model
        if self._last_response:
            attrs["last_response"] = self._last_response[:200]
        return attrs

    def _build_message_with_context(self, message: str, device_states: str = "", user_activity: str = "") -> str:
        """Build message with HA context injection.

        Injects smart home context into the message to help the AI
        understand the user is interacting with Home Assistant.

        Args:
            message: The original user message.
            device_states: Optional device states to include.
            user_activity: Optional user activity to include.

        Returns:
            Message with context prefix.
        """
        # Smart home context prefix
        context = """You are a smart home assistant. You can query and control devices in Home Assistant.

Device states are provided below. Use them to answer questions and control devices.
Available services: light.turn_on/turn_off, switch.turn_on/turn_off, climate.set_temperature, automation.trigger, scene.turn_on, script.run, notify.notify

Respond in the same language as the user. Keep responses concise.

"""
        if device_states:
            context += "## Current Device States\n" + device_states + "\n"

        if user_activity:
            context += user_activity + "\n"

        # Add available services (simplified)
        try:
            services = self._hass.services.async_services()
            relevant_domains = ["light", "switch", "climate", "automation", "scene", "script"]
            service_count = sum(1 for d in relevant_domains if d in services)
            if service_count > 0:
                context += f"## Available Services\nYou can execute: light, switch, climate, automation, scene, script services.\n\n"
        except Exception:
            pass

        return context + message

    def _fire_thought_to_claw(self, thought: str) -> None:
        """Fire reasoning thought to Claw Assistant for display.

        Args:
            thought: The reasoning content to display.
        """
        try:
            try:
                from custom_components.claw_assistant.runtime.core.state import set_current_thought
                from custom_components.claw_assistant.runtime.core.events import fire_live_progress

                set_current_thought(self._hass, thought)
                fire_live_progress(self._hass, thought=thought, phase="thinking")
                _LOGGER.debug("Fired thought to Claw: %s", thought[:100])
            except ImportError:
                _LOGGER.debug("Claw not installed, skipping thought fire")
            except Exception as err:
                _LOGGER.debug("Failed to fire thought to Claw: %s", err)
        except Exception as err:
            _LOGGER.debug("Error firing thought: %s", err)

    async def _get_device_states(self) -> str:
        """Get current device states from Home Assistant.

        Returns:
            A formatted string of device states, or empty string if unavailable.
        """
        import time

        # Use cached states if available (cache for 30 seconds)
        current_time = time.time()
        if self._device_states_cache and (current_time - self._device_states_cache_time) < 30:
            return self._device_states_cache

        try:
            # Use HA's state machine directly (no HTTP needed)
            states = self._hass.states.async_all()

            # Filter to only include devices with useful states
            device_states = []
            for state in states:
                entity_id = state.entity_id
                state_val = state.state
                friendly_name = state.attributes.get("friendly_name", "")

                # Only include relevant device types
                if any(entity_id.startswith(prefix) for prefix in [
                    "light.", "switch.", "climate.", "media_player.",
                    "sensor.", "binary_sensor.", "cover.", "fan.",
                    "lock.", "vacuum.", "humidifier."
                ]):
                    device_states.append(f"- {friendly_name or entity_id}: {state_val}")

            if device_states:
                # Prioritize important devices: lights, switches, climate first
                important = [s for s in device_states if any(s.startswith(p) for p in ["- ", "light.", "switch.", "climate."])]
                others = [s for s in device_states if not any(s.startswith(p) for p in ["- ", "light.", "switch.", "climate."])]
                sorted_states = important + others
                result = "\nCurrent device states:\n" + "\n".join(sorted_states) + "\n\n"
                # Cache the result
                self._device_states_cache = result
                self._device_states_cache_time = current_time
                return result
        except Exception as err:
            _LOGGER.debug("Could not get device states: %s", err)

        return ""

    def _get_user_activity(self) -> str:
        """Get recent user activity from Claw Assistant.

        Returns:
            A formatted string of recent user activities, or empty string if unavailable.
        """
        try:
            from custom_components.claw_assistant.runtime.storage.user_activity import (
                build_activity_prompt_section,
            )
            return build_activity_prompt_section(self._hass)
        except ImportError:
            # Claw not installed
            return ""
        except Exception as err:
            _LOGGER.debug("Could not get user activity: %s", err)
            return ""

    async def _execute_ha_service(self, domain: str, service: str, data: dict[str, Any] = None) -> str:
        """Execute a Home Assistant service.

        Args:
            domain: Service domain (e.g., light, switch, automation)
            service: Service name (e.g., turn_on, turn_off)
            data: Service data (e.g., entity_id, brightness)

        Returns:
            Service execution result or error message.
        """
        try:
            await self._hass.services.async_call(
                domain,
                service,
                data or {},
                blocking=True,
                return_response=True,
            )
            return f"Successfully executed {domain}.{service}"
        except Exception as err:
            _LOGGER.error("Failed to execute %s.%s: %s", domain, service, err)
            return f"Error: {str(err)}"

    async def _try_native_device_control(
        self,
        text: str,
        language: str | None,
        context,
        device_id: str | None,
    ) -> str | None:
        """Try to handle device control via HA's native conversation agent.

        Uses the built-in 'homeassistant' agent which recognizes intents
        like HassTurnOn, HassTurnOff, HassLightSet, etc.
        Returns the speech result if a device was controlled, None otherwise.

        This is a lightweight fast-path — if the text doesn't match any
        native intent, it fails silently and we fall through to MiMo AI.
        """
        # Short-circuit for complex/multi-line messages that won't match
        if not text or len(text) > 200 or "\n" in text:
            return None

        try:
            from homeassistant.components.conversation import async_converse
            from homeassistant.components.conversation.const import (
                HOME_ASSISTANT_AGENT,
            )

            native_result = await async_converse(
                self._hass,
                text=text,
                conversation_id=None,  # fresh context for device control
                context=context,
                language=language or self._hass.config.language,
                agent_id=HOME_ASSISTANT_AGENT,
                device_id=device_id,
            )

            if native_result and native_result.response:
                speech = native_result.response.speech
                if isinstance(speech, dict):
                    plain = speech.get("plain", {})
                    if isinstance(plain, dict):
                        result_text = plain.get("speech", "")
                        if result_text:
                            # Check if the result indicates successful device control
                            # HA typically returns "Done" or similar for successful control
                            if any(keyword in result_text.lower() for keyword in ["done", "executed", "turned", "set", "activated", "opened", "closed"]):
                                _LOGGER.info(
                                    "Native HA device control succeeded: %s",
                                    result_text[:100],
                                )
                                return result_text
                            # Also check if it's a control-related response
                            if any(keyword in result_text.lower() for keyword in ["light", "switch", "climate", "cover"]):
                                _LOGGER.info(
                                    "Native HA device control response: %s",
                                    result_text[:100],
                                )
                                return result_text
        except Exception as err:
            _LOGGER.debug(
                "Native HA device control not applicable (falling through to MiMo): %s",
                err,
            )

        return None

    async def async_process(
        self,
        user_input: _conv().ConversationInput,
    ) -> _conv().ConversationResult:
        """Process a conversation turn using mimo serve + Claw tools.

        Uses local mimo serve for AI responses, and delegates tool calls
        to Claw Assistant's tool execution system.

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
            return _conv().ConversationResult(response=intent_resp)

        message_text = user_input.text
        if not message_text or not message_text.strip():
            intent_resp = intent.IntentResponse(language)
            intent_resp.async_set_speech("Please provide a message to send to MiMo Auto.")
            return _conv().ConversationResult(response=intent_resp)

        # Try native HA device control first
        native_control_result = await self._try_native_device_control(
            text=message_text,
            language=language,
            context=user_input.context,
            device_id=user_input.device_id,
        )
        if native_control_result:
            message_text = f"{message_text}\n\n[系统提示：已通过 Home Assistant 执行设备控制: {native_control_result}]"

        device_states = await self._get_device_states()
        user_activity = self._get_user_activity()

        conversation_id = user_input.conversation_id
        session_id = await self._get_or_create_session(conversation_id)
        if session_id is None:
            intent_resp = intent.IntentResponse(language)
            intent_resp.async_set_speech(ERROR_CONNECTION_FAILED)
            return _conv().ConversationResult(response=intent_resp)

        try:
            async with timeout(MESSAGE_TIMEOUT_SECONDS):
                result = await self._send_message(session_id, message_text, device_states, user_activity)
                if result is None:
                    intent_resp = intent.IntentResponse(language)
                    intent_resp.async_set_speech("MiMo Auto returned an empty response.")
                    return _conv().ConversationResult(response=intent_resp)

                reply_text = result.get("text") if isinstance(result, dict) else result
                tool_calls = result.get("tool_calls") if isinstance(result, dict) else None
                reasoning = result.get("reasoning") if isinstance(result, dict) else None

                self._last_session_id = session_id
                self._last_response = reply_text

                if reasoning:
                    self._fire_thought_to_claw(reasoning)

                # Execute tool calls via Claw if available
                if tool_calls:
                    for tc in tool_calls:
                        tool_result = await self._execute_tool_via_claw(tc)
                        if tool_result:
                            # Feed tool result back to mimo serve
                            await self._send_tool_result(session_id, tc.get("id", ""), tool_result)

                intent_response = intent.IntentResponse(language)
                intent_response.async_set_speech(reply_text or "")

                if tool_calls:
                    intent_response.response_type = intent.IntentResponseType.ACTION_DONE

                return _conv().ConversationResult(
                    response=intent_response,
                    conversation_id=conversation_id or session_id,
                    continue_conversation=bool(tool_calls),
                )

        except asyncio.TimeoutError:
            _LOGGER.error("MiMo conversation timed out after %d seconds", MESSAGE_TIMEOUT_SECONDS)
            if conversation_id and conversation_id in self._session_map:
                del self._session_map[conversation_id]
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

    async def _execute_tool_via_claw(self, tool_call: dict) -> dict | None:
        """Execute a tool call via Claw Assistant's tool system.

        Args:
            tool_call: Dict with 'name' and 'input' keys.

        Returns:
            Tool execution result, or None if failed.
        """
        tool_name = tool_call.get("name", "")
        tool_input = tool_call.get("input", {})

        try:
            # Try to use Claw's tool executor
            from custom_components.claw_assistant.tools.tool_executor import execute_kernel_tool
            from custom_components.claw_assistant.tools.registry import get_tool_registry

            # Get tool registry
            registry = get_tool_registry(self._hass)

            # Find and execute the tool
            if tool_name in registry:
                tool_class = registry[tool_name]
                tool_instance = tool_class()

                # Create tool input
                from homeassistant.helpers import llm
                tool_input_obj = llm.ToolInput(
                    id=f"mimo_{tool_name}_{id(tool_call)}",
                    tool_name=tool_name,
                    tool_args=tool_input,
                )

                # Execute
                from homeassistant.helpers.llm import LLMContext
                llm_context = LLMContext(
                    platform="mimo_auto",
                    context=None,
                    language="zh",
                    assistant="mimo_auto",
                )

                result = await tool_instance.async_call(self._hass, tool_input_obj, llm_context)
                _LOGGER.info("Tool %s executed successfully: %s", tool_name, str(result)[:100])
                return result

        except ImportError:
            _LOGGER.debug("Claw tool executor not available")
        except Exception as err:
            _LOGGER.debug("Failed to execute tool %s: %s", tool_name, err)

        return None

    async def _send_tool_result(self, session_id: str, tool_call_id: str, result: dict) -> None:
        """Send tool execution result back to mimo serve.

        Args:
            session_id: The session ID.
            tool_call_id: The tool call ID.
            result: The tool execution result.
        """
        url = f"{self._coordinator.server_url}/session/{session_id}/tool_result"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    json={
                        "tool_call_id": tool_call_id,
                        "result": result,
                    },
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as response:
                    if response.status == 200:
                        _LOGGER.debug("Tool result sent to mimo serve")
                    else:
                        _LOGGER.warning("Failed to send tool result: HTTP %d", response.status)
        except Exception as err:
            _LOGGER.debug("Error sending tool result: %s", err)

    async def _get_or_create_session(self, conversation_id: str | None) -> str | None:
        """Get existing session or create new one.

        Args:
            conversation_id: The conversation ID from Claw Assistant.

        Returns:
            The mimo session ID, or None if creation failed.
        """
        # If we have a mapped session, try to reuse it
        if conversation_id and conversation_id in self._session_map:
            session_id = self._session_map[conversation_id]
            _LOGGER.debug("Reusing session %s for conversation %s", session_id, conversation_id)
            return session_id

        # If no conversation_id, reuse the last session (for Claw automation requests)
        if not conversation_id and self._last_session_id:
            _LOGGER.debug("Reusing last session %s for request without conversation_id", self._last_session_id)
            return self._last_session_id

        # Create new session
        session_id = await self._create_session()
        if session_id is None:
            return None

        # Map conversation_id to session_id
        if conversation_id:
            self._session_map[conversation_id] = session_id
            _LOGGER.debug("Mapped conversation %s to session %s", conversation_id, session_id)
            self._save_session_map()

        return session_id

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

    async def _send_message(self, session_id: str, message: str, device_states: str = "", user_activity: str = "") -> str | None:
        """Send a message to the MiMo server and collect the response.

        Args:
            session_id: The session ID to send the message to.
            message: The message text content.
            device_states: Optional device states to include in context.
            user_activity: Optional user activity to include in context.

        Returns:
            The AI response text, or None if an error occurred.
        """
        url = f"{self._coordinator.server_url}{API_SEND_MESSAGE.format(session_id=session_id)}"
        _LOGGER.debug("Sending message to MiMo session %s", session_id)

        # Build message with context injection
        full_message = self._build_message_with_context(message, device_states, user_activity)

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    json={
                        "message": full_message,
                        "parts": [{"type": "text", "text": full_message}],
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
                    result = await self._parse_json_stream(response)

                    if result and (result.get("text") or result.get("tool_calls")):
                        _LOGGER.debug(
                            "Received response from MiMo (session %s): text=%d chars, tools=%d",
                            session_id,
                            len(result.get("text") or ""),
                            len(result.get("tool_calls") or []),
                        )
                    else:
                        _LOGGER.warning(
                            "Empty response from MiMo (session %s)", session_id
                        )

                    return result
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            _LOGGER.error("Error sending message: %s", err)
            return None

    async def _parse_json_stream(self, response: aiohttp.ClientResponse) -> dict | None:
        """Parse a chunked JSON stream from the MiMo server response.

        The MiMo server returns a stream of JSON objects (one per chunk)
        with Content-Type: application/json and Transfer-Encoding: chunked.
        Each JSON object has the structure:
            {"info": {...}, "parts": [{"type": "text", "text": "..."}, ...]}

        This parser:
        - Collects all text parts from the final assistant message
        - Extracts tool calls if present
        - Handles incomplete JSON chunks by buffering

        Args:
            response: The HTTP response with JSON chunked encoding.

        Returns:
            A dict with "text" and optionally "tool_calls" keys.
        """
        buffer = ""
        collected_texts: list[str] = []
        collected_tool_calls: list[dict] = []
        collected_reasoning: list[str] = []
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
                        # Extract text parts
                        if ptype == "text":
                            text = part.get("text", "").strip()
                            if text:
                                collected_texts.append(text)
                        # Extract reasoning parts
                        elif ptype == "reasoning":
                            reasoning = part.get("text", "").strip()
                            if reasoning:
                                collected_reasoning.append(reasoning)
                        # Extract tool_use parts
                        elif ptype == "tool_use":
                            tool_name = part.get("name", "")
                            tool_input = part.get("input", {})
                            if tool_name:
                                collected_tool_calls.append({
                                    "name": tool_name,
                                    "input": tool_input,
                                })

                except json.JSONDecodeError:
                    # Need more data, wait for next chunk
                    break

        result = {
            "text": "\n".join(collected_texts) if collected_texts else None,
            "tool_calls": collected_tool_calls if collected_tool_calls else None,
            "reasoning": "\n".join(collected_reasoning) if collected_reasoning else None,
        }
        return result

    def clear_session(self, conversation_id: str) -> None:
        """Clear a session mapping.

        Args:
            conversation_id: The conversation ID to clear.
        """
        if conversation_id in self._session_map:
            del self._session_map[conversation_id]
            _LOGGER.debug("Cleared session mapping for conversation %s", conversation_id)
