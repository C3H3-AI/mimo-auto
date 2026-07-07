#!/usr/bin/env python3
"""Test light control"""
import urllib.request
import json

session_id = "ses_0c8ba3b3fffeICObcCbM7NsNHT"
url = f"http://127.0.0.1:14095/session/{session_id}/message"

data = json.dumps({
    "message": "turn on light",
    "parts": [{"type": "text", "text": "turn on light"}]
}).encode()

req = urllib.request.Request(
    url,
    data=data,
    headers={"Content-Type": "application/json"},
    method="POST"
)

try:
    resp = urllib.request.urlopen(req, timeout=60)
    result = resp.read().decode()
    print(f"Response: {result[:2000]}")
except Exception as e:
    print(f"Error: {e}")
