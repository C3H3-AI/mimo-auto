"""HA device context builder for system prompt injection.

Fetches device states from Home Assistant and builds a context string
that gets injected into the mimo serve system prompt via the `system` field.
Uses caching to avoid fetching on every message.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

HA_URL = "http://supervisor/core"
CACHE_TTL_SECONDS = 30  # Re-fetch device states every 30s

# Domains to include in context (prioritized for a "butler" use case)
PRIORITY_DOMAINS = [
    "light", "climate", "switch", "cover", "media_player",
    "fan", "lock", "vacuum", "camera", "sensor", "binary_sensor",
]


def _get_ha_token() -> str:
    """Get HA token from environment."""
    return os.environ.get("SUPERVISOR_TOKEN") or os.environ.get("HASSIO_TOKEN") or ""


class HAContextBuilder:
    """Builds HA device context for system prompt injection.

    Caches device states to avoid fetching on every message.
    Thread-safe: uses asyncio.Lock for the async path.
    Reuses a single aiohttp.ClientSession across requests.
    """

    def __init__(self) -> None:
        self._cache: str = ""
        self._cache_time: float = 0
        self._lock = asyncio.Lock()
        self._session: aiohttp.ClientSession | None = None

    async def get_context(self) -> str:
        """Get the HA device context string.

        Returns cached version if fresh enough, otherwise fetches fresh data.
        """
        now = time.time()
        if self._cache and (now - self._cache_time) < CACHE_TTL_SECONDS:
            return self._cache

        async with self._lock:
            # Double-check after acquiring lock
            if self._cache and (time.time() - self._cache_time) < CACHE_TTL_SECONDS:
                return self._cache

            context = await self._fetch_context()
            if context:
                self._cache = context
                self._cache_time = time.time()
            return self._cache

    async def _ensure_session(self) -> aiohttp.ClientSession:
        """Ensure we have an active HTTP session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10)
            )
        return self._session

    async def close(self) -> None:
        """Close the HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    async def _fetch_context(self) -> str:
        """Fetch all entity states from HA and build context string."""
        token = _get_ha_token()
        if not token:
            _LOGGER.debug("No HA token available, skipping context injection")
            return ""

        try:
            session = await self._ensure_session()
            async with session.get(
                f"{HA_URL}/api/states",
                headers={"Authorization": f"Bearer {token}"},
            ) as resp:
                if resp.status != 200:
                    _LOGGER.warning("HA API returned %d", resp.status)
                    return ""

                states = await resp.json()
                context = self._build_context(states)
                _LOGGER.debug("HA context built: %d entities, %d chars", len(states), len(context))
                return context

        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            _LOGGER.warning("Failed to fetch HA states: %s", err)
            return ""

    def _build_context(self, states: list[dict[str, Any]]) -> str:
        """Build context string from HA entity states."""
        now = time.strftime("%Y-%m-%d %H:%M")

        # Group by domain, prioritize important ones
        by_domain: dict[str, list[dict]] = {}
        for state in states:
            eid = state.get("entity_id", "")
            domain = eid.split(".")[0] if "." in eid else ""
            if domain not in PRIORITY_DOMAINS:
                continue
            by_domain.setdefault(domain, []).append(state)

        lines = [f"当前时间：{now}", "", "可用设备："]

        for domain in PRIORITY_DOMAINS:
            entities = by_domain.get(domain, [])
            if not entities:
                continue

            lines.append(f"\n[{domain}]")
            for s in entities[:20]:  # Cap per domain
                eid = s.get("entity_id", "")
                state_val = s.get("state", "unknown")
                attrs = s.get("attributes", {})
                name = attrs.get("friendly_name", eid)

                # Build a compact description
                desc = f"- {name} ({eid}): {state_val}"

                # Add key attributes
                extras = []
                if "brightness" in attrs:
                    extras.append(f"brightness={attrs['brightness']}")
                if "temperature" in attrs:
                    extras.append(f"temp={attrs['temperature']}")
                if "current_temperature" in attrs:
                    extras.append(f"current={attrs['current_temperature']}")
                if "humidity" in attrs:
                    extras.append(f"humidity={attrs['humidity']}")
                if " hvac_mode" in attrs or "mode" in attrs:
                    extras.append(f"mode={attrs.get('hvac_mode', attrs.get('mode', ''))}")

                if extras:
                    desc += f" ({', '.join(extras)})"

                lines.append(desc)

        return "\n".join(lines)


    def get_context_cached(self) -> str:
        """Get cached context without fetching. For sync contexts (e.g. feishu worker thread)."""
        return self._cache


# Singleton instance
_builder: HAContextBuilder | None = None


def get_ha_context_builder() -> HAContextBuilder:
    """Get or create the singleton HAContextBuilder."""
    global _builder
    if _builder is None:
        _builder = HAContextBuilder()
    return _builder
