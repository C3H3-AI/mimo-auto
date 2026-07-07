#!/usr/bin/env python3
"""MCP Server for Home Assistant tools.

Exposes Home Assistant tools via MCP (Model Context Protocol)
so mimo serve can call them for device control, automation, etc.
"""
import asyncio
import json
import sys
from typing import Any

# HA API configuration
HA_URL = "http://supervisor/core"
HA_TOKEN = None  # Will be set from environment or config


def get_ha_token() -> str:
    """Get HA token from environment."""
    import os
    token = os.environ.get("SUPERVISOR_TOKEN") or os.environ.get("HASSIO_TOKEN")
    if not token:
        # Try reading from file
        try:
            with open("/run/secrets/supervisor_token", "r") as f:
                token = f.read().strip()
        except:
            pass
    return token or ""


async def call_ha_service(domain: str, service: str, data: dict) -> dict:
    """Call a Home Assistant service."""
    import aiohttp
    token = get_ha_token()
    url = f"{HA_URL}/api/services/{domain}/{service}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=data, headers=headers) as resp:
            return await resp.json()


async def get_entity_state(entity_id: str) -> dict:
    """Get entity state from Home Assistant."""
    import aiohttp
    token = get_ha_token()
    url = f"{HA_URL}/api/states/{entity_id}"
    headers = {"Authorization": f"Bearer {token}"}
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as resp:
            return await resp.json()


async def get_all_states() -> list:
    """Get all entity states."""
    import aiohttp
    token = get_ha_token()
    url = f"{HA_URL}/api/states"
    headers = {"Authorization": f"Bearer {token}"}
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as resp:
            return await resp.json()


# MCP Tool definitions
TOOLS = [
    {
        "name": "ha_turn_on",
        "description": "Turn on a device (light, switch, etc.)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "entity_id": {"type": "string", "description": "Entity ID to turn on"}
            },
            "required": ["entity_id"]
        }
    },
    {
        "name": "ha_turn_off",
        "description": "Turn off a device (light, switch, etc.)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "entity_id": {"type": "string", "description": "Entity ID to turn off"}
            },
            "required": ["entity_id"]
        }
    },
    {
        "name": "ha_toggle",
        "description": "Toggle a device state",
        "inputSchema": {
            "type": "object",
            "properties": {
                "entity_id": {"type": "string", "description": "Entity ID to toggle"}
            },
            "required": ["entity_id"]
        }
    },
    {
        "name": "ha_set_brightness",
        "description": "Set brightness for a light",
        "inputSchema": {
            "type": "object",
            "properties": {
                "entity_id": {"type": "string", "description": "Light entity ID"},
                "brightness": {"type": "integer", "description": "Brightness 0-255"}
            },
            "required": ["entity_id", "brightness"]
        }
    },
    {
        "name": "ha_set_temperature",
        "description": "Set temperature for a climate device",
        "inputSchema": {
            "type": "object",
            "properties": {
                "entity_id": {"type": "string", "description": "Climate entity ID"},
                "temperature": {"type": "number", "description": "Target temperature"}
            },
            "required": ["entity_id", "temperature"]
        }
    },
    {
        "name": "ha_get_state",
        "description": "Get the state of an entity",
        "inputSchema": {
            "type": "object",
            "properties": {
                "entity_id": {"type": "string", "description": "Entity ID to query"}
            },
            "required": ["entity_id"]
        }
    },
    {
        "name": "ha_get_all_lights",
        "description": "Get all lights and their states",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "ha_call_service",
        "description": "Call any Home Assistant service",
        "inputSchema": {
            "type": "object",
            "properties": {
                "domain": {"type": "string", "description": "Service domain (e.g., light, switch)"},
                "service": {"type": "string", "description": "Service name (e.g., turn_on, turn_off)"},
                "data": {"type": "object", "description": "Service data"}
            },
            "required": ["domain", "service", "data"]
        }
    },
]


