#!/usr/bin/env python3
"""Test MCP server"""
import json
import urllib.request

url = "http://127.0.0.1:14095/mcp"
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
    print("Status:", resp.status)
    print("Response:", resp.read().decode()[:1000])
except Exception as e:
    print("Error:", e)
