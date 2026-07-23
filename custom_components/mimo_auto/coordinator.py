"""Coordinator for bridging HA with the MiMo Code Addon.

This coordinator connects to the Addon's `mimo serve` API and monitors
its health. It uses the Supervisor API to detect and manage the Addon
lifecycle. No local subprocess management — the Addon handles that.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp

from homeassistant.core import HomeAssistant

from .const import (
    ADDON_SLUG,
    ADDON_SLUG_LOCAL,
    CONF_SERVER_URL,
    CONF_USE_SUPERVISOR,
    DEFAULT_SERVER_URL,
    HEALTH_CHECK_INTERVAL_SECONDS,
    ADDON_DETECT_TIMEOUT_SECONDS,
    API_TIMEOUT_SECONDS,
)

_LOGGER = logging.getLogger(__name__)


class MiMoCoordinator:
    """Manages connection to the MiMo Code Addon.

    Connects to the Addon's `mimo serve` HTTP API and monitors
    Addon health via periodic checks. Does NOT manage a local
    subprocess — the Addon container handles that independently.
    """

    def __init__(self, hass: HomeAssistant, config: dict[str, Any]) -> None:
        """Initialize the coordinator.

        Args:
            hass: The HomeAssistant instance.
            config: Configuration dict with server_url and addon settings.
        """
        self._hass = hass
        self._server_url: str = config.get(CONF_SERVER_URL, DEFAULT_SERVER_URL)
        self._use_supervisor: bool = config.get(CONF_USE_SUPERVISOR, True)

        self._running = False
        self._connected = False
        self._addon_slug: str | None = None
        self._health_check_task: asyncio.Task | None = None
        self._supervisor_client = None

    @property
    def is_running(self) -> bool:
        """Return whether the Addon is detected and connected."""
        return self._running and self._connected

    @property
    def server_url(self) -> str:
        """Return the mimo serve URL."""
        return self._server_url

    @property
    def addon_slug(self) -> str | None:
        """Return detected addon slug."""
        return self._addon_slug

    async def start(self) -> bool:
        """Connect to the Addon's mimo serve.

        Tries direct HTTP connection first, then falls back to
        Supervisor API detection.

        Returns:
            True if connected successfully.
        """
        # Step 1: Try direct connection to server
        if await self._check_server_healthy():
            _LOGGER.info("Connected to MiMo Code Addon at %s", self._server_url)
            self._running = True
            self._connected = True
            self._start_health_check()
            return True

        # Step 2: Try detecting via Supervisor API
        if self._use_supervisor:
            slug = await self._detect_addon()
            if slug:
                self._addon_slug = slug
                _LOGGER.info("Detected MiMo Code Addon: %s", slug)
                # Try direct connection again (addon may have just started)
                if await self._check_server_healthy():
                    self._running = True
                    self._connected = True
                    self._start_health_check()
                    return True
                # Mark as running but not yet connected (wait for health check)
                self._running = True
                _LOGGER.info(
                    "Addon detected (%s) but server not reachable yet at %s. "
                    "Will retry in health check loop.",
                    slug, self._server_url,
                )
                self._start_health_check()
                return True

        _LOGGER.warning(
            "Could not connect to MiMo Code Addon at %s. "
            "Make sure the addon is installed and running.",
            self._server_url,
        )
        return False

    async def stop(self) -> bool:
        """Stop health check loop.

        Returns:
            True always (no local process to stop).
        """
        self._stop_health_check()
        self._running = False
        self._connected = False
        return True

    async def restart_addon(self) -> bool:
        """Restart the Addon via Supervisor API.

        Returns:
            True if restart was requested successfully.
        """
        if not self._addon_slug or not self._supervisor_client:
            _LOGGER.warning("Cannot restart addon: no Supervisor client available")
            return False

        result = await self._supervisor_client.restart_addon(self._addon_slug)
        success = "error" not in result
        if success:
            _LOGGER.info("Addon %s restart requested", self._addon_slug)
            self._connected = False
        else:
            _LOGGER.error("Failed to restart addon: %s", result.get("error"))
        return success

    async def async_check_health(self) -> bool:
        """Check if the Addon's mimo serve is healthy.

        Returns:
            True if the server responded.
        """
        if not self._running:
            await self.start()
            return self._connected

        healthy = await self._check_server_healthy()
        if healthy != self._connected:
            self._connected = healthy
            _LOGGER.info(
                "MiMo Code Addon connection status changed: %s",
                "connected" if healthy else "disconnected",
            )
        return healthy

    async def _check_server_healthy(self) -> bool:
        """Check if mimo serve is responding.

        Tests the /session endpoint (200 = healthy).

        Returns:
            True if server responded.
        """
        try:
            timeout = aiohttp.ClientTimeout(total=API_TIMEOUT_SECONDS)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(
                    f"{self._server_url}/session",
                ) as response:
                    if response.status == 200:
                        return True
                    _LOGGER.debug(
                        "Server health check returned HTTP %d", response.status
                    )
                    return False
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as err:
            _LOGGER.debug("Server health check failed: %s", err)
            return False

    async def _detect_addon(self) -> str | None:
        """Detect the MiMo Code Addon via Supervisor API.

        Returns:
            Addon slug if detected and started, None otherwise.
        """
        try:
            from homeassistant.components.hassio import get_addons_info

            addons = get_addons_info(self._hass)
            if not isinstance(addons, dict):
                _LOGGER.debug("Supervisor addons info not available")
                return None

            for slug in (ADDON_SLUG, ADDON_SLUG_LOCAL):
                info = addons.get(slug)
                if isinstance(info, dict) and info.get("state") == "started":
                    _LOGGER.info("Detected MiMo Code Addon '%s' is running", slug)
                    return slug

            _LOGGER.debug(
                "MiMo Code Addon not found in Supervisor (checked: %s)",
                [ADDON_SLUG, ADDON_SLUG_LOCAL],
            )
            return None

        except ImportError:
            _LOGGER.debug("hassio module not available (not HAOS?)")
            return None
        except Exception as err:
            _LOGGER.debug("Addon detection error: %s", err)
            return None

    def _start_health_check(self) -> None:
        """Start the periodic health check loop."""
        self._stop_health_check()
        self._health_check_task = self._hass.async_create_task(
            self._health_check_loop(),
            name="mimo_auto_health_check",
        )

    def _stop_health_check(self) -> None:
        """Stop the periodic health check loop."""
        if self._health_check_task is not None:
            self._health_check_task.cancel()
            self._health_check_task = None

    async def _health_check_loop(self) -> None:
        """Periodic health check loop.

        Checks the server health every HEALTH_CHECK_INTERVAL_SECONDS.
        """
        while True:
            try:
                await asyncio.sleep(HEALTH_CHECK_INTERVAL_SECONDS)
                await self.async_check_health()
            except asyncio.CancelledError:
                break
            except Exception as err:
                _LOGGER.error("Unexpected error in health check: %s", err)
