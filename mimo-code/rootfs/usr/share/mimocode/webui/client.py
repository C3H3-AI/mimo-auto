"""Unified MiMo serve HTTP client.

Provides MimoAIClient (async), MimoClientSync (sync wrapper),
and parse_ndjson_chunk (pure function) to eliminate 4x duplicated
NDJSON parsing across channel_manager, feishu_client, agent_impl, and server.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncIterator

import aiohttp

_LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pure function: NDJSON buffer parsing
# ---------------------------------------------------------------------------

def parse_ndjson_chunk(
    buffer: str,
    *,
    collect_text: bool = True,
    collect_reasoning: bool = False,
    collect_tool_calls: bool = False,
    dedup_by_id: bool = False,
    seen_ids: set[str] | None = None,
) -> tuple[list[dict[str, Any]], str]:
    """Parse an NDJSON buffer, returning (extracted objects, remaining buffer).

    This is a pure function with no side effects.  All callers share this
    single implementation instead of maintaining their own parsers.

    Args:
        buffer: Raw text buffer that may contain one or more JSON objects.
        collect_text: Extract ``{"type": "text"}`` parts from assistant messages.
        collect_reasoning: Extract ``{"type": "reasoning"}`` parts.
        collect_tool_calls: Extract ``{"type": "tool-call"}`` parts.
        dedup_by_id: Skip objects whose ``info.id`` is already in *seen_ids*.
        seen_ids: Mutable set for dedup; created internally when *None*.

    Returns:
        (list_of_extracted_objects, leftover_buffer) where each extracted
        object is ``{"type": "text"|"reasoning"|"tool-call", "text": ...}``.
    """
    if seen_ids is None:
        seen_ids = set()

    collected: list[dict[str, Any]] = []
    buf = buffer

    while True:
        buf = buf.lstrip()
        if not buf:
            break

        try:
            obj, idx = json.JSONDecoder().raw_decode(buf)
            buf = buf[idx:]
        except json.JSONDecodeError:
            break

        if not isinstance(obj, dict):
            continue

        info = obj.get("info", {})
        parts = obj.get("parts", [])
        if not isinstance(parts, list):
            continue

        # Only process finished assistant messages
        if info.get("role") != "assistant" or info.get("finish") != "stop":
            continue

        # Dedup
        if dedup_by_id:
            msg_id = info.get("id", "")
            if msg_id in seen_ids:
                continue
            seen_ids.add(msg_id)

        for part in parts:
            if not isinstance(part, dict):
                continue
            ptype = part.get("type")
            text = part.get("text", "").strip() if isinstance(part.get("text"), str) else ""

            if ptype == "text" and collect_text and text:
                collected.append({"type": "text", "text": text})
            elif ptype == "reasoning" and collect_reasoning and text:
                collected.append({"type": "reasoning", "text": text})
            elif ptype == "tool-call" and collect_tool_calls:
                collected.append({
                    "type": "tool-call",
                    "tool_name": part.get("toolName", ""),
                    "args": part.get("args"),
                })

    return collected, buf


def collect_text_from_ndjson(buffer: str) -> tuple[str, str]:
    """Convenience: parse NDJSON and return concatenated text + remaining buffer."""
    items, remaining = parse_ndjson_chunk(buffer, collect_text=True)
    texts = [i["text"] for i in items if i["type"] == "text"]
    return "\n".join(texts), remaining


# ---------------------------------------------------------------------------
# Async client
# ---------------------------------------------------------------------------

class MimoAIClient:
    """Async HTTP client for mimo serve.

    Wraps /session and /session/{id}/message endpoints with NDJSON
    streaming support, session management, and health checks.
    """

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:14096",
        session: aiohttp.ClientSession | None = None,
        default_timeout: float = 180.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._own_session = session is None
        self._session = session
        self._default_timeout = default_timeout

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
            self._own_session = True
        return self._session

    async def ensure_session(self, session_id: str, timeout: float = 5.0) -> str:
        """Create a session on mimo serve, return its ID.

        If *session_id* is provided and the server accepts it, it is reused.
        """
        session = await self._ensure_session()
        try:
            async with session.post(
                f"{self._base_url}/session",
                json={"id": session_id} if session_id else {},
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("id", session_id)
                _LOGGER.warning("ensure_session: HTTP %d", resp.status)
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            _LOGGER.warning("ensure_session failed: %s", err)
        return session_id

    async def send_message(
        self, text: str, session_id: str, *, timeout: float | None = None
    ) -> str:
        """Send a message and return the concatenated response text."""
        parts: list[str] = []
        async for event in self.send_message_stream(text, session_id, timeout=timeout):
            if event.get("type") == "text":
                parts.append(event["text"])
        return "\n".join(parts)

    async def send_message_stream(
        self, text: str, session_id: str, *, timeout: float | None = None
    ) -> AsyncIterator[dict]:
        """Send a message and yield parsed NDJSON events as dicts.

        Yields dicts like ``{"type": "text", "text": "..."}`` or
        ``{"type": "reasoning", "text": "..."}``.
        """
        session = await self._ensure_session()
        url = f"{self._base_url}/session/{session_id}/message"
        body = {
            "message": text,
            "parts": [{"type": "text", "text": text}],
        }
        tout = aiohttp.ClientTimeout(total=timeout or self._default_timeout)

        try:
            async with session.post(url, json=body, timeout=tout) as resp:
                if resp.status != 200:
                    _LOGGER.error("send_message: HTTP %d", resp.status)
                    return

                buf = ""
                seen_ids: set[str] = set()
                async for chunk_bytes in resp.content:
                    if not chunk_bytes:
                        continue
                    buf += chunk_bytes.decode("utf-8", errors="replace")

                    items, buf = parse_ndjson_chunk(
                        buf,
                        collect_text=True,
                        collect_reasoning=True,
                        collect_tool_calls=True,
                        dedup_by_id=True,
                        seen_ids=seen_ids,
                    )
                    for item in items:
                        yield item

        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            _LOGGER.error("send_message_stream failed: %s", err)

    async def health_check(self, timeout: float = 5.0) -> bool:
        """Return True if mimo serve responds to /session."""
        session = await self._ensure_session()
        try:
            async with session.get(
                f"{self._base_url}/session",
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                return resp.status in (200, 404)
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
            return False

    async def close(self) -> None:
        if self._own_session and self._session and not self._session.closed:
            await self._session.close()
            self._session = None


# ---------------------------------------------------------------------------
# Sync wrapper (for thread contexts: feishu WS thread, etc.)
# ---------------------------------------------------------------------------

class MimoClientSync:
    """Synchronous wrapper around MimoAIClient for use in non-async threads.

    Uses ``loop.run_until_complete()`` internally.  Must NOT be called from
    within an existing asyncio event loop (use ``hass.async_add_executor_job``
    or a dedicated thread instead).
    """

    def __init__(self, base_url: str = "http://127.0.0.1:14096") -> None:
        self._base_url = base_url
        self._client: MimoAIClient | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def _get_loop(self) -> asyncio.AbstractEventLoop:
        if self._loop is None or self._loop.is_closed():
            self._loop = asyncio.new_event_loop()
        return self._loop

    def _get_client(self) -> MimoAIClient:
        if self._client is None:
            self._client = MimoAIClient(base_url=self._base_url)
        return self._client

    def ensure_session(self, session_id: str, timeout: float = 5.0) -> str:
        """Create a session, return its ID."""
        loop = self._get_loop()
        client = self._get_client()
        return loop.run_until_complete(client.ensure_session(session_id, timeout))

    def send_message(self, text: str, session_id: str, timeout: float = 180.0) -> str:
        """Send a message and return the full response text."""
        loop = self._get_loop()
        client = self._get_client()
        return loop.run_until_complete(client.send_message(text, session_id, timeout=timeout))

    def send_message_stream(
        self, text: str, session_id: str, timeout: float = 180.0
    ) -> list[dict]:
        """Send a message and return all parsed NDJSON events as a list.

        Collects the async stream into a list for use in synchronous contexts.
        """
        loop = self._get_loop()
        client = self._get_client()

        async def _collect() -> list[dict]:
            events: list[dict] = []
            async for event in client.send_message_stream(text, session_id, timeout=timeout):
                events.append(event)
            return events

        return loop.run_until_complete(_collect())

    def health_check(self, timeout: float = 5.0) -> bool:
        """Check if mimo serve is reachable."""
        loop = self._get_loop()
        client = self._get_client()
        return loop.run_until_complete(client.health_check(timeout))

    def close(self) -> None:
        """Close the underlying async client and event loop."""
        if self._client:
            loop = self._get_loop()
            if not loop.is_closed():
                loop.run_until_complete(self._client.close())
            self._client = None
        if self._loop and not self._loop.is_closed():
            self._loop.close()
            self._loop = None

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass
