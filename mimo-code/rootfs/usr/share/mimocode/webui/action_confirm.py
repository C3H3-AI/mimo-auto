"""Action confirmation manager for sensitive tool calls.

When the AI returns a tool-call event, this module:
1. Builds a confirmation request with action details
2. Sends it to the user via the channel (card for Feishu, text for WeChat)
3. Waits for user approval/rejection
4. Executes the HA service call if approved

Flow:
  AI tool-call → channel_manager intercepts → build confirmation
  → send card/text to user → wait for callback
  → if approved: execute HA service → reply result
  → if rejected: reply "已取消"
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable

_LOGGER = logging.getLogger(__name__)

# HA API configuration (from ha_context.py)
HA_URL = "http://supervisor/core"

# Confirmation timeout (5 minutes)
CONFIRM_TIMEOUT_SECONDS = 300

# Tool name → human-readable description
_TOOL_DESCRIPTIONS: dict[str, str] = {
    "ha_turn_on": "打开",
    "ha_turn_off": "关闭",
    "ha_toggle": "切换",
    "ha_set_brightness": "设置亮度",
    "ha_set_temperature": "设置温度",
    "ha_get_state": "查询状态",
    "ha_get_all_lights": "获取所有灯光",
    "ha_call_service": "调用服务",
}

# Tools that require confirmation (sensitive operations)
_SENSITIVE_TOOLS: set[str] = {
    "ha_turn_on", "ha_turn_off", "ha_toggle",
    "ha_set_brightness", "ha_set_temperature",
    "ha_call_service",
}

# Tools that can execute without confirmation (read-only)
_SAFE_TOOLS: set[str] = {
    "ha_get_state", "ha_get_all_lights",
}


@dataclass
class PendingConfirmation:
    """A pending tool confirmation waiting for user response."""
    confirm_id: str
    tool_name: str
    tool_args: dict[str, Any]
    description: str
    created_at: float
    # Channel info for sending the result back
    channel_key: str
    sender_id: str
    chat_id: str
    account_id: str
    # Callback to send reply via the channel
    reply_callback: Callable[[str], Awaitable[None]] | None = None
    # Future that resolves when user responds
    _future: asyncio.Future | None = field(default=None, repr=False)


class ActionConfirmManager:
    """Manages pending action confirmations."""

    def __init__(self) -> None:
        self._pending: dict[str, PendingConfirmation] = {}
        self._ha_token: str = ""

    def _get_ha_token(self) -> str:
        """Get HA token from environment."""
        if not self._ha_token:
            import os
            self._ha_token = os.environ.get("SUPERVISOR_TOKEN") or os.environ.get("HASSIO_TOKEN") or ""
        return self._ha_token

    def build_confirmation(
        self,
        tool_name: str,
        tool_args: dict[str, Any],
        channel_key: str,
        sender_id: str,
        chat_id: str,
        account_id: str,
        reply_callback: Callable[[str], Awaitable[None]] | None = None,
    ) -> PendingConfirmation | None:
        """Build a confirmation request for a tool call.

        Returns None if the tool doesn't need confirmation (safe tool).
        """
        # Safe tools execute without confirmation
        if tool_name in _SAFE_TOOLS:
            return None

        # Build human-readable description
        action_desc = _TOOL_DESCRIPTIONS.get(tool_name, tool_name)
        entity_id = tool_args.get("entity_id", "")
        extra = ""
        if "brightness" in tool_args:
            extra = f"，亮度={tool_args['brightness']}"
        elif "temperature" in tool_args:
            extra = f"，温度={tool_args['temperature']}°C"
        elif "domain" in tool_args and "service" in tool_args:
            extra = f"（{tool_args['domain']}.{tool_args['service']}）"

        description = f"{action_desc} {entity_id}{extra}"

        confirm_id = f"cfm_{uuid.uuid4().hex[:12]}"
        pending = PendingConfirmation(
            confirm_id=confirm_id,
            tool_name=tool_name,
            tool_args=tool_args,
            description=description,
            created_at=time.time(),
            channel_key=channel_key,
            sender_id=sender_id,
            chat_id=chat_id,
            account_id=account_id,
            reply_callback=reply_callback,
        )

        self._pending[confirm_id] = pending
        _LOGGER.info("Confirmation required: %s [%s]", description, confirm_id)
        return pending

    async def wait_for_confirmation(
        self, confirm_id: str, timeout: float = CONFIRM_TIMEOUT_SECONDS
    ) -> bool:
        """Wait for user to approve/reject. Returns True if approved."""
        pending = self._pending.get(confirm_id)
        if not pending:
            return False

        loop = asyncio.get_event_loop()
        pending._future = loop.create_future()

        try:
            result = await asyncio.wait_for(pending._future, timeout=timeout)
            return result
        except asyncio.TimeoutError:
            _LOGGER.info("Confirmation timed out: %s", confirm_id)
            return False
        finally:
            self._pending.pop(confirm_id, None)

    def resolve_confirmation(self, confirm_id: str, approved: bool) -> bool:
        """Resolve a pending confirmation (called from card callback or text handler)."""
        pending = self._pending.get(confirm_id)
        if not pending or not pending._future:
            _LOGGER.warning("Confirmation %s not found or already resolved", confirm_id)
            return False

        if not pending._future.done():
            pending._future.set_result(approved)
            _LOGGER.info("Confirmation %s resolved: %s", confirm_id, "approved" if approved else "rejected")
            return True
        return False

    async def execute_tool(
        self, tool_name: str, tool_args: dict[str, Any]
    ) -> dict[str, Any]:
        """Execute a tool call via HA REST API."""
        import aiohttp

        token = self._get_ha_token()
        if not token:
            return {"success": False, "error": "No HA token available"}

        try:
            if tool_name == "ha_turn_on":
                entity_id = tool_args.get("entity_id", "")
                data = {"entity_id": entity_id}
                if "brightness" in tool_args:
                    data["brightness"] = tool_args["brightness"]
                return await self._call_ha_service("light", "turn_on", data, token)

            elif tool_name == "ha_turn_off":
                return await self._call_ha_service(
                    "homeassistant", "turn_off",
                    {"entity_id": tool_args.get("entity_id", "")}, token
                )

            elif tool_name == "ha_toggle":
                return await self._call_ha_service(
                    "homeassistant", "toggle",
                    {"entity_id": tool_args.get("entity_id", "")}, token
                )

            elif tool_name == "ha_set_brightness":
                return await self._call_ha_service("light", "turn_on", {
                    "entity_id": tool_args.get("entity_id", ""),
                    "brightness": tool_args.get("brightness", 128),
                }, token)

            elif tool_name == "ha_set_temperature":
                return await self._call_ha_service("climate", "set_temperature", {
                    "entity_id": tool_args.get("entity_id", ""),
                    "temperature": tool_args.get("temperature", 26),
                }, token)

            elif tool_name == "ha_call_service":
                return await self._call_ha_service(
                    tool_args.get("domain", ""),
                    tool_args.get("service", ""),
                    tool_args.get("data", {}),
                    token,
                )

            elif tool_name == "ha_get_state":
                return await self._get_entity_state(
                    tool_args.get("entity_id", ""), token
                )

            else:
                return {"success": False, "error": f"Unknown tool: {tool_name}"}

        except Exception as err:
            _LOGGER.error("Tool execution failed: %s", err)
            return {"success": False, "error": str(err)}

    async def _call_ha_service(
        self, domain: str, service: str, data: dict, token: str
    ) -> dict[str, Any]:
        """Call a Home Assistant service."""
        import aiohttp

        url = f"{HA_URL}/api/services/{domain}/{service}"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=data, headers=headers) as resp:
                if resp.status >= 400:
                    err_body = await resp.text()
                    return {"success": False, "error": f"HA service failed ({resp.status}): {err_body}"}
                return {"success": True, "message": f"已执行 {domain}.{service}"}

    async def _get_entity_state(self, entity_id: str, token: str) -> dict[str, Any]:
        """Get entity state from HA."""
        import aiohttp

        url = f"{HA_URL}/api/states/{entity_id}"
        headers = {"Authorization": f"Bearer {token}"}

        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as resp:
                state = await resp.json()
                return {
                    "success": True,
                    "state": state.get("state"),
                    "attributes": state.get("attributes", {}),
                }

    def get_pending(self, confirm_id: str) -> PendingConfirmation | None:
        """Get a pending confirmation by ID."""
        return self._pending.get(confirm_id)

    def cleanup_expired(self) -> int:
        """Remove expired confirmations. Returns count removed."""
        now = time.time()
        expired = [
            cid for cid, p in self._pending.items()
            if now - p.created_at > CONFIRM_TIMEOUT_SECONDS
        ]
        for cid in expired:
            pending = self._pending.pop(cid, None)
            if pending and pending._future and not pending._future.done():
                pending._future.set_result(False)
        return len(expired)


# Singleton
_confirm_manager: ActionConfirmManager | None = None


def get_confirm_manager() -> ActionConfirmManager:
    """Get or create the singleton ActionConfirmManager."""
    global _confirm_manager
    if _confirm_manager is None:
        _confirm_manager = ActionConfirmManager()
    return _confirm_manager
