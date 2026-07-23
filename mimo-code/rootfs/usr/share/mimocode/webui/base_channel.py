"""Base channel protocol and shared utilities.

Defines the unified interface that all channel clients must implement.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol, runtime_checkable

_LOGGER = logging.getLogger(__name__)


@runtime_checkable
class ChannelProtocol(Protocol):
    """Unified interface for all IM channel clients."""

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
        """Start the channel."""
        ...

    async def stop(self) -> None:
        """Stop the channel."""
        ...


def build_system_prompt(device_context: str) -> str:
    """Build the system prompt for HA butler conversations.

    Shared across all channels to ensure consistency.
    Includes persona, device context, and learned lessons.
    """
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
