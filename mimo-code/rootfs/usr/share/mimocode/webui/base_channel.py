"""Base channel protocol and shared channel implementation.

Defines the unified interface that all channel clients must implement.
All channels run in the same asyncio event loop (no threads).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Awaitable, Protocol

_LOGGER = logging.getLogger(__name__)


# ------------------------------------------------------------------ #
# Channel Protocol (interface contract)
# ------------------------------------------------------------------ #

class ChannelProtocol(Protocol):
    """Unified interface for all IM channel clients."""

    @property
    def channel_type(self) -> str:
        """Channel type identifier (feishu, personal_wechat, wechat_work, ...)."""
        ...

    @property
    def account_id(self) -> str:
        """Account identifier for multi-account support."""
        ...

    @property
    def is_connected(self) -> bool:
        """Whether the channel is currently connected."""
        ...

    @property
    def last_error(self) -> str | None:
        """Last error message, if any."""
        ...

    def get_status(self) -> dict[str, Any]:
        """Return channel status dict."""
        ...

    async def start(self) -> None:
        """Start the channel (runs as task in the event loop)."""
        ...

    async def stop(self) -> None:
        """Stop the channel."""
        ...

    async def send_text(self, text: str, **kwargs: Any) -> str | None:
        """Send a text message. Returns message_id if applicable."""
        ...

    async def send_thinking(self, text: str, **kwargs: Any) -> str | None:
        """Send a thinking/reasoning message with distinct style.

        Default implementation sends as regular text with prefix.
        Override for channel-specific styling (e.g. card, markdown).
        """
        ...


# ------------------------------------------------------------------ #
# Base channel implementation with shared logic
# ------------------------------------------------------------------ #

class BaseChannel:
    """Base class for all IM channels with shared session/reasoning logic.

    Subclasses must implement:
      - channel_type (property)
      - account_id (property)
      - send_text()
      - _start_channel()  - establish connection / start polling

    Subclasses may override:
      - send_thinking()  - for channel-specific thinking styling
      - build_channel_key(sender_id, chat_id)  - custom session key format
    """

    def __init__(
        self,
        on_message: Callable[[dict[str, Any]], Awaitable[str]],
        account_id: str = "default",
        show_reasoning: bool = True,
    ) -> None:
        self._on_message = on_message
        self._account_id = account_id
        self._show_reasoning = show_reasoning
        self._running = False
        self._status: str = "disconnected"
        self._last_error: str | None = None

    # -- Properties to be overridden by subclasses --

    @property
    def channel_type(self) -> str:
        raise NotImplementedError

    @property
    def account_id(self) -> str:
        return self._account_id

    @property
    def is_connected(self) -> bool:
        return self._running and self._status == "connected"

    @property
    def last_error(self) -> str | None:
        return self._last_error

    def get_status(self) -> dict[str, Any]:
        return {
            "type": self.channel_type,
            "account_id": self._account_id,
            "connected": self.is_connected,
            "status": self._status,
            "error": self._last_error,
        }

    # -- Lifecycle --

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._status = "connecting"
        await self._start_channel()

    async def stop(self) -> None:
        self._running = False
        self._status = "disconnected"
        await self._stop_channel()

    async def _start_channel(self) -> None:
        """Establish connection. Override in subclass."""
        raise NotImplementedError

    async def _stop_channel(self) -> None:
        """Tear down connection. Override in subclass."""
        pass

    # -- Session key generation --

    def build_channel_key(self, sender_id: str, chat_id: str = "") -> str:
        """Build stable channel_key for session management.

        Format: "{channel_type}:{account_id}:{sender_id}"
        This ensures each sender in each channel gets their own session.
        """
        key = f"{self.channel_type}:{self._account_id}:{sender_id}"
        if chat_id:
            key = f"{key}:{chat_id}"
        return key

    # -- Message handling --

    async def handle_incoming(
        self,
        text: str,
        sender_id: str,
        chat_id: str = "",
        media_info: str = "",
    ) -> str:
        """Process an incoming message through the unified message handler.

        Builds the message dict with channel context and calls on_message.
        Returns response text.
        """
        message = {
            "text": text,
            "sender_id": sender_id,
            "chat_id": chat_id,
            "account_id": self._account_id,
            "channel_type": self.channel_type,
            "channel_key": self.build_channel_key(sender_id, chat_id),
            "media_info": media_info,
        }
        response = await self._on_message(message)
        return response or ""

    # -- Sending methods --

    async def send_text(self, text: str, **kwargs: Any) -> str | None:
        """Send a text message. Must be overridden."""
        raise NotImplementedError

    async def send_thinking(self, text: str, **kwargs: Any) -> str | None:
        """Send a thinking/reasoning message.

        Default implementation sends as regular text with a 💭 prefix.
        Override in subclass for channel-specific styling (e.g. card/markdown).
        """
        if not self._show_reasoning:
            return None
        styled = f"💭 {text}"
        return await self.send_text(styled, **kwargs)


# ------------------------------------------------------------------ #
# Utilities shared across channels
# ------------------------------------------------------------------ #

def build_system_prompt(device_context: str) -> str:
    """Build the system prompt for HA butler conversations."""
    from persona import get_persona_store

    persona = get_persona_store()
    persona_prompt = persona.build_persona_prompt()

    base = (
        f"{persona_prompt}\n\n"
        "根据用户请求控制设备或回答问题。\n"
        "如果用户要求控制设备，直接调用对应工具。\n"
        "回复要简洁友好。"
    )
    if device_context:
        return f"{base}\n\n{device_context}"
    return base


def is_rate_limit(text: str) -> bool:
    """Detect mimo serve rate-limiting messages."""
    rate_limit_keywords = [
        "排队等待", "token plan", "/login",
        "subscribe", "free mode", "queue",
        "rate limit", "too many requests",
    ]
    lower = text.lower()
    return any(kw in lower for kw in rate_limit_keywords)


RATE_LIMIT_MESSAGE = "AI 服务繁忙，请稍后再试。"
