#!/usr/bin/env python3
"""Test MCP server directly"""
import json
import urllib.request

url = "http://127.0.0.1:14095/mcp"

# Test tools/list
data = json.dumps({
    "jsonrpc": "2.0",
    "method": "tools/list",
    "params": {},
    "id": 1
}).encode()

req = urllib.request.Request(
    url,
    data=data,
    headers={"Content-Type": "application/json"},
    method="POST"
)

try:
    resp = urllib.request.urlopen(req, timeout=10)
    result = resp.read().decode()
    print("MCP tools/list response:")
    print(result[:2000])
except Exception as e:
    print(f"Error: {e}")