async def handle_tool_call(tool_name: str, arguments: dict) -> dict:
    """Handle a tool call and return the result."""
    try:
        if tool_name == "ha_turn_on":
            result = await call_ha_service("homeassistant", "turn_on", {"entity_id": arguments["entity_id"]})
            return {"success": True, "message": f"Turned on {arguments['entity_id']}"}

        elif tool_name == "ha_turn_off":
            result = await call_ha_service("homeassistant", "turn_off", {"entity_id": arguments["entity_id"]})
            return {"success": True, "message": f"Turned off {arguments['entity_id']}"}

        elif tool_name == "ha_toggle":
            result = await call_ha_service("homeassistant", "toggle", {"entity_id": arguments["entity_id"]})
            return {"success": True, "message": f"Toggled {arguments['entity_id']}"}

        elif tool_name == "ha_set_brightness":
            result = await call_ha_service("light", "turn_on", {
                "entity_id": arguments["entity_id"],
                "brightness": arguments["brightness"]
            })
            return {"success": True, "message": f"Set brightness to {arguments['brightness']}"}

        elif tool_name == "ha_set_temperature":
            result = await call_ha_service("climate", "set_temperature", {
                "entity_id": arguments["entity_id"],
                "temperature": arguments["temperature"]
            })
            return {"success": True, "message": f"Set temperature to {arguments['temperature']}"}

        elif tool_name == "ha_get_state":
            state = await get_entity_state(arguments["entity_id"])
            return {"success": True, "state": state.get("state"), "attributes": state.get("attributes", {})}

        elif tool_name == "ha_get_all_lights":
            states = await get_all_states()
            lights = [s for s in states if s["entity_id"].startswith("light.")]
            return {"success": True, "lights": [{"id": s["entity_id"], "state": s["state"], "name": s.get("attributes", {}).get("friendly_name", "")} for s in lights]}

        elif tool_name == "ha_call_service":
            result = await call_ha_service(arguments["domain"], arguments["service"], arguments.get("data", {}))
            return {"success": True, "result": result}

        else:
            return {"success": False, "error": f"Unknown tool: {tool_name}"}

    except Exception as e:
        return {"success": False, "error": str(e)}


# MCP Server implementation
class MCPServer:
    """Simple MCP server implementation."""

    def __init__(self):
        self.tools = {t["name"]: t for t in TOOLS}

    async def handle_request(self, request: dict) -> dict:
        """Handle an MCP request."""
        method = request.get("method", "")
        params = request.get("params", {})
        request_id = request.get("id")

        if method == "initialize":
            return self.handle_initialize(request_id, params)
        elif method == "tools/list":
            return self.handle_tools_list(request_id)
        elif method == "tools/call":
            return await self.handle_tools_call(request_id, params)
        else:
            return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": f"Method not found: {method}"}}

    def handle_initialize(self, request_id: int, params: dict) -> dict:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "ha-mcp-server", "version": "1.0.0"}
            }
        }

    def handle_tools_list(self, request_id: int) -> dict:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {"tools": TOOLS}
        }

    async def handle_tools_call(self, request_id: int, params: dict) -> dict:
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})

        result = await handle_tool_call(tool_name, arguments)

        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}],
                "isError": not result.get("success", False)
            }
        }


async def main():
    """Run MCP server on stdio."""
    server = MCPServer()

    while True:
        try:
            line = await asyncio.get_event_loop().run_in_executor(None, sys.stdin.readline)
            if not line:
                break

            request = json.loads(line.strip())
            response = await server.handle_request(request)

            print(json.dumps(response, ensure_ascii=False))
            sys.stdout.flush()

        except json.JSONDecodeError:
            continue
        except Exception as e:
            print(json.dumps({"jsonrpc": "2.0", "error": {"code": -32603, "message": str(e)}}))
            sys.stdout.flush()


if __name__ == "__main__":
    asyncio.run(main())
