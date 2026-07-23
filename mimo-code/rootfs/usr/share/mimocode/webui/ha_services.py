"""HA Service integration for mimo_auto.

Provides HA service calls and entity management,
allowing the AI to control devices via structured actions.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

HA_URL = "http://supervisor/core"


def _get_ha_token() -> str:
    """Get HA token from environment."""
    return os.environ.get("SUPERVISOR_TOKEN") or os.environ.get("HASSIO_TOKEN") or ""


async def call_ha_service(
    domain: str,
    service: str,
    entity_id: str,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Call a Home Assistant service.

    Args:
        domain: Service domain (e.g., light, climate, switch).
        service: Service name (e.g., turn_on, turn_off).
        entity_id: Target entity ID.
        data: Additional service data.

    Returns:
        Service call result.
    """
    token = _get_ha_token()
    if not token:
        return {"success": False, "error": "No HA token available"}

    payload = {"entity_id": entity_id}
    if data:
        payload.update(data)

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{HA_URL}/api/services/{domain}/{service}",
                json=payload,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status >= 400:
                    body = await resp.text()
                    return {"success": False, "error": f"HTTP {resp.status}: {body}"}
                return {"success": True, "result": await resp.json()}
    except Exception as err:
        return {"success": False, "error": str(err)}


async def get_entity_state(entity_id: str) -> dict[str, Any] | None:
    """Get entity state from Home Assistant."""
    token = _get_ha_token()
    if not token:
        return None

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{HA_URL}/api/states/{entity_id}",
                headers={"Authorization": f"Bearer {token}"},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 200:
                    return await resp.json()
    except Exception as err:
        _LOGGER.warning("Failed to get entity state: %s", err)
    return None


async def list_entities(domain_filter: str | None = None) -> list[dict[str, Any]]:
    """List all entities, optionally filtered by domain."""
    token = _get_ha_token()
    if not token:
        return []

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{HA_URL}/api/states",
                headers={"Authorization": f"Bearer {token}"},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status == 200:
                    states = await resp.json()
                    if domain_filter:
                        states = [s for s in states if s["entity_id"].startswith(f"{domain_filter}.")]
                    return [
                        {
                            "entity_id": s["entity_id"],
                            "state": s["state"],
                            "name": s.get("attributes", {}).get("friendly_name", ""),
                        }
                        for s in states
                    ]
    except Exception as err:
        _LOGGER.warning("Failed to list entities: %s", err)
    return []


async def trigger_automation(entity_id: str) -> dict[str, Any]:
    """Trigger an automation."""
    return await call_ha_service("automation", "trigger", entity_id)


async def send_notification(
    message: str,
    title: str = "MiMo 管家",
    target: str | None = None,
) -> dict[str, Any]:
    """Send a notification."""
    service = target or "persistent_notification"
    data = {"message": message, "title": title}
    return await call_ha_service("notify", service, "", data)
