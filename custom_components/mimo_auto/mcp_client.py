"""MCP (Model Context Protocol) client for HA integration.

Provides a client for connecting to the external HA MCP addon
and calling its 83 tools for device control, automation, and system management.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

# MCP protocol version
MCP_PROTOCOL_VERSION = "2024-11-05"

# Default MCP addon URL pattern
DEFAULT_MCP_URL_TEMPLATE = "http://{host}:{port}{path}"


class MCPClient:
    """Client for connecting to the HA MCP addon.

    Uses Streamable HTTP transport to communicate with the MCP server.
    """

    def __init__(
        self,
        hass,
        url: str | None = None,
        timeout: int = 30,
    ) -> None:
        """Initialize the MCP client.

        Args:
            hass: HomeAssistant instance.
            url: MCP server URL. If None, auto-detect from HA config.
            timeout: Request timeout in seconds.
        """
        self._hass = hass
        self._url = url
        self._timeout = timeout
        self._session: aiohttp.ClientSession | None = None
        self._tools_cache: list[dict] | None = None

    @property
    def is_available(self) -> bool:
        """Check if MCP client is configured and URL is set."""
        return self._url is not None

    async def _ensure_session(self) -> aiohttp.ClientSession:
        """Ensure we have an active HTTP session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self._timeout)
            )
        return self._session

    async def close(self) -> None:
        """Close the HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    async def list_tools(self) -> list[dict]:
        """List all available MCP tools.

        Returns:
            List of tool definitions with name, description, and input schema.
        """
        if self._tools_cache is not None:
            return self._tools_cache

        try:
            session = await self._ensure_session()
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/list",
                "params": {},
            }

            async with session.post(self._url, json=payload) as resp:
                if resp.status != 200:
                    _LOGGER.warning("MCP tools/list returned status %d", resp.status)
                    return []

                data = await resp.json()
                tools = data.get("result", {}).get("tools", [])
                self._tools_cache = tools
                _LOGGER.debug("MCP listed %d tools", len(tools))
                return tools

        except Exception as err:
            _LOGGER.error("Failed to list MCP tools: %s", err)
            return []

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Call an MCP tool.

        Args:
            tool_name: Name of the tool to call.
            arguments: Tool arguments.

        Returns:
            Tool execution result.
        """
        try:
            session = await self._ensure_session()
            payload = {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": tool_name,
                    "arguments": arguments or {},
                },
            }

            async with session.post(self._url, json=payload) as resp:
                if resp.status != 200:
                    _LOGGER.warning(
                        "MCP tools/call %s returned status %d",
                        tool_name,
                        resp.status,
                    )
                    return {"error": f"MCP returned status {resp.status}"}

                data = await resp.json()
                result = data.get("result", {})

                # Check for error in response
                if "error" in data:
                    return {"error": data["error"]}

                return result

        except Exception as err:
            _LOGGER.error("Failed to call MCP tool %s: %s", tool_name, err)
            return {"error": str(err)}

    async def health_check(self) -> bool:
        """Check if the MCP server is reachable.

        Returns:
            True if server is healthy, False otherwise.
        """
        if not self._url:
            return False

        try:
            session = await self._ensure_session()
            # Try to initialize
            payload = {
                "jsonrpc": "2.0",
                "id": 0,
                "method": "initialize",
                "params": {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {
                        "name": "mimo_auto",
                        "version": "1.0.0",
                    },
                },
            }

            async with session.post(self._url, json=payload) as resp:
                return resp.status == 200

        except Exception as err:
            _LOGGER.debug("MCP health check failed: %s", err)
            return False

    def get_tool_by_name(self, tool_name: str) -> dict | None:
        """Get a tool definition by name.

        Args:
            tool_name: Name of the tool.

        Returns:
            Tool definition dict, or None if not found.
        """
        if not self._tools_cache:
            return None

        for tool in self._tools_cache:
            if tool.get("name") == tool_name:
                return tool
        return None

    def get_tools_by_category(self, category: str) -> list[dict]:
        """Get tools filtered by category prefix.

        Args:
            category: Category prefix (e.g., "light", "climate", "automation").

        Returns:
            List of matching tool definitions.
        """
        if not self._tools_cache:
            return []

        return [
            tool
            for tool in self._tools_cache
            if tool.get("name", "").startswith(category)
        ]
