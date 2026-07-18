"""Supervisor API client for HA addon and system management.

Provides access to the HA Supervisor API for addon management,
system operations, and diagnostics.
"""

from __future__ import annotations

import logging
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

# Supervisor API base URL (inside HA container)
SUPERVISOR_API_BASE = "http://supervisor"


class SupervisorClient:
    """Client for the HA Supervisor API.

    Communicates with the Supervisor API running inside the HA container.
    """

    def __init__(
        self,
        hass,
        token: str | None = None,
        base_url: str = SUPERVISOR_API_BASE,
        timeout: int = 10,
    ) -> None:
        """Initialize the Supervisor client.

        Args:
            hass: HomeAssistant instance.
            token: Supervisor authentication token.
            base_url: Supervisor API base URL.
            timeout: Request timeout in seconds.
        """
        self._hass = hass
        self._token = token
        self._base_url = base_url
        self._timeout = timeout
        self._session: aiohttp.ClientSession | None = None

    @property
    def is_available(self) -> bool:
        """Check if Supervisor client is configured."""
        return self._token is not None

    async def _ensure_session(self) -> aiohttp.ClientSession:
        """Ensure we have an active HTTP session."""
        if self._session is None or self._session.closed:
            headers = {}
            if self._token:
                headers["Authorization"] = f"Bearer {self._token}"

            self._session = aiohttp.ClientSession(
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=self._timeout),
            )
        return self._session

    async def close(self) -> None:
        """Close the HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    async def _request(
        self,
        method: str,
        path: str,
        data: dict | None = None,
    ) -> dict[str, Any]:
        """Make a request to the Supervisor API.

        Args:
            method: HTTP method.
            path: API path.
            data: Request body data.

        Returns:
            Response data.
        """
        try:
            session = await self._ensure_session()
            url = f"{self._base_url}{path}"

            async with session.request(method, url, json=data) as resp:
                if resp.status == 200:
                    return await resp.json()
                elif resp.status == 204:
                    return {"success": True}
                else:
                    body = await resp.text()
                    return {"error": f"HTTP {resp.status}: {body[:200]}"}

        except Exception as err:
            _LOGGER.error("Supervisor API request failed: %s", err)
            return {"error": str(err)}

    # ==================== Addon Management ====================

    async def list_addons(self) -> dict[str, Any]:
        """List all installed addons.

        Returns:
            Dict with addon list.
        """
        return await self._request("GET", "/addons")

    async def get_addon_info(self, addon_slug: str) -> dict[str, Any]:
        """Get information about an addon.

        Args:
            addon_slug: Addon slug.

        Returns:
            Addon information.
        """
        return await self._request("GET", f"/addons/{addon_slug}")

    async def start_addon(self, addon_slug: str) -> dict[str, Any]:
        """Start an addon.

        Args:
            addon_slug: Addon slug.

        Returns:
            Operation result.
        """
        return await self._request("POST", f"/addons/{addon_slug}/start")

    async def stop_addon(self, addon_slug: str) -> dict[str, Any]:
        """Stop an addon.

        Args:
            addon_slug: Addon slug.

        Returns:
            Operation result.
        """
        return await self._request("POST", f"/addons/{addon_slug}/stop")

    async def restart_addon(self, addon_slug: str) -> dict[str, Any]:
        """Restart an addon.

        Args:
            addon_slug: Addon slug.

        Returns:
            Operation result.
        """
        return await self._request("POST", f"/addons/{addon_slug}/restart")

    async def update_addon(self, addon_slug: str) -> dict[str, Any]:
        """Update an addon.

        Args:
            addon_slug: Addon slug.

        Returns:
            Operation result.
        """
        return await self._request("POST", f"/addons/{addon_slug}/update")

    # ==================== System Operations ====================

    async def get_supervisor_info(self) -> dict[str, Any]:
        """Get Supervisor information.

        Returns:
            Supervisor info.
        """
        return await self._request("GET", "/supervisor/info")

    async def get_core_info(self) -> dict[str, Any]:
        """Get HA Core information.

        Returns:
            Core info.
        """
        return await self._request("GET", "/core/info")

    async def get_host_info(self) -> dict[str, Any]:
        """Get host information.

        Returns:
            Host info.
        """
        return await self._request("GET", "/host/info")

    async def get_hardware_info(self) -> dict[str, Any]:
        """Get hardware information.

        Returns:
            Hardware info.
        """
        return await self._request("GET", "/hardware/info")

    async def restart_core(self) -> dict[str, Any]:
        """Restart HA Core.

        Returns:
            Operation result.
        """
        return await self._request("POST", "/core/restart")

    async def update_core(self) -> dict[str, Any]:
        """Update HA Core.

        Returns:
            Operation result.
        """
        return await self._request("POST", "/core/update")

    async def restart_supervisor(self) -> dict[str, Any]:
        """Restart Supervisor.

        Returns:
            Operation result.
        """
        return await self._request("POST", "/supervisor/restart")

    async def update_supervisor(self) -> dict[str, Any]:
        """Update Supervisor.

        Returns:
            Operation result.
        """
        return await self._request("POST", "/supervisor/update")

    # ==================== Backup Management ====================

    async def list_backups(self) -> dict[str, Any]:
        """List all backups.

        Returns:
            Backup list.
        """
        return await self._request("GET", "/backups")

    async def create_backup(
        self,
        name: str | None = None,
        folders: list[str] | None = None,
        addons: list[str] | None = None,
    ) -> dict[str, Any]:
        """Create a new backup.

        Args:
            name: Optional backup name.
            folders: List of folders to backup.
            addons: List of addons to backup.

        Returns:
            Operation result.
        """
        data = {}
        if name:
            data["name"] = name
        if folders:
            data["folders"] = folders
        if addons:
            data["addons"] = addons

        return await self._request("POST", "/backups/new/full", data)

    async def restore_backup(self, slug: str) -> dict[str, Any]:
        """Restore a backup.

        Args:
            slug: Backup slug.

        Returns:
            Operation result.
        """
        return await self._request("POST", f"/backups/{slug}/restore/full")

    async def delete_backup(self, slug: str) -> dict[str, Any]:
        """Delete a backup.

        Args:
            slug: Backup slug.

        Returns:
            Operation result.
        """
        return await self._request("DELETE", f"/backups/{slug}")

    # ==================== Health Check ====================

    async def health_check(self) -> bool:
        """Check if Supervisor API is reachable.

        Returns:
            True if Supervisor is healthy, False otherwise.
        """
        try:
            result = await self.get_supervisor_info()
            return "error" not in result
        except Exception as err:
            _LOGGER.debug("Supervisor health check failed: %s", err)
            return False
