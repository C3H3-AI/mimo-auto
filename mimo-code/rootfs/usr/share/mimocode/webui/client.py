"""Unified MiMo serve HTTP client.

Provides MimoAIClient for async communication with mimo serve,
and parse_ndjson_chunk for NDJSON parsing.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncIterator

import aiohttp

_LOGGER = logging.getLogger(__name__)


class MimoAPIError(Exception):
    """Raised when mimo serve returns a non-2xx HTTP status."""

    def __init__(self, status: int, body: str = "") -> None:
        super().__init__(f"mimo serve HTTP {status}: {body[:200]}")
        self.status = status
        self.body = body


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

        # Only collect from assistant messages.
        # Do NOT require finish=="stop" — truncated responses (finish:"length")
        # still contain valid text that should be shown to the user.
        if info.get("role") != "assistant":
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
        """Verify session exists, create only if needed."""
        session = await self._ensure_session()

        # Verify existing session
        if session_id:
            try:
                async with session.get(
                    f"{self._base_url}/session/{session_id}",
                    timeout=aiohttp.ClientTimeout(total=timeout),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        # Check for error in response body
                        # mimo server returns {"name":"NotFoundError","data":{...}} for expired sessions
                        if isinstance(data, dict) and ("error" in data or data.get("name") == "NotFoundError"):
                            _LOGGER.debug("ensure_session: session %s has error, will create new", session_id)
                        else:
                            _LOGGER.debug("ensure_session: session %s exists, reusing", session_id)
                            return session_id
                    _LOGGER.debug("ensure_session: session %s returned %d, will create new", session_id, resp.status)
            except (aiohttp.ClientError, asyncio.TimeoutError) as err:
                _LOGGER.debug("ensure_session: verify failed for %s: %s, will create new", session_id, err)

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
                        _LOGGER.info("ensure_session: created new session %s", new_id)
                        return new_id
                _LOGGER.warning("ensure_session: POST /session returned %d", resp.status)
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            _LOGGER.error("ensure_session failed: %s", err)

        return session_id

    async def send_message(
        self, text: str, session_id: str, *,
        system: str | None = None,
        timeout: float | None = None,
        on_event: Any = None,
    ) -> str:
        """Send a message and return the concatenated response text.

        If on_event callback is provided, it's called for each event as it arrives.
        """
        parts: list[str] = []
        async for event in self.send_message_stream(
            text, session_id, system=system, timeout=timeout
        ):
            if event.get("type") == "text":
                parts.append(event["text"])
            if on_event:
                try:
                    await on_event(event)
                except Exception:
                    pass
        return "\n".join(parts)

    async def send_message_stream(
        self, text: str, session_id: str, *,
        system: str | None = None,
        timeout: float | None = None,
        on_event: Any = None,
    ) -> AsyncIterator[dict]:
        """Send a message and yield parsed NDJSON events as dicts.

        If on_event callback is provided, it's called for each event as it arrives.
        """
        session = await self._ensure_session()
        url = f"{self._base_url}/session/{session_id}/message"
        body: dict[str, Any] = {
            "message": text,
            "parts": [{"type": "text", "text": text}],
        }
        if system:
            body["system"] = system

        tout = aiohttp.ClientTimeout(total=timeout or self._default_timeout)
        _LOGGER.debug("send_message_stream: POST %s (text=%d chars, system=%d chars)",
                       url, len(text), len(system) if system else 0)

        try:
            async with session.post(url, json=body, timeout=tout) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    _LOGGER.error("send_message: HTTP %d for session %s: %s", resp.status, session_id, body[:200])
                    raise MimoAPIError(resp.status, body)

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
            _LOGGER.error("send_message_stream failed for session %s: %s", session_id, err)
            # Re-raise so caller (send_message / channel_manager) knows the stream
            # was interrupted — don't silently swallow.
            raise

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

    async def __aenter__(self) -> "MimoAIClient":
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()
