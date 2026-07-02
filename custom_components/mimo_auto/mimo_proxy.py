"""API proxy views for MiMo server.

Proxies requests from the HA frontend (HTTPS) to the local MiMo server (HTTP),
avoiding mixed-content browser restrictions.
"""

from __future__ import annotations

import logging
from typing import Any

import aiohttp
from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


def _get_coordinator(hass: HomeAssistant) -> Any | None:
    """Get the first active coordinator."""
    if DOMAIN not in hass.data:
        return None
    for entry_id, data in hass.data[DOMAIN].items():
        if isinstance(data, dict):
            coordinator = data.get("coordinator")
            if coordinator and coordinator.is_running:
                return coordinator
    return None


class MiMoCreateSessionView(HomeAssistantView):
    """Create a new session on the MiMo server."""

    url = "/api/mimo_auto/proxy/session"
    name = "api:mimo_auto:proxy:session"
    requires_auth = False  # iframe panels cannot pass HA tokens

    async def post(self, request: web.Request) -> web.Response:
        """Forward POST /session to the MiMo server."""
        hass = request.app["hass"]
        coordinator = _get_coordinator(hass)
        if not coordinator:
            return self.json(
                {"error": "MiMo server is not running"}, status_code=503
            )

        base_url = f"http://127.0.0.1:{coordinator.port}"
        async with aiohttp.ClientSession() as session:
            async with session.post(f"{base_url}/session", json={}) as resp:
                data = await resp.json()
                return self.json(data)


class MiMoSendMessageView(HomeAssistantView):
    """Send a message to a MiMo session."""

    url = "/api/mimo_auto/proxy/session/{session_id}/message"
    name = "api:mimo_auto:proxy:message"
    requires_auth = False  # iframe panels cannot pass HA tokens

    async def post(self, request: web.Request, session_id: str) -> web.Response:
        """Forward POST /session/{id}/message to the MiMo server."""
        hass = request.app["hass"]
        coordinator = _get_coordinator(hass)
        if not coordinator:
            return self.json(
                {"error": "MiMo server is not running"}, status_code=503
            )

        body = await request.json()
        base_url = f"http://127.0.0.1:{coordinator.port}"
        target_url = f"{base_url}/session/{session_id}/message"

        async with aiohttp.ClientSession() as client_session:
            async with client_session.post(
                target_url, json=body
            ) as mimo_resp:
                content_type = mimo_resp.headers.get(
                    "Content-Type", ""
                ).lower()

                if "ndjson" in content_type or "stream" in content_type:
                    # Stream NDJSON response back to the client
                    resp = web.StreamResponse(
                        status=mimo_resp.status,
                        headers={
                            "Content-Type": "application/x-ndjson",
                            "Cache-Control": "no-cache",
                            "X-Accel-Buffering": "no",
                        },
                    )
                    await resp.prepare(request)
                    async for chunk in mimo_resp.content.iter_chunked(4096):
                        await resp.write(chunk)
                    return resp

                # Non-streaming response (JSON)
                data = await mimo_resp.json()
                return self.json(data)


def async_register_proxy_views(hass: HomeAssistant) -> None:
    """Register the MiMo proxy HTTP views."""
    hass.http.register_view(MiMoCreateSessionView)
    hass.http.register_view(MiMoSendMessageView)
    _LOGGER.debug("MiMo proxy views registered")