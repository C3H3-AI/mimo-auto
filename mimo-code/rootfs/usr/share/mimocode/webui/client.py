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
    """Parse an NDJSON buffer, returning (extracted objects, remaining buffer)."""
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

        if info.get("role") != "assistant" or info.get("finish") != "stop":
            continue

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


# ---------------------------------------------------------------------------
# Async client
# ---------------------------------------------------------------------------

class MimoAIClient:
    """Async HTTP client for mimo serve."""

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
        """Verify session exists, create only if needed.

        If *session_id* is provided, check via GET /session/{id}.
        Only POST /session if verification fails.
        """
        session = await self._ensure_session()

        # Verify existing session
        if session_id:
            try:
                async with session.get(
                    f"{self._base_url}/session/{session_id}",
                    timeout=aiohttp.ClientTimeout(total=timeout),
                ) as resp:
                    if resp.status == 200:
                        return session_id
            except (aiohttp.ClientError, asyncio.TimeoutError):
                pass

        # Create new session
        try:
            async with session.post(
                f"{self._base_url}/session",
                json={},
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    new_id = data.get("id", "")
                    if new_id:
                        return new_id
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            _LOGGER.warning("ensure_session failed: %s", err)

        return session_id

    async def send_message(
        self, text: str, session_id: str, *,
        system: str | None = None,
        timeout: float | None = None,
    ) -> str:
        """Send a message and return the concatenated response text."""
        parts: list[str] = []
        async for event in self.send_message_stream(
            text, session_id, system=system, timeout=timeout
        ):
            if event.get("type") == "text":
                parts.append(event["text"])
        return "\n".join(parts)

    async def send_message_stream(
        self, text: str, session_id: str, *,
        system: str | None = None,
        timeout: float | None = None,
    ) -> AsyncIterator[dict]:
        """Send a message and yield parsed NDJSON events as dicts."""
        session = await self._ensure_session()
        url = f"{self._base_url}/session/{session_id}/message"
        body: dict[str, Any] = {
            "message": text,
            "parts": [{"type": "text", "text": text}],
        }
        if system:
            body["system"] = system

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
    """Synchronous wrapper around MimoAIClient for use in non-async threads."""

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
        """Verify session exists, create only if needed."""
        loop = self._get_loop()
        client = self._get_client()
        return loop.run_until_complete(client.ensure_session(session_id, timeout))

    def send_message(
        self, text: str, session_id: str, *,
        system: str | None = None,
        timeout: float = 180.0,
    ) -> str:
        """Send a message and return the full response text."""
        loop = self._get_loop()
        client = self._get_client()
        return loop.run_until_complete(
            client.send_message(text, session_id, system=system, timeout=timeout)
        )

    def send_message_stream(
        self, text: str, session_id: str, *,
        system: str | None = None,
        timeout: float = 180.0,
    ) -> list[dict]:
        """Send a message and return all parsed NDJSON events as a list."""
        loop = self._get_loop()
        client = self._get_client()

        async def _collect() -> list[dict]:
            events: list[dict] = []
            async for event in client.send_message_stream(
                text, session_id, system=system, timeout=timeout
            ):
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
